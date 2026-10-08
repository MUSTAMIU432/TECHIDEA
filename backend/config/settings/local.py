"""
Local development settings.

Used when DJANGO_SETTINGS_MODULE is not overridden — the default for
running the project on a developer machine.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import ENVIRONMENT, env

# These settings are permissive by design (debug on by default, localhost
# origins). They serve a developer machine and CI only, so a deployed
# environment name here means production.py was forgotten - refuse to start.
_LOCAL_ENVIRONMENTS = ('local', 'ci')

if ENVIRONMENT not in _LOCAL_ENVIRONMENTS:
    raise ImproperlyConfigured(
        f'ENVIRONMENT {ENVIRONMENT!r} must not use config.settings.local, which is only '
        f'for {" and ".join(_LOCAL_ENVIRONMENTS)}. Deployed environments must set '
        'DJANGO_SETTINGS_MODULE=config.settings.production.'
    )

DEBUG = env.bool('DJANGO_DEBUG', default=True)

if not ALLOWED_HOSTS:  # noqa: F405
    ALLOWED_HOSTS = ['localhost', '127.0.0.1']

# The Vite dev server (frontend/) runs on :5173 and calls this API.
_LOCAL_FRONTEND_ORIGINS = ['http://localhost:5173', 'http://127.0.0.1:5173']

if not CORS_ALLOWED_ORIGINS:  # noqa: F405
    CORS_ALLOWED_ORIGINS = _LOCAL_FRONTEND_ORIGINS

if not CSRF_TRUSTED_ORIGINS:  # noqa: F405
    CSRF_TRUSTED_ORIGINS = _LOCAL_FRONTEND_ORIGINS

# Password-reset and activation emails are links into the frontend, so local
# development needs the Vite dev server's origin to build them. Left unset in
# the environment, a reset link would point nowhere and the flow would be
# impossible to test by hand; set it explicitly when running the app on a
# different port or a real hostname (e.g. FRONTEND_URL=http://192.168.1.10:5173
# to test on a phone).
if not FRONTEND_URL:  # noqa: F405
    FRONTEND_URL = _LOCAL_FRONTEND_ORIGINS[0]

# Same reasoning for mail: the console backend (base.py's default) prints the
# message - reset link included - to the terminal running the server, which is
# enough to click through the whole flow locally with no mail account. Spelled
# out here so it is obvious that this is a deliberate local-only choice, not an
# accident of a missing setting; production.py rejects it outright.
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
