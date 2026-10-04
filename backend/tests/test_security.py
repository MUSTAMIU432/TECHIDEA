"""
Security configuration.

These tests pin the security invariants of each settings module without any
deployment. Values are read from a fresh interpreter (so backend/.env can't
leak in) and no database connection is made. Rejection of missing or unsafe
production input is covered in test_settings.py.
"""

import json
import re
import subprocess
import sys

import pytest

from .settings_helpers import BACKEND_DIR, VALID_PRODUCTION_ENV, clean_env, import_settings

PRODUCTION = 'config.settings.production'
LOCAL = 'config.settings.local'

BROWSER_PROTECTION = {
    'SECURE_CONTENT_TYPE_NOSNIFF': True,
    'SECURE_REFERRER_POLICY': 'same-origin',
    'SECURE_CROSS_ORIGIN_OPENER_POLICY': 'same-origin',
    'X_FRAME_OPTIONS': 'DENY',
    'SESSION_COOKIE_HTTPONLY': True,
    'SESSION_COOKIE_SAMESITE': 'Lax',
    'CSRF_COOKIE_SAMESITE': 'Lax',
    'CORS_ALLOW_ALL_ORIGINS': False,
    # Sprint 1 (S1-003): the refresh-token cookie requires credentialed
    # cross-origin requests (frontend and backend are different origins even
    # in local dev), so this flipped from False to True. Safe only because
    # CORS_ALLOW_ALL_ORIGINS stays False and CORS_ALLOWED_ORIGINS stays an
    # explicit, non-wildcard list - see test_cors_credentials_require_an_
    # explicit_non_wildcard_origin_allowlist below, and config/settings/base.py.
    'CORS_ALLOW_CREDENTIALS': True,
}


def settings_values(module, names, overrides=None):
    """Return the named settings of `module`, evaluated in a clean subprocess."""
    code = (
        f'import json; from {module.rsplit(".", 1)[0]} import {module.rsplit(".", 1)[1]} as s; '
        f'print(json.dumps({{n: getattr(s, n, None) for n in {list(names)!r}}}))'
    )
    result = import_settings(module, {'DJANGO_SETTINGS_MODULE': module, **(overrides or {})}, code)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def rejects(module, overrides, message):
    result = import_settings(module, {'DJANGO_SETTINGS_MODULE': module, **overrides})
    assert result.returncode != 0, f'{module} should have been rejected'
    assert 'ImproperlyConfigured' in result.stderr
    assert message in result.stderr


# --- Shared browser protections -------------------------------------------------


@pytest.mark.parametrize('module', [PRODUCTION, LOCAL])
def test_browser_protections_are_enabled_in_every_environment(module):
    overrides = {'ENVIRONMENT': 'local'} if module == LOCAL else {}

    values = settings_values(module, BROWSER_PROTECTION, overrides)

    assert values == BROWSER_PROTECTION


def test_security_middleware_is_installed_before_anything_that_responds():
    values = settings_values(PRODUCTION, ['MIDDLEWARE'])
    middleware = values['MIDDLEWARE']

    assert middleware[0] == 'django.middleware.security.SecurityMiddleware'
    for required in (
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.middleware.clickjacking.XFrameOptionsMiddleware',
    ):
        assert required in middleware


# --- Production ------------------------------------------------------------------


def test_production_secure_defaults():
    values = settings_values(
        PRODUCTION,
        [
            'DEBUG',
            'SECURE_SSL_REDIRECT',
            'SESSION_COOKIE_SECURE',
            'CSRF_COOKIE_SECURE',
            'SECURE_HSTS_SECONDS',
            'SECURE_HSTS_INCLUDE_SUBDOMAINS',
            'SECURE_HSTS_PRELOAD',
            'SECURE_PROXY_SSL_HEADER',
            'ALLOWED_HOSTS',
        ],
        {'ENVIRONMENT': 'production'},
    )

    assert values == {
        'DEBUG': False,
        'SECURE_SSL_REDIRECT': True,
        'SESSION_COOKIE_SECURE': True,
        'CSRF_COOKIE_SECURE': True,
        'SECURE_HSTS_SECONDS': 31536000,
        # Broad HSTS scopes are opt-in, never a default.
        'SECURE_HSTS_INCLUDE_SUBDOMAINS': False,
        'SECURE_HSTS_PRELOAD': False,
        # A forwarded-protocol header is not trusted unless explicitly enabled.
        'SECURE_PROXY_SSL_HEADER': None,
        'ALLOWED_HOSTS': ['api.example.test'],
    }


@pytest.mark.parametrize('environment', ['development', 'staging', 'production'])
def test_every_deployed_environment_gets_the_same_security_posture(environment):
    values = settings_values(
        PRODUCTION,
        ['DEBUG', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE', 'SECURE_SSL_REDIRECT'],
        {'ENVIRONMENT': environment},
    )

    assert values == {
        'DEBUG': False,
        'SESSION_COOKIE_SECURE': True,
        'CSRF_COOKIE_SECURE': True,
        'SECURE_SSL_REDIRECT': True,
    }


@pytest.mark.parametrize(
    ('environment', 'seconds'),
    [('production', 31536000), ('staging', 3600), ('development', 3600)],
)
def test_hsts_default_depends_on_the_environment(environment, seconds):
    values = settings_values(
        PRODUCTION,
        ['SECURE_HSTS_SECONDS', 'SECURE_HSTS_INCLUDE_SUBDOMAINS', 'SECURE_HSTS_PRELOAD'],
        {'ENVIRONMENT': environment},
    )

    # Short outside production; the broad scopes are off everywhere by default.
    assert values == {
        'SECURE_HSTS_SECONDS': seconds,
        'SECURE_HSTS_INCLUDE_SUBDOMAINS': False,
        'SECURE_HSTS_PRELOAD': False,
    }


@pytest.mark.parametrize('environment', ['production', 'staging', 'development'])
def test_explicit_hsts_seconds_override_the_environment_default(environment):
    values = settings_values(
        PRODUCTION,
        ['SECURE_HSTS_SECONDS'],
        {'ENVIRONMENT': environment, 'DJANGO_SECURE_HSTS_SECONDS': '600'},
    )

    assert values == {'SECURE_HSTS_SECONDS': 600}


def test_cookies_stay_secure_when_the_https_redirect_is_disabled():
    values = settings_values(
        PRODUCTION,
        ['SECURE_SSL_REDIRECT', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE'],
        {'DJANGO_SECURE_SSL_REDIRECT': 'False'},
    )

    assert values == {
        'SECURE_SSL_REDIRECT': False,
        'SESSION_COOKIE_SECURE': True,
        'CSRF_COOKIE_SECURE': True,
    }


def test_hsts_can_be_configured_from_the_environment():
    values = settings_values(
        PRODUCTION,
        ['SECURE_HSTS_SECONDS', 'SECURE_HSTS_INCLUDE_SUBDOMAINS', 'SECURE_HSTS_PRELOAD'],
        {
            'DJANGO_SECURE_HSTS_SECONDS': '63072000',
            'DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS': 'True',
            'DJANGO_SECURE_HSTS_PRELOAD': 'True',
        },
    )

    assert values == {
        'SECURE_HSTS_SECONDS': 63072000,
        'SECURE_HSTS_INCLUDE_SUBDOMAINS': True,
        'SECURE_HSTS_PRELOAD': True,
    }


@pytest.mark.parametrize(
    'overrides',
    [
        {'DJANGO_SECURE_HSTS_SECONDS': '-1'},
        # Preload needs includeSubDomains ...
        {'DJANGO_SECURE_HSTS_PRELOAD': 'True'},
        # ... and at least a one-year max-age.
        {
            'DJANGO_SECURE_HSTS_PRELOAD': 'True',
            'DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS': 'True',
            'DJANGO_SECURE_HSTS_SECONDS': '3600',
        },
    ],
)
def test_unsafe_hsts_configuration_is_rejected(overrides):
    rejects(PRODUCTION, overrides, 'DJANGO_SECURE_HSTS')


def test_forwarded_protocol_header_is_trusted_only_when_enabled():
    values = settings_values(
        PRODUCTION, ['SECURE_PROXY_SSL_HEADER'], {'DJANGO_TRUST_X_FORWARDED_PROTO': 'True'}
    )

    assert values['SECURE_PROXY_SSL_HEADER'] == ['HTTP_X_FORWARDED_PROTO', 'https']


def test_production_cors_is_restricted_to_the_configured_origins():
    values = settings_values(
        PRODUCTION,
        [
            'CORS_ALLOWED_ORIGINS',
            'CORS_ALLOW_ALL_ORIGINS',
            'CORS_URLS_REGEX',
            'CSRF_TRUSTED_ORIGINS',
        ],
    )

    assert values == {
        'CORS_ALLOWED_ORIGINS': ['https://app.example.test'],
        'CORS_ALLOW_ALL_ORIGINS': False,
        # GraphQL, plus the attachment byte-transfer endpoints (S2-007 and the
        # console's audited evidence download), which live outside `/graphql/`
        # and are unreachable from the browser without this - see the note at the
        # setting. Endpoint-scoped rather than broad: what is restricted here is
        # *which paths* may be called cross-origin, and that list is asserted
        # path by path below and in `tests/test_cors.py`. The origin allow-list
        # above is unchanged, so this widens no origin.
        'CORS_URLS_REGEX': r'^(?:/graphql/|/ideas/\d+/attachments/|/administration/attachments/)',
        'CSRF_TRUSTED_ORIGINS': ['https://app.example.test'],
    }


def test_production_cors_pattern_covers_the_browser_api_and_nothing_else():
    # The security property behind the pattern above, asserted as behaviour
    # rather than as a copy of the string: the surfaces the app calls are
    # covered, and everything else on this origin - Django's own admin, the
    # health probe - is not.
    pattern = settings_values(PRODUCTION, ['CORS_URLS_REGEX'])['CORS_URLS_REGEX']

    for path in ('/graphql/', '/ideas/1/attachments/', '/administration/attachments/2/download/'):
        assert re.match(pattern, path), path

    for path in ('/admin/', '/health/', '/ideas/1/', '/ideas/1/comments/'):
        assert not re.match(pattern, path), path


def test_production_cors_defaults_to_no_cross_origin_access():
    values = settings_values(
        PRODUCTION,
        ['CORS_ALLOWED_ORIGINS', 'CSRF_TRUSTED_ORIGINS'],
        {'CORS_ALLOWED_ORIGINS': '', 'CSRF_TRUSTED_ORIGINS': ''},
    )

    assert values == {'CORS_ALLOWED_ORIGINS': [], 'CSRF_TRUSTED_ORIGINS': []}


def test_no_credentials_are_hardcoded_in_settings():
    values = settings_values(PRODUCTION, ['SECRET_KEY', 'DATABASES'])

    # Every credential must come from the (fake) environment supplied by the test.
    assert values['SECRET_KEY'] == VALID_PRODUCTION_ENV['DJANGO_SECRET_KEY']
    database = values['DATABASES']['default']
    assert database['PASSWORD'] == 'app-password'
    assert database['USER'] == 'app_user'


# --- Local / CI must never be deployable ------------------------------------------


@pytest.mark.parametrize('environment', ['ci', 'test', 'demo', ''])
def test_production_settings_reject_ci_and_unknown_environments(environment):
    rejects(PRODUCTION, {'ENVIRONMENT': environment}, 'ENVIRONMENT')


@pytest.mark.parametrize('environment', ['development', 'staging', 'production'])
def test_local_settings_reject_deployed_environments(environment):
    rejects(LOCAL, {'ENVIRONMENT': environment}, 'config.settings.local')


@pytest.mark.parametrize('environment', ['local', 'ci'])
def test_local_settings_accept_local_and_ci(environment):
    values = settings_values(LOCAL, ['ENVIRONMENT'], {'ENVIRONMENT': environment})

    assert values == {'ENVIRONMENT': environment}


def test_local_development_stays_practical():
    values = settings_values(
        LOCAL,
        [
            'DEBUG',
            'ALLOWED_HOSTS',
            'CORS_ALLOWED_ORIGINS',
            'SESSION_COOKIE_SECURE',
            'SECURE_SSL_REDIRECT',
        ],
        {
            'ENVIRONMENT': 'local',
            'DJANGO_DEBUG': None,
            'DJANGO_ALLOWED_HOSTS': None,
            'CORS_ALLOWED_ORIGINS': None,
            'CSRF_TRUSTED_ORIGINS': None,
        },
    )

    assert values['DEBUG'] is True
    assert values['ALLOWED_HOSTS'] == ['localhost', '127.0.0.1']
    assert 'http://localhost:5173' in values['CORS_ALLOWED_ORIGINS']
    # Plain-HTTP localhost must keep working: no HTTPS redirect or secure-only cookies.
    assert not values['SECURE_SSL_REDIRECT']
    assert not values['SESSION_COOKIE_SECURE']


# --- Behaviour of the running application ------------------------------------------


def run_production_request(secure, environment='production'):
    """Request /health/ through the production settings; return status and headers."""
    code = (
        'import django, json; django.setup(); '
        'from django.test import Client; '
        f'r = Client(SERVER_NAME="api.example.test").get("/health/", secure={secure!r}); '
        'print(json.dumps({"status": r.status_code, "headers": dict(r.headers)}))'
    )
    result = import_settings(PRODUCTION, {'ENVIRONMENT': environment}, code)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_production_redirects_http_to_https():
    response = run_production_request(secure=False)

    assert response['status'] == 301
    assert response['headers']['Location'] == 'https://api.example.test/health/'


def test_production_https_responses_carry_security_headers():
    response = run_production_request(secure=True)
    headers = response['headers']

    assert response['status'] == 200
    assert headers['Strict-Transport-Security'] == 'max-age=31536000'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert headers['X-Frame-Options'] == 'DENY'
    assert headers['Referrer-Policy'] == 'same-origin'
    assert headers['Cross-Origin-Opener-Policy'] == 'same-origin'


def test_staging_hsts_header_uses_the_short_default():
    response = run_production_request(secure=True, environment='staging')

    assert response['headers']['Strict-Transport-Security'] == 'max-age=3600'


def test_local_responses_carry_browser_protection_headers(client):
    response = client.get('/health/')

    assert response.status_code == 200
    assert response['X-Content-Type-Options'] == 'nosniff'
    assert response['X-Frame-Options'] == 'DENY'
    assert response['Referrer-Policy'] == 'same-origin'
    # No HSTS over plain-HTTP localhost.
    assert 'Strict-Transport-Security' not in response


def test_deployment_check_reports_only_the_expected_warnings():
    """`manage.py check --deploy` must stay clean apart from documented opt-ins."""
    result = subprocess.run(
        [sys.executable, 'manage.py', 'check', '--deploy'],
        cwd=BACKEND_DIR,
        env=clean_env(PRODUCTION),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    reported = {
        line.split(')')[0].split('(')[1]
        for line in result.stderr.splitlines()
        if line.startswith('?:')
    }
    # includeSubDomains and preload are deliberate opt-ins (see docs/environments.md).
    assert reported <= {'security.W005', 'security.W021'}, result.stderr
