from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from identity.models import (
    EmailToken,
    EmailTokenPurpose,
    ExternalIdentity,
    RefreshSession,
    User,
)

VALID_PASSWORD = 'a-strong-unique-pass-1'


def _make_user(**overrides):
    fields = {
        'email': 'ada@example.com',
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    password = fields.pop('password', VALID_PASSWORD)
    return User.objects.create_user(password=password, **fields)


@pytest.mark.django_db
class TestUserModel:
    def test_user_can_be_created(self):
        user = _make_user()

        assert user.pk is not None
        assert user.email == 'ada@example.com'
        assert user.first_name == 'Ada'
        assert user.last_name == 'Lovelace'
        assert user.phone_number == '+255712345678'

    def test_email_is_unique(self):
        _make_user(email='ada@example.com')

        with pytest.raises(IntegrityError):
            User.objects.create_user(
                email='ada@example.com',
                first_name='Someone',
                last_name='Else',
                phone_number='+255700000000',
                password=VALID_PASSWORD,
            )

    @pytest.mark.parametrize(
        ('raw', 'expected'),
        [
            ('USER@EXAMPLE.COM', 'user@example.com'),
            ('User@Example.com', 'user@example.com'),
            ('  user@example.com  ', 'user@example.com'),
        ],
    )
    def test_email_is_normalized_on_create(self, raw, expected):
        user = _make_user(email=raw)

        assert user.email == expected

    def test_normalizing_case_prevents_duplicate_accounts(self):
        _make_user(email='user@example.com')

        with pytest.raises(IntegrityError):
            User.objects.create_user(
                email='USER@EXAMPLE.COM',
                first_name='Someone',
                last_name='Else',
                phone_number='+255700000000',
                password=VALID_PASSWORD,
            )

    def test_password_is_hashed_not_stored_in_plaintext(self, settings):
        # The suite-wide fast hasher (conftest.py) is swapped back for the
        # production default so this asserts on the real algorithm.
        settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.PBKDF2PasswordHasher']
        user = _make_user(password=VALID_PASSWORD)

        assert user.password != VALID_PASSWORD
        assert VALID_PASSWORD not in user.password
        assert user.password.startswith('pbkdf2_')

    def test_check_password_validates_the_correct_password(self):
        user = _make_user(password=VALID_PASSWORD)

        assert user.check_password(VALID_PASSWORD) is True

    def test_check_password_rejects_an_incorrect_password(self):
        user = _make_user(password=VALID_PASSWORD)

        assert user.check_password('something-else-entirely') is False

    def test_is_verified_defaults_to_false(self):
        user = _make_user()

        assert user.is_verified is False

    def test_is_active_defaults_to_true(self):
        user = _make_user()

        assert user.is_active is True

    def test_invalid_phone_number_is_rejected_on_full_clean(self):
        user = User(
            email='ada2@example.com',
            first_name='Ada',
            last_name='Lovelace',
            phone_number='not-a-phone-number',
        )
        user.set_password(VALID_PASSWORD)

        with pytest.raises(ValidationError):
            user.full_clean()

    def test_valid_international_phone_numbers_are_accepted(self):
        for number in ('+255712345678', '+15551234567', '+905551234567'):
            user = User(
                email=f'user-{number}@example.com',
                first_name='Ada',
                last_name='Lovelace',
                phone_number=number,
            )
            user.set_password(VALID_PASSWORD)
            user.full_clean()  # must not raise

    def test_str_returns_email(self):
        user = _make_user()

        assert str(user) == 'ada@example.com'

    def test_save_normalizes_email_even_outside_the_manager(self):
        user = User(
            email='Direct@Example.com',
            first_name='Ada',
            last_name='Lovelace',
            phone_number='+255712345678',
        )
        user.set_password(VALID_PASSWORD)
        user.save()

        assert user.email == 'direct@example.com'

    def test_get_full_name_joins_first_and_last_name(self):
        user = _make_user(first_name='Ada', last_name='Lovelace')

        assert user.get_full_name() == 'Ada Lovelace'

    def test_get_short_name_returns_first_name(self):
        user = _make_user(first_name='Ada')

        assert user.get_short_name() == 'Ada'

    def test_create_user_without_an_email_is_rejected(self):
        with pytest.raises(ValueError, match='email'):
            User.objects.create_user(
                email='',
                first_name='Ada',
                last_name='Lovelace',
                phone_number='+255712345678',
                password=VALID_PASSWORD,
            )


@pytest.mark.django_db
class TestCreateSuperuser:
    def test_creates_a_privileged_active_verified_user(self):
        user = User.objects.create_superuser(
            email='root@example.com',
            first_name='Root',
            last_name='User',
            phone_number='+255712345678',
            password=VALID_PASSWORD,
        )

        assert user.is_staff is True
        assert user.is_superuser is True
        assert user.is_active is True
        assert user.is_verified is True
        assert user.check_password(VALID_PASSWORD) is True

    def test_rejects_is_staff_false(self):
        with pytest.raises(ValueError, match='is_staff'):
            User.objects.create_superuser(
                email='root@example.com',
                first_name='Root',
                last_name='User',
                phone_number='+255712345678',
                password=VALID_PASSWORD,
                is_staff=False,
            )

    def test_rejects_is_superuser_false(self):
        with pytest.raises(ValueError, match='is_superuser'):
            User.objects.create_superuser(
                email='root@example.com',
                first_name='Root',
                last_name='User',
                phone_number='+255712345678',
                password=VALID_PASSWORD,
                is_superuser=False,
            )


@pytest.mark.django_db
class TestExternalIdentityModel:
    def test_can_be_linked_to_a_user(self):
        user = _make_user()

        identity = ExternalIdentity.objects.create(
            user=user, provider='google', provider_subject='abc123', email='ada@example.com'
        )

        assert identity.user_id == user.pk
        assert user.external_identities.count() == 1

    def test_provider_and_subject_are_unique_together(self):
        user = _make_user()
        ExternalIdentity.objects.create(
            user=user, provider='google', provider_subject='abc123', email='ada@example.com'
        )

        with pytest.raises(IntegrityError):
            ExternalIdentity.objects.create(
                user=user, provider='google', provider_subject='abc123', email='ada@example.com'
            )

    def test_str_returns_provider_and_subject(self):
        user = _make_user()
        identity = ExternalIdentity.objects.create(
            user=user, provider='google', provider_subject='abc123', email='ada@example.com'
        )

        assert str(identity) == 'google:abc123'


@pytest.mark.django_db
class TestRefreshSessionModel:
    def _make_session(self, **overrides):
        fields = {
            'user': overrides.pop('user', None) or _make_user(),
            'token_hash': 'a' * 64,
            'expires_at': timezone.now() + timedelta(days=30),
        }
        fields.update(overrides)
        return RefreshSession.objects.create(**fields)

    def test_str_includes_user_and_created_at(self):
        session = self._make_session()

        expected = f'RefreshSession(user={session.user_id}, created_at={session.created_at})'
        assert str(session) == expected

    def test_is_active_when_not_expired_and_not_revoked(self):
        session = self._make_session()

        assert session.is_active is True

    def test_is_not_active_once_revoked(self):
        session = self._make_session(revoked_at=timezone.now())

        assert session.is_active is False

    def test_is_not_active_once_expired(self):
        session = self._make_session(expires_at=timezone.now() - timedelta(seconds=1))

        assert session.is_active is False

    def test_token_hash_is_unique(self):
        user = _make_user()
        self._make_session(user=user, token_hash='b' * 64)

        with pytest.raises(IntegrityError):
            self._make_session(user=user, token_hash='b' * 64)


@pytest.mark.django_db
class TestEmailTokenModel:
    """
    The emailed credential itself.

    A second single-use, expiring credential alongside `RefreshSession`, and
    for the same reasons: only a hash is stored, expiry and use are recorded
    rather than implied by deletion, and the row stays after it is spent so
    that "already used" remains distinguishable from "never existed" in the
    record - even though the API deliberately refuses to tell the two apart.
    """

    def _make_token(self, **overrides):
        fields = {
            'user': overrides.pop('user', None) or _make_user(),
            'purpose': EmailTokenPurpose.PASSWORD_RESET,
            'token_hash': 'c' * 64,
            'expires_at': timezone.now() + timedelta(hours=1),
        }
        fields.update(overrides)
        return EmailToken.objects.create(**fields)

    def test_str_includes_user_and_purpose(self):
        token = self._make_token()

        assert str(token) == f'EmailToken(user={token.user_id}, purpose={token.purpose})'

    def test_is_active_when_unused_and_unexpired(self):
        assert self._make_token().is_active is True

    def test_is_not_active_once_used(self):
        assert self._make_token(used_at=timezone.now()).is_active is False

    def test_is_not_active_once_expired(self):
        assert self._make_token(expires_at=timezone.now() - timedelta(seconds=1)).is_active is False

    def test_token_hash_is_unique(self):
        self._make_token(token_hash='d' * 64)

        with pytest.raises(IntegrityError):
            self._make_token(token_hash='d' * 64)

    def test_purpose_records_which_operation_the_token_authorizes(self):
        """
        Kept in the row rather than implied by which table it lives in, so a
        reset token presented to the activation mutation is refused as being
        for the wrong operation instead of looking like an unknown token.
        """
        assert EmailTokenPurpose.PASSWORD_RESET == 'password_reset'
        assert EmailTokenPurpose.ACTIVATION == 'activation'
        assert self._make_token().purpose == EmailTokenPurpose.PASSWORD_RESET

    def test_the_two_purposes_are_independent_for_one_user(self):
        """
        Both tokens for the same user coexist: asking for a reset must not
        burn an activation link, or a user who forgot their password could
        not confirm their address afterwards.
        """
        user = _make_user()

        reset = self._make_token(user=user, purpose=EmailTokenPurpose.PASSWORD_RESET)
        activation = self._make_token(
            user=user, purpose=EmailTokenPurpose.ACTIVATION, token_hash='e' * 64
        )

        assert reset.is_active is True
        assert activation.is_active is True

    def test_deleting_a_user_deletes_their_tokens(self):
        """
        An emailed credential must not outlive the account it grants access
        to, so this cascades like every other child row.
        """
        user = _make_user()
        self._make_token(user=user)

        user.delete()

        assert EmailToken.objects.count() == 0

    def test_it_has_no_field_that_could_hold_the_raw_token(self):
        """
        The storage boundary, asserted structurally: the model has nowhere to
        put the plaintext even by accident. A future field that did would fail
        here rather than in a post-incident review.
        """
        field_names = {field.name for field in EmailToken._meta.get_fields()}

        assert 'token' not in field_names
        assert 'raw_token' not in field_names
        assert 'token_hash' in field_names

    def test_created_at_is_recorded_and_rows_are_newest_first(self):
        """
        `ordering` is what makes "the most recently emailed link" a cheap
        query for the supersede path, and it is asserted so a change to it
        cannot quietly alter which token a caller is offered.
        """
        user = _make_user()
        first = self._make_token(user=user, token_hash='f' * 64)
        second = self._make_token(user=user, token_hash='1' * 64)

        assert first.created_at is not None
        assert second.created_at is not None
        assert list(EmailToken.objects.all()) == [second, first]
