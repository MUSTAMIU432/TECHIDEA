import time
from datetime import timedelta

import jwt
import pytest
from django.conf import settings
from django.test import override_settings

from identity.tokens import ACCESS_TOKEN_TYPE, TokenError, decode_access_token, issue_access_token


class TestIssueAccessToken:
    def test_returns_a_token_and_an_expiry(self):
        token, expires_at = issue_access_token(user_id=42)

        assert isinstance(token, str)
        assert expires_at is not None

    def test_token_contains_only_the_expected_claims(self):
        token, _ = issue_access_token(user_id=42)

        payload = jwt.decode(token, settings.JWT_SIGNING_KEY, algorithms=['HS256'])

        assert set(payload.keys()) == {'sub', 'type', 'jti', 'iat', 'exp'}
        assert payload['sub'] == '42'
        assert payload['type'] == ACCESS_TOKEN_TYPE
        assert 'password' not in payload
        assert 'email' not in payload
        assert 'is_staff' not in payload

    def test_each_token_has_a_unique_id(self):
        token_a, _ = issue_access_token(user_id=42)
        token_b, _ = issue_access_token(user_id=42)

        assert decode_access_token(token_a).token_id != decode_access_token(token_b).token_id


class TestDecodeAccessToken:
    def test_decodes_a_valid_token(self):
        token, expires_at = issue_access_token(user_id=42)

        claims = decode_access_token(token)

        assert claims.user_id == 42
        # JWT `exp`/`iat` are integer seconds (per spec), so sub-second
        # precision is lost on the round trip - compare within a second.
        assert abs((claims.expires_at - expires_at).total_seconds()) < 1

    def test_rejects_an_empty_token(self):
        with pytest.raises(TokenError):
            decode_access_token('')

    def test_rejects_a_malformed_token(self):
        with pytest.raises(TokenError):
            decode_access_token('not-a-jwt-at-all')

    def test_rejects_a_tampered_signature(self):
        token, _ = issue_access_token(user_id=42)
        tampered = token[:-1] + ('a' if token[-1] != 'a' else 'b')

        with pytest.raises(TokenError):
            decode_access_token(tampered)

    def test_rejects_a_token_signed_with_a_different_key(self):
        payload = {
            'sub': '42',
            'type': ACCESS_TOKEN_TYPE,
            'jti': 'x',
            'iat': int(time.time()),
            'exp': int(time.time()) + 3600,
        }
        forged = jwt.encode(payload, 'a-completely-different-signing-key', algorithm='HS256')

        with pytest.raises(TokenError):
            decode_access_token(forged)

    def test_rejects_an_expired_token(self):
        with override_settings(ACCESS_TOKEN_LIFETIME=timedelta(seconds=-1)):
            token, _ = issue_access_token(user_id=42)

        with pytest.raises(TokenError):
            decode_access_token(token)

    def test_rejects_a_token_of_the_wrong_type(self):
        payload = {
            'sub': '42',
            'type': 'refresh',
            'jti': 'x',
            'iat': int(time.time()),
            'exp': int(time.time()) + 3600,
        }
        token = jwt.encode(payload, settings.JWT_SIGNING_KEY, algorithm='HS256')

        with pytest.raises(TokenError):
            decode_access_token(token)

    def test_rejects_a_token_missing_the_subject_claim(self):
        payload = {
            'type': ACCESS_TOKEN_TYPE,
            'jti': 'x',
            'iat': int(time.time()),
            'exp': int(time.time()) + 3600,
        }
        token = jwt.encode(payload, settings.JWT_SIGNING_KEY, algorithm='HS256')

        with pytest.raises(TokenError):
            decode_access_token(token)

    @pytest.mark.parametrize('missing_claim', ['iat', 'exp'])
    def test_rejects_a_token_missing_a_timestamp_claim(self, missing_claim):
        payload = {
            'sub': '42',
            'type': ACCESS_TOKEN_TYPE,
            'jti': 'x',
            'iat': int(time.time()),
            'exp': int(time.time()) + 3600,
        }
        payload.pop(missing_claim)
        token = jwt.encode(payload, settings.JWT_SIGNING_KEY, algorithm='HS256')

        with pytest.raises(TokenError):
            decode_access_token(token)

    def test_rejects_the_none_algorithm(self):
        # Classic JWT attack: set alg=none and strip the signature, hoping a
        # lenient decoder accepts it unsigned. Pinning `algorithms=['HS256']`
        # on decode must always refuse this outright.
        unsigned = jwt.api_jws.encode(
            b'{"sub": "42", "type": "access", "jti": "x", "iat": 0, "exp": 9999999999}',
            key=None,
            algorithm='none',
        )

        with pytest.raises(TokenError):
            decode_access_token(unsigned)
