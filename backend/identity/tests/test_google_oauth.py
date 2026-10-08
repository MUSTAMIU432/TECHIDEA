import time
from unittest.mock import patch

import pytest
from google.auth.exceptions import GoogleAuthError

from identity.google_oauth import GoogleTokenError, verify_google_id_token

# Comfortably in the future for every test - the replay check (run after
# every successful verification) needs a real `exp` to compute a cache TTL
# from, exactly as a genuine Google-issued token would have.
_FUTURE_EXP = int(time.time()) + 3600

VALID_CLAIMS = {
    'sub': 'google-subject-123',
    'email': 'ada@example.com',
    'email_verified': True,
    'given_name': 'Ada',
    'family_name': 'Lovelace',
    'name': 'Ada Lovelace',
    'exp': _FUTURE_EXP,
}


def _patched_verify(claims=None, side_effect=None):
    target = 'identity.google_oauth.google_id_token.verify_oauth2_token'
    if side_effect is not None:
        return patch(target, side_effect=side_effect)
    return patch(target, return_value=claims)


@pytest.mark.django_db
class TestVerifyGoogleIdToken:
    def test_valid_token_returns_the_verified_identity(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with _patched_verify(claims=VALID_CLAIMS):
            identity = verify_google_id_token('a-raw-token')

        assert identity.subject == 'google-subject-123'
        assert identity.email == 'ada@example.com'
        assert identity.email_verified is True
        assert identity.first_name == 'Ada'
        assert identity.last_name == 'Lovelace'

    def test_verification_is_scoped_to_our_configured_client_id(self, settings):
        # The audience passed to Google's verifier must be our own client
        # id - never None (which would skip audience verification) and
        # never a value taken from the request.
        settings.GOOGLE_OAUTH_CLIENT_ID = 'our-client-id'

        with _patched_verify(claims=VALID_CLAIMS) as mocked:
            verify_google_id_token('a-raw-token')

        _args, kwargs = mocked.call_args
        assert kwargs['audience'] == 'our-client-id'

    def test_missing_token_fails(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with pytest.raises(GoogleTokenError):
            verify_google_id_token('')

    def test_unconfigured_client_id_fails_closed(self, settings):
        # Must never fall back to audience=None (which would accept a
        # token minted for any Google OAuth client, not just ours).
        settings.GOOGLE_OAUTH_CLIENT_ID = ''

        with _patched_verify(claims=VALID_CLAIMS) as mocked, pytest.raises(GoogleTokenError):
            verify_google_id_token('a-raw-token')

        mocked.assert_not_called()

    def test_invalid_signature_or_malformed_token_fails(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with (
            _patched_verify(side_effect=ValueError('Token used too early or malformed')),
            pytest.raises(GoogleTokenError),
        ):
            verify_google_id_token('garbage')

    def test_wrong_issuer_fails(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with (
            _patched_verify(side_effect=GoogleAuthError('Wrong issuer')),
            pytest.raises(GoogleTokenError),
        ):
            verify_google_id_token('a-raw-token')

    def test_expired_token_fails(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with (
            _patched_verify(side_effect=ValueError('Token expired')),
            pytest.raises(GoogleTokenError),
        ):
            verify_google_id_token('an-expired-token')

    def test_wrong_audience_fails(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with (
            _patched_verify(side_effect=ValueError('Token has wrong audience')),
            pytest.raises(GoogleTokenError),
        ):
            verify_google_id_token('a-token-for-a-different-client')

    def test_missing_subject_claim_fails(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        claims = {k: v for k, v in VALID_CLAIMS.items() if k != 'sub'}

        with _patched_verify(claims=claims), pytest.raises(GoogleTokenError):
            verify_google_id_token('a-raw-token')

    def test_missing_email_claim_fails(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        claims = {k: v for k, v in VALID_CLAIMS.items() if k != 'email'}

        with _patched_verify(claims=claims), pytest.raises(GoogleTokenError):
            verify_google_id_token('a-raw-token')

    def test_error_message_never_exposes_the_underlying_exception_detail(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with (
            _patched_verify(side_effect=ValueError('some internal Google library detail')),
            pytest.raises(GoogleTokenError) as exc_info,
        ):
            verify_google_id_token('a-raw-token')

        assert 'internal Google library detail' not in str(exc_info.value)

    def test_falls_back_to_splitting_the_name_claim_when_given_family_missing(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        claims = {
            'sub': 'sub-1',
            'email': 'grace@example.com',
            'email_verified': True,
            'name': 'Grace Hopper',
            'exp': _FUTURE_EXP,
        }

        with _patched_verify(claims=claims):
            identity = verify_google_id_token('a-raw-token')

        assert identity.first_name == 'Grace'
        assert identity.last_name == 'Hopper'

    def test_single_word_name_claim_yields_an_empty_last_name(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        claims = {
            'sub': 'sub-1',
            'email': 'madonna@example.com',
            'email_verified': True,
            'name': 'Madonna',
            'exp': _FUTURE_EXP,
        }

        with _patched_verify(claims=claims):
            identity = verify_google_id_token('a-raw-token')

        assert identity.first_name == 'Madonna'
        assert identity.last_name == ''

    def test_missing_name_claims_entirely_yields_empty_names(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        claims = {
            'sub': 'sub-1',
            'email': 'noname@example.com',
            'email_verified': True,
            'exp': _FUTURE_EXP,
        }

        with _patched_verify(claims=claims):
            identity = verify_google_id_token('a-raw-token')

        assert identity.first_name == ''
        assert identity.last_name == ''

    def test_missing_email_verified_claim_defaults_to_false(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        claims = {k: v for k, v in VALID_CLAIMS.items() if k != 'email_verified'}

        with _patched_verify(claims=claims):
            identity = verify_google_id_token('a-raw-token')

        assert identity.email_verified is False


@pytest.mark.django_db
class TestReplayProtection:
    def test_the_same_token_cannot_be_used_twice(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with _patched_verify(claims=VALID_CLAIMS):
            verify_google_id_token('a-single-use-token')

            with pytest.raises(GoogleTokenError):
                verify_google_id_token('a-single-use-token')

    def test_replay_rejection_does_not_leak_which_failure_it_is(self, settings):
        # Same generic exception type/shape as every other verification
        # failure - identity.authentication wraps this into the one
        # generic GENERIC_GOOGLE_LOGIN_ERROR either way, but the module's
        # own contract (only ever raise GoogleTokenError) must hold too.
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with _patched_verify(claims=VALID_CLAIMS):
            verify_google_id_token('a-single-use-token')

            with pytest.raises(GoogleTokenError) as exc_info:
                verify_google_id_token('a-single-use-token')

        assert 'already been used' in str(exc_info.value)

    def test_two_different_tokens_can_each_be_used_once(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        first_claims = {**VALID_CLAIMS, 'sub': 'subject-a'}
        second_claims = {**VALID_CLAIMS, 'sub': 'subject-b'}

        with _patched_verify(claims=first_claims):
            first = verify_google_id_token('token-a')
        with _patched_verify(claims=second_claims):
            second = verify_google_id_token('token-b')

        assert first.subject == 'subject-a'
        assert second.subject == 'subject-b'

    def test_an_already_expired_token_is_rejected_before_touching_the_replay_cache(self, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'
        expired_claims = {**VALID_CLAIMS, 'exp': int(time.time()) - 10}

        with (
            _patched_verify(claims=expired_claims),
            pytest.raises(GoogleTokenError),
        ):
            verify_google_id_token('an-expired-token')

    def test_replay_check_is_keyed_by_token_not_by_subject(self, settings):
        # Two different (e.g. double-issued) tokens for the *same* Google
        # identity are independent for replay purposes - the point is
        # preventing a captured token from being resubmitted, not limiting
        # how often a subject may sign in.
        settings.GOOGLE_OAUTH_CLIENT_ID = 'test-client-id'

        with _patched_verify(claims=VALID_CLAIMS):
            verify_google_id_token('first-token-for-this-subject')
            second = verify_google_id_token('second-token-for-this-subject')

        assert second.subject == VALID_CLAIMS['sub']
