"""
Google ID token (OIDC) verification for "Sign in with Google".

This verifies an ID token from Google Identity Services' (GIS) Sign In With
Google flow: the browser obtains a Google-signed JWT directly from Google
and hands it to us; we verify it here and never trust anything else the
browser might claim about the user (email, name, Google user id, ...).

Why the ID-token flow rather than the classic OAuth2 authorization-code
exchange: this platform only needs to know *who* signed in, not ongoing
delegated access to a Google API on the user's behalf (Gmail, Calendar,
...). The ID-token flow is Google's own documented mechanism for exactly
that narrower case, and - unlike the authorization-code flow - never
involves a client secret on either side: there is no code-exchange step,
so nothing is ever exchanged that a secret would protect. That is why
there is no `GOOGLE_OAUTH_CLIENT_SECRET` setting anywhere in this project;
it would be dead configuration for this flow, not a missing one.

Verification is one call to Google's own client library
(`verify_oauth2_token`), which:
- fetches Google's current public signing keys and checks the token's
  signature against them (rejects a forged or tampered token);
- checks the `iss` claim is really Google's (`accounts.google.com` or
  `https://accounts.google.com`);
- checks the `aud` claim equals our own OAuth client id, when one is
  configured (rejects a token minted for a different application);
- checks the token hasn't expired.

That covers signature, issuer, audience and expiration; this module adds
what the library doesn't: refusing to run at all when no client id is
configured (see the audience note below), requiring the `sub`/`email`
claims we need to identify the account, and rejecting a token that has
already been used once (see `_reject_if_already_used` below).

Replay protection, and why this is a one-time-use check rather than a
transmitted `nonce`: a classic OIDC `nonce` mainly defends the
authorization-code/implicit redirect flow, where the ID token travels
through a browser redirect URL that can leak into Referer headers, proxy
or server logs, or browser history, and be replayed into a *different*
browser session than the one that requested it - the nonce, generated and
remembered by the client before the redirect, lets the client detect that
mismatch. GIS's Sign In With Google button flow used here doesn't redirect
at all - the credential is delivered to the page directly via a JS
callback - so that specific leak vector mostly doesn't apply, and there is
no pre-existing client-side session to have stored an expected nonce value
in anyway (this app deliberately has none before authentication succeeds).
A nonce transmitted alongside the credential in the same GraphQL request
would add complexity without closing anything a plain attacker capturing
the whole request couldn't trivially resubmit unchanged.

What a captured, still-unexpired credential *can* still do here - via a
compromised browser extension, an XSS bug, a server-side log capturing
GraphQL request bodies, or a network intermediary - is being replayed to
`googleLogin` a second time. `_reject_if_already_used` closes that
completely rather than narrowing it: the first successful verification of
a given token is recorded (by its hash, never the raw token) as consumed,
and any later attempt with the identical token is rejected outright, for
as long as the token itself would otherwise still be valid.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings
from django.core.cache import caches
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jwt import PyJWTError

# Reused across calls: it caches Google's fetched public keys internally
# rather than re-fetching them on every sign-in.
_GOOGLE_REQUEST = google_requests.Request()

# Cache-key namespace for consumed-token tracking (see
# _reject_if_already_used). A prefix, not a secret - ruff's hardcoded-
# password heuristic just pattern-matches the word "token".
_REPLAY_CACHE_KEY_PREFIX = 'identity:google_id_token_used:'


def _replay_cache():
    """
    The cache backing consumed-token tracking.

    Resolved per call rather than captured at import time so the alias comes
    from settings, and named explicitly (`replay_protection`, not `default`)
    so this security state can never silently share a namespace with
    general-purpose caching that something else is free to flush.

    The alias is configured in config/settings/base.py: a per-process
    in-memory backend for local development, and a shared server (Redis,
    Memcached, ...) in every deployed environment, where
    `config/settings/production.py` refuses to start otherwise. That is the
    whole requirement replay protection has: a token consumed by *any* process
    must be seen as consumed by every other one.
    """
    return caches[settings.REPLAY_PROTECTION_CACHE_ALIAS]


class GoogleTokenError(Exception):
    """Raised for any invalid, expired, malformed, wrong-audience/issuer,
    unconfigured, or otherwise unverifiable Google ID token, and for a
    verified token missing a claim we require."""


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    first_name: str
    last_name: str


def _split_full_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or '').strip().split(maxsplit=1)
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], parts[1]


def _reject_if_already_used(raw_token: str, claims: dict) -> None:
    """
    Raise GoogleTokenError if `raw_token` (already verified as genuine, at
    this point) has been successfully used before.

    Recorded by the token's own SHA-256 hash - the same idiom
    `identity.authentication` already uses for refresh credentials - never
    the raw token itself, with a cache TTL equal to the token's own
    remaining lifetime (from its `exp` claim): the record never needs to
    outlive the token it guards, since an expired token is already rejected
    by `verify_oauth2_token` on its own. `cache.add` only succeeds if the
    key is not already present, which is what makes this correct against a
    genuinely concurrent double-submission of the same token, not just a
    later sequential replay.

    Which cache: the dedicated `replay_protection` alias
    (`settings.REPLAY_PROTECTION_CACHE_ALIAS`, see `_replay_cache`). A
    per-process in-memory backend is complete for a single-process
    deployment and is what local development uses; every deployed environment
    is required to configure a backend all of its processes share, and
    `config/settings/production.py` refuses to start if it does not - which
    is what makes this a real control rather than a single-process
    convenience. `cache.add` is an atomic "set if absent" on Redis,
    Memcached and the database backend, so a concurrent double-submission
    still loses for exactly one of the two callers.
    """
    expires_at = datetime.fromtimestamp(claims['exp'], tz=UTC)
    ttl_seconds = int((expires_at - datetime.now(tz=UTC)).total_seconds())
    if ttl_seconds <= 0:
        # Already expired - verify_oauth2_token would have rejected this on
        # its own; defensive only, so a non-positive TTL is never passed to
        # cache.add (some backends treat that as "cache forever").
        raise GoogleTokenError('Invalid or expired Google credential.')

    cache_key = _REPLAY_CACHE_KEY_PREFIX + hashlib.sha256(raw_token.encode('utf-8')).hexdigest()
    if not _replay_cache().add(cache_key, True, timeout=ttl_seconds):
        raise GoogleTokenError('This Google credential has already been used.')


def verify_google_id_token(raw_token: str) -> GoogleIdentity:
    """
    Verify `raw_token` and return the identity it asserts.

    Raises GoogleTokenError for: a missing token, Google sign-in not being
    configured, a bad signature, wrong issuer, wrong audience (a token
    minted for a different OAuth client), an expired token, a malformed
    token, a token that has already been used once (see
    `_reject_if_already_used`), or a verified token missing the `sub` or
    `email` claim.
    """
    if not raw_token:
        raise GoogleTokenError('Missing Google credential.')

    client_id = settings.GOOGLE_OAUTH_CLIENT_ID
    if not client_id:
        # `verify_oauth2_token(..., audience=None)` would skip audience
        # verification entirely - accepting a token minted for *any*
        # Google OAuth client, not just ours. Failing closed here, rather
        # than passing audience=None, is what makes leaving Google sign-in
        # unconfigured (e.g. local dev without a Google Cloud project) safe
        # instead of silently insecure.
        raise GoogleTokenError('Google sign-in is not configured.')

    try:
        claims = google_id_token.verify_oauth2_token(raw_token, _GOOGLE_REQUEST, audience=client_id)
    except (ValueError, GoogleAuthError, PyJWTError) as exc:
        # Broad on purpose: verify_oauth2_token's own implementation raises
        # a plain ValueError for most failures (bad signature, malformed
        # token, wrong audience) and google.auth.exceptions.GoogleAuthError
        # specifically for a wrong issuer; PyJWTError is caught too as a
        # defensive backstop in case a future library version lets one of
        # PyJWT's own decode exceptions (e.g. ExpiredSignatureError)
        # surface directly. Never expose which case occurred.
        raise GoogleTokenError('Invalid or expired Google credential.') from exc

    _reject_if_already_used(raw_token, claims)

    subject = claims.get('sub')
    email = claims.get('email')
    if not subject or not email:
        raise GoogleTokenError('Google credential is missing required identity claims.')

    first_name, last_name = _split_full_name(claims.get('name', ''))
    first_name = claims.get('given_name') or first_name
    last_name = claims.get('family_name') or last_name

    return GoogleIdentity(
        subject=subject,
        email=email,
        email_verified=bool(claims.get('email_verified', False)),
        first_name=first_name,
        last_name=last_name,
    )
