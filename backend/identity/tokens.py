"""
JWT access-token issuance and verification.

This module handles only the *access* token - a short-lived, stateless
JWT. The refresh credential is deliberately not a JWT at all; see
`identity.models.RefreshSession` for why, and `identity.authentication` for
where it's issued, rotated and revoked.

Claims are kept minimal on purpose: subject (user id), token type, a unique
id, issued-at and expiry. Never the password/hash, never unnecessary
personal information - anything else a resolver needs about the user is
looked up fresh from the database via the resolved User, not trusted from
the token payload.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from django.conf import settings

# A token 'type' claim value, not a credential - ruff's hardcoded-password
# heuristic just pattern-matches the word "token".
ACCESS_TOKEN_TYPE = 'access'  # noqa: S105
_ALGORITHM = 'HS256'


class TokenError(Exception):
    """Raised for any missing, malformed, expired, tampered, or wrong-type token."""


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: int
    token_id: str
    issued_at: datetime
    expires_at: datetime


def issue_access_token(user_id: int) -> tuple[str, datetime]:
    """Return `(token, expires_at)` for a new short-lived access token."""
    now = datetime.now(tz=UTC)
    expires_at = now + settings.ACCESS_TOKEN_LIFETIME
    payload = {
        'sub': str(user_id),
        'type': ACCESS_TOKEN_TYPE,
        'jti': uuid.uuid4().hex,
        'iat': now,
        'exp': expires_at,
    }
    token = jwt.encode(payload, settings.JWT_SIGNING_KEY, algorithm=_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str) -> AccessTokenClaims:
    """
    Decode and verify `token`.

    Verifies the signature, the expiry, and the token type; pinning
    `algorithms=[...]` to exactly the one used to sign also rules out
    algorithm-confusion attacks (e.g. a token claiming `alg: none`, or one
    signed with a different algorithm entirely). Raises TokenError - never
    a raw PyJWT exception - for anything invalid, so callers only need to
    handle one exception type.
    """
    if not token:
        raise TokenError('Missing access token.')

    try:
        payload = jwt.decode(token, settings.JWT_SIGNING_KEY, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError('Invalid or expired access token.') from exc

    if payload.get('type') != ACCESS_TOKEN_TYPE:
        raise TokenError('Invalid or expired access token.')

    try:
        user_id = int(payload['sub'])
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError('Invalid or expired access token.') from exc

    try:
        issued_at = datetime.fromtimestamp(payload['iat'], tz=UTC)
        expires_at = datetime.fromtimestamp(payload['exp'], tz=UTC)
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise TokenError('Invalid or expired access token.') from exc

    return AccessTokenClaims(
        user_id=user_id,
        token_id=payload.get('jti', ''),
        issued_at=issued_at,
        expires_at=expires_at,
    )
