"""
Cache configuration for cross-process security state (S1-009).

`identity.google_oauth`'s replay protection and `identity.throttling`'s
rate-limit counters are both *state the whole deployment has to agree on*:
one process recording "this Google credential is spent" or "this account is
locked out" is worthless if the next process to receive the same request
cannot see it. A per-process in-memory cache is not a shared cache - it is a
separate copy per worker - so it is correct for a single process and silently
useless for several.

These tests pin the two halves of that rule: local development may use the
per-process backend, and no deployed environment may. Each case imports the
settings module in a fresh interpreter (see `.settings_helpers`), so a
deployed configuration is evaluated exactly as it would be at startup.
"""

import pytest

from .settings_helpers import import_settings

# A syntactically valid cache URL. No connection is made - settings
# configuration is what is under test, not reachability.
REDIS_URL = 'redis://cache.example.test:6379/1'
MEMCACHED_URL = 'pymemcache://cache.example.test:11211'

_PRINT_BACKENDS = (
    'from config.settings import {module} as s; '
    'print(s.REPLAY_PROTECTION_CACHE_ALIAS, "|", '
    's.CACHES[s.REPLAY_PROTECTION_CACHE_ALIAS]["BACKEND"], "|", '
    's.CACHES[s.REPLAY_PROTECTION_CACHE_ALIAS]["LOCATION"], "|", '
    's.CACHES["default"]["BACKEND"])'
)


def _read_backends(module, overrides=None):
    """`(replay alias, replay backend, replay location, default backend)`."""
    result = import_settings(module, overrides, code=_PRINT_BACKENDS.format(module=module))

    assert result.returncode == 0, result.stderr
    alias, replay_backend, replay_location, default_backend = result.stdout.strip().split(' | ')
    return alias, replay_backend, replay_location, default_backend


class TestProductionRequiresASharedCache:
    def test_production_without_a_cache_url_is_rejected(self):
        result = import_settings('config.settings.production', {'CACHE_URL': None})

        assert result.returncode != 0
        assert 'ImproperlyConfigured' in result.stderr
        assert 'CACHE_URL' in result.stderr

    def test_production_with_a_blank_cache_url_is_rejected(self):
        result = import_settings('config.settings.production', {'CACHE_URL': ''})

        assert result.returncode != 0
        assert 'CACHE_URL' in result.stderr

    @pytest.mark.parametrize('url', ['locmemcache://', 'locmemcache://replay-protection'])
    def test_production_refuses_a_local_in_memory_cache(self, url):
        # The failure mode this exists to prevent: a deployed environment
        # that starts up cleanly with a per-process cache, and therefore
        # accepts a replayed Google credential on every worker but the one
        # that first saw it.
        result = import_settings('config.settings.production', {'CACHE_URL': url})

        assert result.returncode != 0
        assert 'ImproperlyConfigured' in result.stderr
        assert 'CACHE_URL' in result.stderr

    @pytest.mark.parametrize('url', [REDIS_URL, MEMCACHED_URL])
    def test_production_accepts_a_shared_cache_url(self, url):
        result = import_settings('config.settings.production', {'CACHE_URL': url})

        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize('url', [REDIS_URL, MEMCACHED_URL])
    def test_production_configures_the_replay_protection_alias_from_that_url(self, url):
        alias, replay_backend, replay_location, _ = _read_backends('production', {'CACHE_URL': url})

        assert alias == 'replay_protection'
        # Whichever shared driver the URL selects, the *security* alias gets
        # it - a deployment must not end up with a shared `default` cache and
        # a per-process replay cache, which is the configuration that looks
        # right and protects nothing.
        assert 'locmem' not in replay_backend.lower()
        assert 'django.core.cache.backends' in replay_backend
        # ...pointing at the server django-environ was told to use (Redis
        # keeps the whole URL as its LOCATION; Memcached keeps host:port).
        assert 'cache.example.test' in replay_location

    def test_production_keeps_replay_protection_on_its_own_alias(self):
        # Not cosmetic: a dedicated alias is what keeps this security state
        # out of whatever namespace general-purpose caching flushes.
        _, replay_backend, _, _ = _read_backends('production', {'CACHE_URL': REDIS_URL})

        assert replay_backend


class TestLocalDevelopmentMayUseTheInProcessCache:
    def test_local_without_a_cache_url_uses_an_in_memory_cache(self):
        _, replay_backend, _, _ = _read_backends('base', {'CACHE_URL': ''})

        assert 'locmem' in replay_backend.lower()

    def test_local_accepts_an_explicit_shared_cache_url_too(self):
        # Same machine, several uvicorn/gunicorn workers: the switch must be
        # available without changing code.
        _, replay_backend, _, _ = _read_backends('base', {'CACHE_URL': REDIS_URL})

        assert 'locmem' not in replay_backend.lower()

    def test_the_two_aliases_stay_separate_in_local_development(self):
        _, replay_backend, _, default_backend = _read_backends('base', {'CACHE_URL': ''})

        assert replay_backend == default_backend
        # Separate key namespaces despite the same backend class: a
        # `cache.clear()` aimed at `default` must not be able to wipe the
        # replay records.
        assert replay_backend == 'django.core.cache.backends.locmem.LocMemCache'


class TestReplayProtectionUsesTheSharedAlias:
    """
    The wiring itself: the check must go to the `replay_protection` alias and
    not to `default`, and must resolve the alias at call time so a test (or a
    deployment) that reconfigures `CACHES` is honoured.
    """

    def test_the_alias_is_named_replay_protection(self, settings):
        assert settings.REPLAY_PROTECTION_CACHE_ALIAS == 'replay_protection'
        assert 'replay_protection' in settings.CACHES

    def test_replay_protection_reads_from_the_replay_protection_alias(self):
        from django.core.cache import caches

        from identity import google_oauth

        assert google_oauth._replay_cache() is caches['replay_protection']
        assert google_oauth._replay_cache() is not caches['default']

    def test_replay_records_are_written_to_the_replay_protection_alias(self, settings):
        import time

        from django.core.cache import caches

        from identity.google_oauth import _REPLAY_CACHE_KEY_PREFIX, verify_google_id_token
        from identity.tests.test_google_oauth import _patched_verify

        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        claims = {
            'sub': 'google-subject-123',
            'email': 'ada@example.com',
            'email_verified': True,
            'name': 'Ada Lovelace',
            'exp': int(time.time()) + 3600,
        }

        with _patched_verify(claims=claims):
            verify_google_id_token('a-token-only-for-this-test')

        import hashlib

        key = _REPLAY_CACHE_KEY_PREFIX + hashlib.sha256(b'a-token-only-for-this-test').hexdigest()

        # Present in the security alias...
        assert caches['replay_protection'].get(key) is True
        # ...and not visible through general-purpose caching, so a stray
        # `cache.clear()` on `default` cannot erase it.
        assert caches['default'].get(key) is None

    def test_replay_protection_still_rejects_a_replay(self, settings):
        # The behaviour itself is unchanged by the alias switch - asserted
        # here so the migration to a shared cache is provably not a change in
        # what the check does.
        import time

        from identity.google_oauth import GoogleTokenError, verify_google_id_token
        from identity.tests.test_google_oauth import _patched_verify

        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        claims = {
            'sub': 'google-subject-123',
            'email': 'ada@example.com',
            'email_verified': True,
            'name': 'Ada Lovelace',
            'exp': int(time.time()) + 3600,
        }

        with _patched_verify(claims=claims):
            verify_google_id_token('single-use')
            with pytest.raises(GoogleTokenError):
                verify_google_id_token('single-use')
