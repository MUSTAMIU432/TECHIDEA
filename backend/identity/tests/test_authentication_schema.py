import json
from unittest.mock import patch

import jwt
import pytest
from django.conf import settings
from django.test import Client

from graphql_api.schema import schema as root_schema
from identity.google_oauth import GoogleIdentity, GoogleTokenError
from identity.models import ExternalIdentity, RefreshSession, User

REGISTER_MUTATION = """
mutation Register($input: RegisterInput!) {
  register(input: $input) { success }
}
"""

LOGIN_MUTATION = """
mutation Login($input: LoginInput!) {
  login(input: $input) {
    success
    message
    accessToken
    accessTokenExpiresAt
    user { id email isActive isVerified }
  }
}
"""

REFRESH_MUTATION = """
mutation Refresh {
  refreshToken {
    success
    message
    accessToken
    user { id email }
  }
}
"""

LOGOUT_MUTATION = """
mutation Logout {
  logout { success }
}
"""

GOOGLE_LOGIN_MUTATION = """
mutation GoogleLogin($input: GoogleLoginInput!) {
  googleLogin(input: $input) {
    success
    message
    accessToken
    accessTokenExpiresAt
    user { id email isVerified }
  }
}
"""

ME_QUERY = """
query Me {
  me { id email }
}
"""

EMAIL = 'ada@example.com'
PASSWORD = 'a-strong-unique-pass-1'


@pytest.fixture
def gql(client: Client):
    """POST a GraphQL operation to the real /graphql/ endpoint."""

    def post(query, variables=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        return client.post('/graphql/', data=json.dumps(payload), content_type='application/json')

    return post


def _register(gql, email=EMAIL, password=PASSWORD):
    gql(
        REGISTER_MUTATION,
        {
            'input': {
                'firstName': 'Ada',
                'lastName': 'Lovelace',
                'email': email,
                'phoneNumber': '+255712345678',
                'password': password,
            }
        },
    )


def _login(gql, email=EMAIL, password=PASSWORD):
    return gql(LOGIN_MUTATION, {'input': {'email': email, 'password': password}})


def _register_and_login(gql, email=EMAIL, password=PASSWORD):
    _register(gql, email, password)
    return _login(gql, email, password)


# --- login -------------------------------------------------------------------------


@pytest.mark.django_db
def test_login_succeeds_for_a_registered_user(gql):
    response = _register_and_login(gql)

    body = response.json()
    assert 'errors' not in body
    payload = body['data']['login']
    assert payload['success'] is True
    assert payload['accessToken']
    assert payload['accessTokenExpiresAt']
    assert payload['user']['email'] == EMAIL


@pytest.mark.django_db
def test_login_sets_an_httponly_refresh_cookie(gql, client):
    _register_and_login(gql)

    cookie = client.cookies.get('refresh_token')
    assert cookie is not None
    assert cookie.value
    assert cookie['httponly'] is True
    assert cookie['samesite'] == 'Lax'


@pytest.mark.django_db
def test_login_response_never_exposes_password_or_hash(gql):
    response = _register_and_login(gql)

    raw_body = response.content.decode()
    assert 'password' not in response.json()['data']['login']
    user = User.objects.get(email=EMAIL)
    assert user.password not in raw_body


@pytest.mark.django_db
def test_wrong_password_fails_with_a_generic_message(gql):
    _register(gql)

    response = gql(LOGIN_MUTATION, {'input': {'email': EMAIL, 'password': 'the-wrong-password'}})

    payload = response.json()['data']['login']
    assert payload['success'] is False
    assert payload['accessToken'] is None
    assert payload['user'] is None
    # The message is the fixed generic phrase - not, e.g., "wrong password"
    # or anything naming which of email/password was the problem.
    assert payload['message'] == 'Invalid email or password.'


@pytest.mark.django_db
def test_unknown_email_fails_with_the_exact_same_message_as_wrong_password(gql):
    _register(gql)
    wrong_password = gql(LOGIN_MUTATION, {'input': {'email': EMAIL, 'password': 'wrong'}})
    unknown_email = gql(LOGIN_MUTATION, {'input': {'email': 'nobody@example.com', 'password': 'x'}})

    assert (
        wrong_password.json()['data']['login']['message']
        == unknown_email.json()['data']['login']['message']
    )
    assert unknown_email.json()['data']['login']['success'] is False


@pytest.mark.django_db
def test_inactive_user_cannot_login_via_graphql(gql):
    _register(gql)
    User.objects.filter(email=EMAIL).update(is_active=False)

    response = _login(gql)

    assert response.json()['data']['login']['success'] is False


@pytest.mark.django_db
def test_unverified_user_can_login_this_sprint(gql):
    # Explicit, documented policy for S1-003 - see identity/authentication.py.
    response = _register_and_login(gql)

    payload = response.json()['data']['login']
    assert payload['success'] is True
    assert payload['user']['isVerified'] is False


# --- me --------------------------------------------------------------------------


@pytest.mark.django_db
def test_me_returns_null_when_unauthenticated(gql):
    response = gql(ME_QUERY)

    assert response.json()['data']['me'] is None


@pytest.mark.django_db
def test_me_returns_null_for_a_garbage_bearer_token(client):
    response = client.post(
        '/graphql/',
        data=json.dumps({'query': ME_QUERY}),
        content_type='application/json',
        HTTP_AUTHORIZATION='Bearer not-a-real-token',
    )

    assert response.json()['data']['me'] is None


@pytest.mark.django_db
def test_me_returns_the_authenticated_user(gql, client):
    login_response = _register_and_login(gql)
    access_token = login_response.json()['data']['login']['accessToken']

    response = client.post(
        '/graphql/',
        data=json.dumps({'query': ME_QUERY}),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {access_token}',
    )

    assert response.json()['data']['me']['email'] == EMAIL


def _me_with_header(client, header_value):
    kwargs = {}
    if header_value is not None:
        kwargs['HTTP_AUTHORIZATION'] = header_value
    return client.post(
        '/graphql/',
        data=json.dumps({'query': ME_QUERY}),
        content_type='application/json',
        **kwargs,
    )


@pytest.mark.django_db
def test_me_returns_null_for_an_expired_access_token(gql, client):
    login_response = _register_and_login(gql)
    user_id = User.objects.get(email=EMAIL).pk
    expired_token = jwt.encode(
        {'sub': str(user_id), 'type': 'access', 'jti': 'x', 'iat': 0, 'exp': 1},
        settings.JWT_SIGNING_KEY,
        algorithm='HS256',
    )

    response = _me_with_header(client, f'Bearer {expired_token}')

    assert response.json()['data']['me'] is None
    assert login_response.status_code == 200  # sanity: login itself succeeded


@pytest.mark.django_db
def test_me_returns_null_for_a_token_of_the_wrong_type(gql, client):
    _register_and_login(gql)
    user_id = User.objects.get(email=EMAIL).pk
    wrong_type_token = jwt.encode(
        {'sub': str(user_id), 'type': 'refresh', 'jti': 'x', 'iat': 0, 'exp': 9999999999},
        settings.JWT_SIGNING_KEY,
        algorithm='HS256',
    )

    response = _me_with_header(client, f'Bearer {wrong_type_token}')

    assert response.json()['data']['me'] is None


@pytest.mark.django_db
def test_me_returns_null_for_a_token_signed_with_a_different_key(gql, client):
    _register_and_login(gql)
    user_id = User.objects.get(email=EMAIL).pk
    forged_token = jwt.encode(
        {'sub': str(user_id), 'type': 'access', 'jti': 'x', 'iat': 0, 'exp': 9999999999},
        'not-the-real-signing-key',
        algorithm='HS256',
    )

    response = _me_with_header(client, f'Bearer {forged_token}')

    assert response.json()['data']['me'] is None


@pytest.mark.django_db
def test_me_returns_null_for_an_inactive_user(gql, client):
    login_response = _register_and_login(gql)
    access_token = login_response.json()['data']['login']['accessToken']
    User.objects.filter(email=EMAIL).update(is_active=False)

    response = _me_with_header(client, f'Bearer {access_token}')

    assert response.json()['data']['me'] is None


@pytest.mark.django_db
def test_me_identity_comes_from_the_token_not_from_client_supplied_values(gql, client):
    # `me` takes no arguments at all - there is no id/email/role/organization
    # a client could pass in to influence which user is returned. Sending
    # extra, unexpected GraphQL variables must not change the result: the
    # access token is the only input that determines the identity.
    login_response = _register_and_login(gql)
    access_token = login_response.json()['data']['login']['accessToken']
    other_user = User.objects.create_user(
        email='someone-else@example.com',
        first_name='Someone',
        last_name='Else',
        phone_number='+255700000123',
        password='another-strong-pass-1',
    )

    response = client.post(
        '/graphql/',
        data=json.dumps(
            {
                'query': ME_QUERY,
                # A resolver that (incorrectly) read identity from client
                # input would need something like this to be tricked by it;
                # `me` accepts no arguments, so this is simply ignored.
                'variables': {'id': str(other_user.pk), 'email': other_user.email},
            }
        ),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {access_token}',
    )

    assert response.json()['data']['me']['email'] == EMAIL
    assert response.json()['data']['me']['email'] != other_user.email


@pytest.mark.django_db
def test_me_response_never_exposes_password_or_hash(gql, client):
    login_response = _register_and_login(gql)
    access_token = login_response.json()['data']['login']['accessToken']

    full_query = """
    query Me {
      me {
        id email firstName lastName phoneNumber isActive isVerified
      }
    }
    """
    response = client.post(
        '/graphql/',
        data=json.dumps({'query': full_query}),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {access_token}',
    )

    raw_body = response.content.decode()
    user = User.objects.get(email=EMAIL)
    assert user.password not in raw_body
    assert 'password' not in response.json()['data']['me']


@pytest.mark.django_db
def test_me_schema_has_no_password_or_secret_fields():
    # Defense in depth at the schema level, not just the response: the
    # UserType GraphQL type itself must never *declare* a field that could
    # leak a secret, regardless of what any particular query selects.
    user_type = root_schema.get_type_by_name('UserType')
    field_names = {field.name for field in user_type.fields}  # type: ignore[union-attr]

    forbidden_substrings = ('password', 'secret', 'token', 'credential', 'hash')
    for field_name in field_names:
        lowered = field_name.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), field_name


# --- refreshToken ------------------------------------------------------------------


@pytest.mark.django_db
def test_refresh_without_a_cookie_fails(gql):
    response = gql(REFRESH_MUTATION)

    assert response.json()['data']['refreshToken']['success'] is False


@pytest.mark.django_db
def test_refresh_token_rotates_and_returns_a_new_access_token(gql, client):
    login_response = _register_and_login(gql)
    first_access_token = login_response.json()['data']['login']['accessToken']
    first_cookie_value = client.cookies.get('refresh_token').value

    refresh_response = gql(REFRESH_MUTATION)

    payload = refresh_response.json()['data']['refreshToken']
    assert payload['success'] is True
    assert payload['accessToken'] != first_access_token
    assert client.cookies.get('refresh_token').value != first_cookie_value


@pytest.mark.django_db
def test_reusing_a_rotated_refresh_cookie_fails(gql, client):
    _register_and_login(gql)
    first_cookie_value = client.cookies.get('refresh_token').value
    gql(REFRESH_MUTATION)  # rotates the credential

    # Simulate the old (now-revoked) cookie being replayed by the browser.
    client.cookies['refresh_token'] = first_cookie_value
    replay_response = gql(REFRESH_MUTATION)

    assert replay_response.json()['data']['refreshToken']['success'] is False


@pytest.mark.django_db
def test_refresh_token_response_never_exposes_password_or_hash(gql):
    _register_and_login(gql)

    response = gql(REFRESH_MUTATION)

    raw_body = response.content.decode()
    user = User.objects.get(email=EMAIL)
    assert user.password not in raw_body


# --- logout ------------------------------------------------------------------------


@pytest.mark.django_db
def test_logout_revokes_the_session_so_a_later_refresh_fails(gql):
    _register_and_login(gql)

    logout_response = gql(LOGOUT_MUTATION)
    assert logout_response.json()['data']['logout']['success'] is True

    refresh_response = gql(REFRESH_MUTATION)
    assert refresh_response.json()['data']['refreshToken']['success'] is False


@pytest.mark.django_db
def test_logout_clears_the_refresh_cookie(gql, client):
    _register_and_login(gql)

    gql(LOGOUT_MUTATION)

    cookie = client.cookies.get('refresh_token')
    # Django represents a cleared cookie as an empty value with epoch expiry,
    # not the absence of the cookie key.
    assert cookie is None or cookie.value == ''


@pytest.mark.django_db
def test_logout_without_a_prior_login_still_succeeds(gql):
    response = gql(LOGOUT_MUTATION)

    assert response.json()['data']['logout']['success'] is True


@pytest.mark.django_db
def test_refresh_session_row_reflects_revocation_after_logout(gql):
    _register_and_login(gql)
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 1

    gql(LOGOUT_MUTATION)

    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 0


# --- googleLogin ---------------------------------------------------------------------


def _patched_google_identity(identity=None, error=None):
    target = 'identity.authentication.verify_google_id_token'
    if error is not None:
        return patch(target, side_effect=error)
    return patch(target, return_value=identity)


def _google_login(gql, credential='a-credential'):
    return gql(GOOGLE_LOGIN_MUTATION, {'input': {'credential': credential}})


@pytest.mark.django_db
def test_google_login_succeeds_for_a_new_user(gql):
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=True,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        response = _google_login(gql)

    body = response.json()
    assert 'errors' not in body
    payload = body['data']['googleLogin']
    assert payload['success'] is True
    assert payload['accessToken']
    assert payload['accessTokenExpiresAt']
    assert payload['user']['email'] == 'grace@example.com'
    assert payload['user']['isVerified'] is True
    assert User.objects.filter(email='grace@example.com').count() == 1
    assert ExternalIdentity.objects.filter(provider='google').count() == 1


@pytest.mark.django_db
def test_google_login_sets_an_httponly_refresh_cookie(gql, client):
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=True,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        _google_login(gql)

    cookie = client.cookies.get('refresh_token')
    assert cookie is not None
    assert cookie.value
    assert cookie['httponly'] is True
    assert cookie['samesite'] == 'Lax'


@pytest.mark.django_db
def test_google_login_response_never_exposes_password_or_hash(gql):
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=True,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        response = _google_login(gql)

    raw_body = response.content.decode()
    assert 'password' not in response.json()['data']['googleLogin']
    user = User.objects.get(email='grace@example.com')
    assert user.password not in raw_body


@pytest.mark.django_db
def test_google_login_returning_user_authenticates_the_same_account(gql):
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=True,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        first_response = _google_login(gql)
    with _patched_google_identity(identity=identity):
        second_response = _google_login(gql)

    first_id = first_response.json()['data']['googleLogin']['user']['id']
    second_id = second_response.json()['data']['googleLogin']['user']['id']
    assert first_id == second_id
    assert User.objects.filter(email='grace@example.com').count() == 1


@pytest.mark.django_db
def test_google_login_never_auto_links_an_existing_password_account_even_when_verified(gql):
    # Revised policy (post-review): a verified matching email is not enough
    # to auto-link - see the security note in
    # identity/authentication.py's authenticate_with_google docstring. It is
    # enough to be *told* the address is taken, though: this caller proved
    # they control the mailbox, so the refusal comes with an explanation
    # rather than a bare generic error. The link is still refused either
    # way, which is what this test is really about.
    _register(gql, email='grace@example.com')
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=True,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        response = _google_login(gql)

    payload = response.json()['data']['googleLogin']
    assert payload['success'] is False
    assert payload['user'] is None
    assert User.objects.filter(email='grace@example.com').count() == 1
    assert ExternalIdentity.objects.count() == 0


@pytest.mark.django_db
def test_google_login_does_not_link_an_unverified_matching_email(gql):
    _register(gql, email='grace@example.com')
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=False,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        response = _google_login(gql)

    payload = response.json()['data']['googleLogin']
    assert payload['success'] is False
    assert payload['user'] is None
    assert ExternalIdentity.objects.count() == 0


@pytest.mark.django_db
def test_google_login_fails_generically_for_an_invalid_credential(gql):
    with _patched_google_identity(error=GoogleTokenError('bad token')):
        response = _google_login(gql, credential='not-a-real-token')

    payload = response.json()['data']['googleLogin']
    assert payload['success'] is False
    assert payload['accessToken'] is None
    assert payload['user'] is None
    # Generic - never a hint about the underlying provider failure.
    assert payload['message'] == 'Could not sign in with Google.'


@pytest.mark.django_db
def test_google_login_error_message_matches_regardless_of_failure_cause(gql):
    _register(gql, email='grace@example.com')
    unverified_identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=False,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=unverified_identity):
        unverified_response = _google_login(gql)
    with _patched_google_identity(error=GoogleTokenError('bad token')):
        invalid_response = _google_login(gql, credential='garbage')

    assert (
        unverified_response.json()['data']['googleLogin']['message']
        == invalid_response.json()['data']['googleLogin']['message']
    )


@pytest.mark.django_db
def test_google_login_for_an_inactive_unlinked_password_account_is_refused(gql):
    # Refused via the account-linking policy itself here (case 3: an
    # existing, unlinked account with this email), not the is_active check
    # specifically - see test_inactive_returning_google_user_cannot_
    # authenticate in test_authentication.py for the is_active check
    # against a genuinely *linked* Google user.
    _register(gql, email='grace@example.com')
    User.objects.filter(email='grace@example.com').update(is_active=False)
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=True,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        response = _google_login(gql)

    assert response.json()['data']['googleLogin']['success'] is False


@pytest.mark.django_db
def test_google_login_provisions_a_user_with_no_phone_number(gql):
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=True,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        _google_login(gql)

    user = User.objects.get(email='grace@example.com')
    assert user.phone_number == ''


@pytest.mark.django_db
def test_google_login_provisioned_user_can_be_resolved_via_me(gql, client):
    identity = GoogleIdentity(
        subject='google-subject-1',
        email='grace@example.com',
        email_verified=True,
        first_name='Grace',
        last_name='Hopper',
    )

    with _patched_google_identity(identity=identity):
        response = _google_login(gql)
    access_token = response.json()['data']['googleLogin']['accessToken']

    me_response = client.post(
        '/graphql/',
        data=json.dumps({'query': ME_QUERY}),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {access_token}',
    )

    assert me_response.json()['data']['me']['email'] == 'grace@example.com'
