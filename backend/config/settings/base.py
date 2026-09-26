"""
Base Django settings for the Automation Platform backend.

Shared by every environment. Environment-specific settings (local,
production) import from this module and override only what differs.
"""

from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
# BASE_DIR is backend/ (three levels up from this file: settings/base.py).
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# backend/.env is a local-development convenience. Variables already set in
# the real environment (deploy platform, CI, secret manager) take precedence.
environ.Env.read_env(BASE_DIR / '.env')


# Deployment environment label. This is informational (logging, docs, future
# feature gating); security behavior is selected by DJANGO_SETTINGS_MODULE.
# local.py serves `local`; production.py serves development/staging/production.
ENVIRONMENT = env('ENVIRONMENT', default='local')

# Required in every environment - there is deliberately no default, so a
# missing secret fails at startup instead of falling back to a known value.
# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('DJANGO_SECRET_KEY')

# Safe by default; local.py opts in to debug behavior.
DEBUG = env.bool('DJANGO_DEBUG', default=False)

ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=[])

# Browser origins allowed to call the API cross-origin / submit CSRF-protected
# requests. Empty by default; local.py supplies the Vite dev server origin.
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'strawberry_django',
    # Business domain apps (own their models and logic) before the GraphQL
    # adapter layer that exposes them.
    'identity',
    'organizations',
    'ideas',
    'graphql_api',
]

# Sprint 1: the identity app owns the platform's own User model rather than
# Django's default (which is username/password, not email/password). This
# must be set before the first `migrate` in any environment - swapping it
# afterwards is not supported by Django's migration framework.
AUTH_USER_MODEL = 'identity.User'

# JWT access-token signing (identity/tokens.py). Deliberately a separate
# secret from DJANGO_SECRET_KEY: SECRET_KEY is used for several unrelated
# purposes (session/CSRF signing, ...), and rotating it shouldn't force
# rotating - or be blocked by the need to keep valid - every issued access
# token, and vice versa. Required everywhere, like SECRET_KEY, so a missing
# secret fails at startup rather than falling back to a known value.
JWT_SIGNING_KEY = env('DJANGO_JWT_SIGNING_KEY')

# Access tokens are short-lived and stateless (no DB check to verify one) -
# that's the whole point of a JWT here, and why it must expire quickly.
# Refresh credentials are long-lived but server-side (RefreshSession), so
# they can be individually revoked; a stolen refresh credential is the real
# exposure window, not the access token.
ACCESS_TOKEN_LIFETIME = timedelta(minutes=env.int('ACCESS_TOKEN_LIFETIME_MINUTES', default=15))
REFRESH_TOKEN_LIFETIME = timedelta(days=env.int('REFRESH_TOKEN_LIFETIME_DAYS', default=30))

# Google OAuth/OIDC ("Sign in with Google", S1-004). Optional: unset, the
# `googleLogin` mutation always fails closed rather than accepting tokens
# for an unknown audience (see identity/google_oauth.py) - deployments that
# don't need Google sign-in yet, or local dev without a Google Cloud
# project, need not set this. Not secret: a Google OAuth client ID is
# embedded in the frontend bundle by design (it identifies the app, not a
# credential), so it's also read directly by the frontend as
# VITE_GOOGLE_OAUTH_CLIENT_ID. There is deliberately no
# GOOGLE_OAUTH_CLIENT_SECRET: the ID-token flow used here verifies a
# Google-signed credential against Google's public keys and never
# exchanges an authorization code, so the backend has no use for a client
# secret at all (see identity/google_oauth.py's module docstring).
GOOGLE_OAUTH_CLIENT_ID = env('GOOGLE_OAUTH_CLIENT_ID', default='')


# Outgoing email (identity/email.py)
# ---------------------------------------------------------------------------------
# Everything the two emailed flows need: where the browser app lives (to build
# the link a user clicks), how long a link stays usable, and how to reach an
# SMTP server.
#
# Deliberately plain SMTP rather than a Google API client: the only Google
# credential this project holds is a public OAuth *client id* for verifying
# sign-in ID tokens, which is not a credential that can send anything. Sending
# mail through Gmail's SMTP relay needs a mailbox and an app password - no
# service account, no domain-wide delegation, no key file to leak - and
# EMAIL_BACKEND is a plain Django setting, so swapping in a hosted provider
# later (SES, Postmark, django-anymail) is a settings change, not a rewrite.
#
# EMAIL_BACKEND defaults to Django's console backend: it writes the message to
# stdout instead of sending it, so a developer can complete a reset or
# activation locally with no mail account at all. production.py refuses that
# default - a deployment that printed its password-reset links to a log
# instead of mailing them would look like it worked and reach nobody.
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='localhost')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
# Submission (port 587) is encrypted in transit; implicit TLS on 465 is
# instead selected by setting EMAIL_USE_TLS=False and EMAIL_PORT=465, which
# Django expresses as SSL-on-connect.
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='no-reply@localhost')

# The public origin of the browser app, without a trailing slash. Password
# reset and activation emails are links *into the frontend*, not into the API,
# so this is the one setting that decides where a user is sent after clicking
# one. Required in every deployed environment (a link built from an empty
# value is a link to nowhere); local.py fills in the Vite dev server.
FRONTEND_URL = env('FRONTEND_URL', default='').rstrip('/')

# How long an emailed link stays usable. Both are deliberately short: the link
# travels over plaintext-ish channels (a mail client's search index, a shared
# inbox, a chat app someone forwards it into) and is only needed at the moment
# its owner clicks it, so there is no reason for it to stay good for days.
PASSWORD_RESET_TOKEN_LIFETIME = timedelta(
    minutes=env.int('PASSWORD_RESET_TOKEN_LIFETIME_MINUTES', default=60)
)
ACTIVATION_TOKEN_LIFETIME = timedelta(hours=env.int('ACTIVATION_TOKEN_LIFETIME_HOURS', default=48))


# Cache framework
# ---------------------------------------------------------------------------------
# Three aliases, deliberately separated even where they point at the same
# place:
#
#   default           general-purpose caching. Nothing security-critical may
#                     ever depend on this one.
#   replay_protection used *only* by identity.google_oauth to record Google ID
#                     tokens that have already been consumed.
#   auth_throttle     used *only* by identity.throttling to count
#                     authentication attempts.
#
# The latter two are security-critical and therefore deliberately isolated:
# each can be pointed at a different shared server than general caching,
# neither is ever cleared as part of routine cache housekeeping, and
# production.py refuses to start if either resolves to a per-process backend.
#
# In one process, a per-process in-memory cache is complete protection: a
# replayed token is rejected, and an over-limit request refused, by the very
# same worker that recorded the state. Across processes it is not - worker 1
# accepts a Google credential that worker 2 has never seen, and worker 2
# permits another ten login attempts after worker 1 stopped at five - so
# anything that runs as more than one process needs a backend every worker
# shares. CACHE_URL selects it (Redis, Memcached, a database, ...); it is
# required in every deployed environment precisely because that is where
# "more than one process" is the norm.
REPLAY_PROTECTION_CACHE_ALIAS = 'replay_protection'
AUTH_THROTTLE_CACHE_ALIAS = 'auth_throttle'

# The aliases whose contents decide a security outcome. A deployed
# environment may not let any of them resolve to a per-process backend - see
# config/settings/production.py, which enforces exactly this list.
SHARED_CACHE_ALIASES = (REPLAY_PROTECTION_CACHE_ALIAS, AUTH_THROTTLE_CACHE_ALIAS)

_DEFAULT_CACHE = {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'automation-platform-default',
}
_LOCAL_SECURITY_CACHE = {
    'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    'LOCATION': 'automation-platform-security',
}

_cache_url = env('CACHE_URL', default='')
if _cache_url:
    # django-environ parses a URL into a cache config (e.g.
    # redis://localhost:6379/1). All aliases share the server but keep
    # separate key namespaces, so a `cache.clear()` aimed at one can never
    # wipe another's records.
    CACHES = {
        'default': env.cache_url('CACHE_URL'),
        REPLAY_PROTECTION_CACHE_ALIAS: env.cache_url('CACHE_URL'),
        AUTH_THROTTLE_CACHE_ALIAS: env.cache_url('CACHE_URL'),
    }
else:
    CACHES = {
        'default': _DEFAULT_CACHE,
        REPLAY_PROTECTION_CACHE_ALIAS: _LOCAL_SECURITY_CACHE,
        AUTH_THROTTLE_CACHE_ALIAS: _LOCAL_SECURITY_CACHE,
    }


MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Must sit above any middleware that can generate responses (CommonMiddleware).
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

# DATABASE_URL is required and has no fallback, so credentials only ever come
# from the environment. Driver options such as TLS go in the URL query string,
# e.g. ?sslmode=require.
if not env('DATABASE_URL'):
    # Blank is treated as missing; django-environ would only warn and Django
    # would fail later, on first use, with a much less helpful error.
    raise ImproperlyConfigured('DATABASE_URL must be set (see backend/.env.example).')

DATABASES = {
    'default': env.db('DATABASE_URL'),
}
DATABASES['default']['CONN_MAX_AGE'] = env.int('DATABASE_CONN_MAX_AGE', default=0)
DATABASES['default']['CONN_HEALTH_CHECKS'] = DATABASES['default']['CONN_MAX_AGE'] > 0


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = 'static/'


# CORS
# Only the GraphQL API is meant to be called from the browser app. The
# allowed origins themselves come from CORS_ALLOWED_ORIGINS (never a wildcard).

CORS_URLS_REGEX = r'^/graphql/'
# Explicit so a stray setting elsewhere can never open the API to every origin.
CORS_ALLOW_ALL_ORIGINS = False
# Sprint 1 (S1-003): the refresh-token cookie requires the browser to send
# and accept credentials on cross-origin requests (the frontend and backend
# run on different origins even in local dev - :5173 vs :8000), so this can
# no longer stay off. This is safe only because CORS_ALLOWED_ORIGINS is - and
# must remain - an explicit, non-wildcard allow-list (django-cors-headers
# refuses to combine credentials with a wildcard origin regardless, and
# production.py separately rejects wildcards); see docs/environments.md and
# docs/architecture.md for the full authentication cookie/CORS/CSRF design.
CORS_ALLOW_CREDENTIALS = True


# Security headers and cookies
# https://docs.djangoproject.com/en/5.2/ref/middleware/#module-django.middleware.security
#
# Browser-protection defaults shared by every environment. They are pinned here
# (several match Django's own defaults) so they are visible and tested, and so a
# Django upgrade cannot silently loosen them. Transport security (HTTPS
# redirect, secure cookies, HSTS) is enforced by production.py.

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
# Served by XFrameOptionsMiddleware: the app may not be framed by any site.
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
