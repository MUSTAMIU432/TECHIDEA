"""
The complete authentication journey, over real HTTP (S1-009).

Everything here goes through the real endpoint - `django.test.Client` ->
URLconf -> middleware (security, CORS, CSRF, session, auth, clickjacking) ->
`csrf_exempt` GraphQL view -> Strawberry -> the schema's own resolvers ->
`identity.services` / `identity.authentication` -> the database. Nothing is
called directly: the unit suites in `identity/tests/` deliberately stub these
same layers, so a wiring mistake that only shows up end-to-end (a cookie
flag, a rotation that never reaches the browser, a middleware that swallows
the response) would not be caught there.

The journey asserted here is exactly the one the frontend performs, in order:

    register -> login -> Set-Cookie attributes -> authenticated `me`
    -> refreshToken -> refresh rotation verified -> the old refresh
    credential is rejected -> logout -> the refresh session is revoked
    -> the cookie is cleared -> refreshing after logout fails.

Cookies are driven exactly as a browser would drive them: Django's test
client keeps a cookie jar across requests, so a `Set-Cookie` on one response
is automatically presented on the next. Where a *stale* cookie must be
replayed (a rotated credential, a logged-out one) it is put back into the jar
explicitly, which is precisely what a stolen or cached copy looks like to the
server.
"""

import json

import pytest
from django.conf import settings
from django.test import Client

from identity.models import RefreshSession, User

REGISTER_MUTATION = """
mutation Register($input: RegisterInput!) {
  register(input: $input) {
    success
    message
    field
    user { id email firstName lastName phoneNumber isActive isVerified }
  }
}
"""

LOGIN_MUTATION = """
mutation Login($input: LoginInput!) {
  login(input: $input) {
    success
    message
    accessToken
    accessTokenExpiresAt
    user { id email }
  }
}
"""

REFRESH_MUTATION = """
mutation RefreshToken {
  refreshToken {
    success
    message
    accessToken
    accessTokenExpiresAt
    user { id email }
  }
}
"""

LOGOUT_MUTATION = """
mutation Logout {
  logout { success }
}
"""

ME_QUERY = """
query Me {
  me { id email isActive isVerified }
}
"""

PASSWORD = 'a-strong-unique-pass-1'


class GraphQLClient:
    """
    A minimal browser-like GraphQL caller over Django's test client.

    Wraps `Client` rather than replacing it: the point of these tests is the
    *real* request path, including the response headers and cookie jar that a
    browser would act on.
    """

    def __init__(self, client: Client):
        self.client = client

    def raw(self, query, variables=None, *, bearer=None, content_type='application/json'):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        headers = {}
        if bearer is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {bearer}'
        return self.client.post(
            '/graphql/', data=json.dumps(payload), content_type=content_type, **headers
        )

    def run(self, query, variables=None, *, bearer=None, root_field=None):
        """POST an operation and return its single root field, failing loudly on
        a GraphQL-level error (which is how every failure in this project is
        meant to surface: a `success: false` payload, never an `errors` array).
        """
        response = self.raw(query, variables, bearer=bearer)
        assert response.status_code == 200, response.content
        body = response.json()
        assert 'errors' not in body, body
        return body['data'][root_field] if root_field else body['data']

    def register(self, *, first='Ada', last='Lovelace', email='ada@example.com', password=PASSWORD):
        return self.run(
            REGISTER_MUTATION,
            {
                'input': {
                    'firstName': first,
                    'lastName': last,
                    'email': email,
                    'phoneNumber': '+255712345678',
                    'password': password,
                }
            },
            root_field='register',
        )

    def login(self, email='ada@example.com', password=PASSWORD):
        return self.run(
            LOGIN_MUTATION,
            {'input': {'email': email, 'password': password}},
            root_field='login',
        )

    def refresh(self):
        return self.run(REFRESH_MUTATION, root_field='refreshToken')

    def logout(self):
        return self.run(LOGOUT_MUTATION, root_field='logout')

    def me(self, access_token):
        return self.run(ME_QUERY, bearer=access_token, root_field='me')


@pytest.fixture
def gql(client: Client) -> GraphQLClient:
    return GraphQLClient(client)


def _set_cookie_attributes(response, name):
    """
    A `{attribute: value}` map for `name` as set on `response`.

    Read from `response.cookies` (Django's `SimpleCookie`) rather than from
    the `Set-Cookie` response header: Django's test client returns the view's
    response object without the WSGI layer that serialises cookies into
    headers, so the header is empty in tests even when a real server would
    send one. The attributes themselves are the same objects a browser would
    read.
    """
    assert name in response.cookies, f'no Set-Cookie for {name!r} in {response.cookies}'
    cookie = response.cookies[name]
    return {key: cookie[key] for key in cookie}


def _cookie_value(response, name):
    assert name in response.cookies, f'no Set-Cookie for {name!r} in {response.cookies}'
    return response.cookies[name].value


@pytest.mark.django_db
def test_complete_authentication_journey_over_real_http(gql):
    """
    The whole required sequence in one test, in order, with every intermediate
    state asserted as it is reached. Split into per-step tests it would still
    pass while a real user's flow broke between two of them (e.g. a rotation
    that revokes server-side but never reaches the browser's cookie jar).
    """

    # --- register ------------------------------------------------------------
    registration = gql.register()
    assert registration['success'] is True
    assert registration['field'] is None
    assert registration['user']['email'] == 'ada@example.com'
    assert User.objects.filter(email='ada@example.com').count() == 1
    # Registration alone must not authenticate anybody.
    assert not gql.client.cookies.get('refresh_token')

    # --- login ---------------------------------------------------------------
    login_response = gql.raw(
        LOGIN_MUTATION, {'input': {'email': 'ada@example.com', 'password': PASSWORD}}
    )
    login = login_response.json()['data']['login']
    assert login['success'] is True
    assert login['accessToken']
    assert login['accessTokenExpiresAt']
    first_access_token = login['accessToken']
    first_refresh_value = _cookie_value(login_response, 'refresh_token')

    # --- Set-Cookie attributes ----------------------------------------------
    attributes = _set_cookie_attributes(login_response, 'refresh_token')
    # HttpOnly: no JavaScript may ever read or forward this credential.
    assert attributes['httponly'] is True
    # Scoped to the API endpoint only, so it is never sent to /health/ or /admin/.
    assert attributes['path'] == '/graphql/'
    # Lax, not Strict: Strict would withhold the cookie from the cross-origin
    # POSTs this flow depends on... and, crucially, not None - an omitted
    # SameSite is what a browser would treat as Lax, but relying on that is
    # exactly the kind of implicit default this project refuses elsewhere.
    assert attributes.get('samesite') == 'Lax'
    # Expiry, so the credential does not outlive the server-side session.
    assert attributes.get('expires')
    # The Secure flag follows the environment's own cookie policy; asserted
    # against the setting rather than hardcoded so the test is meaningful in
    # both local (plain HTTP) and deployed (HTTPS-only) configurations. (A
    # Morsel always carries every reserved key, so the flag's *value* is what
    # is checked - a real server only emits the attribute when it is true.)
    assert bool(attributes['secure']) is settings.SESSION_COOKIE_SECURE

    # The access token is in the body; the refresh credential never is.
    assert 'refresh_token' not in login_response.json()['data']['login']
    assert 'refreshToken' not in json.dumps(login_response.json())

    # --- authenticated me ----------------------------------------------------
    me = gql.me(first_access_token)
    assert me['email'] == 'ada@example.com'
    assert me['isActive'] is True

    # --- refreshToken --------------------------------------------------------
    refresh_response = gql.raw(REFRESH_MUTATION)
    refresh = refresh_response.json()['data']['refreshToken']
    assert refresh['success'] is True
    assert refresh['message'] == 'Session renewed.'
    second_access_token = refresh['accessToken']
    assert refresh['accessTokenExpiresAt']
    second_refresh_value = _cookie_value(refresh_response, 'refresh_token')

    # --- rotation ------------------------------------------------------------
    assert second_refresh_value != first_refresh_value, 'the refresh credential must rotate'
    assert second_access_token != first_access_token
    # The new credential is a different server-side session, and the old one is
    # revoked the moment the new one starts - not before, not after.
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 1
    rotated = RefreshSession.objects.get(token_hash=_token_hash(second_refresh_value))
    assert rotated.replaced_by is None
    assert rotated.revoked_at is None
    # The old session points at its replacement: the rotation chain is
    # recorded, which is what makes reuse detectable at all.
    old_session = RefreshSession.objects.get(token_hash=_token_hash(first_refresh_value))
    assert old_session.revoked_at is not None
    assert old_session.replaced_by_id == rotated.pk
    # The renewed access token is the same user, and still works.
    assert gql.me(second_access_token)['email'] == 'ada@example.com'
    # Rotation is what the browser now holds.
    assert gql.client.cookies['refresh_token'].value == second_refresh_value

    # --- the old refresh credential is rejected -------------------------------
    gql.client.cookies['refresh_token'] = first_refresh_value
    replay = gql.raw(REFRESH_MUTATION).json()['data']['refreshToken']
    assert replay['success'] is False
    assert replay['accessToken'] is None
    assert replay['user'] is None
    # ...and the failed attempt clears the dead cookie instead of leaving the
    # browser to resend a credential that can never work.
    assert gql.client.cookies.get('refresh_token').value in ('', None)
    # The current session survived the replay attempt - a replay must not be
    # able to destroy a live session.
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 1

    # --- logout --------------------------------------------------------------
    gql.client.cookies['refresh_token'] = second_refresh_value
    logout = gql.logout()
    assert logout['success'] is True

    # --- the refresh session is revoked --------------------------------------
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 0
    assert RefreshSession.objects.get(token_hash=_token_hash(second_refresh_value)).revoked_at

    # --- the cookie is cleared -----------------------------------------------
    assert gql.client.cookies.get('refresh_token').value in ('', None)

    # --- refreshing after logout fails ---------------------------------------
    gql.client.cookies['refresh_token'] = second_refresh_value
    after_logout = gql.raw(REFRESH_MUTATION).json()['data']['refreshToken']
    assert after_logout['success'] is False
    assert after_logout['accessToken'] is None
    # The revocation is not a client-side illusion: presenting the exact
    # revoked credential to a fresh request is still refused.
    gql.client.cookies['refresh_token'] = second_refresh_value
    assert gql.raw(REFRESH_MUTATION).json()['data']['refreshToken']['success'] is False
    # And it stays refused - a revoked session is not silently resurrected.
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 0


def _token_hash(raw_token):
    import hashlib

    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


@pytest.mark.django_db
def test_login_is_required_before_me_and_refresh_are_useful(gql):
    """
    The negative half of the journey, so the positive one above can't pass
    while the endpoint accepts anonymous callers.
    """
    assert gql.run(ME_QUERY, root_field='me') is None
    assert gql.refresh()['success'] is False
    assert gql.logout()['success'] is True


@pytest.mark.django_db
def test_registered_user_can_sign_in_again_after_logout(gql):
    """
    A full second round trip proves logout revoked the *session* rather than
    the account: the same credentials must still authenticate, and produce a
    brand new credential.
    """
    gql.register()
    assert gql.login()['success'] is True
    first_refresh = _cookie_value(gql.raw(REFRESH_MUTATION), 'refresh_token')
    gql.logout()

    second = gql.login()

    assert second['success'] is True
    assert gql.me(second['accessToken'])['email'] == 'ada@example.com'
    assert gql.client.cookies['refresh_token'].value != first_refresh
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_duplicate_registration_does_not_disturb_an_existing_session(gql):
    """
    The one place a "this email is already registered" answer is safe to give,
    and the reason it must not leak a session: a second registration attempt
    for an existing account reports the collision on the `email` field and
    leaves every existing refresh session untouched.
    """
    gql.register()
    gql.login()
    live_refresh = gql.client.cookies['refresh_token'].value

    duplicate = gql.register()

    assert duplicate['success'] is False
    assert duplicate['field'] == 'email'
    assert duplicate['user'] is None
    assert User.objects.filter(email='ada@example.com').count() == 1
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 1
    # The existing session still works, and still holds the same credential.
    assert gql.client.cookies['refresh_token'].value == live_refresh
    assert gql.refresh()['success'] is True
