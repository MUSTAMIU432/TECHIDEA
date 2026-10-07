"""
Identity domain models.

`User` is the platform account (email/password today; external providers
and future authentication mechanisms build on top of it, not into it).
`ExternalIdentity` is the minimal foundation for linking a User to an
external provider identity (Google now, others later) without putting any
provider-specific field directly on User. `RefreshSession` is the
server-side record backing a refresh credential (see its own docstring for
why a stateless refresh JWT was deliberately not used instead), and
`EmailToken` is the same idea for the one-time credentials that arrive by
email (password reset, account activation).
"""

import re
from typing import ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# E.164-shaped: a leading '+', a non-zero first digit, then more digits,
# 7-15 digits total (E.164's own maximum). Deliberately not tied to any
# single country's numbering plan - the platform serves many - and
# deliberately not using a phone-number library, since none is already a
# project dependency and this is the level of authority the frontend's own
# (equally permissive) validation implies.
PHONE_NUMBER_PATTERN = re.compile(r'^\+[1-9]\d{6,14}$')


def validate_phone_number(value: str) -> None:
    """Authoritative, intentionally permissive phone number format check."""
    if not PHONE_NUMBER_PATTERN.match(value or ''):
        raise ValidationError(
            'Enter a valid phone number in international format, e.g. +255712345678.'
        )


class UserManager(BaseUserManager):
    """Creates Users with a hashed password and a normalized email."""

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        """Lowercase the whole address, not just the domain.

        Django's built-in `BaseUserManager.normalize_email` only lowercases
        the domain part, leaving the local part's case untouched (RFC 5321
        technically permits a case-sensitive local part). In practice no
        mainstream mail provider treats the local part as case-sensitive, so
        leaving it alone would let `USER@example.com` and `user@example.com`
        register as two separate platform accounts - the exact bug this
        model is responsible for preventing. This intentionally does not
        apply provider-specific rules (e.g. Gmail's dot-insensitivity or
        +tag stripping): only case-folding, which holds for every provider.
        """
        return (email or '').strip().lower()

    def get_by_natural_key(self, email):
        # Overridden (the default is `self.get(**{USERNAME_FIELD: username})`,
        # an exact match) so logging in with a different case than you
        # registered with - e.g. `USER@example.com` after registering as
        # `user@example.com` - still finds the account. Without this,
        # `django.contrib.auth.authenticate()` would look up the raw,
        # un-normalized input and silently fail to find a real user.
        return self.get(email=self.normalize_email(email))

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('Users must have an email address.')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('is_verified', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')

        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Platform account. Email is the login identifier (see UserManager)."""

    email = models.EmailField('email address', unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    # String, not an integer field: phone numbers are not numbers you'd do
    # arithmetic on, and a leading '+' / leading zeros must survive storage.
    # `blank=True` at the model level: this field is still required by
    # registration (identity/services.py checks it explicitly, before this
    # is ever reached), but a Google-provisioned account (S1-004) has no
    # phone number available from Google's identity claims and must not
    # invent one - see identity/authentication.py's `_provision_google_user`.
    # `blank=True` only tells Django's own full_clean()/forms that an empty
    # value is acceptable at the schema level; it does not weaken the
    # `validate_phone_number` validator for any value that IS supplied.
    phone_number = models.CharField(max_length=20, blank=True, validators=[validate_phone_number])

    avatar_key = models.CharField(
        max_length=255,
        blank=True,
        help_text='Storage key of the profile photo (see identity.avatars); empty for none.',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Unset to deactivate an account without deleting it.',
    )
    is_verified = models.BooleanField(
        default=False,
        help_text='Email ownership has been confirmed. Not set automatically on registration.',
    )
    is_staff = models.BooleanField(
        default=False,
        help_text='Can access the Django admin site.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS: ClassVar[list[str]] = ['first_name', 'last_name', 'phone_number']

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at']

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        # Defense in depth: every path that constructs a User (the manager,
        # the admin, a shell, a fixture) ends up here, so the unique
        # constraint on `email` can only ever be bypassed by case if this is
        # skipped entirely - `create_user` already normalizes too, making
        # that the common case, not the only one.
        self.email = UserManager.normalize_email(self.email)
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return f'{self.first_name} {self.last_name}'.strip()

    def get_short_name(self) -> str:
        return self.first_name


class ExternalIdentity(models.Model):
    """
    Links a User to an identity from an external provider (Google now,
    others later). Deliberately minimal and provider-agnostic: no
    provider-specific field (e.g. `google_id`) lives on User itself, so
    adding another provider never touches the User model.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='external_identities')
    provider = models.CharField(max_length=32, help_text="e.g. 'google'.")
    provider_subject = models.CharField(
        max_length=255,
        help_text="The provider's stable unique identifier for this identity "
        "(e.g. Google's `sub` claim). Opaque to us; never assume a format.",
    )
    email = models.EmailField(
        help_text='The email the provider reported at link time. Informational - '
        'the platform identity is `user`, not this address.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['provider', 'provider_subject'],
                name='unique_external_identity_per_provider',
            ),
        ]
        ordering: ClassVar[list[str]] = ['-created_at']
        verbose_name_plural = 'external identities'

    def __str__(self) -> str:
        return f'{self.provider}:{self.provider_subject}'


class RefreshSession(models.Model):
    """
    Server-side record backing one issued refresh credential.

    Deliberately not a second, longer-lived JWT: a stateless refresh JWT
    can't be individually revoked (logout, a stolen device, detected replay
    all need to invalidate one specific credential without waiting out its
    lifetime), so the refresh credential is an opaque random string and
    this row is the only place its validity is decided. See
    `identity.authentication` for issuance, rotation and revocation.

    Only a hash of the credential is stored (`token_hash`), the same
    principle as password hashing: if the database leaked, the raw
    credential couldn't be reconstructed or replayed from it. Unlike a
    password, the credential is already high-entropy random data, not a
    low-entropy human-chosen secret, so it's hashed with a fast
    cryptographic hash (SHA-256, in `identity.authentication`) rather than
    a slow, adaptive password hasher - a raw brute-force attack isn't the
    threat model here the way it is for passwords.

    Rotation: using a credential successfully revokes this row
    (`revoked_at`) and links to its replacement (`replaced_by`), rather
    than deleting it. Keeping the revoked row (instead of deleting it) is
    what makes reuse of an already-rotated credential detectable as a
    *revoked* lookup rather than simply "not found" - the row is still
    there to say "this was valid once, and was already used."
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='refresh_sessions')
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text='SHA-256 hex digest of the raw refresh credential. Never the credential itself.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    # Points from the OLD session to the NEW one that replaced it via
    # rotation. Null for a session that hasn't been rotated (yet).
    replaced_by = models.OneToOneField(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='replaces',
    )

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at']
        indexes: ClassVar[list[models.Index]] = [
            # The hot lookup path: "is this presented credential valid?"
            # (token_hash is already indexed via its own unique=True, so
            # this covers the other two: listing/cleaning up a user's
            # sessions, and expiring old ones.)
            models.Index(fields=['user', 'revoked_at']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self) -> str:
        return f'RefreshSession(user={self.user_id}, created_at={self.created_at})'

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()


class EmailTokenPurpose(models.TextChoices):
    """
    What an `EmailToken` authorizes, kept in the row rather than implied by
    which table it lives in.

    Both purposes share one table, one hashing scheme and one lookup path, and
    the only real difference between them is which operation consumes the
    token. Encoding the purpose in the row means the two can never be crossed:
    a password-reset token presented to the activation mutation is not "an
    unknown token" that happens to look similar, it is a token for the wrong
    operation and is refused as such. One table per purpose would buy nothing
    and double the indexes.
    """

    # (ruff's hardcoded-password heuristic just pattern-matches the word
    # "password" in a name; this is an enum of purposes, not a credential.)
    PASSWORD_RESET = 'password_reset'  # noqa: S105
    ACTIVATION = 'activation'


class EmailToken(models.Model):
    """
    One single-use, expiring, emailed credential (password reset, account
    activation).

    Same shape of decision as `RefreshSession` above - the raw credential is
    never stored, only a SHA-256 hash of it, so a database leak yields nothing
    replayable - and for the same reason: it is high-entropy random data
    (`secrets.token_urlsafe`, in `identity.services`), not a low-entropy
    human-chosen secret like a password, so a fast hash is the right tool and a
    slow adaptive one would only delay an honest user.

    `used_at` rather than deletion, and for the same reason `RefreshSession`
    keeps its revoked rows: a token that has already been spent is
    meaningfully different from one that never existed, and only the former
    can be reported as "this link has already been used" rather than a
    misleading "invalid link".

    Issuance supersedes: asking for a new reset link invalidates any earlier
    unused one for the same purpose, so only the most recently emailed link
    ever works. That is the behaviour people expect from a "resend" button
    anyway, and it means a leaked older email is not a live credential for as
    long as its window lasts. A superseded token is recorded in `used_at`
    too, which is slightly imprecise about the reason - nothing redeemed it -
    but is the distinction not worth a third column: from every caller's side
    an invalid superseded link and a spent one are the same unusable link, and
    no code branches on why. Tokens that were *already* spent are left alone
    when a new one is issued, so superseding cannot erase the record that a
    link was once redeemed.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_tokens')
    purpose = models.CharField(max_length=32, choices=EmailTokenPurpose.choices)
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text='SHA-256 hex digest of the raw emailed token. Never the token itself.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at']
        indexes: ClassVar[list[models.Index]] = [
            # The lookup a spent/expired cleanup runs: "which unused tokens
            # are past their expiry?" (token_hash itself is already indexed
            # by its own unique=True).
            models.Index(fields=['expires_at']),
            # Issuing a new token supersedes the user's outstanding ones for
            # the same purpose, and the activation page has to find every live
            # activation token for an account.
            models.Index(fields=['user', 'purpose']),
        ]

    def __str__(self) -> str:
        return f'EmailToken(user={self.user_id}, purpose={self.purpose})'

    @property
    def is_active(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()
