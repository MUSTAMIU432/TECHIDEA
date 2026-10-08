"""
The password-reset and account-activation journeys, over real HTTP (S1-010).

Everything here goes through the real endpoint - `django.test.Client` ->
URLconf -> middleware -> the `csrf_exempt` GraphQL view -> Strawberry ->
the resolvers -> `identity.services` -> the database - and the token is
obtained the only way a user obtains it: by reading the link out of the
message that was sent. Nothing is stubbed and nothing is called directly,
because the properties worth protecting here are exactly the ones that only
exist end to end:

- the link in the email points at a URL the frontend actually serves, so the
  flow is completable by a person and not only by a test;
- the reset really does invalidate the old password, and really does end
  every session the account had, including the one the browser is holding;
- the generic answer to a reset request is generic *over HTTP*, where a
  GraphQL-level difference would be visible to a caller.

The two flows are also exercised in sequence, because they share a table, a
hashing scheme and a throttle cache, and an interaction between them is
exactly the kind of thing a per-flow unit test cannot see.
"""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from identity.models import RefreshSession, User
from identity.schema import REFRESH_TOKEN_COOKIE_NAME

PASSWORD = 'a-strong-unique-pass-1'
NEW_PASSWORD = 'an-even-stronger-pass-2'

REGISTER = """
mutation Register($input: RegisterInput!) {
  register(input: $input) { success message field }
}
"""

LOGIN = """
mutation Login($input: LoginInput!) {
  login(input: $input) { success message accessToken }
}
"""

REFRESH = """
mutation RefreshToken {
  refreshToken { success message accessToken }
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
  activateAccount(input: $input) { success message field user { id isVerified } }
}
"""

RESEND_ACTIVATION = """
mutation ResendActivationEmail($input: ResendActivationEmailInput!) {
  resendActivationEmail(input: $input) { success message field }
}
"""


class Browser:
    """A browser-like GraphQL caller over Django's test client, with a cookie jar."""

    def __init__(self, client: Client):
        self.client = client

    def call(self, query, variables=None, root_field=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        response = self.client.post(
            '/graphql/', data=json.dumps(payload), content_type='application/json'
        )
        assert response.status_code == 200, response.content
        body = response.json()
        assert 'errors' not in body, body
        return response, body['data'][root_field]

    def field(self, query, variables=None, root_field=None):
        return self.call(query, variables, root_field)[1]

    def register(self, email='ada@example.com'):
        return self.field(
            REGISTER,
            {
                'input': {
                    'firstName': 'Ada',
                    'lastName': 'Lovelace',
                    'email': email,
                    'phoneNumber': '+255712345678',
                    'password': PASSWORD,
                }
            },
            'register',
        )

    def login(self, email='ada@example.com', password=PASSWORD):
        return self.field(LOGIN, {'input': {'email': email, 'password': password}}, 'login')

    def refresh(self):
        return self.field(REFRESH, None, 'refreshToken')

    def request_reset(self, email='ada@example.com'):
        return self.field(
            REQUEST_PASSWORD_RESET, {'input': {'email': email}}, 'requestPasswordReset'
        )

    def reset_password(self, token, new_password=NEW_PASSWORD):
        return self.field(
            RESET_PASSWORD,
            {'input': {'token': token, 'newPassword': new_password}},
            'resetPassword',
        )

    def activate(self, token):
        return self.field(ACTIVATE_ACCOUNT, {'input': {'token': token}}, 'activateAccount')

    def resend_activation(self, email='ada@example.com'):
        return self.field(RESEND_ACTIVATION, {'input': {'email': email}}, 'resendActivationEmail')


@pytest.fixture
def browser(client: Client) -> Browser:
    return Browser(client)


RESET_PATH = '/reset-password'
ACTIVATION_PATH = '/activate-account'


def links_for(path):
    """
    Every action link in the outbox that points at `path`, oldest first.

    Path-aware rather than "the last message", because both flows are
    genuinely in flight in some of these tests: registration sends an
    activation mail, so `mail.outbox[-1]` is only the reset mail if the reset
    was the last thing sent. Reading the wrong one would have these tests
    pass (or fail) for the wrong reason.
    """
    found = []
    for message in mail.outbox:
        for line in message.body.splitlines():
            stripped = line.strip()
            if f'{path}?token=' in stripped:
                found.append(stripped)
    return found


def last_token(path):
    links = links_for(path)
    assert links, f'no {path} link in {[m.subject for m in mail.outbox]}'
    return parse_qs(urlparse(links[-1]).query)['token'][0]


def reset_token():
    return last_token(RESET_PATH)


def activation_token():
    return last_token(ACTIVATION_PATH)


@pytest.fixture
def registered(browser: Browser):
    assert browser.register()['success'] is True
    return browser


@pytest.mark.django_db
def test_the_whole_password_reset_journey_over_real_http(registered):
    """
    The complete flow a locked-out person actually performs:

        register -> sign in -> ask for a reset -> read the emailed link
        -> set a new password -> the old password stops working
        -> the new one works -> the pre-reset session is dead.
    """
    assert registered.login()['success'] is True
    # A second session for the same account - what an attacker holding a
    # stolen cookie would be sitting on.
    elsewhere = RefreshSession.objects.create(
        user=User.objects.get(email='ada@example.com'),
        token_hash='a' * 64,
        expires_at=timezone.now() + timedelta(days=30),
    )

    result = registered.request_reset()
    assert result['success'] is True
    # Registration already sent the activation mail; this is the one reset mail.
    assert len(links_for(RESET_PATH)) == 1

    # The link points into the browser app, at a route the frontend serves.
    url = urlparse(links_for(RESET_PATH)[-1])
    assert url.path == RESET_PATH
    assert url.query.startswith('token=')

    reset = registered.reset_password(reset_token())
    assert reset['success'] is True

    # The old password is dead, the new one works.
    assert registered.login(password=PASSWORD)['success'] is False
    assert registered.login(password=NEW_PASSWORD)['success'] is True

    # ...and every session that existed before the reset was revoked, so a
    # stolen cookie cannot outlive the password change. Checked through the
    # model rather than through `refreshToken` because this browser's own
    # cookie was cleared by the response (a separate, tested behaviour); this
    # is about the session somebody else is holding.
    elsewhere.refresh_from_db()
    assert elsewhere.is_active is False


@pytest.mark.django_db
def test_the_link_can_only_be_followed_once(registered):
    """
    A link that stays valid would be a standing credential in a mail client's
    search index, a shared inbox, or a chat app somebody forwarded it into.
    """
    registered.request_reset()
    token = reset_token()

    assert registered.reset_password(token)['success'] is True
    second = registered.reset_password(token)

    assert second['success'] is False
    assert second['field'] == 'token'


@pytest.mark.django_db
def test_requesting_a_reset_twice_keeps_only_the_newest_link_working(registered):
    registered.request_reset()
    first = reset_token()

    registered.request_reset()
    second = reset_token()

    assert first != second
    # The superseded link is refused...
    assert registered.reset_password(first)['success'] is False
    # ...and the current one works.
    assert registered.reset_password(second)['success'] is True


@pytest.mark.django_db
def test_the_whole_activation_journey_over_real_http(registered):
    """
        register -> read the activation link emailed at registration
        -> follow it -> the address is confirmed -> a second link works.

    Registration sends the first message, so this is the flow a new account
    performs without doing anything special.
    """
    user = User.objects.get(email='ada@example.com')
    assert user.is_verified is False
    # Registration sent exactly one message, and it is the activation link.
    assert len(mail.outbox) == 1
    assert urlparse(links_for(ACTIVATION_PATH)[-1]).path == ACTIVATION_PATH

    result = registered.activate(activation_token())

    assert result['success'] is True
    assert result['user']['isVerified'] is True
    user.refresh_from_db()
    assert user.is_verified is True


@pytest.mark.django_db
def test_a_lost_activation_link_can_be_resent(registered):
    """
    The real reason `resendActivationEmail` exists: the first message was lost
    or filtered, so the account is still sitting unconfirmed and its only
    link is gone. Resending supersedes the old one, so a leaked earlier email
    stops being a live credential.
    """
    first = activation_token()
    registered.resend_activation()

    # A second message, carrying a different token.
    assert len(links_for(ACTIVATION_PATH)) == 2
    fresh = activation_token()
    assert fresh != first

    # The superseded link no longer works...
    assert registered.activate(first)['success'] is False
    # ...and the fresh one does.
    assert registered.activate(fresh)['success'] is True


@pytest.mark.django_db
def test_an_unverified_account_can_still_sign_in(registered):
    """
    Verification is deliberately not a login requirement: an unverified
    account owns a legitimate account and must not be locked out of it.
    """
    assert User.objects.get(email='ada@example.com').is_verified is False

    assert registered.login()['success'] is True


@pytest.mark.django_db
def test_the_two_flows_do_not_interfere(registered):
    """
    They share a table, a hashing scheme and a throttle cache, so the
    interaction is worth pinning: asking for a reset must not burn the
    activation link, and activating must not burn the reset link.
    """
    from_the_signup = activation_token()
    registered.request_reset()
    from_the_reset = reset_token()

    # Asking for a reset did not burn the activation link...
    assert registered.activate(from_the_signup)['success'] is True
    # ...and activating did not burn the reset link.
    assert registered.reset_password(from_the_reset)['success'] is True


@pytest.mark.django_db
def test_a_request_for_an_unknown_address_completes_identically(registered):
    """
    Over HTTP, where a difference would actually be observable. Both the
    payload and the absence of any outbound message are part of the answer.
    """
    known = registered.request_reset('ada@example.com')
    mail.outbox.clear()
    unknown = registered.request_reset('nobody-at-all@example.com')

    assert known == unknown
    assert mail.outbox == []


@pytest.mark.django_db
def test_a_bogus_link_is_a_payload_and_never_a_graphql_error(registered):
    """
    The contract the frontend depends on: an unusable link is an *answer*, not
    a crash. A GraphQL `errors` array here would make a stale link in
    somebody's inbox look like a server outage.
    """
    response, result = registered.call(
        RESET_PASSWORD,
        {'input': {'token': 'not-a-real-token', 'newPassword': NEW_PASSWORD}},
        'resetPassword',
    )

    assert result['success'] is False
    assert result['field'] == 'token'
    assert response.status_code == 200


@pytest.mark.django_db
def test_a_reset_clears_the_browsers_own_dead_cookie(registered):
    """
    The browser presented a live cookie; the reset revoked it server-side and
    cleared it client-side in the same response, so the next request cannot
    keep sending a credential that can never work.
    """
    registered.login()
    assert REFRESH_TOKEN_COOKIE_NAME in registered.client.cookies

    registered.request_reset()
    response, result = registered.call(
        RESET_PASSWORD,
        {'input': {'token': reset_token(), 'newPassword': NEW_PASSWORD}},
        'resetPassword',
    )

    assert result['success'] is True
    cleared = response.cookies[REFRESH_TOKEN_COOKIE_NAME]
    assert cleared.value == ''
    assert cleared['max-age'] == 0
