"""
Environment configuration for the emailed flows (S1-010).

Every setting the two flows need is read from the environment, and five of
them are *required* in a deployed environment by
`config/settings/production.py`. That combination is where configuration
bugs live, and they are invisible until a deployment: a variable the code
reads under one name while the template documents another is silently
inert, and a required variable with no example value in
`backend/.env.example` is a trap set for whoever deploys next.

So this file asserts the wiring rather than the values:

- `base.py` reads each variable under the name the template documents, and
  gives the optional ones sane defaults;
- `production.py` refuses to start on every one of the five, and refuses the
  console email backend specifically - a deployment that printed its
  password-reset links to a log instead of mailing them would look like it
  worked and reach nobody, and the request that "failed" is the one that
  reports success either way;
- `local.py` defaults to the console backend and the Vite dev server, so the
  whole flow is clickable by hand with no mail account;
- `backend/.env.example` documents every required variable as an assignable
  example, and carries no value that could be mistaken for a real
  credential.

The production cases each run in a fresh interpreter, so `backend/.env` (which
only fills in variables that are *unset*) cannot leak into the result and
mask a missing requirement.
"""

import re

import pytest

from .settings_helpers import BACKEND_DIR, import_settings

REPO_DIR = BACKEND_DIR.parent
ENV_EXAMPLE = BACKEND_DIR / '.env.example'

CONSOLE_BACKEND = 'django.core.mail.backends.console.EmailBackend'
SMTP_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# Every variable production.py refuses to start without, with a value that
# satisfies it. Used to prove each guard fires on its own: start from a
# configuration that loads, remove exactly one thing, and the failure must
# name that thing.
REQUIRED_IN_PRODUCTION = {
    'EMAIL_BACKEND': SMTP_BACKEND,
    'EMAIL_HOST_USER': 'mailer@example.test',
    'EMAIL_HOST_PASSWORD': 'app-only-mail-password',
    'DEFAULT_FROM_EMAIL': 'no-reply@example.test',
    'FRONTEND_URL': 'https://app.example.test',
}

# Variables with defaults, so an unset one is not an error locally.
OPTIONAL_WITH_DEFAULTS = {
    'EMAIL_HOST': 'localhost',
    'EMAIL_PORT': '587',
    'EMAIL_USE_TLS': 'True',
    'PASSWORD_RESET_TOKEN_LIFETIME_MINUTES': '60',
    'ACTIVATION_TOKEN_LIFETIME_HOURS': '48',
}


def assert_rejected(overrides, message):
    result = import_settings('config.settings.production', overrides)
    assert result.returncode != 0, f'production settings should have been rejected: {overrides}'
    assert 'ImproperlyConfigured' in result.stderr
    assert message in result.stderr, result.stderr


def read_settings(module, **overrides):
    """Import a settings module in a clean interpreter and return it."""
    result = import_settings(
        module,
        overrides,
        code=(
            f'import {module} as s, json;'
            ' print(json.dumps({'
            '"FRONTEND_URL": s.FRONTEND_URL,'
            '"EMAIL_BACKEND": s.EMAIL_BACKEND,'
            '"EMAIL_HOST": s.EMAIL_HOST,'
            '"EMAIL_PORT": s.EMAIL_PORT,'
            '"EMAIL_USE_TLS": s.EMAIL_USE_TLS,'
            '"EMAIL_HOST_USER": s.EMAIL_HOST_USER,'
            '"DEFAULT_FROM_EMAIL": s.DEFAULT_FROM_EMAIL,'
            '"PASSWORD_RESET_MINUTES": s.PASSWORD_RESET_TOKEN_LIFETIME.total_seconds() / 60,'
            '"ACTIVATION_HOURS": s.ACTIVATION_TOKEN_LIFETIME.total_seconds() / 3600,'
            '}))'
        ),
    )
    assert result.returncode == 0, result.stderr
    import json

    return json.loads(result.stdout.strip().splitlines()[-1])


# --- base.py: every variable is read, and the optional ones have defaults -----------


class TestBaseSettings:
    def test_the_defaults_are_the_ones_local_development_relies_on(self):
        values = read_settings(
            'config.settings.base',
            EMAIL_BACKEND=None,
            EMAIL_HOST=None,
            EMAIL_PORT=None,
            EMAIL_USE_TLS=None,
            EMAIL_HOST_USER=None,
            DEFAULT_FROM_EMAIL=None,
            FRONTEND_URL=None,
            PASSWORD_RESET_TOKEN_LIFETIME_MINUTES=None,
            ACTIVATION_TOKEN_LIFETIME_HOURS=None,
        )

        # The console backend is what makes the flow completable by hand with
        # no mail account: it prints the reset link to the terminal.
        assert values['EMAIL_BACKEND'] == CONSOLE_BACKEND
        assert values['EMAIL_HOST'] == 'localhost'
        assert values['EMAIL_PORT'] == 587
        assert values['EMAIL_USE_TLS'] is True
        assert values['PASSWORD_RESET_MINUTES'] == 60
        assert values['ACTIVATION_HOURS'] == 48

    def test_a_trailing_slash_on_the_frontend_url_is_stripped(self):
        """
        Normalized once, here, where the value is read. `identity.email`
        composes the origin with a frontend path, so an un-stripped trailing
        slash would put a `//` in every link in every inbox - which is the
        kind of thing that works in a test (the path still resolves) and looks
        broken to a person.
        """
        values = read_settings('config.settings.base', FRONTEND_URL='https://app.example.test/')

        assert values['FRONTEND_URL'] == 'https://app.example.test'

    def test_the_lifetimes_are_configurable(self):
        values = read_settings(
            'config.settings.base',
            PASSWORD_RESET_TOKEN_LIFETIME_MINUTES='15',
            ACTIVATION_TOKEN_LIFETIME_HOURS='6',
        )

        assert values['PASSWORD_RESET_MINUTES'] == 15
        assert values['ACTIVATION_HOURS'] == 6

    def test_every_documented_variable_is_read_under_that_name(self):
        """
        The exact-name property that bit this project once already with
        `GOOGLE_OAUTH_CLIENT_ID` (see tests/test_google_oauth_configuration.py):
        a variable read under a name the template does not document is inert,
        with no error - the flow simply fails closed at runtime, in production,
        having looked configured all along.
        """
        content = ENV_EXAMPLE.read_text()
        for variable in (*REQUIRED_IN_PRODUCTION, *OPTIONAL_WITH_DEFAULTS):
            assert variable in content, f'{variable} is not in backend/.env.example'

        # And the reverse direction: a variable the template documents that the
        # settings never read is a setting that does nothing.
        source = (BACKEND_DIR / 'config' / 'settings' / 'base.py').read_text()
        for variable in (*REQUIRED_IN_PRODUCTION, *OPTIONAL_WITH_DEFAULTS):
            assert f"'{variable}'" in source, f'{variable} is documented but never read'


# --- local.py: usable with no mail account ------------------------------------------


class TestLocalSettings:
    """
    `config.settings.local` refuses any deployed environment name, so these
    cases set ENVIRONMENT explicitly - `tests.settings_helpers` is built
    around a production-shaped baseline, and reusing it here means pinning
    the one variable this module insists on.
    """

    def test_the_console_backend_is_the_local_default(self):
        """
        Spelled out in local.py rather than inherited by accident, so it reads
        as the deliberate local-only choice it is - and production.py rejects
        it outright.
        """
        values = read_settings(
            'config.settings.local', ENVIRONMENT='local', EMAIL_BACKEND=None, FRONTEND_URL=None
        )

        assert values['EMAIL_BACKEND'] == CONSOLE_BACKEND

    def test_the_frontend_url_defaults_to_the_dev_server(self):
        """
        The reset link points into the browser app, so without this a local
        reset link would point nowhere and the flow could not be tested by
        hand at all.
        """
        values = read_settings('config.settings.local', ENVIRONMENT='local', FRONTEND_URL=None)

        assert values['FRONTEND_URL'].startswith('http://localhost:5173')

    def test_an_explicit_frontend_url_is_not_overridden(self):
        values = read_settings(
            'config.settings.local',
            ENVIRONMENT='local',
            FRONTEND_URL='http://192.168.1.10:5173',
        )

        assert values['FRONTEND_URL'] == 'http://192.168.1.10:5173'


# --- production.py: fail closed -----------------------------------------------------


class TestProductionSettings:
    @pytest.mark.parametrize('variable', sorted(REQUIRED_IN_PRODUCTION))
    def test_each_required_variable_is_required_on_its_own(self, variable):
        """
        One guard, one variable, started from a configuration that loads. This
        is the test that catches a newly-required variable that
        `VALID_PRODUCTION_ENV` has not been updated for: the fixture fails to
        load first, and every other production test in the suite fails with it
        rather than pointing at the real cause.
        """
        assert_rejected({variable: None}, variable)

    @pytest.mark.parametrize('variable', ['EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD'])
    def test_blank_credentials_are_rejected(self, variable):
        assert_rejected({variable: ''}, variable)

    def test_the_console_backend_is_rejected(self):
        """
        The one that matters most, and the reason these guards exist at all: a
        deployment printing its password-reset links to a log instead of
        mailing them would report success for every reset request - the
        generic response is the same whether or not the address has an
        account - so nothing would look broken until somebody complained.
        """
        assert_rejected({'EMAIL_BACKEND': CONSOLE_BACKEND}, 'EMAIL_BACKEND')

    @pytest.mark.parametrize('sender', ['', 'no-reply@localhost'])
    def test_the_local_default_sender_is_rejected(self, sender):
        """
        A placeholder sender is how reset mail ends up in spam: SPF and DKIM
        are checked against the domain in From, so `no-reply@localhost` cannot
        pass for anybody.
        """
        assert_rejected({'DEFAULT_FROM_EMAIL': sender}, 'DEFAULT_FROM_EMAIL')

    def test_a_missing_frontend_url_is_rejected(self):
        """
        A link built from an empty origin is a link to nowhere, and both
        emailed flows would report success while delivering nothing.
        """
        assert_rejected({'FRONTEND_URL': ''}, 'FRONTEND_URL')

    @pytest.mark.parametrize(
        'origin', ['http://app.example.test', 'app.example.test', 'ftp://app.example.test']
    )
    def test_a_non_https_frontend_url_is_rejected(self, origin):
        """
        The link carries a single-use token in its query string, so an http://
        origin hands that credential to the network in the clear - and a
        scheme-less value is not a URL the browser can resolve.
        """
        assert_rejected({'FRONTEND_URL': origin}, 'FRONTEND_URL')

    def test_a_fully_specified_configuration_loads(self):
        result = import_settings('config.settings.production', dict(REQUIRED_IN_PRODUCTION))

        assert result.returncode == 0, result.stderr


# --- backend/.env.example: documented, and carrying nothing real --------------------


class TestEnvExample:
    @pytest.mark.parametrize('variable', sorted(REQUIRED_IN_PRODUCTION))
    def test_every_required_variable_is_documented(self, variable):
        """
        production.py's own error messages tell the operator to "see
        backend/.env.example". A required variable with no entry there is a
        dead end at exactly the moment somebody is trying to fix a
        deployment.
        """
        content = ENV_EXAMPLE.read_text()

        assert variable in content, f'{variable} is required but not documented'

    @pytest.mark.parametrize('variable', sorted(OPTIONAL_WITH_DEFAULTS))
    def test_every_optional_variable_is_documented_too(self, variable):
        content = ENV_EXAMPLE.read_text()

        assert variable in content, f'{variable} is read but not documented'

    def test_the_console_backend_is_shown_as_the_local_default(self):
        """
        The one value that is safe to commit and the one a developer needs
        before anything else works locally.
        """
        content = ENV_EXAMPLE.read_text()

        assert CONSOLE_BACKEND in content
        assert SMTP_BACKEND in content

    def test_the_lifetimes_are_documented(self):
        content = ENV_EXAMPLE.read_text()

        assert 'PASSWORD_RESET_TOKEN_LIFETIME_MINUTES' in content
        assert 'ACTIVATION_TOKEN_LIFETIME_HOURS' in content

    def test_no_real_looking_host_is_committed(self):
        """
        An example host is fine; somebody's actual SMTP relay is an
        environment-specific value that does not belong in the repository
        even though it is not a secret on its own.
        """
        content = ENV_EXAMPLE.read_text()
        real_host = re.compile(r'^\s*#?\s*EMAIL_HOST=\s*smtp\.(?!example)', re.MULTILINE)

        assert not real_host.search(content), 'backend/.env.example names a real SMTP host'

    def test_no_real_looking_credential_is_committed(self):
        for pattern, description in (
            (r'GOCSPX-', 'a Google client secret'),
            (r'-----BEGIN [A-Z ]*PRIVATE KEY-----', 'a private key'),
            (r'\bya29\.', 'a Google OAuth refresh token'),
            (r'\bxox[baprs]-', 'a Slack token'),
        ):
            content = ENV_EXAMPLE.read_text()
            assert not re.search(pattern, content), f'backend/.env.example contains {description}'

    def test_the_example_mailbox_is_on_a_reserved_example_domain(self):
        """
        The one value here that identifies a real mailbox, so the strongest
        constraint applies to it: a reserved example domain, never somebody's
        actual address.
        """
        content = ENV_EXAMPLE.read_text()
        for line in content.splitlines():
            stripped = line.strip().lstrip('#').strip()
            if not stripped.startswith('EMAIL_HOST_USER='):
                continue
            value = stripped.split('=', 1)[1]
            if not value:
                continue  # left unset on purpose
            assert 'example.' in value, f'{stripped} is not an example-domain value'

    def test_the_example_password_is_an_obvious_placeholder(self):
        """
        A password cannot be on an example domain, so it is pinned by shape
        instead: the value has to read as a placeholder, which is also what
        stops a real one being committed here.
        """
        content = ENV_EXAMPLE.read_text()
        placeholder = re.compile(r'(app-only|password|change-me|example|dummy|not-a-real)', re.I)

        for line in content.splitlines():
            stripped = line.strip().lstrip('#').strip()
            if not stripped.startswith('EMAIL_HOST_PASSWORD='):
                continue
            value = stripped.split('=', 1)[1]
            if not value:
                continue
            assert placeholder.search(value), f'{stripped} does not read as a placeholder'
