"""
Google OAuth configuration wiring (S1-009).

Regression cover for a real, already-shipped misconfiguration: the code reads
`GOOGLE_OAUTH_CLIENT_ID` (config/settings/base.py ->
identity/google_oauth.py) while the developer's `backend/.env` had that value
stored under the wrong name (`CLIENT_ID`). Nothing failed - `django-environ`
has no default to complain about, and `GOOGLE_OAUTH_CLIENT_ID` simply
resolved to its `''` default - so Google sign-in was silently disabled with
"Google sign-in is not configured." instead of a startup error naming the
actual problem.

The variable *name* is therefore part of the contract between this project's
`.env` file and its code, and these tests pin it. Each case imports the
settings module in a fresh interpreter so the value is read from a known
environment rather than inherited from the developer's real `backend/.env`
(see `.settings_helpers`).
"""

import ast
import json
import re
from pathlib import Path

import pytest
from django.test import Client

from .settings_helpers import import_settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

# Obviously fake, but shaped like a real one: this is the *name* of the
# contract being pinned, not a credential.
FAKE_CLIENT_ID = '1234567890-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com'

GOOGLE_LOGIN_MUTATION = """
mutation GoogleLogin($input: GoogleLoginInput!) {
  googleLogin(input: $input) { success message accessToken user { id email } }
}
"""

# Printed as a repr and parsed with ast.literal_eval so a value that is
# itself only whitespace (a real `.env` mistake - see the whitespace test
# below) survives the round trip instead of being stripped to nothing.
_PRINT_SETTING = 'from config.settings import {module} as s; print(repr(s.GOOGLE_OAUTH_CLIENT_ID))'


def _read_client_id(module, overrides=None):
    """Evaluate `module` in a clean subprocess and return its client id."""
    result = import_settings(module, overrides, code=_PRINT_SETTING.format(module=module))

    assert result.returncode == 0, result.stderr
    return ast.literal_eval(result.stdout.strip())


def _local_env(**overrides):
    """
    A minimal valid *local* configuration, plus `overrides`.

    `GOOGLE_OAUTH_CLIENT_ID` is always present unless a test deliberately
    removes it, because `django-environ`'s `read_env` only fills in variables
    that are *unset* - leaving it out of the subprocess environment would let
    the developer's real `backend/.env` answer for it. Pass `''` to model
    "not configured" explicitly.
    """
    env = {
        'ENVIRONMENT': 'local',
        'DJANGO_SECRET_KEY': 'local-test-key',
        'DATABASE_URL': 'postgres://u:p@localhost:5432/db',
        'GOOGLE_OAUTH_CLIENT_ID': '',
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


class TestClientIdVariableName:
    """`GOOGLE_OAUTH_CLIENT_ID` is the name every environment must use."""

    def test_local_settings_read_google_oauth_client_id(self):
        assert _read_client_id('base', _local_env(GOOGLE_OAUTH_CLIENT_ID=FAKE_CLIENT_ID)) == (
            FAKE_CLIENT_ID
        )

    def test_production_settings_read_the_same_variable(self):
        assert (
            _read_client_id('production', {'GOOGLE_OAUTH_CLIENT_ID': FAKE_CLIENT_ID})
            == FAKE_CLIENT_ID
        )

    def test_the_documented_name_is_never_silently_a_different_one_per_module(self):
        # A cheap guard against a rename landing in one settings module only:
        # every module that re-exports the base setting must keep reading the
        # same variable.
        for module, overrides in (
            ('base', _local_env(GOOGLE_OAUTH_CLIENT_ID=FAKE_CLIENT_ID)),
            ('local', _local_env(GOOGLE_OAUTH_CLIENT_ID=FAKE_CLIENT_ID)),
            ('production', {'GOOGLE_OAUTH_CLIENT_ID': FAKE_CLIENT_ID}),
        ):
            assert _read_client_id(module, overrides) == FAKE_CLIENT_ID, module

    @pytest.mark.parametrize(
        'wrong_name',
        ['CLIENT_ID', 'GOOGLE_CLIENT_ID', 'VITE_GOOGLE_OAUTH_CLIENT_ID'],
    )
    def test_a_misnamed_variable_is_ignored_rather_than_accepted(self, wrong_name):
        # The exact shape of the bug this file exists for: a plausible-looking
        # variable in `.env` must NOT be picked up as the client id. If it ever
        # were, Google sign-in would verify against the wrong audience while
        # looking correctly configured - and nothing anywhere would fail.
        env = _local_env(**{wrong_name: FAKE_CLIENT_ID})

        assert _read_client_id('base', env) == ''

    def test_explicitly_empty_resolves_to_empty_so_google_login_fails_closed(self):
        assert _read_client_id('base', _local_env(GOOGLE_OAUTH_CLIENT_ID='')) == ''

    def test_whitespace_only_is_treated_as_configured_but_unusable(self):
        # Pinned deliberately rather than "fixed": django-environ does not
        # strip, so a stray space yields a non-empty value that can never
        # match a real audience - and the mutation must still fail closed on
        # it (proved in TestGoogleLoginFailsClosed below) rather than
        # silently skip audience verification.
        assert _read_client_id('base', _local_env(GOOGLE_OAUTH_CLIENT_ID=' ')) == ' '


class TestGoogleLoginFailsClosed:
    """
    With no usable client id, `googleLogin` must refuse - and must refuse
    *before* calling Google's verifier, since the only way to "succeed"
    without an audience is to pass `audience=None`, which accepts a token
    minted for any Google OAuth client on the internet.
    """

    @pytest.fixture
    def gql(self, client: Client):
        def post(credential):
            return client.post(
                '/graphql/',
                data=json.dumps(
                    {
                        'query': GOOGLE_LOGIN_MUTATION,
                        'variables': {'input': {'credential': credential}},
                    }
                ),
                content_type='application/json',
            )

        return post

    def _google_verifier_calls(self, monkeypatch):
        """Fail the test if Google's own token verifier is reached."""
        calls = []

        def _record(*args, **kwargs):
            calls.append(kwargs)
            raise AssertionError('Google token verification must not run without a client id.')

        monkeypatch.setattr('identity.google_oauth.google_id_token.verify_oauth2_token', _record)
        return calls

    @pytest.mark.django_db
    def test_mutation_fails_when_the_client_id_is_absent(self, settings, gql):
        settings.GOOGLE_OAUTH_CLIENT_ID = ''

        response = gql('header.payload.signature')

        payload = response.json()['data']['googleLogin']
        assert payload['success'] is False
        assert payload['accessToken'] is None
        assert payload['user'] is None
        assert payload['message'] == 'Could not sign in with Google.'

    @pytest.mark.django_db
    def test_google_verification_is_never_reached_without_a_client_id(
        self, settings, gql, monkeypatch
    ):
        settings.GOOGLE_OAUTH_CLIENT_ID = ''
        calls = self._google_verifier_calls(monkeypatch)

        response = gql('header.payload.signature')

        assert response.json()['data']['googleLogin']['success'] is False
        assert calls == []

    @pytest.mark.django_db
    def test_configured_client_id_is_actually_used_as_the_expected_audience(
        self, settings, monkeypatch
    ):
        settings.GOOGLE_OAUTH_CLIENT_ID = FAKE_CLIENT_ID
        seen = {}

        def _capture(_token, _request, *, audience=None, **kwargs):
            seen['audience'] = audience
            raise ValueError('stop here - the point is the audience')

        monkeypatch.setattr('identity.google_oauth.google_id_token.verify_oauth2_token', _capture)

        from identity.authentication import AuthenticationError, authenticate_with_google

        with pytest.raises(AuthenticationError):
            authenticate_with_google('a-credential')

        # Proves the end-to-end wiring: the value configured as
        # GOOGLE_OAUTH_CLIENT_ID is the audience Google's verifier is asked to
        # check, and never None.
        assert seen['audience'] == FAKE_CLIENT_ID


class TestTemplateAndDocsConsistency:
    """
    The committed templates and docs are the only place a developer learns
    the variable name. They must agree with each other and with the code, and
    must never carry a real environment-specific value.
    """

    def test_backend_env_example_documents_the_expected_variable(self):
        content = (BACKEND_DIR / '.env.example').read_text()

        # `GOOGLE_OAUTH_CLIENT_ID=` must appear as an assignable example, not
        # just inside prose - and the *exact* name matters (see this module's
        # docstring for the misconfiguration that made that worth pinning).
        assert 'GOOGLE_OAUTH_CLIENT_ID=' in content
        assert not any(
            line.startswith('# GOOGLE_OAUTH_CLIENT_ID=') and 'your-client-id' not in line
            for line in content.splitlines()
        ), 'backend/.env.example should show GOOGLE_OAUTH_CLIENT_ID as an example assignment'

    def test_frontend_env_example_documents_the_matching_variable(self):
        content = (REPO_DIR / 'frontend' / '.env.example').read_text()

        assert 'VITE_GOOGLE_OAUTH_CLIENT_ID' in content
        assert 'GOOGLE_OAUTH_CLIENT_ID' in content  # the "must match" note

    def test_both_templates_require_the_two_variables_to_match(self):
        backend = (BACKEND_DIR / '.env.example').read_text()
        frontend = (REPO_DIR / 'frontend' / '.env.example').read_text()

        assert 'frontend/.env' in backend
        assert 'VITE_GOOGLE_OAUTH_CLIENT_ID' in backend
        assert 'backend/.env' in frontend
        assert 'GOOGLE_OAUTH_CLIENT_ID' in frontend

    def test_environments_doc_pairs_the_two_variable_names(self):
        doc = (REPO_DIR / 'docs' / 'environments.md').read_text()

        assert 'GOOGLE_OAUTH_CLIENT_ID' in doc
        assert 'VITE_GOOGLE_OAUTH_CLIENT_ID' in doc

    @pytest.mark.parametrize(
        'template',
        ['backend/.env.example', 'frontend/.env.example'],
    )
    def test_no_template_carries_a_real_looking_google_client_id(self, template):
        # A real client id is `<digits>-<32 chars>.apps.googleusercontent.com`.
        # Placeholders must not be substituted with a real one, ever: a
        # committed client id is a real environment-specific value even
        # though it is not a secret.

        content = (REPO_DIR / template).read_text()
        real_client_id = re.compile(r'\b\d{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com\b')

        assert not real_client_id.search(content), f'{template} contains a real client id'

    def test_no_template_commits_a_google_client_secret(self):
        # There is deliberately no client secret in this flow at all; a
        # committed one would be an actual credential.
        for template in ('backend/.env.example', 'frontend/.env.example'):
            content = (REPO_DIR / template).read_text()
            assert 'GOCSPX-' not in content
            assert 'GOOGLE_OAUTH_CLIENT_SECRET' not in content

    def test_local_google_client_id_matches_the_frontend_when_both_are_configured(self):
        """
        The consistency check the templates can only ask for: when the
        developer's real `.env` files (gitignored, absent in CI) both define
        a client id, they must be the same one - that is the entire contract
        between the two sides of the flow. Skipped when either is unset, so
        this is a check, not a requirement to have Google sign-in configured.
        """
        backend_env = _read_env_file(BACKEND_DIR / '.env')
        frontend_env = _read_env_file(REPO_DIR / 'frontend' / '.env')

        backend_id = backend_env.get('GOOGLE_OAUTH_CLIENT_ID', '').strip()
        frontend_id = frontend_env.get('VITE_GOOGLE_OAUTH_CLIENT_ID', '').strip()

        if not backend_id or not frontend_id:
            pytest.skip('Google sign-in is not configured in both local .env files.')

        assert backend_id == frontend_id


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file's own assignments (no expansion, no interpolation)."""
    if not path.exists():
        return {}
    values = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip()
    return values
