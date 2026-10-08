"""
Identity domain services.

GraphQL (or any other adapter - a future REST endpoint, a management
command) calls these functions rather than talking to the User model
directly, so registration, password reset and account activation stay owned
by the identity app instead of leaking into the API layer.
"""

import hashlib
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from identity.email import send_activation_email, send_password_reset_email
from identity.models import (
    EmailToken,
    EmailTokenPurpose,
    RefreshSession,
    User,
    validate_phone_number,
)

# High-entropy opaque token, 384 bits from `secrets` (cryptographically
# secure), matching the refresh credential's strength - see EmailToken's
# docstring for why this is hashed with a fast SHA-256 rather than a slow
# password hasher. It is emailed, so unlike a password it is never chosen by a
# human and there is nothing for a strength rule to protect against.
_EMAIL_TOKEN_BYTES = 48

# The one answer `request_password_reset` ever gives. Whether the address has
# an account, does not, has been deactivated, or signed up through Google and
# has no password to reset, the caller hears exactly this - so the endpoint
# cannot be used to find out whether an address is registered. The same string
# is what the frontend shows on success, which is why the two are kept
# identical: the UI must not accidentally narrow what the API disclosed.
# (`noqa: S105` on the message names below: ruff's hardcoded-password
# heuristic just pattern-matches the word "password" in a name. They are
# user-facing copy - see identity/schema.py's matching note.)
PASSWORD_RESET_REQUESTED_MESSAGE = (
    "If an account exists for that email, we've sent instructions to reset your password."  # noqa: S105
)

# The equivalent one answer for "please send me an activation link". Neutral
# about account existence for the same reason as the message above, and
# deliberately a different string: the two flows are reached from different
# pages, and a user who requested a reset should not be told we emailed an
# activation link (nor vice versa).
ACCOUNT_ACTIVATION_REQUESTED_MESSAGE = (
    "If that email needs confirming, we've sent it a confirmation link."
)

# The one answer every failure to redeem an emailed link gives, whether the
# token was never real, has expired, has already been used, or belongs to the
# other kind of link. It names the remedy (ask for a new one) and nothing
# about the token's history, which would otherwise tell an attacker holding a
# stolen link exactly how far it got.
PASSWORD_RESET_LINK_INVALID_MESSAGE = 'This password reset link is invalid or has expired.'  # noqa: S105
ACTIVATION_LINK_INVALID_MESSAGE = 'This confirmation link is invalid or has expired.'


class RegistrationError(Exception):
    """
    Raised for any registration input/business-rule failure.

    `field` names the offending input field (e.g. `'email'`) when the
    error applies to one, so a caller like the GraphQL layer can report it
    the same way the frontend already reports its own client-side
    validation errors - field-by-field, not just a single blob of text.
    It's `None` for whole-form errors.
    """

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class EmailLinkError(Exception):
    """
    Raised for any failure of an emailed-link flow: password reset and
    account activation.

    Carries the same `message`/`field` pair as `RegistrationError` and is
    handled identically by the GraphQL layer, so the frontend can show a
    field-level failure (`'password'`, `'token'`, `'email'`) next to the
    input that caused it, exactly as it does for its own client-side
    validation errors.

    Every message this exception is raised with is safe to show verbatim.
    That is a property of the raising sites, not of the class: a token that
    cannot be redeemed is always `*_LINK_INVALID_MESSAGE`, never a
    report of what was actually wrong with it.
    """

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


@dataclass(frozen=True)
class RegistrationInput:
    first_name: str
    last_name: str
    email: str
    phone_number: str
    password: str


def _require_non_empty(value: str, field: str, message: str) -> str:
    value = (value or '').strip()
    if not value:
        raise RegistrationError(message, field=field)
    return value


def _validate_and_normalize_email(email: str) -> str:
    """
    Validate `email` and return it normalized.

    Security note: unlike a "forgot password" flow (which must return the
    same generic response whether or not an account exists, to avoid
    confirming account existence to an attacker), registration is initiated
    by someone actively trying to claim that exact email. Telling them it's
    already taken is required for them to know to sign in instead, and is no
    more than they could already infer from a slower, generic failure - so
    this reports it plainly rather than inventing an ambiguous response.
    """
    if not (email or '').strip():
        raise RegistrationError('Email is required.', field='email')

    normalized_email = User.objects.normalize_email(email)
    try:
        validate_email(normalized_email)
    except ValidationError:
        raise RegistrationError('Enter a valid email address.', field='email') from None

    if User.objects.filter(email=normalized_email).exists():
        raise RegistrationError('An account with this email already exists.', field='email')

    return normalized_email


def _validate_email_syntax(email: str, error_class: type[Exception]) -> str:
    """
    Validate `email`'s *shape* and return it normalized, without touching the
    database.

    The deliberately different sibling of `_validate_and_normalize_email`,
    for the flows that must not report whether an account exists. Rejecting
    "that is not an email address" leaks nothing about the platform either
    way - the answer would be identical for a registered address and an
    unregistered one - so these flows can still be honest about a typo
    instead of answering a useless generic message to input that was never
    going to be looked up.
    """
    if not (email or '').strip():
        raise error_class('Email is required.', field='email')

    normalized_email = User.objects.normalize_email(email)
    try:
        validate_email(normalized_email)
    except ValidationError:
        raise error_class('Enter a valid email address.', field='email') from None

    return normalized_email


def _validate_phone_number(phone_number: str) -> str:
    phone_number = (phone_number or '').strip()
    if not phone_number:
        raise RegistrationError('Phone number is required.', field='phone_number')

    try:
        validate_phone_number(phone_number)
    except ValidationError as exc:
        raise RegistrationError(exc.messages[0], field='phone_number') from None

    return phone_number


def _validate_password(password: str, user: User, error_class: type[Exception]) -> None:
    """
    Run Django's password validators against `password` for `user`.

    `error_class` is the exception the caller wants failures reported as
    (`RegistrationError` or `EmailLinkError`) - both take the same
    `(message, field)` arguments, and both are safe to show verbatim, so the
    one rule set serves registration and password reset alike. Sharing this
    is the point: a password that registration would have refused must not
    become acceptable because it arrived through a different door.
    """
    if not password:
        raise error_class('Password is required.', field='password')

    try:
        password_validation.validate_password(password, user=user)
    except ValidationError as exc:
        raise error_class(' '.join(exc.messages), field='password') from None


def register_user(data: RegistrationInput) -> User:
    """
    Validate `data` and create a new platform account.

    On success the account is left unverified and an activation link is
    emailed to it (see `send_account_activation_email`). The link is a
    convenience, not a gate: `login` does not require `is_verified`, so an
    unverified account can sign in exactly as before - see
    `activate_account`'s docstring for what activation does and does not buy.

    Raises RegistrationError for any validation or business-rule failure
    (missing name, invalid email, an email already registered, an invalid
    phone number, or a password that fails Django's password validators).
    Never lets a raw Django/database exception escape - callers only need
    to handle RegistrationError.
    """
    first_name = _require_non_empty(data.first_name, 'first_name', 'First name is required.')
    last_name = _require_non_empty(data.last_name, 'last_name', 'Last name is required.')
    normalized_email = _validate_and_normalize_email(data.email)
    phone_number = _validate_phone_number(data.phone_number)

    user = User(
        email=normalized_email,
        first_name=first_name,
        last_name=last_name,
        phone_number=phone_number,
    )

    # Validated against the (unsaved) user instance so
    # UserAttributeSimilarityValidator can compare it against the name/email
    # above, then hashed - never persisted or logged in plain text.
    _validate_password(data.password, user, RegistrationError)
    user.set_password(data.password)

    try:
        user.full_clean()
    except ValidationError as exc:
        message = '; '.join(m for messages in exc.message_dict.values() for m in messages)
        raise RegistrationError(message or 'Invalid registration details.') from None

    try:
        with transaction.atomic():
            user.save()
    except IntegrityError:
        # Race: two concurrent registrations for the same email landed
        # between the `.exists()` check above and this save. The database's
        # unique constraint on `email` is the real guarantee; the earlier
        # check is only a fast path that produces a friendlier error in the
        # overwhelmingly common non-race case.
        raise RegistrationError(
            'An account with this email already exists.', field='email'
        ) from None

    # Outside the transaction above, deliberately: the mail must not go out for
    # an account whose registration later rolls back. A delivery failure here
    # is logged rather than raised (see identity/email.py) - the account
    # exists, so failing the registration would be a lie, and the user can
    # always ask for a new link.
    send_account_activation_email(user)

    return user


# --- emailed-link flows: password reset and account activation --------------------
#
# Everything below shares one shape: mint a single-use token, put it in an
# email, and redeem it later. The three properties that matter are the same
# for both flows, and are stated once here rather than repeated per function:
#
#   1. The raw token exists in exactly one place - the email - and is stored
#      only as a SHA-256 hash, so it cannot be recovered from the database.
#   2. It is single-use, expiring, and superseded by the next request, so a
#      leaked link is a short-lived, already-spent thing rather than a
#      standing credential.
#   3. The *request* side never reveals whether the address has an account
#      (PASSWORD_RESET_REQUESTED_MESSAGE), and the *redeem* side never
#      reveals why a token failed (*_LINK_INVALID_MESSAGE). Between them,
#      these two endpoints answer almost nothing an attacker could ask.


def _hash_email_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _issue_email_token(user: User, purpose: str, lifetime) -> str:
    """
    Mint a single-use token for `user`, supersede their outstanding ones, and
    return the raw value.

    The raw token is returned rather than looked up afterwards precisely
    because it cannot be: nothing stored here can reproduce it. A caller that
    loses it (a failed send, say) must mint a new one, which is why there is
    no "resend the same token" path.
    """
    raw_token = secrets.token_urlsafe(_EMAIL_TOKEN_BYTES)
    now = timezone.now()

    with transaction.atomic():
        # Supersede. A token the user has already spent is left alone; this
        # only burns the ones that are still live, so it cannot be used to
        # erase the evidence that a link was already redeemed.
        EmailToken.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(
            used_at=now
        )
        EmailToken.objects.create(
            user=user,
            purpose=purpose,
            token_hash=_hash_email_token(raw_token),
            expires_at=now + lifetime,
        )

    return raw_token


def _redeem_email_token(raw_token: str, purpose: str) -> EmailToken:
    """
    Look up a live `EmailToken` of `purpose` and return it, or raise.

    Raises EmailLinkError with the message belonging to `purpose`, chosen
    here rather than by the caller so a password-reset token can never be
    redeemed as an activation (or reported with the other flow's wording).
    `field='token'` is deliberate: the failure is about the *link*, not about
    anything the user typed, and the frontend uses it to decide that showing
    the password form again is pointless.
    """
    message = (
        PASSWORD_RESET_LINK_INVALID_MESSAGE
        if purpose == EmailTokenPurpose.PASSWORD_RESET
        else ACTIVATION_LINK_INVALID_MESSAGE
    )

    if not (raw_token or '').strip():
        raise EmailLinkError(message, field='token')

    token = (
        EmailToken.objects.select_related('user')
        .filter(token_hash=_hash_email_token(raw_token), purpose=purpose)
        .first()
    )

    if token is None or not token.is_active:
        # One answer for "no such token", "expired" and "already used". A
        # message per case would let whoever holds a stolen link - or guesses
        # at one - learn which of those they are up against, which is free
        # reconnaissance against a live credential.
        raise EmailLinkError(message, field='token')

    return token


def _mark_email_token_used(token: EmailToken) -> None:
    """Burn `token` so it can never be redeemed again."""
    EmailToken.objects.filter(pk=token.pk, used_at__isnull=True).update(used_at=timezone.now())


def send_account_activation_email(user: User) -> None:
    """
    Mint an activation token for `user` and email them the link.

    Called by `register_user` on every new account, and by
    `request_account_activation` when the original mail was lost. Never
    raises: a delivery failure is logged (see `identity/email.py`) and
    superseded by the caller's own outcome.
    """
    raw_token = _issue_email_token(
        user, EmailTokenPurpose.ACTIVATION, settings.ACTIVATION_TOKEN_LIFETIME
    )
    send_activation_email(user, raw_token)


def request_password_reset(email: str) -> str:
    """
    Start a password reset for `email`, returning the message to show the
    caller.

    Raises EmailLinkError only for input that is not a usable email address at
    all - a shape problem, reported identically whether or not the address is
    registered, so saying so discloses nothing.

    Everything else returns `PASSWORD_RESET_REQUESTED_MESSAGE`: an address
    with no account, a deactivated account, and an account that signed up
    through Google and so has no password to reset are all silent successes.
    That is not a courtesy - it is the whole design. This endpoint takes an
    attacker-supplied address and is exactly the shape of an enumeration
    oracle ("does this person have an account here?"), and the one thing that
    prevents it from being one is that the answer does not depend on the
    answer.

    Residual risk, stated rather than hidden: sending the mail is synchronous,
    so a request for a real account takes measurably longer (an SMTP round
    trip) than one for a nonexistent address. That timing gap is a weaker
    oracle than the response body - noise, retries and any proxy flatten it -
    but it is not zero. What bounds it in practice is the throttle
    (`identity.throttling`): an attacker gets five probes per address per hour
    and twenty per address per hour per client, and a timing signal needs
    many samples of the *same* address to beat noise, so the same limit that
    stops this endpoint being used as a mail cannon also caps the number of
    samples an enumeration attack can collect. Closing the gap properly means
    moving the send off the request path onto a task queue, which this
    project does not have yet; that is the fix to make when it does.
    """
    normalized_email = _validate_email_syntax(email, EmailLinkError)

    user = User.objects.filter(email=normalized_email).first()
    if user is None or not user.is_active or not user.has_usable_password():
        # No user, a deactivated one, or a Google-only account whose password
        # is unusable. All three answer the same way, and none is reported,
        # logged, or counted anywhere that could tell them apart. Note that
        # the Google case is a real limitation rather than an oversight: such
        # an account cannot be signed into with a password, so "reset your
        # password" is not a question it can answer. Adding a password by
        # emailed link is a deliberate future feature (it would have to prove
        # mailbox control first, and there is no session to attach it to).
        return PASSWORD_RESET_REQUESTED_MESSAGE

    raw_token = _issue_email_token(
        user, EmailTokenPurpose.PASSWORD_RESET, settings.PASSWORD_RESET_TOKEN_LIFETIME
    )
    send_password_reset_email(user, raw_token)

    return PASSWORD_RESET_REQUESTED_MESSAGE


def reset_password(raw_token: str, new_password: str) -> User:
    """
    Redeem a password-reset token and set `new_password` on its account.

    Raises EmailLinkError for an unusable token (`field='token'`) or for a
    password the validators reject (`field='password'`) - the same rule set
    registration applies, so a password that could not have been registered
    cannot be set here either.

    On success every refresh session for the account is revoked. This is the
    step that makes a reset a reset: without it, a password changed *because
    it may have been stolen* leaves every existing session - including the
    attacker's, on their own device - signed in and working until it expires
    on its own schedule. Whoever prompted the reset is signed out too and has
    to sign in again with the new password, which is a mild inconvenience
    that is the correct trade for a security control.
    """
    token = _redeem_email_token(raw_token, EmailTokenPurpose.PASSWORD_RESET)
    user = token.user

    _validate_password(new_password, user, EmailLinkError)

    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=['password'])

        # Every live session, not just one: the point is to evict a session the
        # person who prompted the reset knows nothing about, and there is no
        # way to tell that one apart from the others.
        RefreshSession.objects.filter(user=user, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )

        _mark_email_token_used(token)

    return user


class AccountError(Exception):
    """A refused change to the signed-in user's own account; safe to show verbatim."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def update_profile(user: User, first_name: str, last_name: str, phone_number: str) -> User:
    """Set the user's name and phone number, validated like registration. Email is not editable."""
    try:
        first_name = _require_non_empty(first_name, 'first_name', 'First name is required.')
        last_name = _require_non_empty(last_name, 'last_name', 'Last name is required.')
        phone_number = _validate_phone_number(phone_number)
    except RegistrationError as exc:
        raise AccountError(exc.message, field=exc.field) from None

    user.first_name = first_name
    user.last_name = last_name
    user.phone_number = phone_number
    user.save(update_fields=['first_name', 'last_name', 'phone_number', 'updated_at'])
    return user


def change_password(
    user: User, current_password: str, new_password: str, keep_refresh_token: str = ''
) -> User:
    """
    Replace the password of a signed-in user who proves they know the current one.

    Every other session is revoked - the same reasoning as `reset_password` - but
    the one making the change (`keep_refresh_token`) stays signed in.
    """
    from identity.authentication import revoke_other_sessions

    if not user.check_password(current_password or ''):
        raise AccountError('Your current password is incorrect.', field='current_password')
    if current_password == new_password:
        raise AccountError(
            'Choose a new password that is different from the current one.', field='new_password'
        )
    try:
        _validate_password(new_password, user, AccountError)
    except AccountError as exc:
        raise AccountError(exc.message, field='new_password') from None

    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=['password', 'updated_at'])
        revoke_other_sessions(user, keep_refresh_token)
    return user


def request_account_activation(email: str) -> str:
    """
    Email a fresh activation link to `email`, returning the message to show
    the caller.

    The same account-existence-neutral contract as `request_password_reset`,
    for the same reason: a caller-supplied address must not be usable to test
    whether it is registered. The message is deliberately *not* the same
    string - it says what was sent, and the two flows are distinguishable by
    the page the caller is on.

    Silently does nothing for a missing, deactivated or already-verified
    account. The last one is a real decision: re-verifying a verified account
    would be a no-op at best, and a token that quietly extends the life of an
    already-valid account is a credential nobody asked for.
    """
    normalized_email = _validate_email_syntax(email, EmailLinkError)

    user = User.objects.filter(email=normalized_email).first()
    if user is None or not user.is_active or user.is_verified:
        return ACCOUNT_ACTIVATION_REQUESTED_MESSAGE

    send_account_activation_email(user)

    return ACCOUNT_ACTIVATION_REQUESTED_MESSAGE


def activate_account(raw_token: str) -> User:
    """
    Redeem an activation token and mark its account's email as verified.

    Raises EmailLinkError for any unusable token (`field='token'`), with the
    same single message whatever went wrong - see `_redeem_email_token`.

    Idempotent on an already-verified account, in the narrow sense that
    reaching a verified account at all means the caller holds a valid
    activation token, so there is nothing to refuse: the flag is set (already
    set), the token is burned either way, and the operation reports success.
    That is a deliberate choice for one specific situation - a mail client
    that prefetches a link, or a user who clicks twice - and not a licence to
    treat a bad token as good, which is why the token is still required to
    be valid in the first place.

    What activation is and is not: it is a statement that someone proved
    control of this mailbox, which is worth recording and which other features
    will want to gate on. It is not a login requirement - `login` does not
    check `is_verified`, deliberately, so an unverified account is not locked
    out of an account it legitimately owns. Turning verification into an
    authorization gate is a separate decision, and one that should be made
    deliberately, in one place, once there is a reason to.
    """
    token = _redeem_email_token(raw_token, EmailTokenPurpose.ACTIVATION)
    user = token.user

    with transaction.atomic():
        if not user.is_verified:
            user.is_verified = True
            user.save(update_fields=['is_verified'])
        # Burned even when the account was already verified: the token has been
        # redeemed, and a link should stop working once it has been followed.
        _mark_email_token_used(token)

    return user
