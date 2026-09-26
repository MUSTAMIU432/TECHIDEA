"""
Production-grade settings.

Selected explicitly via DJANGO_SETTINGS_MODULE=config.settings.production and
used by every deployed environment (development, staging, production) so that
shared environments get the same security posture as production. The
environment is named by ENVIRONMENT.

All security-sensitive values must come from the environment — nothing here
should hardcode a secret or a permissive default. Missing or unsafe
configuration fails at startup with ImproperlyConfigured.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import (
    ALLOWED_HOSTS,
    CACHES,
    CORS_ALLOWED_ORIGINS,
    CSRF_TRUSTED_ORIGINS,
    DEFAULT_FROM_EMAIL,
    EMAIL_BACKEND,
    EMAIL_HOST_PASSWORD,
    EMAIL_HOST_USER,
    ENVIRONMENT,
    FRONTEND_URL,
    JWT_SIGNING_KEY,
    SECRET_KEY,
    SHARED_CACHE_ALIASES,
    env,
)

DEPLOYED_ENVIRONMENTS = ('development', 'staging', 'production')

_PLACEHOLDER_SECRET_MARKERS = ('change-me', 'changeme', 'django-insecure')
# Matches Django's own `check --deploy` threshold.
_MIN_SECRET_KEY_LENGTH = 50
# One year: the production default, and the minimum browsers require for HSTS preload.
_PRODUCTION_HSTS_SECONDS = 31536000
# One hour: the default for development and staging, so a first rollout or a
# misconfigured host is forgotten by browsers quickly.
_SHORT_HSTS_SECONDS = 3600


if ENVIRONMENT not in DEPLOYED_ENVIRONMENTS:
    raise ImproperlyConfigured(
        f'ENVIRONMENT must be one of {", ".join(DEPLOYED_ENVIRONMENTS)} when using '
        f'config.settings.production (got {ENVIRONMENT!r}). Use config.settings.local '
        'for local development.'
    )

if len(SECRET_KEY) < _MIN_SECRET_KEY_LENGTH or any(
    marker in SECRET_KEY.lower() for marker in _PLACEHOLDER_SECRET_MARKERS
):
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY must be a real, unique secret of at least '
        f'{_MIN_SECRET_KEY_LENGTH} characters, not a placeholder.'
    )

if len(JWT_SIGNING_KEY) < _MIN_SECRET_KEY_LENGTH or any(
    marker in JWT_SIGNING_KEY.lower() for marker in _PLACEHOLDER_SECRET_MARKERS
):
    raise ImproperlyConfigured(
        'DJANGO_JWT_SIGNING_KEY must be a real, unique secret of at least '
        f'{_MIN_SECRET_KEY_LENGTH} characters, not a placeholder.'
    )

if JWT_SIGNING_KEY == SECRET_KEY:
    raise ImproperlyConfigured(
        'DJANGO_JWT_SIGNING_KEY must not be the same value as DJANGO_SECRET_KEY - '
        'they protect different things and must be able to rotate independently.'
    )

if env.bool('DJANGO_DEBUG', default=False):
    raise ImproperlyConfigured('DJANGO_DEBUG must not be enabled in a deployed environment.')

DEBUG = False

# Shared cache. Two things in this project are *state the whole deployment
# has to agree on*: `identity.google_oauth`'s replay protection (a Google ID
# token already spent) and `identity.throttling`'s rate-limit counters (an
# account or client already over its limit). Both are only correct across
# more than one process if every process shares one cache - a per-process
# in-memory backend is not a cache in that situation, it is a per-process
# copy, and the second worker would not see what the first one recorded. A
# deployed environment is exactly the situation where more than one process
# runs, so this is required here rather than merely recommended.
# See config/settings/base.py's cache section for the alias split.
if not env('CACHE_URL', default=''):
    raise ImproperlyConfigured(
        'CACHE_URL must be set in a deployed environment (e.g. '
        'redis://localhost:6379/1). A per-process cache is not usable for the '
        'Google ID-token replay protection or authentication throttling once the '
        'app runs as more than one process. See config/settings/base.py.'
    )

for _alias in SHARED_CACHE_ALIASES:
    if 'locmem' in CACHES[_alias]['BACKEND'].lower():
        raise ImproperlyConfigured(
            f'CACHE_URL must not point at a local in-memory cache in a deployed '
            f'environment: security state recorded by one process (the '
            f'{_alias!r} cache) would be invisible to every other process.'
        )

if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        'DJANGO_ALLOWED_HOSTS must list explicit hostnames (no wildcard) in a deployed environment.'
    )

for _setting_name, _origins in (
    ('CORS_ALLOWED_ORIGINS', CORS_ALLOWED_ORIGINS),
    ('CSRF_TRUSTED_ORIGINS', CSRF_TRUSTED_ORIGINS),
):
    for _origin in _origins:
        if '*' in _origin or not _origin.startswith('https://'):
            raise ImproperlyConfigured(
                f'{_setting_name} entries must be explicit https:// origins '
                f'without wildcards (got {_origin!r}).'
            )

# Outgoing email. The two emailed flows (password reset, account activation)
# are only useful if the mail actually leaves the process, and both fail in a
# way that is easy to miss: a reset request reports the same generic success
# whether or not an account exists, precisely so it can't be used to probe for
# one - which also means a deployment whose mail is silently broken looks
# exactly like a deployment nobody has used yet. So refuse to start on a
# backend that cannot send, rather than discover it from a support ticket.
if EMAIL_BACKEND == 'django.core.mail.backends.console.EmailBackend':
    raise ImproperlyConfigured(
        'EMAIL_BACKEND must not be the console backend in a deployed environment: '
        'it prints messages to the log instead of sending them, so password-reset '
        'and activation links would reach nobody while the mutations still reported '
        'success. Set it to django.core.mail.backends.smtp.EmailBackend (see '
        'backend/.env.example).'
    )

for _setting_name, _value in (
    ('EMAIL_HOST_USER', EMAIL_HOST_USER),
    ('EMAIL_HOST_PASSWORD', EMAIL_HOST_PASSWORD),
):
    if not _value:
        raise ImproperlyConfigured(f'{_setting_name} must be set in a deployed environment.')

if not DEFAULT_FROM_EMAIL or DEFAULT_FROM_EMAIL == 'no-reply@localhost':
    raise ImproperlyConfigured(
        'DEFAULT_FROM_EMAIL must be set to a real sender address in a deployed '
        'environment (see backend/.env.example).'
    )

# Where emailed links point. A missing value produces a link to nowhere, and a
# wrong scheme (http) hands the token to the network in the clear, so both are
# refused here rather than discovered by a user clicking a broken link.
if not FRONTEND_URL:
    raise ImproperlyConfigured(
        'FRONTEND_URL must be set in a deployed environment, e.g. '
        'https://app.example.com. Password-reset and activation links are built '
        'from it (see identity/email.py).'
    )

if not FRONTEND_URL.startswith('https://'):
    raise ImproperlyConfigured(
        f'FRONTEND_URL must be an https:// origin in a deployed environment '
        f'(got {FRONTEND_URL!r}); an emailed link over plain http would expose '
        'the one-time token in it to anyone on the network path.'
    )

# Transport security. Cookies are never sent over plain HTTP; this is not
# configurable so it cannot be turned off by mistake.
SECURE_SSL_REDIRECT = env.bool('DJANGO_SECURE_SSL_REDIRECT', default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Behind a TLS-terminating proxy or load balancer Django only sees plain HTTP,
# so it would redirect forever and never send HSTS. Opt in ONLY if that proxy
# always sets X-Forwarded-Proto itself and strips any client-supplied value;
# otherwise a client could spoof HTTPS.
if env.bool('DJANGO_TRUST_X_FORWARDED_PROTO', default=False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# HTTP Strict Transport Security. Browsers cache the policy, so production
# defaults to one year and development/staging to one hour; set
# DJANGO_SECURE_HSTS_SECONDS to override either. It applies to this host only
# unless the two opt-ins below are enabled: includeSubDomains affects every
# subdomain and preload is effectively permanent. Enable them deliberately,
# once every subdomain is confirmed HTTPS-only.
SECURE_HSTS_SECONDS = env.int(
    'DJANGO_SECURE_HSTS_SECONDS',
    default=_PRODUCTION_HSTS_SECONDS if ENVIRONMENT == 'production' else _SHORT_HSTS_SECONDS,
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
SECURE_HSTS_PRELOAD = env.bool('DJANGO_SECURE_HSTS_PRELOAD', default=False)

if SECURE_HSTS_SECONDS < 0:
    raise ImproperlyConfigured('DJANGO_SECURE_HSTS_SECONDS must not be negative.')

if SECURE_HSTS_PRELOAD and (
    not SECURE_HSTS_INCLUDE_SUBDOMAINS or SECURE_HSTS_SECONDS < _PRODUCTION_HSTS_SECONDS
):
    raise ImproperlyConfigured(
        'DJANGO_SECURE_HSTS_PRELOAD requires DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=True and '
        f'DJANGO_SECURE_HSTS_SECONDS of at least {_PRODUCTION_HSTS_SECONDS}.'
    )

# Keep database connections open between requests (seconds).
DATABASES['default']['CONN_MAX_AGE'] = env.int('DATABASE_CONN_MAX_AGE', default=60)  # noqa: F405
DATABASES['default']['CONN_HEALTH_CHECKS'] = DATABASES['default']['CONN_MAX_AGE'] > 0  # noqa: F405
