from unittest.mock import patch

import pytest
from django.db import IntegrityError

from identity.models import User
from identity.services import RegistrationError, RegistrationInput, register_user

VALID_FIELDS = {
    'first_name': 'Ada',
    'last_name': 'Lovelace',
    'email': 'ada@example.com',
    'phone_number': '+255712345678',
    'password': 'a-strong-unique-pass-1',
}


def _input(**overrides):
    fields = {**VALID_FIELDS, **overrides}
    return RegistrationInput(**fields)


@pytest.mark.django_db
class TestRegisterUser:
    def test_creates_a_real_persisted_user(self):
        user = register_user(_input())

        assert user.pk is not None
        assert User.objects.filter(pk=user.pk).exists()

    def test_persists_the_expected_fields(self):
        user = register_user(_input())

        assert user.first_name == 'Ada'
        assert user.last_name == 'Lovelace'
        assert user.email == 'ada@example.com'
        assert user.phone_number == '+255712345678'

    def test_hashes_the_password(self):
        user = register_user(_input())

        assert user.password != VALID_FIELDS['password']
        assert user.check_password(VALID_FIELDS['password']) is True

    def test_does_not_persist_confirm_password_or_terms_accepted(self):
        user = register_user(_input())

        assert not hasattr(user, 'confirm_password')
        assert not hasattr(user, 'terms_accepted')

    def test_normalizes_email_case(self):
        user = register_user(_input(email='ADA@EXAMPLE.COM'))

        assert user.email == 'ada@example.com'

    def test_rejects_duplicate_email_case_insensitively(self):
        register_user(_input())

        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(email='ADA@example.com'))

        assert exc_info.value.field == 'email'
        assert User.objects.filter(email='ada@example.com').count() == 1

    def test_rejects_missing_email(self):
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(email=''))

        assert exc_info.value.field == 'email'

    def test_rejects_invalid_email_format(self):
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(email='not-an-email'))

        assert exc_info.value.field == 'email'

    def test_rejects_missing_password(self):
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(password=''))

        assert exc_info.value.field == 'password'

    def test_rejects_a_weak_password(self):
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(password='short'))

        assert exc_info.value.field == 'password'
        assert not User.objects.filter(email='ada@example.com').exists()

    def test_rejects_missing_first_name(self):
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(first_name=''))

        assert exc_info.value.field == 'first_name'

    def test_rejects_missing_last_name(self):
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(last_name=''))

        assert exc_info.value.field == 'last_name'

    def test_rejects_missing_phone_number(self):
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(phone_number=''))

        assert exc_info.value.field == 'phone_number'

    def test_rejects_an_invalid_phone_number(self):
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(phone_number='12345'))

        assert exc_info.value.field == 'phone_number'

    def test_no_partial_user_is_left_behind_after_a_rejected_registration(self):
        with pytest.raises(RegistrationError):
            register_user(_input(password='short'))

        assert User.objects.count() == 0

    def test_full_clean_backstop_rejects_a_first_name_over_the_column_limit(self):
        # Not covered by the explicit checks above (those only require
        # first_name to be non-empty) - this exercises full_clean() as a
        # backstop against database-level constraints the pre-checks don't
        # duplicate, such as CharField's max_length.
        with pytest.raises(RegistrationError) as exc_info:
            register_user(_input(first_name='A' * 200))

        assert not User.objects.filter(email='ada@example.com').exists()
        assert exc_info.value.message

    def test_concurrent_registration_race_is_handled(self):
        # Simulates two requests for the same email landing between the
        # pre-check and the save: the pre-check alone can't catch this, so
        # the database's unique constraint (surfaced as IntegrityError) is
        # the real guarantee, converted into the same friendly error.
        with (
            patch.object(User, 'save', side_effect=IntegrityError('duplicate key')),
            pytest.raises(RegistrationError) as exc_info,
        ):
            register_user(_input())

        assert exc_info.value.field == 'email'
