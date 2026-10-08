"""
Throttling of the emailed-link operations, over real HTTP (S1-010).

The mechanism - fixed-window counting, hashed subjects, atomic
`add`/`incr`, fail-closed on a broken cache - is `identity.throttling`'s and
is already covered by `tests/test_throttling.py`. What is new with the
emailed flows is *which* limits exist and what each one is for, and that is
what this file pins down. Four of them, for four different threats:

- `requestPasswordReset` per submitted address - the mail-cannon limit. Five
  an hour is far above what somebody locked out of their own account needs
  and far below anything worth automating.
- `requestPasswordReset` per client address - the spray limit, which the
  per-address counter cannot see at all, since every victim's address carries
  its own separate budget.
- `resetPassword` and `activateAccount` per client address - not
  credential-guessing limits (the token is 384 bits of `secrets` output, which
  no amount of trying guesses) but limits on how much *work* an anonymous
  caller can make the server do: a password re-hash, a revoke-all-sessions
  update, a lookup and a flag flip.
- `resendActivationEmail` per submitted address and per client - the same
  mail-cannon reason, tighter still, because a resend is only ever wanted
  because a first message was lost.

The property most easily broken and hardest to notice is the informational
one: these limits are keyed on the *submitted* address and never resolved to
a user, so a miss has to cost the caller exactly the same budget as a hit. If
it did not, the throttle itself would become the account-existence oracle
the whole flow exists to avoid. Several tests below compare the two
outcomes directly for exactly that reason.
"""

import json

import pytest
from django.core import mail
from django.test import Client

from identity import throttling
from identity.models import User

PASSWORD = 'a-strong-unique-pass-1'
NEW_PASSWORD = 'an-even-stronger-pass-2'
EXISTING_EMAIL = 'ada@example.com'
ABSENT_EMAIL = 'nobody-at-all@example.com'

REGISTER = """
mutation Register($input: RegisterInput!) {
  register(input: $input) { success }
}
"""

REQUEST_PASSWORD_RESET = """
mutation RequestPasswordReset($input: RequestPasswordResetInput!) {
  requestPasswordReset(input: $input) { success message field }
}
"""

RESET_PASSWORD = """
mutation ResetPassword($input: ResetPasswordInput!) {
  resetPassword(input: $input) { success message field }
}
"""

ACTIVATE_ACCOUNT = """
mutation ActivateAccount($input: ActivateAccountInput!) {
  activateAccount(input: $input) { success message field }
}
"""

RESEND_ACTIVATION = """
mutation ResendActivationEmail($input: ResendActivationEmailInput!) {
  resendActivationEmail(input: $input) { success message field }
}
"""


class Api:
    """A real HTTP GraphQL caller, optionally impersonating a client address."""

    def __init__(self, client: Client, remote_addr='203.0.113.10'):
        self.client = client
        self.remote_addr = remote_addr

    def at(self, remote_addr):
        return Api(self.client, remote_addr)

    def post(self, query, variables=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        response = self.client.post(
            '/graphql/',
            data=json.dumps(payload),
            content_type='application/json',
            REMOTE_ADDR=self.remote_addr,
        )
        assert response.status_code == 200, response.content
        return response

    def field(self, query, name, variables=None):
        body = self.post(query, variables).json()
        assert 'errors' not in body, body
        return body['data'][name]

    def register(self, email=EXISTING_EMAIL):
        return self.field(
            REGISTER,
            'register',
            {
                'input': {
                    'firstName': 'Ada',
                    'lastName': 'Lovelace',
                    'email': email,
                    'phoneNumber': '+255712345678',
                    'password': PASSWORD,
                }
            },
        )

    def request_reset(self, email=EXISTING_EMAIL):
        return self.field(
            REQUEST_PASSWORD_RESET, 'requestPasswordReset', {'input': {'email': email}}
        )

    def reset_password(self, token, new_password=NEW_PASSWORD):
        return self.field(
            RESET_PASSWORD,
            'resetPassword',
            {'input': {'token': token, 'newPassword': new_password}},
        )

    def activate(self, token):
        return self.field(ACTIVATE_ACCOUNT, 'activateAccount', {'input': {'token': token}})

    def resend_activation(self, email=EXISTING_EMAIL):
        return self.field(RESEND_ACTIVATION, 'resendActivationEmail', {'input': {'email': email}})


@pytest.fixture
def api(client: Client) -> Api:
    return Api(client)


@pytest.fixture
def registered(api: Api) -> Api:
    assert api.register()['success'] is True
    mail.outbox.clear()  # the registration's activation mail
    return api


def exhaust(caller, policy, *args):
    """
    Spend `policy`'s whole budget and return the refusal.

    Only valid where this policy is the *first* limit the call can hit - a
    per-client loop must vary its address, or the tighter per-address policy
    refuses first and the loop never reaches the budget it meant to spend.
    """
    for _ in range(policy.limit):
        result = caller(*args)
        assert result['message'] != throttling.THROTTLED_MESSAGE, result
    return caller(*args)


def attempts_before_refusal(caller, policy, *args):
    """
    The 1-based attempt at which `caller` is first refused by `policy`.

    Comparing this number between an address that has an account and one that
    does not is the only way to show a miss is not cheaper than a hit: both
    must run out of budget at exactly the same attempt.
    """
    for attempt in range(1, policy.limit + 2):
        if caller(*args)['message'] == throttling.THROTTLED_MESSAGE:
            return attempt
    raise AssertionError(f'{policy.name} never refused the caller')


# --- requestPasswordReset -----------------------------------------------------------


@pytest.mark.django_db
class TestPasswordResetRequestThrottling:
    def test_the_per_address_budget_is_enforced_and_then_reported(self, registered: Api):
        result = exhaust(registered.request_reset, throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT)

        assert result['success'] is False
        assert result['message'] == throttling.THROTTLED_MESSAGE

    def test_a_throttle_names_no_field(self, registered: Api):
        """
        A throttle is not a validation failure. Attributing it to the email
        input would put an error next to a field that was perfectly valid.
        """
        result = exhaust(registered.request_reset, throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT)

        assert result['field'] is None

    def test_an_over_limit_request_sends_no_mail(self, registered: Api):
        """
        The limit exists to bound how much mail this endpoint can cause, so an
        over-limit request must not reach the send at all. The messages from
        the requests that were *within* budget are cleared first, so this
        asserts about the refused one only.
        """
        exhaust(registered.request_reset, throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT)
        mail.outbox.clear()

        registered.request_reset(EXISTING_EMAIL)

        assert mail.outbox == []

    def test_an_absent_address_costs_exactly_as_much_budget_as_a_real_one(self, api: Api):
        """
        The informational property, and the reason the per-address subject is
        the *submitted* address rather than a looked-up account.

        A miss that were cheaper than a hit would make this endpoint an oracle
        in two requests: probe until the refusal, and whether you were refused
        on attempt six or not at all tells you whether the address is
        registered. So the two are driven from separate client addresses - or
        the per-client counter would be shared and the comparison meaningless -
        and the attempt at which each runs out must be the same number.
        """
        api.register()
        prober = api.at('198.51.100.10')
        stranger = api.at('198.51.100.20')

        real = attempts_before_refusal(
            prober.request_reset, throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT, EXISTING_EMAIL
        )
        absent = attempts_before_refusal(
            stranger.request_reset, throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT, ABSENT_EMAIL
        )

        assert real == absent == throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT.limit + 1

    def test_the_counter_is_not_shared_between_two_addresses(self, registered: Api):
        """
        Otherwise one noisy caller could lock a second, innocent person out of
        resetting their own password.
        """
        exhaust(registered.request_reset, throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT)

        assert registered.request_reset('someone.else@example.com')['success'] is True

    def test_the_spray_limit_is_per_client_address_not_per_victim(self, registered: Api):
        """
        One address mailing many different victims: each victim carries their
        own budget, so only the per-client counter can see it.
        """
        for index in range(throttling.PASSWORD_RESET_REQUEST_PER_CLIENT.limit):
            registered.request_reset(f'victim-{index}@example.com')

        result = registered.request_reset('one-more-victim@example.com')

        assert result['message'] == throttling.THROTTLED_MESSAGE

    def test_a_second_client_address_has_its_own_budget(self, registered: Api):
        for index in range(throttling.PASSWORD_RESET_REQUEST_PER_CLIENT.limit):
            registered.request_reset(f'victim-{index}@example.com')

        elsewhere = registered.at('198.51.100.7')

        assert elsewhere.request_reset(EXISTING_EMAIL)['success'] is True


# --- resetPassword ------------------------------------------------------------------


@pytest.mark.django_db
class TestPasswordResetExecutionThrottling:
    def _token(self, api: Api):
        assert api.request_reset()['success'] is True
        body = mail.outbox[-1].body
        from urllib.parse import parse_qs, urlparse

        url = next(line.strip() for line in body.splitlines() if '?token=' in line)
        return parse_qs(urlparse(url).query)['token'][0]

    def test_the_per_client_budget_is_enforced(self, registered: Api):
        for _ in range(throttling.PASSWORD_RESET_PER_CLIENT.limit):
            result = registered.reset_password('nonsense')
            assert result['message'] != throttling.THROTTLED_MESSAGE

        result = registered.reset_password('nonsense')

        assert result['success'] is False
        assert result['message'] == throttling.THROTTLED_MESSAGE

    def test_a_throttled_reset_does_not_reach_the_password_change(self, registered: Api):
        """
        The limit is on the work an anonymous request can cause - a re-hash and
        a revoke-all-sessions update - so an over-limit caller must not reach
        it, even holding a perfectly valid token.
        """
        token = self._token(registered)
        for _ in range(throttling.PASSWORD_RESET_PER_CLIENT.limit):
            registered.reset_password('nonsense')

        result = registered.reset_password(token)

        assert result['message'] == throttling.THROTTLED_MESSAGE
        assert User.objects.get(email=EXISTING_EMAIL).check_password(PASSWORD)

    def test_a_throttled_reset_does_not_spend_the_token(self, registered: Api):
        """
        The link must survive the caller being throttled: being over a request
        rate says nothing about whether the link is genuine, and burning it
        would let a burst of junk traffic invalidate a real user's only way in.
        """
        token = self._token(registered)
        for _ in range(throttling.PASSWORD_RESET_PER_CLIENT.limit):
            registered.reset_password('nonsense')
        registered.reset_password(token)

        elsewhere = registered.at('198.51.100.7')
        assert elsewhere.reset_password(token)['success'] is True

    def test_a_valid_token_is_not_a_way_out_of_the_limit(self, registered: Api):
        """
        Why this policy is keyed on the client address rather than the token: a
        token-keyed counter would let a caller who happened to hold a valid
        one escape by presenting a different (invalid) one.
        """
        token = self._token(registered)
        for _ in range(throttling.PASSWORD_RESET_PER_CLIENT.limit):
            registered.reset_password('nonsense')

        # Same client, real token: still refused.
        assert registered.reset_password(token)['message'] == throttling.THROTTLED_MESSAGE


# --- activateAccount ----------------------------------------------------------------


@pytest.mark.django_db
class TestActivationThrottling:
    def test_the_per_client_budget_is_enforced(self, registered: Api):
        for _ in range(throttling.ACTIVATION_PER_CLIENT.limit):
            result = registered.activate('nonsense')
            assert result['message'] != throttling.THROTTLED_MESSAGE

        result = registered.activate('nonsense')

        assert result['success'] is False
        assert result['message'] == throttling.THROTTLED_MESSAGE

    def test_activation_and_reset_have_separate_budgets(self, registered: Api):
        """
        They are different operations with genuinely different traffic, so one
        deployment may legitimately want them set differently - exhausting one
        must not throttle the other.
        """
        for _ in range(throttling.ACTIVATION_PER_CLIENT.limit):
            registered.activate('nonsense')

        result = registered.reset_password('nonsense')

        assert result['message'] != throttling.THROTTLED_MESSAGE


# --- resendActivationEmail ----------------------------------------------------------


@pytest.mark.django_db
class TestActivationResendThrottling:
    def test_the_per_address_budget_is_the_tightest_in_the_module(self, registered: Api):
        result = exhaust(registered.resend_activation, throttling.ACTIVATION_RESEND_PER_ACCOUNT)

        assert result['message'] == throttling.THROTTLED_MESSAGE

    def test_an_over_limit_resend_sends_no_mail(self, registered: Api):
        exhaust(registered.resend_activation, throttling.ACTIVATION_RESEND_PER_ACCOUNT)
        mail.outbox.clear()

        registered.resend_activation(EXISTING_EMAIL)

        assert mail.outbox == []

    def test_an_absent_address_costs_exactly_as_much_budget_as_a_real_one(self, api: Api):
        """The same property as the reset request, for the resend limit."""
        api.register()
        prober = api.at('198.51.100.10')
        stranger = api.at('198.51.100.20')

        real = attempts_before_refusal(
            prober.resend_activation, throttling.ACTIVATION_RESEND_PER_ACCOUNT, EXISTING_EMAIL
        )
        absent = attempts_before_refusal(
            stranger.resend_activation, throttling.ACTIVATION_RESEND_PER_ACCOUNT, ABSENT_EMAIL
        )

        assert real == absent == throttling.ACTIVATION_RESEND_PER_ACCOUNT.limit + 1

    def test_the_spray_limit_is_enforced_per_client(self, registered: Api):
        for index in range(throttling.ACTIVATION_RESEND_PER_CLIENT.limit):
            registered.resend_activation(f'victim-{index}@example.com')

        result = registered.resend_activation('one-more-victim@example.com')

        assert result['message'] == throttling.THROTTLED_MESSAGE

    def test_a_resend_budget_is_separate_from_the_reset_request_budget(self, registered: Api):
        exhaust(registered.resend_activation, throttling.ACTIVATION_RESEND_PER_ACCOUNT)

        result = registered.request_reset(EXISTING_EMAIL)

        assert result['message'] != throttling.THROTTLED_MESSAGE


# --- shared properties --------------------------------------------------------------


@pytest.mark.django_db
class TestSharedThrottleProperties:
    @pytest.mark.parametrize(
        'policy',
        [
            throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT,
            throttling.PASSWORD_RESET_REQUEST_PER_CLIENT,
            throttling.PASSWORD_RESET_PER_CLIENT,
            throttling.ACTIVATION_PER_CLIENT,
            throttling.ACTIVATION_RESEND_PER_ACCOUNT,
            throttling.ACTIVATION_RESEND_PER_CLIENT,
        ],
    )
    def test_every_email_policy_counts_on_the_dedicated_alias(self, settings, policy):
        from django.core.cache import caches
        from django.test import RequestFactory

        request = RequestFactory().post('/graphql/', REMOTE_ADDR='203.0.113.10')
        subject = 'ada@example.com' if 'account' in policy.name else request.META['REMOTE_ADDR']
        throttling.register_attempt(policy, subject)

        dedicated = caches[settings.AUTH_THROTTLE_CACHE_ALIAS]
        general = caches['default']
        key = throttling._cache_key(policy, subject)
        assert dedicated.get(key) == 1
        assert general.get(key) is None

    def test_the_throttle_message_names_no_account_and_no_address(self):
        """
        One message for every limit. It has to distinguish nothing: not which
        operation was hit, not which account, not whether the address exists.
        """
        for policy in (
            throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT,
            throttling.PASSWORD_RESET_PER_CLIENT,
            throttling.ACTIVATION_PER_CLIENT,
            throttling.ACTIVATION_RESEND_PER_ACCOUNT,
        ):
            assert policy.name not in throttling.THROTTLED_MESSAGE

        assert EXISTING_EMAIL not in throttling.THROTTLED_MESSAGE
        assert 'password' not in throttling.THROTTLED_MESSAGE.lower()
        assert 'activation' not in throttling.THROTTLED_MESSAGE.lower()

    def test_a_broken_throttle_cache_refuses_an_email_operation(self, monkeypatch, registered):
        """
        Fail closed. If the counter cannot be read, no request may be admitted
        on the assumption that the caller is legitimate - including these,
        where admitting one costs an outbound message.
        """
        from django.core.cache import InvalidCacheBackendError

        class _Broken:
            def add(self, *args, **kwargs):
                raise InvalidCacheBackendError('throttle cache unavailable')

        monkeypatch.setattr(throttling, '_throttle_cache', lambda: _Broken())

        result = registered.request_reset(EXISTING_EMAIL)

        assert result['success'] is False
        assert result['message'] == throttling.THROTTLED_MESSAGE
        assert mail.outbox == []
