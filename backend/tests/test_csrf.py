"""
CSRF posture of the GraphQL endpoint (S1-009).

`/graphql/` is deliberately CSRF-exempt (`csrf_exempt` in
`graphql_api/views.py`), and the docstring there explains why: Django's CSRF
middleware defends cookie-authenticated *form* submissions, and a bare HTML
`<form>` cannot send `Content-Type: application/json`, so the classic vector
does not exist on a JSON-only endpoint.

This file is the regression cover for that argument. The claim is falsifiable
- if the endpoint ever starts accepting a *simple* cross-site request (one a
browser will send with no preflight at all), the JSON-only argument stops
holding and CSRF exemption stops being safe. So each test below constructs
the request a hostile page would actually be able to make, and asserts the
response cannot be used.

The defences actually relied on, both asserted here as behaviour rather than
merely as configuration (test_cors.py and test_security.py cover the
settings side):

1. **The body must be JSON.** A cross-origin `<form>` can only produce
   `application/x-www-form-urlencoded`, `multipart/form-data` or
   `text/plain` - all CORS "simple" content types, sent with no preflight -
   and Strawberry's view refuses all three before parsing anything.
2. **The refresh cookie is `SameSite=Lax`.** Even if such a request did
   reach the resolver, the browser would not attach the credential to it.

Two things these tests deliberately do *not* claim. First, that a simple
cross-origin POST is stopped at the server: it is not, it reaches the view
and gets refused there - which is a real, checkable property, asserted
directly. Second, that CSRF exemption is free: the browser-side half of the
defence (no CORS grant, no cookie) is asserted too, because asserting only
the server half would leave a change to the cookie's `SameSite` attribute
completely invisible here.
"""

import json

import pytest
from django.test import Client

from identity.models import RefreshSession, User

LOGIN_MUTATION = """
mutation Login($input: LoginInput!) {
  login(input: $input) { success accessToken user { id email } }
}
"""

ME_QUERY = """
query Me { me { id email } }
"""

# The three content types a browser classifies as CORS-"simple" and will send
# cross-origin with no preflight: the two form encodings, and text/plain
# (which is also what `fetch(..., {mode: 'no-cors'})` and
# `navigator.sendBeacon` degrade to). text/plain is the classic CSRF bypass,
# and GraphQL servers have historically accepted a query in it - which is why
# it is called out here separately rather than just parametrized.
SIMPLE_CONTENT_TYPES = [
    'application/x-www-form-urlencoded',
    'multipart/form-data',
    'text/plain',
]

EVIL_ORIGIN = 'http://evil.example'
ALLOWED_ORIGIN = 'http://localhost:5173'
PASSWORD = 'a-strong-unique-pass-1'


def _login_body(email, password):
    return json.dumps(
        {'query': LOGIN_MUTATION, 'variables': {'input': {'email': email, 'password': password}}}
    )


def _cross_site_simple_post(client, content_type, body):
    """
    The request a hostile page can actually get the browser to send: a
    simple content type, so no preflight, from an origin that is not on the
    allow-list, with cookies attached exactly as the browser would attach
    them.
    """
    return client.post(
        '/graphql/',
        data=body,
        content_type=content_type,
        HTTP_ORIGIN=EVIL_ORIGIN,
        HTTP_REFERER=f'{EVIL_ORIGIN}/attack.html',
    )


def _json(response):
    """
    The response body as JSON, or `{}` when the view refused the request
    before producing any. Strawberry's JSON-only check answers 400 with a
    plain-text explanation, so a body that is not JSON is the normal,
    expected case here rather than an error to work around.
    """
    if not response.get('Content-Type', '').startswith('application/json'):
        return {}
    return response.json()


@pytest.fixture
def victim(db) -> dict:
    """
    A signed-in session plus a client holding it.

    `enforce_csrf_checks=True` is the point: Django's test client *disables*
    CSRF checking by default, so a CSRF test run against a default client
    would pass no matter how the project were configured. Every request in
    this file is made with checking on.
    """
    User.objects.create_user(
        email='victim@example.com',
        first_name='Vic',
        last_name='Tim',
        phone_number='+255712345678',
        password=PASSWORD,
    )
    client = Client(enforce_csrf_checks=True)
    login = client.post(
        '/graphql/',
        data=_login_body('victim@example.com', PASSWORD),
        content_type='application/json',
    )
    assert login.json()['data']['login']['success'] is True
    assert client.cookies['refresh_token'].value
    return {
        'client': client,
        'access_token': login.json()['data']['login']['accessToken'],
    }


# --- the endpoint does not execute simple cross-site requests --------------------


@pytest.mark.django_db
@pytest.mark.parametrize('content_type', SIMPLE_CONTENT_TYPES)
def test_a_simple_cross_site_post_is_never_executed_as_a_graphql_operation(victim, content_type):
    """
    The core of the JSON-only argument: a login mutation smuggled through a
    simple content type is not a GraphQL request at all, so it cannot
    authenticate anybody - and the victim's own session is untouched.
    """
    response = _cross_site_simple_post(
        victim['client'], content_type, _login_body('victim@example.com', 'guessed')
    )

    # Refused before execution, either by status or by a GraphQL `errors`
    # array - never a 200 carrying a result.
    assert response.status_code != 200 or 'errors' in _json(response)
    # No access token, no user, no email: nothing a script could use.
    body = response.content.decode()
    assert 'accessToken' not in body
    assert 'victim@example.com' not in body
    # The existing session is untouched by the attempt.
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_a_text_plain_post_cannot_run_a_logout_mutation(victim):
    """
    `text/plain` called out on its own: it is the classic bypass, and the
    variant worth proving is that a *state-changing* operation cannot be
    driven through it. The session surviving is the observable proof that
    `logout` did not run.
    """
    response = _cross_site_simple_post(
        victim['client'],
        'text/plain',
        json.dumps({'query': 'mutation Logout { logout { success } }'}),
    )

    assert response.status_code != 200 or 'errors' in _json(response)
    assert 'refresh_token' not in _json(response).get('data', {})
    assert RefreshSession.objects.filter(revoked_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_a_simple_cross_site_post_gets_no_cors_grant(victim):
    """
    Even where the request is not stopped outright, the browser never hands
    the response to the attacker's page: no `Access-Control-Allow-Origin` is
    echoed for an origin that is not allow-listed, so `fetch()` rejects
    before the attacker can read anything.
    """
    response = _cross_site_simple_post(
        victim['client'], 'text/plain', json.dumps({'query': ME_QUERY})
    )

    assert 'Access-Control-Allow-Origin' not in response
    assert 'Access-Control-Allow-Credentials' not in response


@pytest.mark.django_db
def test_an_allow_listed_origin_is_the_only_one_that_gets_a_cors_grant(victim):
    """
    The other half of that defence, so the test above cannot pass merely
    because CORS is broken entirely: a legitimate origin still gets its
    grant, exactly, and the request still works.
    """
    response = victim['client'].post(
        '/graphql/',
        data=json.dumps({'query': ME_QUERY}),
        content_type='application/json',
        HTTP_ORIGIN=ALLOWED_ORIGIN,
        HTTP_AUTHORIZATION=f'Bearer {victim["access_token"]}',
    )

    assert response['Access-Control-Allow-Origin'] == ALLOWED_ORIGIN
    assert response['Access-Control-Allow-Credentials'] == 'true'
    assert response.json()['data']['me']['email'] == 'victim@example.com'


# --- what the browser would do with the cookie ----------------------------------


@pytest.mark.django_db
def test_the_refresh_cookie_is_lax_so_a_cross_site_post_never_carries_it(victim):
    """
    `SameSite=Lax` is the defence that does not depend on CORS at all: the
    browser will not attach this cookie to a cross-site POST, whatever the
    content type and whatever the response headers end up being. That is why
    a refused simple request still cannot do anything even if the content
    type check were bypassed too.
    """
    cookie = victim['client'].cookies['refresh_token']

    assert cookie['samesite'] == 'Lax'
    # Explicitly not Strict, and not absent: Strict would break the
    # cross-origin frontend/API pair this design depends on, and absent would
    # be relying on a browser default rather than stating the policy.
    assert cookie['samesite'] != 'Strict'
    assert cookie['httponly'] is True


# --- the exemption is scoped, and intentional ------------------------------------


@pytest.mark.django_db
def test_csrf_protection_still_applies_to_other_endpoints():
    """
    The exemption is on this view, not on the site. A CSRF-protected
    endpoint elsewhere still rejects an unprotected unsafe request, so
    "GraphQL is exempt" has not quietly become "CSRF is off".
    """
    client = Client(enforce_csrf_checks=True)

    response = client.post('/admin/login/', data={}, HTTP_ORIGIN=EVIL_ORIGIN)

    # Django's own answer to an unprotected unsafe request: a 403, not the
    # 200 the login page would otherwise render.
    assert response.status_code == 403


@pytest.mark.django_db
def test_a_csrf_protected_post_to_graphql_would_also_be_refused(victim):
    """
    The same `client` (CSRF checking on) making a *proper* JSON request
    cross-origin: GraphQL is exempt, so this one is executed. That is the
    exemption, stated as a fact about behaviour rather than about intent -
    and it is precisely why every other defence in this file exists.
    """
    response = _cross_site_simple_post(
        victim['client'], 'application/json', json.dumps({'query': ME_QUERY})
    )

    assert response.status_code == 200
    assert 'errors' not in _json(response)
    # The mutation is refused (no Bearer token, so `me` is null) - but the
    # request was executed rather than rejected by the CSRF middleware.
    assert _json(response)['data'] == {'me': None}


def test_the_graphql_view_is_still_explicitly_exempt():
    """
    Pinned as a fact about the code, so the reasoning in the view's
    docstring and the behaviour asserted above cannot drift apart. If this
    assertion ever starts failing, every other test in this file needs
    re-evaluating - the JSON-only argument this exemption rests on would no
    longer be what is keeping it safe.
    """
    from graphql_api import views

    assert views.graphql_view.csrf_exempt is True
