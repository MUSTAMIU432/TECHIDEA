"""
The four emailed-link mutations at the GraphQL boundary (S1-010).

`identity.services` decides; these resolvers translate. What is worth
testing here is the translation, and the two contracts the frontend depends
on:

1. **Every business failure is a payload, never a GraphQL `errors` array.**
   The frontend treats a thrown GraphQL error as "the server is broken" and a
   `success: false` payload as the operation's own answer, so a resolver that
   raised on a bad token would make an invalid link look like an outage. The
   tests assert `'errors' not in body` on every failure path for that reason.

2. **`field` names the input the failure belongs to, in camelCase.** That is
   what lets the frontend put a backend error next to the input that caused
   it, exactly where it shows its own client-side ones - and a whole-form
   failure (a throttled request) must carry no field at all, because no field
   is at fault and attributing it to one would be a lie the UI would render.

The information-disclosure property itself is asserted in
`identity/tests/test_email_links.py`; what is checked here is that the
resolvers pass those answers through unchanged rather than decorating them.
"""

import json
from datetime import timedelta

import pytest
from django.core import mail
from django.test import Client
from django.utils import timezone

from identity.models import EmailToken, User
from identity.schema import REFRESH_TOKEN_COOKIE_NAME

REQUEST_PASSWORD_RESET = """
mutation RequestPasswordReset($input: RequestPasswordResetInput!) {
  requestPasswordReset(input: $input) { success message field user { id email } }
}
"""

RESET_PASSWORD = """
mutation ResetPassword($input: ResetPasswordInput!) {
  resetPassword(input: $input) { success message field user { id email isVerified } }
}
"""

ACTIVATE_ACCOUNT = """
mutation ActivateAccount($input: ActivateAccountInput!) {
  activateAccount(input: $input) { success message field user { id email isVerified } }
}
"""

RESEND_ACTIVATION = """
mutation ResendActivationEmail($input: ResendActivationEmailInput!) {
  resendActivationEmail(input: $input) { success message field user { id email } }
}
"""

PASSWORD = 'a-strong-unique-pass-1'
NEW_PASSWORD = 'an-even-stronger-pass-2'


@pytest.fixture
def client(client: Client) -> Client:
    return client


@pytest.fixture
def gql(client: Client):
    """POST a GraphQL operation to the real /graphql/ endpoint, as one browser."""

    def post(query, variables=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        return client.post('/graphql/', data=json.dumps(payload), content_type='application/json')

    return post


def run(gql, query, root_field, variables=None):
    """
    Execute an operation and return its root field.

    Fails loudly on a GraphQL-level `errors` array: for every mutation in
    this module, a business failure is a `success: false` payload, so an
    `errors` entry means the resolver raised rather than answered.
    """
    response = gql(query, variables)
    assert response.status_code == 200, response.content
    body = response.json()
    assert 'errors' not in body, body
    return body['data'][root_field]


def make_user(email='ada@example.com', **overrides):
    fields = {
        'email': email,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password=PASSWORD, **fields)


LOGIN = """
mutation Login($input: LoginInput!) {
  login(input: $input) { success accessToken }
}
"""


def _sign_in(gql, user, password=PASSWORD):
    """Sign in through the same client, so the cookie jar is the browser's."""
    result = run(gql, LOGIN, 'login', {'input': {'email': user.email, 'password': password}})
    assert result['success'] is True
    return result['accessToken']


def token_from_last_email():
    from urllib.parse import parse_qs, urlparse

    body = mail.outbox[-1].body
    url = next(line.strip() for line in body.splitlines() if '?token=' in line)
    return parse_qs(urlparse(url).query)['token'][0]


@pytest.fixture
def registered_user(db):
    return make_user()


# --- requestPasswordReset ------------------------------------------------------------


@pytest.mark.django_db
class TestRequestPasswordResetMutation:
    def test_it_answers_successfully_for_a_registered_address(self, gql, registered_user):
        result = run(
            gql,
            REQUEST_PASSWORD_RESET,
            'requestPasswordReset',
            {'input': {'email': registered_user.email}},
        )

        assert result['success'] is True
        # No user is attached: the operation establishes no session and the
        # caller is not told which account, if any, was involved.
        assert result['user'] is None
        assert result['field'] is None
        assert len(mail.outbox) == 1

    def test_it_answers_identically_for_an_address_with_no_account(self, gql, registered_user):
        """
        The two answers compared directly. Any difference here - a different
        message, a timing-shaped field, a `user` on one and not the other -
        would make this endpoint an account-existence oracle.
        """
        existing = run(
            gql,
            REQUEST_PASSWORD_RESET,
            'requestPasswordReset',
            {'input': {'email': registered_user.email}},
        )
        absent = run(
            gql,
            REQUEST_PASSWORD_RESET,
            'requestPasswordReset',
            {'input': {'email': 'nobody-at-all@example.com'}},
        )

        assert existing == absent
        # ...and the absent address was not mailed either.
        assert [message.to for message in mail.outbox] == [[registered_user.email]]

    def test_a_malformed_address_is_reported_against_the_email_field(self, gql):
        result = run(
            gql,
            REQUEST_PASSWORD_RESET,
            'requestPasswordReset',
            {'input': {'email': 'not-an-email'}},
        )

        assert result['success'] is False
        assert result['field'] == 'email'

    def test_a_throttled_request_carries_no_field(self, gql, registered_user):
        """
        A throttle is not a validation failure, so naming a field would put an
        error next to an input that was perfectly valid.
        """
        from identity import throttling

        for _ in range(throttling.PASSWORD_RESET_REQUEST_PER_ACCOUNT.limit):
            run(
                gql,
                REQUEST_PASSWORD_RESET,
                'requestPasswordReset',
                {'input': {'email': registered_user.email}},
            )

        result = run(
            gql,
            REQUEST_PASSWORD_RESET,
            'requestPasswordReset',
            {'input': {'email': registered_user.email}},
        )

        assert result['success'] is False
        assert result['field'] is None
        assert result['message'] == throttling.THROTTLED_MESSAGE


# --- resetPassword -------------------------------------------------------------------


@pytest.mark.django_db
class TestResetPasswordMutation:
    def _request(self, gql, email):
        run(gql, REQUEST_PASSWORD_RESET, 'requestPasswordReset', {'input': {'email': email}})
        return token_from_last_email()

    def test_a_valid_token_resets_the_password(self, gql, registered_user):
        token = self._request(gql, registered_user.email)

        result = run(
            gql,
            RESET_PASSWORD,
            'resetPassword',
            {'input': {'token': token, 'newPassword': NEW_PASSWORD}},
        )

        assert result['success'] is True
        assert result['field'] is None
        # The affected account is returned: unlike the request side, the caller
        # has just proved control of this mailbox, so naming it discloses
        # nothing they could not establish themselves.
        assert result['user']['email'] == registered_user.email
        registered_user.refresh_from_db()
        assert registered_user.check_password(NEW_PASSWORD)

    def test_an_invalid_token_is_a_payload_not_a_graphql_error(self, gql, registered_user):
        result = run(
            gql,
            RESET_PASSWORD,
            'resetPassword',
            {'input': {'token': 'nonsense', 'newPassword': NEW_PASSWORD}},
        )

        assert result['success'] is False
        assert result['field'] == 'token'
        assert result['user'] is None

    def test_a_rejected_password_is_reported_against_the_password_field(self, gql, registered_user):
        token = self._request(gql, registered_user.email)

        result = run(
            gql,
            RESET_PASSWORD,
            'resetPassword',
            {'input': {'token': token, 'newPassword': 'short'}},
        )

        assert result['success'] is False
        assert result['field'] == 'password'

    def test_a_successful_reset_clears_the_callers_own_refresh_cookie(self, gql, registered_user):
        """
        The reset revoked every session for the account, including the one this
        browser is holding, so the cookie it still has is dead. Clearing it
        here means the next request cannot keep presenting a credential that
        can never work again.
        """
        _sign_in(gql, registered_user)
        token = self._request(gql, registered_user.email)

        response = gql(
            RESET_PASSWORD,
            {'input': {'token': token, 'newPassword': NEW_PASSWORD}},
        )

        body = response.json()
        assert 'errors' not in body, body
        assert body['data']['resetPassword']['success'] is True
        # An immediate expiry is Django's way of deleting a cookie.
        cleared = response.cookies[REFRESH_TOKEN_COOKIE_NAME]
        assert cleared.value == ''
        assert cleared['max-age'] == 0

    def test_the_old_session_cannot_refresh_after_a_reset(self, gql, registered_user):
        """
        The security property behind clearing the cookie, asserted at the level
        it actually matters: a session issued before the reset is dead, not
        merely un-cookied.
        """
        from identity.models import RefreshSession

        session = RefreshSession.objects.create(
            user=registered_user,
            token_hash='a' * 64,
            expires_at=timezone.now() + timedelta(days=30),
        )
        token = self._request(gql, registered_user.email)

        run(
            gql,
            RESET_PASSWORD,
            'resetPassword',
            {'input': {'token': token, 'newPassword': NEW_PASSWORD}},
        )

        session.refresh_from_db()
        assert session.is_active is False

    def test_a_throttled_reset_is_refused_without_spending_the_token(self, gql, registered_user):
        from identity import throttling

        token = self._request(gql, registered_user.email)
        for _ in range(throttling.PASSWORD_RESET_PER_CLIENT.limit):
            run(
                gql,
                RESET_PASSWORD,
                'resetPassword',
                {'input': {'token': 'nonsense', 'newPassword': NEW_PASSWORD}},
            )

        result = run(
            gql,
            RESET_PASSWORD,
            'resetPassword',
            {'input': {'token': token, 'newPassword': NEW_PASSWORD}},
        )

        assert result['success'] is False
        assert result['message'] == throttling.THROTTLED_MESSAGE
        # The over-limit caller must not have reached the password change.
        registered_user.refresh_from_db()
        assert registered_user.check_password(PASSWORD)


# --- activateAccount -----------------------------------------------------------------


@pytest.mark.django_db
class TestActivateAccountMutation:
    def test_a_valid_token_marks_the_address_verified(self, gql, registered_user):
        from identity.services import send_account_activation_email

        send_account_activation_email(registered_user)
        token = token_from_last_email()

        result = run(gql, ACTIVATE_ACCOUNT, 'activateAccount', {'input': {'token': token}})

        assert result['success'] is True
        assert result['user']['isVerified'] is True
        registered_user.refresh_from_db()
        assert registered_user.is_verified is True

    def test_an_invalid_token_is_a_payload_not_a_graphql_error(self, gql, registered_user):
        result = run(gql, ACTIVATE_ACCOUNT, 'activateAccount', {'input': {'token': 'nonsense'}})

        assert result['success'] is False
        assert result['field'] == 'token'
        assert result['user'] is None

    def test_activating_does_not_authenticate(self, gql, registered_user):
        """
        Activation establishes no session: there is no access token on the
        payload and no refresh cookie, so a confirmation link can never be a
        way to sign somebody in.
        """
        from identity.services import send_account_activation_email

        send_account_activation_email(registered_user)
        response = gql(ACTIVATE_ACCOUNT, {'input': {'token': token_from_last_email()}})

        assert 'refresh' not in response.cookies
        assert 'accessToken' not in response.json()['data']['activateAccount']


# --- resendActivationEmail -----------------------------------------------------------


@pytest.mark.django_db
class TestResendActivationEmailMutation:
    def test_it_emails_a_fresh_link_for_an_unverified_account(self, gql, registered_user):
        from identity.services import send_account_activation_email

        send_account_activation_email(registered_user)
        mail.outbox.clear()

        result = run(
            gql,
            RESEND_ACTIVATION,
            'resendActivationEmail',
            {'input': {'email': registered_user.email}},
        )

        assert result['success'] is True
        assert result['user'] is None
        assert len(mail.outbox) == 1

    def test_it_answers_identically_for_an_address_with_no_account(self, gql, registered_user):
        existing = run(
            gql,
            RESEND_ACTIVATION,
            'resendActivationEmail',
            {'input': {'email': registered_user.email}},
        )
        mail.outbox.clear()
        absent = run(
            gql,
            RESEND_ACTIVATION,
            'resendActivationEmail',
            {'input': {'email': 'nobody-at-all@example.com'}},
        )

        assert existing == absent
        assert mail.outbox == []

    def test_an_already_verified_account_gets_no_new_link(self, gql, registered_user):
        from identity.services import send_account_activation_email

        send_account_activation_email(registered_user)
        registered_user.is_verified = True
        registered_user.save(update_fields=['is_verified'])
        mail.outbox.clear()

        result = run(
            gql,
            RESEND_ACTIVATION,
            'resendActivationEmail',
            {'input': {'email': registered_user.email}},
        )

        assert result['success'] is True
        assert mail.outbox == []
        assert (
            EmailToken.objects.filter(user=registered_user, used_at__isnull=True).count() == 1
        )  # the original, still live

    def test_a_malformed_address_is_reported_against_the_email_field(self, gql):
        result = run(gql, RESEND_ACTIVATION, 'resendActivationEmail', {'input': {'email': 'nope'}})

        assert result['success'] is False
        assert result['field'] == 'email'

    def test_a_throttled_resend_names_no_field(self, gql, registered_user):
        from identity import throttling

        for _ in range(throttling.ACTIVATION_RESEND_PER_ACCOUNT.limit):
            run(
                gql,
                RESEND_ACTIVATION,
                'resendActivationEmail',
                {'input': {'email': registered_user.email}},
            )

        result = run(
            gql,
            RESEND_ACTIVATION,
            'resendActivationEmail',
            {'input': {'email': registered_user.email}},
        )

        assert result['success'] is False
        assert result['field'] is None
        assert result['message'] == throttling.THROTTLED_MESSAGE
