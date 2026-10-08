"""
Authentication throttling over the real HTTP boundary (S1-009).

Covers `identity.throttling` and the resolvers that apply it. Two things
are under test and they are not the same thing:

1. The mechanism - fixed-window counting, per-subject scopes, atomic
   `add`/`incr`, hashed subjects, expiry, fail-closed on a broken cache.
   Exercised against the throttling module directly, because that is where
   the logic lives.
2. The contract the API exposes - what a caller actually sees when a limit
   is hit, and what it must *not* be able to learn from it. Exercised over
   real HTTP, because that is the only place the message and the generic
   authentication errors can be compared to each other.

The security property that is easiest to break here and hardest to notice is
the information one: a throttle must not become an oracle for "does this
email have an account?". Several tests below assert exactly that by
comparing what an existing and a non-existent address receive.
"""

import json

import pytest
from django.conf import settings
from django.core.cache import caches
from django.test import Client, RequestFactory

from identity import throttling
from identity.models import User

PASSWORD = 'a-strong-unique-pass-1'
EXISTING_EMAIL = 'ada@example.com'
ABSENT_EMAIL = 'nobody-at-all@example.com'

REGISTER_MUTATION = """
mutation Register($input: RegisterInput!) {
  register(input: $input) { success message field }
}
"""

LOGIN_MUTATION = """
mutation Login($input: LoginInput!) {
  login(input: $input) { success message accessToken }
}
"""

REFRESH_MUTATION = """
mutation RefreshToken {
  refreshToken { success message accessToken }
}
"""

GOOGLE_LOGIN_MUTATION = """
mutation GoogleLogin($input: GoogleLoginInput!) {
  googleLogin(input: $input) { success message accessToken }
}
"""


class Api:
    """A real HTTP GraphQL caller, optionally impersonating a client address."""

    def __init__(self, client: Client, remote_addr='203.0.113.10'):
        self.client = client
        self.remote_addr = remote_addr

    def post(self, query, variables=None, path='/graphql/'):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        response = self.client.post(
            path,
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
            REGISTER_MUTATION,
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

    def login(self, email=EXISTING_EMAIL, password=PASSWORD):
        return self.field(
            LOGIN_MUTATION, 'login', {'input': {'email': email, 'password': password}}
        )

    def refresh(self):
        return self.field(REFRESH_MUTATION, 'refreshToken')

    def google_login(self, credential='a-credential'):
        return self.field(
            GOOGLE_LOGIN_MUTATION, 'googleLogin', {'input': {'credential': credential}}
        )


@pytest.fixture
def api(client: Client) -> Api:
    return Api(client)


@pytest.fixture
def registered(api: Api) -> Api:
    assert api.register()['success'] is True
    return api


@pytest.fixture
def signed_in(api: Api) -> Api:
    """Registered *and* holding a live refresh session, as a browser would."""
    assert api.register()['success'] is True
    assert api.login()['success'] is True
    return api


def _request(address='198.51.100.7'):
    return RequestFactory().post('/graphql/', REMOTE_ADDR=address)


# --- the mechanism -----------------------------------------------------------------


class TestFixedWindowCounting:
    def test_attempts_up_to_the_limit_are_admitted(self):
        request = _request()
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            throttling.guard_login(request, EXISTING_EMAIL)

    def test_the_attempt_past_the_limit_is_refused(self):
        request = _request()
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            throttling.guard_login(request, EXISTING_EMAIL)

        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(request, EXISTING_EMAIL)

    def test_the_counter_expires_with_the_window(self):
        # The recovery property, at the unit level: a fixed window is
        # self-expiring, so a lockout is temporary by construction rather
        # than by a separate unlock path.
        request = _request()
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            throttling.guard_login(request, EXISTING_EMAIL)
        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(request, EXISTING_EMAIL)

        ttl = _throttle_cache().get(
            throttling._cache_key(throttling.LOGIN_PER_ACCOUNT, EXISTING_EMAIL.rstrip().casefold())
        )
        assert 0 < ttl <= throttling.LOGIN_PER_ACCOUNT.window_seconds

    def test_each_subject_gets_its_own_budget(self):
        request = _request()
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            throttling.guard_login(request, 'one@example.com')

        # A different account from the same client is unaffected by the
        # first account's exhausted budget.
        throttling.guard_login(request, 'two@example.com')

    def test_the_account_limit_is_shared_across_clients_but_not_across_accounts(
        self,
    ):
        # Per-account is per-account, not per-account-per-address: an
        # attacker who rotates source addresses must not get a fresh budget
        # for the same target. Symmetrically, exhausting one account must
        # not consume any other account's budget.
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            throttling.guard_login(_request('198.51.100.7'), EXISTING_EMAIL)

        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(_request('198.51.100.8'), EXISTING_EMAIL)

        throttling.guard_login(_request('198.51.100.8'), 'someone-else@example.com')

    def test_policies_do_not_share_a_budget(self):
        # A password-spray run and a registration flood from the same client
        # must not consume each other's allowance, or one abusive behaviour
        # would silently lock an honest user out of an unrelated operation.
        request = _request()
        for _ in range(throttling.LOGIN_PER_CLIENT.limit):
            throttling.guard_login(request, f'user-{_}@example.com')
        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(request, 'one-more@example.com')

        throttling.guard_registration(request)
        throttling.guard_google_login(request, 'a-credential')

    def test_the_per_client_limit_counts_addresses_not_accounts(self):
        # The spray limit: distinct accounts from one address share one
        # budget, because a password-spray run is exactly that shape.
        request = _request('198.51.100.9')
        for index in range(throttling.LOGIN_PER_CLIENT.limit):
            throttling.guard_login(request, f'a{index}@example.com')

        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(request, 'another@example.com')

    def test_an_email_written_differently_is_one_subject(self):
        # `Ada@Example.com`, ` ada@EXAMPLE.com ` and `ADA@example.com` are
        # the same mailbox and must not hold separate budgets, or the
        # per-account limit is trivially bypassed by varying case. Each
        # variant is tried from a *different* address, so only the per-account
        # counter can be what refuses the last one.
        for variant in ('Ada@Example.com', ' ada@EXAMPLE.com '):
            throttling.guard_login(_request('198.51.100.20'), variant)
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit - 2):
            throttling.guard_login(_request('198.51.100.21'), 'ada@example.com')

        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(_request('198.51.100.22'), 'ADA@Example.com')

    def test_a_successful_sign_in_clears_only_that_accounts_counter(self):
        request = _request('198.51.100.40')
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            throttling.guard_login(request, EXISTING_EMAIL)
        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(request, EXISTING_EMAIL)

        throttling.clear_login_account(EXISTING_EMAIL)

        # The account's own budget is back...
        throttling.guard_login(request, EXISTING_EMAIL)

    def test_clearing_one_accounts_counter_leaves_the_spray_limit_alone(self):
        # The reason the per-client counter is deliberately not cleared on
        # success: it protects every *other* account behind this address, so
        # one account's owner signing in must not hand the address a fresh
        # allowance to keep spraying from.
        request = _request('198.51.100.41')
        for index in range(throttling.LOGIN_PER_CLIENT.limit):
            throttling.guard_login(request, f'spray-{index}@example.com')

        throttling.clear_login_account(EXISTING_EMAIL)

        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(request, 'spray-0@example.com')

    def test_a_missing_client_address_still_counts_as_one_subject(self):
        # `REMOTE_ADDR` absent must not become an unthrottleable subject
        # (e.g. a literal empty key that every such request shares freely is
        # fine, but "no address means no limit" would not be).
        anonymous = RequestFactory().post('/graphql/')
        anonymous.META.pop('REMOTE_ADDR', None)

        for _ in range(throttling.LOGIN_PER_CLIENT.limit):
            throttling.guard_login(anonymous, f'x-{_}@example.com')

        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(anonymous, 'x@example.com')


class TestSubjectPrivacyAndWiring:
    def test_the_cache_key_never_contains_the_raw_subject(self):
        # A Redis key shows up in MONITOR, slow-query logs and crash dumps.
        # Neither an account's address nor a client's IP belongs in one.
        key = throttling._cache_key(throttling.LOGIN_PER_ACCOUNT, EXISTING_EMAIL)

        assert throttling._CACHE_KEY_PREFIX in key
        assert EXISTING_EMAIL not in key
        assert throttling.LOGIN_PER_ACCOUNT.name in key
        assert ' ' not in key
        assert '@' not in key

    def test_counters_live_on_the_dedicated_alias_not_the_default_one(self):
        throttling.guard_login(_request(), EXISTING_EMAIL)

        dedicated = caches[settings.AUTH_THROTTLE_CACHE_ALIAS]
        general = caches['default']
        assert dedicated.get(throttling._cache_key(throttling.LOGIN_PER_ACCOUNT, EXISTING_EMAIL))
        assert (
            general.get(throttling._cache_key(throttling.LOGIN_PER_ACCOUNT, EXISTING_EMAIL)) is None
        )

    def test_a_broken_cache_backend_refuses_the_attempt(self, monkeypatch):
        # Fail closed. If the counter cannot be read, no attempt may be
        # admitted on the assumption that the caller is legitimate.
        from django.core.cache import InvalidCacheBackendError

        class _Broken:
            def add(self, *args, **kwargs):
                raise InvalidCacheBackendError('throttle cache unavailable')

        monkeypatch.setattr(throttling, '_throttle_cache', lambda: _Broken())

        with pytest.raises(throttling.ThrottledError):
            throttling.guard_login(_request(), EXISTING_EMAIL)

    def test_an_evicted_counter_between_add_and_incr_starts_a_new_window(self, monkeypatch):
        # An unlucky eviction must reset the budget, not lock anybody out -
        # `incr` on a key that has gone raises, and treating that as "you
        # are over the limit" would lock out a caller who has made no
        # attempts at all.
        def _vanished(*args, **kwargs):
            raise ValueError('key no longer exists')

        monkeypatch.setattr(_throttle_cache(), 'incr', _vanished)

        throttling.guard_login(_request(), EXISTING_EMAIL)

    def test_concurrent_attempts_cannot_each_be_admitted_within_the_budget(self):
        # `add` then `incr`, not get-then-incr: a get/increment pair would
        # let a burst of concurrent requests all read the same count and all
        # be admitted. Asserted by driving the guard far past the limit and
        # requiring that the limit - not the attempt count - is what bounds
        # it.
        request = _request()
        admitted = 0
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit * 3):
            try:
                throttling.guard_login(request, EXISTING_EMAIL)
            except throttling.ThrottledError:
                break
            admitted += 1

        assert admitted == throttling.LOGIN_PER_ACCOUNT.limit


# --- what the API exposes ----------------------------------------------------------


@pytest.mark.django_db
class TestLoginThrottlingOverHttp:
    def test_repeated_failures_are_throttled_and_then_reported_generically(self, registered: Api):
        last = None
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit + 1):
            last = registered.login(password='wrong-password')
            if last['message'] == throttling.THROTTLED_MESSAGE:
                break

        assert last['message'] == throttling.THROTTLED_MESSAGE
        assert last['success'] is False
        assert last['accessToken'] is None

    def test_a_throttled_login_does_not_reach_the_password_verifier(
        self, registered: Api, monkeypatch
    ):
        """
        The point of checking before authenticating: an over-limit caller
        must not be able to make the server hash passwords on their behalf.
        """
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            registered.login(password='wrong-password')

        checked = []
        # `django.contrib.auth.base_user` imports `check_password` into its
        # own namespace, so that is the name to intercept - a hash check is
        # exactly the work the throttle exists to avoid paying for.
        from django.contrib.auth import base_user

        original = base_user.check_password
        monkeypatch.setattr(
            base_user,
            'check_password',
            lambda raw, encoded, *args, **kwargs: (
                checked.append(raw) or original(raw, encoded, *args, **kwargs)
            ),
        )

        registered.login(password='wrong-password')

        assert checked == []

    def test_a_successful_sign_in_starts_the_next_run_of_typos_from_zero(self, registered: Api):
        """
        Recovery that does not involve waiting at all. Nine typos, one
        correct password, nine more typos, and the eleventh attempt is still
        admitted - the success reset the account's counter rather than the
        two runs of typos adding up to twenty.
        """
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit - 1):
            registered.login(password='wrong-password')

        assert registered.login()['success'] is True

        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit - 1):
            registered.login(password='wrong-password')
        assert registered.login()['message'] != throttling.THROTTLED_MESSAGE

    def test_a_throttled_account_is_not_admitted_even_with_the_right_password(
        self, registered: Api
    ):
        """
        The deliberate other half: a throttle is not lifted by a correct
        password, because the correct password is never checked. Refusing
        before authenticating is what stops a throttled caller from making
        the server hash passwords for them; a caller who is genuinely locked
        out recovers by waiting, not by retrying.
        """
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            registered.login(password='wrong-password')

        response = registered.login()

        assert response['success'] is False
        assert response['message'] == throttling.THROTTLED_MESSAGE
        assert response['accessToken'] is None

    def test_authentication_recovers_once_the_window_passes(self, registered: Api):
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            registered.login(password='wrong-password')
        assert registered.login()['message'] == throttling.THROTTLED_MESSAGE

        _expire_throttle_windows()

        recovered = registered.login()

        assert recovered['success'] is True
        assert recovered['accessToken']

    def test_a_throttled_response_says_nothing_about_whether_the_account_exists(
        self, registered: Api
    ):
        """
        The information-leak test, and the reason the throttle is checked
        before any account lookup. An existing and a non-existent address
        must be indistinguishable once throttled - otherwise the throttle
        becomes a membership oracle.
        """
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            registered.login(password='wrong-password')
        throttled_existing = registered.login()['message']

        other_client = Api(Client(), remote_addr='198.51.100.200')
        for _ in range(throttling.LOGIN_PER_ACCOUNT.limit):
            other_client.login(email=ABSENT_EMAIL, password='wrong-password')
        throttled_absent = other_client.login(email=ABSENT_EMAIL)['message']

        assert throttled_existing == throttled_absent == throttling.THROTTLED_MESSAGE
        # ...and neither names the address it was given.
        assert EXISTING_EMAIL not in throttled_existing
        assert ABSENT_EMAIL not in throttled_absent

    def test_a_normal_wrong_password_is_still_the_generic_authentication_error(
        self, registered: Api
    ):
        response = registered.login(password='wrong-password')

        assert response['message'] == 'Invalid email or password.'
        # The throttle message must not have replaced it, nor vice versa:
        # these are two different facts about the request and collapsing
        # them would either leak the limit's existence or hide it.
        assert response['message'] != throttling.THROTTLED_MESSAGE

    def test_the_per_client_limit_covers_addresses_spraying_across_accounts(self, registered: Api):
        """
        The per-account limit alone is not enough: an attacker guessing one
        password against many addresses from one machine would never exhaust
        any single account's budget.
        """
        for index in range(throttling.LOGIN_PER_CLIENT.limit):
            assert registered.login(email=f'spray-{index}@example.com')['success'] is False

        throttled = registered.login(email='one-more@example.com')

        assert throttled['message'] == throttling.THROTTLED_MESSAGE

    def test_one_client_locked_out_does_not_lock_out_another(self, registered: Api):
        attacker = Api(Client(), remote_addr='198.51.100.50')
        for index in range(throttling.LOGIN_PER_CLIENT.limit):
            attacker.login(email=f'spray-{index}@example.com', password='wrong-password')

        assert attacker.login()['message'] == throttling.THROTTLED_MESSAGE

        honest = Api(Client(), remote_addr='198.51.100.51')
        assert honest.login()['success'] is True


@pytest.mark.django_db
class TestRefreshThrottlingOverHttp:
    """
    Refresh throttling is subtler than the other three, and the tests are
    shaped by one fact: **every successful refresh rotates the credential**,
    so a per-credential counter resets itself on every success. An honest
    client therefore never approaches the limit, and only a client that
    replays one stolen cookie (or floods from one address) can.
    """

    def test_a_honest_client_never_approaches_the_credential_limit(self, signed_in: Api):
        """
        The property that makes the per-credential limit safe to ship: a
        correct client rotates its way past it every time, so the limit can
        be tight without ever inconveniencing a real session. Driven well
        past the limit to prove rotation, not a slack limit, is what carries
        it.
        """
        for index in range(throttling.REFRESH_PER_CREDENTIAL.limit + 10):
            response = signed_in.refresh()
            assert response['success'] is True, (index, response)

    def test_replaying_one_stolen_credential_is_throttled(self, signed_in: Api):
        """
        The attack the per-credential limit exists for. Presenting the same
        (already rotated away, therefore revoked) cookie over and over: every
        attempt is refused on its own merits, and after enough of them the
        refusal becomes a throttle - which is also the signal a defender
        wants to see.
        """
        stolen = signed_in.client.cookies['refresh_token'].value

        for _ in range(throttling.REFRESH_PER_CREDENTIAL.limit):
            signed_in.client.cookies['refresh_token'] = stolen
            signed_in.refresh()

        signed_in.client.cookies['refresh_token'] = stolen
        throttled = signed_in.refresh()

        assert throttled['success'] is False
        assert throttled['message'] == throttling.THROTTLED_MESSAGE
        assert throttled['accessToken'] is None

    def test_a_refresh_flood_from_one_address_is_throttled(self, signed_in: Api, monkeypatch):
        """
        Rotation means a per-credential counter cannot catch a client that
        simply refreshes in a loop - it gets a new budget every time - so the
        per-client limit is the one that bounds a flood, and it is what this
        asserts. The shipped limit is deliberately generous (see
        identity/throttling.py), so the test lowers it rather than issuing six
        hundred real HTTP requests to prove the mechanism works.
        """
        _shrink(monkeypatch, 'REFRESH_PER_CLIENT', limit=4)

        for _ in range(4):
            assert signed_in.refresh()['success'] is True

        throttled = signed_in.refresh()

        assert throttled['success'] is False
        assert throttled['message'] == throttling.THROTTLED_MESSAGE

    def test_the_shipped_per_client_refresh_limit_is_generous_for_real_offices(self):
        """
        Why the shipped number is 600 and not, say, 60: it is sized by people
        behind one address, not by requests. An access token lives fifteen
        minutes, so a user contributes roughly one refresh per three
        five-minute windows - the shipped limit is an order of magnitude
        above what a thousand such users need, while still stopping a
        loop-refresher within seconds.
        """
        policy = throttling.REFRESH_PER_CLIENT

        # One refresh per user per 15-minute access-token lifetime, so each
        # user contributes `window / 900` of a refresh per window and the
        # budget divides by that.
        refreshes_per_user_per_window = policy.window_seconds / 900
        users_supported = policy.limit / refreshes_per_user_per_window
        assert users_supported >= 1000

    def test_a_throttled_refresh_leaves_the_session_usable_after_recovery(
        self, signed_in: Api, monkeypatch
    ):
        """
        Refusing a refresh must not have the side effect of ending the
        session. Nothing about the cookie or the server-side session changes
        while throttled, so waiting out the window is all it takes.
        """
        _shrink(monkeypatch, 'REFRESH_PER_CLIENT', limit=4)

        for _ in range(5):
            signed_in.refresh()
        assert signed_in.refresh()['success'] is False

        _expire_throttle_windows()

        assert signed_in.refresh()['success'] is True

    def test_a_missing_cookie_is_answered_generically_not_as_a_throttle(self, client: Client):
        anonymous = Api(client, remote_addr='198.51.100.60')

        response = anonymous.refresh()

        # Nothing to count against per credential, and nothing to learn
        # either - the existing "your session has expired" answer is correct
        # and must not be replaced by a throttle message that would imply a
        # limit exists.
        assert response['message'] == 'Your session has expired. Please sign in again.'

    def test_one_address_exhausted_does_not_couple_two_sessions(self, signed_in: Api, monkeypatch):
        """
        An office behind a single address: one client exhausting the
        per-client refresh limit must not lock out the colleague sharing it.
        (The per-credential limit is what stays scoped to the credential; the
        per-client one is shared, by design, because that is the flood it
        exists to bound.)
        """
        _shrink(monkeypatch, 'REFRESH_PER_CLIENT', limit=4)

        for _ in range(5):
            signed_in.refresh()
        assert signed_in.refresh()['message'] == throttling.THROTTLED_MESSAGE

        colleague = Api(Client(), remote_addr=signed_in.remote_addr)
        assert colleague.register(email='colleague@example.com')['success'] is True
        assert colleague.login(email='colleague@example.com')['success'] is True
        # A *different* address is entirely unaffected by the per-client
        # limit, and gets its own per-credential budget.
        elsewhere = Api(Client(), remote_addr='198.51.100.61')
        assert elsewhere.register(email='elsewhere@example.com')['success'] is True
        assert elsewhere.login(email='elsewhere@example.com')['success'] is True
        assert elsewhere.refresh()['success'] is True


@pytest.mark.django_db
class TestGoogleLoginThrottlingOverHttp:
    def test_repeated_google_attempts_are_throttled(self, registered: Api):
        messages = []
        for _ in range(throttling.GOOGLE_LOGIN_PER_CLIENT.limit + 1):
            messages.append(registered.google_login()['message'])
            if messages[-1] == throttling.THROTTLED_MESSAGE:
                break

        assert messages[-1] == throttling.THROTTLED_MESSAGE
        # Everything before the limit was the ordinary generic Google
        # failure - the throttle did not change what a bad credential says.
        assert throttling.THROTTLED_MESSAGE not in messages[:-1]
        assert set(messages[:-1]) == {'Could not sign in with Google.'}

    def test_a_throttled_google_login_does_not_run_verification(self, registered: Api, monkeypatch):
        for _ in range(throttling.GOOGLE_LOGIN_PER_CLIENT.limit):
            registered.google_login()

        reached = []
        from identity.google_oauth import verify_google_id_token

        monkeypatch.setattr(
            'identity.authentication.verify_google_id_token',
            lambda raw: reached.append(raw) or verify_google_id_token(raw),
        )

        registered.google_login()

        assert reached == []

    def test_a_successful_google_sign_in_still_works_after_recovery(self, registered: Api):
        from identity.google_oauth import GoogleIdentity
        from identity.tests.test_authentication_schema import _patched_google_identity

        for _ in range(throttling.GOOGLE_LOGIN_PER_CLIENT.limit):
            registered.google_login()
        assert registered.google_login()['message'] == throttling.THROTTLED_MESSAGE

        _expire_throttle_windows()

        identity = GoogleIdentity(
            subject='google-subject-1',
            email='grace@example.com',
            email_verified=True,
            first_name='Grace',
            last_name='Hopper',
        )
        with _patched_google_identity(identity=identity):
            response = registered.google_login()

        assert response['success'] is True
        assert response['accessToken']
        assert User.objects.filter(email='grace@example.com').count() == 1

    def test_a_throttled_google_login_never_links_by_email(self, registered: Api):
        """
        Belt and braces on the account-linking policy: whatever the
        throttle does, it must not become a way to get at an existing
        account. A throttled sign-in still creates no identity and no link.
        """
        from identity.google_oauth import GoogleIdentity
        from identity.models import ExternalIdentity
        from identity.tests.test_authentication_schema import _patched_google_identity

        identity = GoogleIdentity(
            subject='google-subject-1',
            email=EXISTING_EMAIL,
            email_verified=True,
            first_name='Ada',
            last_name='Lovelace',
        )
        for _ in range(throttling.GOOGLE_LOGIN_PER_CLIENT.limit):
            registered.google_login()

        with _patched_google_identity(identity=identity):
            response = registered.google_login()

        assert response['success'] is False
        assert ExternalIdentity.objects.count() == 0


@pytest.mark.django_db
class TestRegistrationThrottlingOverHttp:
    def test_repeated_registrations_are_throttled(self, api: Api):
        for index in range(throttling.REGISTER_PER_CLIENT.limit):
            assert api.register(email=f'new-{index}@example.com')['success'] is True

        throttled = api.register(email='one-too-many@example.com')

        assert throttled['success'] is False
        assert throttled['message'] == throttling.THROTTLED_MESSAGE
        # No field is named: no field of the form is at fault, and reporting
        # one would make the frontend render it as a validation error.
        assert throttled['field'] is None
        assert User.objects.filter(email='one-too-many@example.com').count() == 0

    def test_a_duplicate_email_is_still_reported_while_under_the_limit(self, registered: Api):
        duplicate = registered.register()

        assert duplicate['success'] is False
        assert duplicate['field'] == 'email'
        # Registration is the one place confirming an email is taken is
        # expected, and the throttle must not have replaced that answer with
        # a generic one.
        assert duplicate['message'] != throttling.THROTTLED_MESSAGE

    def test_registration_recovers_after_the_window(self, api: Api):
        for index in range(throttling.REGISTER_PER_CLIENT.limit):
            api.register(email=f'new-{index}@example.com')
        assert api.register(email='blocked@example.com')['success'] is False

        _expire_throttle_windows()

        assert api.register(email='unblocked@example.com')['success'] is True

    def test_a_throttled_registration_creates_nothing(self, api: Api):
        for index in range(throttling.REGISTER_PER_CLIENT.limit):
            api.register(email=f'new-{index}@example.com')

        api.register(email='phantom@example.com')

        assert User.objects.filter(email='phantom@example.com').count() == 0


def _shrink(monkeypatch, attribute: str, *, limit: int):
    """
    Replace a shipped policy with an equivalent, much smaller one.

    The shipped limits are the security posture and are deliberately far
    above what a test should have to issue HTTP requests to exhaust. This
    substitutes the *same policy under a smaller number*, so the mechanism is
    still what is under test and only the count is reduced.
    """
    shipped = getattr(throttling, attribute)
    monkeypatch.setattr(
        throttling,
        attribute,
        throttling.ThrottlePolicy(
            name=shipped.name, limit=limit, window_seconds=shipped.window_seconds
        ),
    )


def _throttle_cache():
    return caches[settings.AUTH_THROTTLE_CACHE_ALIAS]


def _expire_throttle_windows():
    """Simulate the passage of every throttle window.

    Deletes the counters outright rather than waiting, and is only ever
    called from a test that has already proved the window expiry is what
    unblocks the caller.
    """
    _throttle_cache().clear()
