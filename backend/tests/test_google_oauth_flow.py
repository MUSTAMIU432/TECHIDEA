"""
The Google sign-in flow, end to end over real HTTP (S1-009).

The chain under test is the whole one, at every layer except Google's own
signature check:

    Google ID token
      -> googleLogin (real endpoint, real middleware, real resolver)
      -> identity.google_oauth verification (real module)
      -> ExternalIdentity
      -> User
      -> authenticated session (access token + HttpOnly refresh cookie)
      -> a protected operation with that session

The only thing stubbed is `google_id_token.verify_oauth2_token` - the one
function that talks to Google to fetch and check a signature against Google's
published keys. That cannot be exercised for real in a test suite: it needs
network access and a genuinely Google-signed credential, and faking either
would prove nothing. Stubbing it is therefore as close to the real thing as
this can get, and it leaves every layer this project actually owns -
configuration reading, audience selection, replay protection, account
resolution, session issuance, and the authorization that follows - real.

The `verify_oauth2_token` stub is still asked to behave like the real thing
about the property that matters most: it raises for a bad signature, a wrong
issuer, a wrong audience or an expired token, and each of those rejections
is asserted here to be indistinguishable from any other failure.
"""

import json
import time
from unittest.mock import patch

import pytest
from django.test import Client
from google.auth.exceptions import GoogleAuthError

from identity.google_oauth import GoogleIdentity
from identity.models import ExternalIdentity, RefreshSession, User

GOOGLE_LOGIN_MUTATION = """
mutation GoogleLogin($input: GoogleLoginInput!) {
  googleLogin(input: $input) {
    success
    message
    accessToken
    accessTokenExpiresAt
    user { id email firstName lastName phoneNumber isActive isVerified }
  }
}
"""

ME_QUERY = """
query Me { me { id email isVerified } }
"""

ORGANIZATIONS_QUERY = """
query MeOrganizations {
  meOrganizations { organization { id name } membership { id status } }
}
"""

REGISTER_MUTATION = """
mutation Register($input: RegisterInput!) {
  register(input: $input) { success message field }
}
"""

CREATE_ORGANIZATION_MUTATION = """
mutation CreateOrganization($input: CreateOrganizationInput!) {
  createOrganization(input: $input) { success organization { id name } }
}
"""

# Shaped like a real Google client id, and fake: this is the value the
# verifier is asked to check the `aud` claim against, not a credential.
CLIENT_ID = '1234567890-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com'

GENERIC_ERROR = 'Could not sign in with Google.'
# The one message that is *not* generic. Only ever returned to a caller whose
# credential Google verified as `email_verified`, i.e. someone who has already
# proven they control the mailbox in question - so it discloses nothing they
# could not establish themselves, and is not an enumeration oracle.
# See identity.authentication.GoogleEmailInUseError.
EMAIL_IN_USE_ERROR = (
    'An account already exists for this email. '
    'Sign in with the account you already use for this email address, '
    'or sign up with a different email address.'
)
PASSWORD = 'a-strong-unique-pass-1'

_FUTURE_EXP = int(time.time()) + 3600


def _claims(**overrides):
    """A realistic set of Google ID-token claims for a verified identity."""
    claims = {
        'sub': 'google-subject-1',
        'email': 'grace@example.com',
        'email_verified': True,
        'given_name': 'Grace',
        'family_name': 'Hopper',
        'name': 'Grace Hopper',
        # A real Google token also carries iss/aud/iat/exp; the tests that
        # care about audience set `aud` explicitly.
        'iss': 'https://accounts.google.com',
        'aud': CLIENT_ID,
        'exp': _FUTURE_EXP,
    }
    claims.update(overrides)
    return claims


class GoogleStub:
    """
    Stands in for `verify_oauth2_token`, faithfully enough to be useful.

    Reproduces the real function's decisions for the inputs these tests
    care about - audience mismatch, wrong issuer, expiry, malformed token -
    by raising, and returns the claims for a genuine one. A test that
    patches only this is still testing this project's real verification
    logic, including its own pre-checks.
    """

    def __init__(self, claims=None, failure=None):
        self.claims = claims
        self.failure = failure
        self.calls = []

    def __call__(self, token, request, *, audience=None, **kwargs):
        self.calls.append({'token': token, 'audience': audience})
        if self.failure is not None:
            raise self.failure
        if self.claims is None:
            raise ValueError('Token used too early or malformed')
        if not token.startswith('header.payload.'):
            raise ValueError('Token used too early or malformed')
        if audience and self.claims.get('aud') != audience:
            raise ValueError('Token has wrong audience')
        return dict(self.claims)


def _google_verify(claims=None, failure=None):
    return patch(
        'identity.google_oauth.google_id_token.verify_oauth2_token',
        GoogleStub(claims=claims, failure=failure),
    )


class Api:
    def __init__(self, client: Client):
        self.client = client

    def raw(self, query, variables=None, bearer=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        headers = {}
        if bearer is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {bearer}'
        return self.client.post(
            '/graphql/', data=json.dumps(payload), content_type='application/json', **headers
        )

    def data(self, query, variables=None, bearer=None):
        body = self.raw(query, variables, bearer).json()
        assert 'errors' not in body, body
        return body['data']

    def google_login(self, credential='header.payload.signature'):
        return self.data(GOOGLE_LOGIN_MUTATION, {'input': {'credential': credential}})[
            'googleLogin'
        ]


@pytest.fixture(autouse=True)
def configured_client(settings):
    """The client id every test here needs, in the setting the code reads."""
    settings.GOOGLE_OAUTH_CLIENT_ID = CLIENT_ID


@pytest.fixture
def api(client: Client) -> Api:
    return Api(client)


def _register_local_user(api: Api, email):
    return api.data(
        REGISTER_MUTATION,
        {
            'input': {
                'firstName': 'Local',
                'lastName': 'Person',
                'email': email,
                'phoneNumber': '+255712345678',
                'password': PASSWORD,
            }
        },
    )['register']


# --- the successful path -----------------------------------------------------------


@pytest.mark.django_db
def test_a_valid_google_credential_signs_a_new_user_all_the_way_in(api: Api):
    """
    The complete chain in one test, asserted at every link, because a break
    anywhere in it produces the same symptom from the user's side (the
    button does nothing) and no other test would localise it.
    """
    with _google_verify(claims=_claims()) as verify:
        response = api.google_login()

    # Google's verifier was asked to check our own client id as the
    # audience - not None, which would accept a token minted for any Google
    # OAuth client on the internet.
    assert verify.calls[0]['audience'] == CLIENT_ID

    payload = response
    assert payload['success'] is True
    assert payload['accessToken']
    assert payload['accessTokenExpiresAt']
    assert payload['user']['email'] == 'grace@example.com'
    assert payload['user']['isVerified'] is True

    # ExternalIdentity -> User, as the design says: the stable Google
    # subject, never the email, is what the link is keyed on.
    identity = ExternalIdentity.objects.get(provider='google', provider_subject='google-subject-1')
    user = User.objects.get(email='grace@example.com')
    assert identity.user_id == user.pk
    assert identity.email == 'grace@example.com'
    # Provisioned with an unusable password: this account can only ever be
    # reached through the linked Google identity.
    assert user.has_usable_password() is False
    # Google's own profile claims were used, not an invented placeholder.
    assert (user.first_name, user.last_name) == ('Grace', 'Hopper')

    # An authenticated session: a refresh session exists, and the credential
    # is in the browser's HttpOnly cookie.
    assert RefreshSession.objects.filter(user=user, revoked_at__isnull=True).count() == 1
    assert api.client.cookies['refresh_token']['httponly'] is True

    # ...and that session reaches a protected query.
    assert api.data(ME_QUERY, bearer=payload['accessToken'])['me']['email'] == ('grace@example.com')


@pytest.mark.django_db
def test_a_google_signed_in_user_reaches_a_protected_organization_operation(api: Api):
    """
    "Protected application", concretely: the session a Google sign-in
    produces is good for a tenant-scoped, permission-checked operation, not
    merely for `me`.
    """
    with _google_verify(claims=_claims()):
        access_token = api.google_login()['accessToken']

    with _google_verify(claims=_claims(sub='google-subject-1', exp=_FUTURE_EXP)):
        created = api.data(
            CREATE_ORGANIZATION_MUTATION,
            {'input': {'name': 'Hopper Works'}},
            bearer=access_token,
        )['createOrganization']

    assert created['success'] is True
    organizations = api.data(ORGANIZATIONS_QUERY, bearer=access_token)['meOrganizations']
    assert [item['organization']['name'] for item in organizations] == ['Hopper Works']


@pytest.mark.django_db
def test_a_returning_google_user_authenticates_the_same_account(api: Api):
    with _google_verify(claims=_claims()) as verify_first:
        first = api.google_login('header.payload.first-sign-in')
    # A second, freshly-issued credential for the same Google subject - the
    # normal case of a user signing in again later. It has to be a
    # *different* credential: the first one is now spent, and reusing it
    # would be a replay (see TestReplayProtection below).
    with _google_verify(claims=_claims()) as verify_second:
        second = api.google_login('header.payload.second-sign-in')

    assert first['user']['id'] == second['user']['id']
    assert User.objects.filter(email='grace@example.com').count() == 1
    assert ExternalIdentity.objects.filter(provider='google').count() == 1
    # Two distinct credentials, both accepted - the replay check is per
    # token, not per account, so a returning user is not locked out.
    assert verify_first.calls[0]['token'] != verify_second.calls[0]['token']
    # Each sign-in gets its own session, and the first is still valid.
    assert RefreshSession.objects.filter(user__email='grace@example.com').count() == 2
    assert api.data(ME_QUERY, bearer=first['accessToken'])['me']['email'] == ('grace@example.com')


# --- rejected credentials -----------------------------------------------------------


@pytest.mark.django_db
def test_an_invalid_credential_is_refused_generically(api: Api):
    with _google_verify(claims=_claims(), failure=ValueError('Token used too early or malformed')):
        response = api.google_login('header.payload.bad-signature')

    assert response['success'] is False
    assert response['accessToken'] is None
    assert response['user'] is None
    assert response['message'] == GENERIC_ERROR
    # Nothing was created, and no cookie was set.
    assert User.objects.count() == 0
    assert ExternalIdentity.objects.count() == 0
    assert 'refresh_token' not in api.client.cookies


@pytest.mark.django_db
def test_a_credential_for_a_different_audience_is_refused(api: Api):
    """
    A genuine, correctly signed Google credential - just not one issued for
    this application. This is the case `GOOGLE_OAUTH_CLIENT_ID` exists to
    handle, and the misconfiguration that hides it (a missing client id
    making every sign-in fail closed) is a bug, not a pass.
    """
    foreign = '999999999999-zzzzzzzzzzzzzzzzzzzzzzzzzzzz.apps.googleusercontent.com'
    with _google_verify(claims=_claims(aud=foreign)):
        response = api.google_login()

    assert response['success'] is False
    assert response['message'] == GENERIC_ERROR
    assert User.objects.count() == 0
    assert ExternalIdentity.objects.count() == 0


@pytest.mark.django_db
def test_an_expired_credential_is_refused(api: Api):
    expired = _claims(exp=int(time.time()) - 60)
    # The real verifier raises on this; the stub reproduces that, and
    # `_reject_if_already_used` independently refuses a non-positive TTL.
    with _google_verify(claims=expired):
        response = api.google_login()

    assert response['success'] is False
    assert response['message'] == GENERIC_ERROR
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_a_credential_from_another_issuer_is_refused(api: Api):
    wrong_issuer = GoogleAuthError(
        'Wrong issuer. Expected one of accounts.google.com or https://accounts.google.com.'
    )
    with _google_verify(claims=_claims(), failure=wrong_issuer):
        response = api.google_login()

    assert response['success'] is False
    assert response['message'] == GENERIC_ERROR


@pytest.mark.django_db
def test_a_malformed_credential_is_refused_without_reaching_a_provision(api: Api):
    with _google_verify(claims=_claims()) as verify:
        response = api.google_login('not-even-close-to-a-jwt')

    assert response['success'] is False
    assert response['message'] == GENERIC_ERROR
    assert verify.calls, "Google's own verifier must have been asked to judge it"
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_every_credential_failure_produces_an_identical_response(api: Api):
    """
    The information-leak property, end to end. A bad signature, a wrong
    audience, an expired credential, an unconfigured client id and a
    refused account-linking collision must be indistinguishable to the
    caller - otherwise the difference between them is an oracle.
    """
    responses = []

    with _google_verify(claims=_claims(), failure=ValueError('bad signature')):
        responses.append(api.google_login())
    with _google_verify(claims=_claims(aud='someone-else.apps.googleusercontent.com')):
        responses.append(api.google_login())
    with _google_verify(claims=_claims(exp=int(time.time()) - 60)):
        responses.append(api.google_login())
    with _google_verify(claims=None):
        responses.append(api.google_login())

    messages = {response['message'] for response in responses}
    assert messages == {GENERIC_ERROR}
    for response in responses:
        assert response['accessToken'] is None
        assert response['user'] is None
        assert response['success'] is False


# --- Policy B: never link by email --------------------------------------------------


@pytest.mark.django_db
def test_an_existing_local_account_with_the_same_email_is_not_linked(api: Api):
    """
    The rejected security design, kept rejected, proved at the HTTP boundary.
    A Google credential whose `email_verified` is true and whose email
    matches a password account exactly still does not sign in: no link is
    created, and the password account is untouched.
    """
    assert _register_local_user(api, 'grace@example.com')['success'] is True
    local = User.objects.get(email='grace@example.com')

    with _google_verify(claims=_claims(email_verified=True)):
        response = api.google_login()

    assert response['success'] is False
    # Refused, but actionable: this caller proved they control the mailbox,
    # so they are told the address is taken rather than being left with a
    # dead end.
    assert response['message'] == EMAIL_IN_USE_ERROR
    assert response['accessToken'] is None
    # No ExternalIdentity was created - the account was not linked, by
    # email or by anything else.
    assert ExternalIdentity.objects.count() == 0
    # And the existing account is completely unchanged...
    local.refresh_from_db()
    assert local.has_usable_password() is True
    assert User.objects.filter(email='grace@example.com').count() == 1
    # ...including its ability to sign in the normal way, and the Google
    # attempt granted it no session of any kind.
    assert 'refresh_token' not in api.client.cookies
    assert RefreshSession.objects.filter(user=local).count() == 0


@pytest.mark.django_db
def test_a_verified_google_sign_in_on_a_used_email_gets_an_actionable_message(api: Api):
    """
    The user-facing behaviour this change is for: a Google sign-in on an
    address that already has an account is refused *and explained*, so the
    person knows to sign in with their existing account or pick another
    address - instead of a bare "Could not sign in with Google."
    """
    assert _register_local_user(api, 'grace@example.com')['success'] is True

    with _google_verify(claims=_claims(email_verified=True)):
        response = api.google_login()

    assert response['success'] is False
    assert response['message'] == EMAIL_IN_USE_ERROR
    # Still refused: an explanation is not a link.
    assert response['user'] is None
    assert response['accessToken'] is None
    assert ExternalIdentity.objects.count() == 0
    assert User.objects.filter(email='grace@example.com').count() == 1
    assert 'refresh_token' not in api.client.cookies


@pytest.mark.django_db
def test_the_actionable_message_never_reaches_an_unverified_caller(api: Api):
    """
    The gate, at the boundary a real caller uses.

    An unverified credential proves nothing about who is asking, so it gets
    the same generic answer as any other failure. This is the assertion that
    stops the feature becoming an account-existence oracle: without it,
    anyone who could present *any* credential for an address would learn
    whether that address was registered.
    """
    assert _register_local_user(api, 'grace@example.com')['success'] is True

    with _google_verify(claims=_claims(email_verified=False)):
        response = api.google_login()

    assert response['message'] == GENERIC_ERROR


@pytest.mark.django_db
def test_an_address_with_no_account_is_indistinguishable_from_a_generic_failure(api: Api):
    """
    The anti-oracle test as the API actually answers it.

    Somebody probing an arbitrary address cannot present a Google credential
    for it, so what they observe is the generic message - byte for byte the
    same as for a registered address, so the two reveal nothing.
    """
    assert _register_local_user(api, 'grace@example.com')['success'] is True
    absent = Api(Client())
    invalid = ValueError('Token used too early or malformed')

    # One caller who cannot produce a valid credential at all - the only kind
    # of caller an address-probing attacker can be - asks about a registered
    # address and about an unregistered one.
    with _google_verify(claims=_claims(), failure=invalid):
        for_registered = api.google_login('header.payload.registered')
    with _google_verify(claims=_claims(), failure=invalid):
        for_unregistered = absent.google_login('header.payload.unregistered')

    assert for_registered['message'] == GENERIC_ERROR
    assert for_unregistered['message'] == for_registered['message']


@pytest.mark.django_db
def test_the_actionable_message_says_nothing_about_the_existing_account(api: Api):
    """
    Existence, and nothing else. No password state, no verification state,
    no provider, no ids - so a second party to the address learns nothing
    about the account beyond the fact that it exists.
    """
    assert _register_local_user(api, 'grace@example.com')['success'] is True
    local = User.objects.get(email='grace@example.com')

    with _google_verify(claims=_claims(email_verified=True)):
        response = api.google_login()

    message = response['message'].lower()
    for secret in ('password', 'verified', 'unverified', 'google', 'subject', str(local.pk)):
        assert secret not in message, f'the message leaked {secret!r}'


@pytest.mark.django_db
def test_an_unverified_matching_email_is_also_refused(api: Api):
    assert _register_local_user(api, 'grace@example.com')['success'] is True

    with _google_verify(claims=_claims(email_verified=False)):
        response = api.google_login()

    assert response['success'] is False
    assert ExternalIdentity.objects.count() == 0
    assert User.objects.filter(email='grace@example.com').count() == 1


@pytest.mark.django_db
def test_a_second_google_identity_reporting_a_taken_email_is_refused(api: Api):
    """
    Not just password accounts: a *different* Google identity whose
    credential reports an email that some other account already owns is
    refused too, rather than attaching itself to that account.
    """
    with _google_verify(claims=_claims(sub='first-google-subject')):
        assert api.google_login()['success'] is True

    with _google_verify(claims=_claims(sub='second-google-subject')):
        response = api.google_login()

    assert response['success'] is False
    assert response['message'] == GENERIC_ERROR
    # The second subject got no link of its own...
    assert ExternalIdentity.objects.filter(provider_subject='second-google-subject').count() == 0
    # ...and did not become a second way into the first one.
    assert User.objects.filter(email='grace@example.com').count() == 1


@pytest.mark.django_db
def test_a_refused_link_still_works_after_the_email_is_freed(api: Api):
    """
    The refusal is about the *collision*, not a permanent taint: once the
    colliding account is gone, the same Google identity can sign in and
    provision normally. (Pinned because "refuse" is easy to implement as
    "remember and block", which would turn a first-come account into a
    permanent claim on the address.)

    Each attempt uses a freshly-issued credential, which is what really
    happens: the refused attempt still consumed the credential it was given
    (replay protection runs before account resolution, so a token that has
    been verified is spent whatever the outcome), and Google issues a new one
    on the next sign-in.
    """
    assert _register_local_user(api, 'grace@example.com')['success'] is True
    with _google_verify(claims=_claims()):
        assert api.google_login('header.payload.first-attempt')['success'] is False

    User.objects.filter(email='grace@example.com').delete()

    with _google_verify(claims=_claims()):
        response = api.google_login('header.payload.second-attempt')

    assert response['success'] is True
    assert ExternalIdentity.objects.filter(provider_subject='google-subject-1').count() == 1


# --- replay protection, end to end --------------------------------------------------


@pytest.mark.django_db
def test_a_captured_credential_cannot_be_replayed(api: Api):
    """
    The replay check, through the real endpoint rather than the module: a
    credential that has already produced a session is refused the second
    time, and the refusal is indistinguishable from any other failure.
    """
    with _google_verify(claims=_claims()):
        first = api.google_login()
    assert first['success'] is True

    with _google_verify(claims=_claims()):
        replay = api.google_login()

    assert replay['success'] is False
    assert replay['message'] == GENERIC_ERROR
    assert replay['accessToken'] is None
    # Only the one session from the first (legitimate) use exists.
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 1
    # And the record is keyed by the credential, so a *different* one still
    # works - the legitimate returning-user path is unaffected.
    with _google_verify(claims=_claims()):
        assert api.google_login('header.payload.a-different-one')['success'] is True


@pytest.mark.django_db
def test_replay_protection_survives_between_separate_sessions(api: Api):
    """
    That the record lives in the shared, cross-request cache rather than in
    anything tied to the client that made the first request.
    """
    with _google_verify(claims=_claims()):
        assert api.google_login()['success'] is True

    other_browser = Api(Client())
    with _google_verify(claims=_claims()):
        response = other_browser.google_login()

    assert response['success'] is False


# --- failures that are the account's own, not the credential's ----------------------


@pytest.mark.django_db
def test_a_deactivated_google_backed_account_cannot_sign_in(api: Api):
    with _google_verify(claims=_claims()):
        assert api.google_login()['success'] is True
    User.objects.filter(email='grace@example.com').update(is_active=False)

    with _google_verify(claims=_claims()):
        response = api.google_login('header.payload.a-different-one')

    assert response['success'] is False
    assert response['message'] == GENERIC_ERROR
    # No new session was issued to a deactivated account.
    assert (
        RefreshSession.objects.filter(
            user__email='grace@example.com', revoked_at__isnull=True
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_a_google_session_rotates_and_revokes_like_any_other(api: Api):
    """
    Google sign-in produces no special kind of session: the same rotation,
    revocation and logout rules apply, so there is exactly one session
    implementation to reason about.
    """
    with _google_verify(claims=_claims()):
        access_token = api.google_login()['accessToken']
    first_refresh = api.client.cookies['refresh_token'].value

    refreshed = api.data('mutation RefreshToken { refreshToken { success accessToken } }')[
        'refreshToken'
    ]

    assert refreshed['success'] is True
    assert refreshed['accessToken'] != access_token
    assert api.client.cookies['refresh_token'].value != first_refresh

    # The original credential is dead, exactly as after an email/password
    # sign-in.
    api.client.cookies['refresh_token'] = first_refresh
    replayed = api.data('mutation RefreshToken { refreshToken { success } }')['refreshToken']
    assert replayed['success'] is False

    # And logout ends it.
    api.data('mutation Logout { logout { success } }')
    assert (
        api.data('mutation RefreshToken { refreshToken { success } }')['refreshToken']['success']
        is False
    )


@pytest.mark.django_db
def test_a_google_signed_in_user_sees_no_google_secrets_in_the_response(api: Api):
    """
    The credential is verified and discarded. It is never echoed back, never
    stored against the identity, and never appears in any response body - a
    leaked credential in a response is a leaked credential in every log that
    records one.
    """
    credential = 'header.payload.a-real-looking-credential'
    with _google_verify(claims=_claims()):
        response = api.raw(GOOGLE_LOGIN_MUTATION, {'input': {'credential': credential}})

    body = response.content.decode()
    assert credential not in body
    assert 'credential' not in response.json()['data']['googleLogin']
    user = User.objects.get(email='grace@example.com')
    assert user.password not in body
    assert not ExternalIdentity.objects.filter(email__contains='header').exists()


@pytest.mark.django_db
def test_google_identity_and_email_are_taken_only_from_the_verified_credential(api: Api):
    """
    Nothing the browser claims alongside the credential is trusted: the
    identity comes out of the verified claims, so a caller cannot mint a
    session for a different person by pairing a valid credential with
    invented details.
    """
    with _google_verify(claims=_claims(sub='google-subject-1', email='grace@example.com')):
        response = api.google_login()

    # The only inputs the mutation takes are the credential and nothing
    # else - there is no email/name/id argument a client could pair with it.
    assert 'email' not in GOOGLE_LOGIN_MUTATION.split('input: $input')[0]
    identity = ExternalIdentity.objects.get()
    assert identity.provider_subject == 'google-subject-1'
    assert User.objects.get(pk=identity.user_id).email == 'grace@example.com'
    assert response['user']['email'] == 'grace@example.com'


def test_the_google_identity_dataclass_is_what_reaches_the_account_layer():
    """
    A small structural check on the seam: verification returns exactly the
    claims the account layer is documented to use, and nothing else - so
    `identity.authentication` cannot come to depend on a claim this test
    would not notice being added.
    """
    assert set(GoogleIdentity.__dataclass_fields__) == {
        'subject',
        'email',
        'email_verified',
        'first_name',
        'last_name',
    }
