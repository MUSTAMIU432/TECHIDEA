"""
Shared helpers for tests that evaluate a settings module in a fresh interpreter.
"""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

# A syntactically valid, obviously fake production configuration.
VALID_PRODUCTION_ENV = {
    'DJANGO_SETTINGS_MODULE': 'config.settings.production',
    'ENVIRONMENT': 'staging',
    'DJANGO_SECRET_KEY': 'x7Kp2mQ9vL4nR8tW1yB6cD3fG5hJ0sZaE2uI4oP7qA9wX1eV3b',
    'DJANGO_JWT_SIGNING_KEY': 'j9Wq3rT6yU1iO4pL8sD2fG5hJ0kZaE7uI4oP2qA6wX9eV1bN3m',
    'DJANGO_DEBUG': 'False',
    'DJANGO_ALLOWED_HOSTS': 'api.example.test',
    'DATABASE_URL': 'postgres://app_user:app-password@db.example.test:5432/app',
    'CORS_ALLOWED_ORIGINS': 'https://app.example.test',
    'CSRF_TRUSTED_ORIGINS': 'https://app.example.test',
    # Required in every deployed environment: a per-process cache is not
    # usable for cross-process security state (Google ID-token replay
    # protection, authentication rate limits). See
    # tests/test_cache_configuration.py.
    'CACHE_URL': 'redis://cache.example.test:6379/1',
    # Outgoing email. config/settings/production.py refuses to start unless
    # all five of these are set, because both emailed flows (password reset,
    # account activation) report success whether or not the mail left the
    # process - so a deployment that cannot send looks exactly like one nobody
    # has used yet. They belong in this fixture for the same reason CACHE_URL
    # does: without them `import config.settings.production` fails on the
    # email guard before any test gets to assert the behaviour it came for,
    # which is how five of these were missing in the first place.
    #
    # The guard is correct and stays; a fixture that cannot load production
    # settings is simply not a production environment. `tests/test_settings.py`
    # asserts this exact dict still loads, so the next variable production.py
    # starts requiring fails there first and alone.
    #
    # EMAIL_HOST and EMAIL_PORT are deliberately absent: they have defaults and
    # no guard, so a required-only fixture does not need them.
    'EMAIL_BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
    'EMAIL_HOST_USER': 'mailer@example.test',
    'EMAIL_HOST_PASSWORD': 'app-only-mail-password',
    'DEFAULT_FROM_EMAIL': 'no-reply@example.test',
    # Where the emailed links point. Must be https in a deployed environment
    # or the one-time token in the link is handed to the network in the clear.
    'FRONTEND_URL': 'https://app.example.test',
}


def clean_env(module, overrides=None):
    """A minimal environment for `module`. An override of None unsets the variable."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DJANGO_', 'CORS_', 'CSRF_'))}
    env.pop('ENVIRONMENT', None)
    env.pop('DATABASE_URL', None)
    env.update(VALID_PRODUCTION_ENV)
    env['DJANGO_SETTINGS_MODULE'] = module
    env.update(overrides or {})
    return {k: v for k, v in env.items() if v is not None}


def import_settings(module, overrides=None, code=None):
    """Import `module` in a clean subprocess."""
    # Fixed interpreter and a code string built by the tests themselves - no untrusted input.
    return subprocess.run(  # noqa: S603
        [sys.executable, '-c', code or f'import {module}'],
        cwd=BACKEND_DIR,
        env=clean_env(module, overrides),
        capture_output=True,
        text=True,
    )
