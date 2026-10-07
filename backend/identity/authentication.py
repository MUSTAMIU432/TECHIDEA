"""
Authentication business logic: login, refresh-token rotation, logout, and
resolving the authenticated user from an access token.

GraphQL (`identity.schema`) calls these functions and only translates their
results to/from GraphQL types; it doesn't implement any authentication
logic of its own.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from django.utils import timezone

from identity.google_oauth import GoogleIdentity, GoogleTokenError, verify_google_id_token
from identity.models import ExternalIdentity, RefreshSession, User
from identity.tokens import TokenError, decode_access_token, issue_access_token

# Shown to the caller for every login/refresh failure, regardless of cause
# (unknown email, wrong password, inactive account, expired/revoked/reused
# refresh credential, ...) - see `login()` and `refresh()` docstrings for
# why each one deliberately collapses its failure modes into this.
GENERIC_LOGIN_ERROR = 'Invalid email or password.'
GENERIC_REFRESH_ERROR = 'Your session has expired. Please sign in again.'

# Shown for every Google sign-in failure. An invalid/expired/replayed
# credential, Google sign-in not being configured, an inactive account, and
# a refused account-linking collision all collapse into this, so the caller
# cannot tell them apart - see `authenticate_with_google`'s docstring.
GENERIC_GOOGLE_LOGIN_ERROR = 'Could not sign in with Google.'

# Shown instead of GENERIC_GOOGLE_LOGIN_ERROR, and *only* to a caller who has
# just proven they control this exact mailbox, when the collision is refused.
# See `GoogleEmailInUseError` for why that disclosure is safe here and why it
# is the one exception to the rule above.
#
# The wording is deliberately neutral about *how* the existing account is
# used: it does not say "sign in with your password", because the account may
# equally belong to a different Google identity reporting the same address
# (see `_resolve_google_user`'s collision branch), and saying so would leak
# the existing account's history to a second party. Both actions offered are
# correct whichever it is.
GOOGLE_EMAIL_IN_USE_ERROR = (
    'An account already exists for this email. '
    'Sign in with the account you already use for this email address, '
    'or sign up with a different email address.'
)

GOOGLE_PROVIDER = 'google'

# High-entropy opaque credential (not a JWT - see RefreshSession). 384 bits
# from `secrets` (cryptographically secure) comfortably exceeds what's
# needed to make guessing infeasible.
_REFRESH_TOKEN_BYTES = 48


class AuthenticationError(Exception):
    """
    Raised for any login/refresh/logout failure.

    The message is always safe to show the caller verbatim: either the
    generic, pre-written text above, or - for the single, narrowly gated
    exception below - text that discloses nothing to a caller who has not
    already proven control of the mailbox in question.
    """


class GoogleEmailInUseError(AuthenticationError):
    """
    A refused account-linking collision, reported specifically.

    **This is a deliberate, load-bearing exception to the generic-message
    rule, and the gate that makes it safe is the point.**

    Telling a caller "an account already exists for this email" is only safe
    when the caller has *already* proven they control that exact mailbox -
    and here they have. Reaching this point means the request presented a
    Google-issued ID token that passed signature, issuer, audience and
    expiry verification, and that its own `email_verified` claim is true.
    Google asserts that claim only after confirming control of the mailbox,
    so the caller could have established this fact themselves by checking
    their own inbox.

    The disclosure is therefore not an enumeration oracle, and the asymmetry
    is worth stating plainly because it is what makes the whole difference:

    - An attacker who wants to know whether `victim@example.com` has an
      account **cannot** use this to find out. They cannot mint a Google
      credential for an address they do not control, so the only addresses
      they can ever get an answer about are their own - which they can
      already check.
    - Someone holding a stolen or phished credential for the address is, at
      that point, already inside the mailbox this message is about.

    Without that gate the change *would* be a serious regression, which is
    why `email_verified` is checked rather than assumed. An unverified
    credential proves nothing, and gets GENERIC_GOOGLE_LOGIN_ERROR like
    every other failure.

    What this class must never be used for: distinguishing *which kind* of
    collision occurred (a password account vs a different Google identity),
    revealing anything about the existing account beyond its existence, or
    being raised for any failure other than the email collision. See
    `_resolve_google_user`, which is the only place that raises it.
    """


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    access_token: str
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime


def _hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _issue_authenticated_session(
    user: User, *, replaces: RefreshSession | None = None
) -> AuthenticatedSession:
    """Issue a fresh access token + refresh session for `user`.

    If `replaces` is given (the refresh flow), that old session is revoked
    and linked to the new one as part of the same operation - the old
    credential stops working at the moment the new one starts, not before
    and not after.
    """
    access_token, access_expires_at = issue_access_token(user.pk)

    raw_refresh_token = secrets.token_urlsafe(_REFRESH_TOKEN_BYTES)
    session = RefreshSession.objects.create(
        user=user,
        token_hash=_hash_refresh_token(raw_refresh_token),
        expires_at=timezone.now() + settings.REFRESH_TOKEN_LIFETIME,
    )

    if replaces is not None:
        replaces.revoked_at = timezone.now()
        replaces.replaced_by = session
        replaces.save(update_fields=['revoked_at', 'replaced_by'])

    return AuthenticatedSession(
        user=user,
        access_token=access_token,
        access_token_expires_at=access_expires_at,
        refresh_token=raw_refresh_token,
        refresh_token_expires_at=session.expires_at,
    )


def login(email: str, password: str) -> AuthenticatedSession:
    """
    Authenticate by email/password and issue a new access token + refresh
    session.

    Uses Django's own `authenticate()` (the `ModelBackend` it resolves to)
    rather than calling `check_password` directly, for two reasons: (1) it
    already runs a dummy password hash when the email doesn't match any
    user, so an unknown-email attempt takes about as long as a
    wrong-password one - not doing this would let a timing difference leak
    which emails are registered; (2) it already rejects an inactive user
    (`is_active=False`) as part of the same call, via
    `user_can_authenticate`, so this function doesn't need a separate check
    that could accidentally diverge from it.

    Raises AuthenticationError with the single generic message for every
    failure - unknown email, wrong password, or an inactive account - so a
    caller can never distinguish one from another. This differs
    deliberately from registration (where confirming an email is already
    taken is expected and necessary): login is the endpoint an attacker
    would use to test a list of addresses, so account existence must never
    leak from it.
    """
    user = authenticate(email=email, password=password)
    if user is None:
        raise AuthenticationError(GENERIC_LOGIN_ERROR)

    return _issue_authenticated_session(user)


def _provision_google_user(identity: GoogleIdentity, normalized_email: str) -> User:
    """
    Create a new User for a first-time Google sign-in.

    `phone_number` is left as `''`: Google's identity claims never include
    one, and inventing a placeholder value would be recorded as real data
    elsewhere in the system. `User.phone_number` is `blank=True` at the
    model level for exactly this case (see its migration) - registration's
    own validation (`identity.services`) still requires a real phone
    number for that path; this is the one path that legitimately creates
    an account without one. Collecting it is deferred to the same later
    "complete your profile" step already planned for every new account
    (see `SignUpForm`'s docstring) - not implemented in S1-004.

    `is_verified` is set from Google's own `email_verified` claim: Google
    has already confirmed this person controls the mailbox (the same
    assurance this platform's own, not-yet-built email verification would
    eventually provide), so it would be inaccurate to leave it False.

    `set_unusable_password()` (Django's own mechanism for exactly this)
    means the account can never authenticate via `login()` with any
    password - only via a linked external identity - until a future
    "add a password" feature explicitly sets one.

    `first_name`/`last_name` are required (non-blank) fields, unlike
    `phone_number` - Google's default consent screen for this flow always
    includes profile claims, so `given_name`/`family_name` (or at least
    `name`) are present in practice. In the extreme edge case where even
    those are absent, the email's local part is used for whichever name is
    missing - real, account-derived data, not an invented value.
    """
    email_local_part = normalized_email.split('@')[0]
    user = User(
        email=normalized_email,
        first_name=identity.first_name or email_local_part,
        last_name=identity.last_name or email_local_part,
        phone_number='',
        is_verified=identity.email_verified,
    )
    user.set_unusable_password()
    user.full_clean(exclude=['password'])
    # No transaction/IntegrityError handling of its own: this always runs
    # inside `_resolve_google_user`'s own atomic block, which owns the
    # single retry-from-scratch on any collision here - see its docstring
    # for why a local "just use whichever user has this email now"
    # fallback would be unsafe.
    user.save()
    return user


def authenticate_with_google(raw_id_token: str) -> AuthenticatedSession:
    """
    Verify a Google ID token and authenticate (or provision) the associated
    User, then issue the same access token + refresh session login() does.

    Account resolution, in order:

    1. An `ExternalIdentity(provider='google', provider_subject=<sub>)`
       already exists -> authenticate its User. This is the common case
       for a returning Google user; the stable `sub` claim (never the
       email) is what's matched, so a Google account changing its email
       later doesn't lose or duplicate its link.
    2. No such ExternalIdentity, and no User exists with the verified
       email -> provision a new User (see `_provision_google_user`) and
       link it.
    3. No such ExternalIdentity, but a User already exists with that
       email (registered with a password, or created earlier by a
       *different* Google identity reporting the same email) -> refused,
       and reported specifically as `GOOGLE_EMAIL_IN_USE_ERROR` when - and
       only when - the credential's own `email_verified` claim is true.
       This platform never links a new identity to an existing account by
       email match alone, regardless of `email_verified` - see the
       security note below. Only an exact `(provider, provider_subject)`
       match (case 1) ever authenticates into an account that already
       exists; an authenticated "link this Google account to my current
       session" flow, initiated by an already-logged-in user, is the
       intended way to add a second sign-in method to an existing
       account, and is out of scope - see docs/architecture.md.

       What changed, and why only the message: the *refusal* is
       unconditional, but the generic message for it was a usability dead
       end - someone whose address is already registered was told
       "Could not sign in with Google." with nothing to act on, having
       correctly refused to be told why. Disclosing the collision to a
       caller who has just presented a credential Google verified as
       `email_verified` is safe, because reaching that point means they
       have already proven control of this exact mailbox and could
       therefore have established the fact themselves. It is not an
       enumeration oracle: nobody can present a Google credential for an
       address they do not control, so the only addresses anyone can get
       an answer about are their own. The gate is enforced in
       `_email_collision_error`, and every other failure - invalid,
       expired, replayed or unverified credential, unconfigured client id,
       inactive account - still answers GENERIC_GOOGLE_LOGIN_ERROR, so the
       response still cannot be used to find out whether an arbitrary
       address has an account. See `GoogleEmailInUseError`.

       Security note (revised after a dedicated review; this is a
       deliberate, load-bearing decision, not the original design):
       auto-linking by verified email was considered and rejected.
       `email_verified=true` is a real, cryptographically-backed assertion
       that *Google* confirmed mailbox control at some point in the past -
       but it says nothing about whether the person completing *this*
       Google sign-in is the same person who registered the platform
       account under that email, particularly once email addresses can be
       reassigned or reused outside this platform's control (e.g. a
       company reissuing a departed employee's address to someone new, who
       then gets a fresh Google Workspace identity with `email_verified=
       true` for it). Auto-linking would silently and permanently hand
       that new mailbox holder access to the *old* account - with no
       consent, no notification to the original owner, and no audit trail
       - and would do so as this platform's *first* email-provenance-based
       path into an existing account, since no password-reset-by-email
       flow exists yet to compare the risk against. (An earlier version of
       this docstring compared auto-linking to Firebase Authentication's
       default behavior; that comparison was wrong - Firebase's actual
       default for a colliding email is to reject the sign-in with
       `auth/account-exists-with-different-credential` and require the
       existing method first, which is this same refuse-first policy, not
       an argument for the opposite.)

    Every failure - an invalid or replayed token, Google sign-in not
    configured, an inactive user, or an email match in case 3 - raises an
    `AuthenticationError` and every one of them is safe to show verbatim. All
    but the last carry the single generic message, so an unauthenticated
    caller learns nothing about which case occurred. The one exception is a
    case-3 collision reported by an `email_verified` credential, which is
    told the address is taken; that caller has already proven control of the
    mailbox, so it discloses nothing about anyone else's account. See
    `GoogleEmailInUseError`.
    """
    try:
        identity = verify_google_id_token(raw_id_token)
    except GoogleTokenError:
        raise AuthenticationError(GENERIC_GOOGLE_LOGIN_ERROR) from None

    user = _resolve_google_user(identity, allow_retry=True)

    if not user.is_active:
        raise AuthenticationError(GENERIC_GOOGLE_LOGIN_ERROR)

    return _issue_authenticated_session(user)


def _resolve_google_user(identity: GoogleIdentity, *, allow_retry: bool) -> User:
    """
    Resolve the User for a verified Google `identity`, per the three cases
    in `authenticate_with_google`'s docstring: an existing link, a genuinely
    new account, or a refusal.

    Race safety (this is the one part of Policy B that's easy to get wrong
    silently): a naive "if provisioning collides, just look up whoever now
    has this email" fallback would - only in the timing window between the
    `exists()` check below and the save a few lines later - let a
    concurrent request that legitimately committed a *different*
    unrelated account (a plain password registration, or a different
    Google identity) in that gap get treated as a fallback hit, silently
    reintroducing exactly the auto-link Policy B exists to prevent, just
    behind a race window instead of the deterministic path. So a collision
    here never trusts "whoever has this email now" - it re-resolves from
    the top instead: if the collision really was two requests for the
    *same* Google subject, the ExternalIdentity lookup below will now find
    it (case 1) and correctly succeed; if the collision was with anything
    else, the `exists()` check below will now correctly see it too and
    correctly refuse. `allow_retry` bounds this to exactly one retry, so a
    persistently failing database fails closed rather than recursing
    forever.
    """
    external_identity = (
        ExternalIdentity.objects.select_related('user')
        .filter(provider=GOOGLE_PROVIDER, provider_subject=identity.subject)
        .first()
    )
    if external_identity is not None:
        return external_identity.user

    normalized_email = User.objects.normalize_email(identity.email)
    if User.objects.filter(email=normalized_email).exists():
        # Never auto-link - see the security note in
        # authenticate_with_google's docstring. The *refusal* is unconditional.
        #
        # The message is not, and that is the whole of the change: a caller
        # who has just presented a credential Google verified as
        # `email_verified` has already proven they control this mailbox, so
        # telling them the address is taken discloses nothing they could not
        # establish themselves, and turns a dead end into a next step. Anyone
        # else - an invalid, expired or replayed credential, an unverified
        # one, an unconfigured client, a deactivated account - still gets the
        # single generic answer, so the response never distinguishes "this
        # address has an account" from "this credential is no good" for
        # anyone who has not earned it.
        #
        # One message for the whole collision class, deliberately: the
        # existing account might be password-registered or belong to a
        # *different* Google identity reporting the same address, and a
        # caller must not be able to tell which.
        raise _email_collision_error(identity)

    try:
        with transaction.atomic():
            user = _provision_google_user(identity, normalized_email)
            ExternalIdentity.objects.create(
                user=user,
                provider=GOOGLE_PROVIDER,
                provider_subject=identity.subject,
                email=normalized_email,
            )
    except IntegrityError:
        if not allow_retry:
            # The retry bound has been reached. This is a genuine database
            # failure rather than a clean collision, so it stays generic even
            # for a verified caller: an attacker who can provoke a unique
            # violation would otherwise learn from a specific message that a
            # row for this address exists.
            raise AuthenticationError(GENERIC_GOOGLE_LOGIN_ERROR) from None
        return _resolve_google_user(identity, allow_retry=False)

    return user


def _email_collision_error(identity: GoogleIdentity) -> AuthenticationError:
    """
    The error for a refused account-linking collision.

    `GoogleEmailInUseError` only when Google's own credential asserts mailbox
    control for this address, and the generic error otherwise - see that
    class's docstring for why that gate is what keeps the disclosure from
    being an enumeration oracle. Returned rather than raised so the decision
    is made in exactly one place and cannot drift between call sites.
    """
    if identity.email_verified:
        return GoogleEmailInUseError(GOOGLE_EMAIL_IN_USE_ERROR)
    return AuthenticationError(GENERIC_GOOGLE_LOGIN_ERROR)


def refresh(raw_refresh_token: str) -> AuthenticatedSession:
    """
    Validate `raw_refresh_token`, rotate it, and issue a new access token.

    Rotation: the presented credential is looked up by its hash and
    checked for validity (not expired, not already revoked, owning user
    still active), then immediately revoked and linked to a newly issued
    replacement - so this exact credential can never be used again. If the
    same raw credential is presented a second time (a stolen or
    already-rotated credential being replayed), the lookup finds a row
    that's already revoked and rejects it, rather than finding nothing -
    that's what makes the reuse detectable at all. This task does not go
    further and automatically revoke the rest of that user's sessions on
    detected reuse; it's flagged here as a natural next step, not silently
    treated as already handled.
    """
    if not raw_refresh_token:
        raise AuthenticationError(GENERIC_REFRESH_ERROR)

    token_hash = _hash_refresh_token(raw_refresh_token)
    session = RefreshSession.objects.select_related('user').filter(token_hash=token_hash).first()

    if session is None or not session.is_active or not session.user.is_active:
        raise AuthenticationError(GENERIC_REFRESH_ERROR)

    session.last_used_at = timezone.now()
    session.save(update_fields=['last_used_at'])

    return _issue_authenticated_session(session.user, replaces=session)


def logout(raw_refresh_token: str) -> None:
    """
    Revoke the refresh session backing `raw_refresh_token`, if any.

    Never raises: an already-invalid, unknown, expired, or missing
    credential still results in "logged out" from the caller's point of
    view - there's nothing actionable to tell them, and distinguishing
    "already logged out" from "never was logged in" would leak session
    validity to whoever holds the (possibly stolen) credential.
    """
    if not raw_refresh_token:
        return

    token_hash = _hash_refresh_token(raw_refresh_token)
    RefreshSession.objects.filter(token_hash=token_hash, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def revoke_other_sessions(user: User, keep_raw_refresh_token: str = '') -> None:
    """
    Revoke every live refresh session of `user` except the one backing
    `keep_raw_refresh_token`, so changing a password signs out every other
    device but not the one it was changed from.
    """
    sessions = RefreshSession.objects.filter(user=user, revoked_at__isnull=True)
    if keep_raw_refresh_token:
        sessions = sessions.exclude(token_hash=_hash_refresh_token(keep_raw_refresh_token))
    sessions.update(revoked_at=timezone.now())


def get_authenticated_user(request: HttpRequest) -> User | None:
    """
    Resolve the authenticated User from `request`'s `Authorization: Bearer
    <token>` header.

    Returns None - never raises - for a missing header, a malformed
    header, an invalid/expired/tampered/wrong-type token, or a token whose
    user no longer exists or has since been deactivated. A GraphQL resolver
    treats all of those identically ("not authenticated"), so this
    collapses them rather than distinguishing why. The user id inside the
    token is never trusted on its own without the signature/expiry/type
    checks in `decode_access_token` - it is not, and must never become, a
    value the frontend can simply assert.
    """
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return None

    token = header.removeprefix('Bearer ').strip()
    try:
        claims = decode_access_token(token)
    except TokenError:
        return None

    return User.objects.filter(pk=claims.user_id, is_active=True).first()
