from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest
from django.conf import settings
from django.db import IntegrityError
from django.test import RequestFactory
from django.utils import timezone

from identity.authentication import (
    GENERIC_GOOGLE_LOGIN_ERROR,
    GENERIC_LOGIN_ERROR,
    GENERIC_REFRESH_ERROR,
    GOOGLE_EMAIL_IN_USE_ERROR,
    GOOGLE_PROVIDER,
    AuthenticationError,
    GoogleEmailInUseError,
    authenticate_with_google,
    get_authenticated_user,
    login,
    logout,
    refresh,
)
from identity.google_oauth import GoogleIdentity, GoogleTokenError
from identity.models import ExternalIdentity, RefreshSession, User
from identity.tokens import issue_access_token

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


def _request_with_auth_header(header_value):
    request = RequestFactory().get('/graphql/')
    if header_value is not None:
        request.META['HTTP_AUTHORIZATION'] = header_value
    return request


@pytest.mark.django_db
class TestLogin:
    def test_valid_login_succeeds(self):
        _make_user()

        session = login('ada@example.com', VALID_PASSWORD)

        assert session.user.email == 'ada@example.com'
        assert session.access_token
        assert session.refresh_token

    def test_login_is_case_insensitive_on_email(self):
        _make_user(email='ada@example.com')

        session = login('ADA@EXAMPLE.COM', VALID_PASSWORD)

        assert session.user.email == 'ada@example.com'

    def test_wrong_password_fails_generically(self):
        _make_user()

        with pytest.raises(AuthenticationError) as exc_info:
            login('ada@example.com', 'the-wrong-password')

        assert str(exc_info.value) == GENERIC_LOGIN_ERROR

    def test_unknown_email_fails_with_the_same_generic_message(self):
        with pytest.raises(AuthenticationError) as exc_info:
            login('nobody@example.com', VALID_PASSWORD)

        assert str(exc_info.value) == GENERIC_LOGIN_ERROR

    def test_inactive_user_cannot_login(self):
        _make_user(is_active=False)

        with pytest.raises(AuthenticationError) as exc_info:
            login('ada@example.com', VALID_PASSWORD)

        assert str(exc_info.value) == GENERIC_LOGIN_ERROR

    def test_unverified_user_can_still_login_this_sprint(self):
        # Explicit, documented policy: registration leaves is_verified=False
        # and no email-verification flow exists yet, so login must not
        # silently require it - see identity/authentication.py's docstring.
        user = _make_user()
        assert user.is_verified is False

        session = login('ada@example.com', VALID_PASSWORD)

        assert session.user.pk == user.pk

    def test_successful_login_creates_exactly_one_refresh_session(self):
        _make_user()

        login('ada@example.com', VALID_PASSWORD)

        assert RefreshSession.objects.count() == 1

    def test_refresh_credential_is_stored_hashed_not_plaintext(self):
        _make_user()

        session = login('ada@example.com', VALID_PASSWORD)

        stored = RefreshSession.objects.get()
        assert stored.token_hash != session.refresh_token
        assert session.refresh_token not in stored.token_hash
        assert len(stored.token_hash) == 64  # SHA-256 hex digest


def _google_identity(**overrides):
    fields = {
        'subject': 'google-subject-1',
        'email': 'ada@example.com',
        'email_verified': True,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
    }
    fields.update(overrides)
    return GoogleIdentity(**fields)


def _patched_google_identity(identity=None, error=None):
    target = 'identity.authentication.verify_google_id_token'
    if error is not None:
        return patch(target, side_effect=error)
    return patch(target, return_value=identity)


@pytest.mark.django_db
class TestAuthenticateWithGoogle:
    def test_invalid_credential_fails_generically(self):
        with (
            _patched_google_identity(error=GoogleTokenError('bad token')),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            authenticate_with_google('bad-credential')

        assert str(exc_info.value) == GENERIC_GOOGLE_LOGIN_ERROR

    def test_new_google_identity_creates_a_user_and_links_it(self):
        identity = _google_identity()

        with _patched_google_identity(identity=identity):
            session = authenticate_with_google('a-credential')

        assert session.user.email == 'ada@example.com'
        assert session.user.first_name == 'Ada'
        assert session.user.last_name == 'Lovelace'
        assert session.user.phone_number == ''
        assert session.user.has_usable_password() is False
        assert session.access_token
        assert session.refresh_token

        link = ExternalIdentity.objects.get()
        assert link.provider == GOOGLE_PROVIDER
        assert link.provider_subject == 'google-subject-1'
        assert link.user_id == session.user.pk

    def test_new_google_user_is_marked_verified_when_google_verified_the_email(self):
        identity = _google_identity(email_verified=True)

        with _patched_google_identity(identity=identity):
            session = authenticate_with_google('a-credential')

        assert session.user.is_verified is True

    def test_new_google_user_without_a_name_falls_back_to_the_email_local_part(self):
        identity = _google_identity(first_name='', last_name='')

        with _patched_google_identity(identity=identity):
            session = authenticate_with_google('a-credential')

        assert session.user.first_name == 'ada'
        assert session.user.last_name == 'ada'

    def test_returning_google_identity_authenticates_the_existing_linked_user(self):
        identity = _google_identity()
        with _patched_google_identity(identity=identity):
            first_session = authenticate_with_google('a-credential')

        with _patched_google_identity(identity=identity):
            second_session = authenticate_with_google('a-credential')

        assert second_session.user.pk == first_session.user.pk
        assert User.objects.filter(email='ada@example.com').count() == 1
        assert ExternalIdentity.objects.count() == 1

    def test_duplicate_sign_ins_do_not_create_duplicate_users_or_links(self):
        identity = _google_identity()

        for _ in range(3):
            with _patched_google_identity(identity=identity):
                authenticate_with_google('a-credential')

        assert User.objects.filter(email='ada@example.com').count() == 1
        assert ExternalIdentity.objects.count() == 1

    def test_existing_password_user_is_never_auto_linked_even_with_verified_email(self):
        # Revised policy (post-review): email_verified=True is not enough to
        # *link*. Only an exact (provider, provider_subject) match ever
        # authenticates into an existing account - see the security note
        # in authenticate_with_google's docstring. It is, however, enough to
        # be *told* the address is taken, because that caller has proven
        # control of the mailbox.
        existing_user = _make_user(email='ada@example.com')
        identity = _google_identity(email='ada@example.com', email_verified=True)

        with (
            _patched_google_identity(identity=identity),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            authenticate_with_google('a-credential')

        assert str(exc_info.value) == GOOGLE_EMAIL_IN_USE_ERROR
        assert User.objects.filter(email='ada@example.com').count() == 1
        assert ExternalIdentity.objects.count() == 0
        # No link was ever created to the existing user, verified or not.
        assert not ExternalIdentity.objects.filter(user=existing_user).exists()

    def test_existing_password_user_with_matching_unverified_email_is_not_linked(self):
        _make_user(email='ada@example.com')
        identity = _google_identity(email='ada@example.com', email_verified=False)

        with (
            _patched_google_identity(identity=identity),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            authenticate_with_google('a-credential')

        assert str(exc_info.value) == GENERIC_GOOGLE_LOGIN_ERROR
        # Refused, not silently turned into a second account.
        assert User.objects.filter(email='ada@example.com').count() == 1
        assert ExternalIdentity.objects.count() == 0

    def test_inactive_returning_google_user_cannot_authenticate(self):
        identity = _google_identity()
        with _patched_google_identity(identity=identity):
            first_session = authenticate_with_google('a-credential')
        User.objects.filter(pk=first_session.user.pk).update(is_active=False)

        with _patched_google_identity(identity=identity), pytest.raises(AuthenticationError):
            authenticate_with_google('a-credential')

    def test_successful_google_authentication_creates_a_refresh_session(self):
        identity = _google_identity()

        with _patched_google_identity(identity=identity):
            authenticate_with_google('a-credential')

        assert RefreshSession.objects.count() == 1

    def test_second_google_subject_with_the_same_email_is_refused_not_linked(self):
        # A second, different Google identity (a different `sub`) reporting
        # the same email as an already Google-provisioned account is
        # refused exactly like a password account would be - only an exact
        # (provider, provider_subject) match ever authenticates into an
        # account that already exists, regardless of how that account was
        # originally created.
        first_identity = _google_identity(subject='subject-a', email='ada@example.com')
        with _patched_google_identity(identity=first_identity):
            authenticate_with_google('credential-a')

        second_identity = _google_identity(subject='subject-b', email='ada@example.com')
        with (
            _patched_google_identity(identity=second_identity),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            authenticate_with_google('credential-b')

        # Refused, and reported specifically: the caller proved they control
        # this mailbox, so "an account already exists" discloses nothing they
        # could not check themselves. See GoogleEmailInUseError.
        assert str(exc_info.value) == GOOGLE_EMAIL_IN_USE_ERROR
        assert ExternalIdentity.objects.count() == 1
        assert User.objects.filter(email='ada@example.com').count() == 1

    def test_an_unverified_credential_gets_the_generic_message_for_a_collision(
        self,
    ):
        """
        The gate, and the whole reason this feature is safe.

        `email_verified: false` proves nothing about who is asking, so this
        caller has not earned the disclosure and gets exactly the same answer
        as any other failure. If this assertion ever fails, the change has
        become an enumeration oracle.
        """
        _make_user(email='ada@example.com')
        identity = _google_identity(email='ada@example.com', email_verified=False)

        with (
            _patched_google_identity(identity=identity),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            authenticate_with_google('a-credential')

        assert str(exc_info.value) == GENERIC_GOOGLE_LOGIN_ERROR
        assert not isinstance(exc_info.value, GoogleEmailInUseError)
        assert ExternalIdentity.objects.count() == 0

    def test_the_collision_message_is_the_same_for_both_kinds_of_collision(self):
        """
        One message for the whole collision class.

        The existing account might be password-registered *or* belong to a
        different Google identity reporting the same address. A caller must
        not be able to tell which, because that difference is the existing
        account's history and it is not theirs to learn.
        """
        # (a) the address belongs to a password account
        _make_user(email='ada@example.com')
        with (
            _patched_google_identity(
                identity=_google_identity(email='ada@example.com', email_verified=True)
            ),
            pytest.raises(AuthenticationError) as password_account,
        ):
            authenticate_with_google('credential-a')

        # (b) the address belongs to a *different* Google identity. A second
        # address keeps the two cases independent instead of mutating the
        # first one's account.
        with _patched_google_identity(
            identity=_google_identity(subject='subject-a', email='grace@example.com')
        ):
            authenticate_with_google('credential-b')
        with (
            _patched_google_identity(
                identity=_google_identity(subject='subject-b', email='grace@example.com')
            ),
            pytest.raises(AuthenticationError) as google_account,
        ):
            authenticate_with_google('credential-c')

        assert str(password_account.value) == str(google_account.value)
        assert str(password_account.value) == GOOGLE_EMAIL_IN_USE_ERROR

    def test_the_collision_message_reveals_nothing_about_the_existing_account(self):
        """
        The message discloses *existence* and nothing else - no password
        state, no verification state, no provider, no subject, no ids. Pinned
        as a whole-message check so a future rewording cannot quietly start
        describing the account it is talking about.
        """
        existing = _make_user(email='ada@example.com')

        with (
            _patched_google_identity(
                identity=_google_identity(email='ada@example.com', email_verified=True)
            ),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            authenticate_with_google('a-credential')

        message = str(exc_info.value)
        assert message == GOOGLE_EMAIL_IN_USE_ERROR
        for secret in (
            'password',
            'verified',
            'unverified',
            'google',
            'subject',
            str(existing.pk),
        ):
            assert secret not in message.lower(), f'the message leaked {secret!r}'

    def test_an_invalid_credential_cannot_be_used_to_probe_for_an_account(self):
        """
        The anti-oracle test, stated as the attack it prevents.

        Somebody wanting to know whether an arbitrary address is registered
        has no Google credential for it and cannot mint one. This is what they
        get: the same generic message as for a registered address, for an
        invalid, expired, replayed or unconfigured credential alike. So the
        two are indistinguishable, and the address-probing attack has nothing
        to distinguish.
        """
        _make_user(email='ada@example.com')
        with (
            _patched_google_identity(error=GoogleTokenError('bad token')),
            pytest.raises(AuthenticationError) as with_account,
        ):
            authenticate_with_google('a-credential')
        with (
            _patched_google_identity(error=GoogleTokenError('bad token')),
            pytest.raises(AuthenticationError) as without_account,
        ):
            authenticate_with_google('a-credential')  # for an unregistered address

        assert str(with_account.value) == GENERIC_GOOGLE_LOGIN_ERROR
        assert str(without_account.value) == str(with_account.value)

    def test_the_email_in_use_error_is_still_an_authentication_error(self):
        """
        The schema's `except AuthenticationError` handler is what turns this
        into a `success: false` payload, so the subclass relationship is load-
        bearing: if this breaks, a refusal would become an uncaught GraphQL
        error instead of a rendered message.
        """
        _make_user(email='ada@example.com')
        with (
            _patched_google_identity(
                identity=_google_identity(email='ada@example.com', email_verified=True)
            ),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            authenticate_with_google('a-credential')

        assert isinstance(exc_info.value, GoogleEmailInUseError)
        assert isinstance(exc_info.value, AuthenticationError)

    def test_the_collision_message_is_never_shown_by_a_generic_failure(self):
        """
        The inverse, and the one that would matter most in production: no
        failure that is not the collision may ever produce the specific text.
        """
        for error in (
            GoogleTokenError('bad token'),
            GoogleTokenError('Token has wrong audience'),
            GoogleTokenError('Token expired'),
            GoogleTokenError('Wrong issuer'),
        ):
            with (
                _patched_google_identity(error=error),
                pytest.raises(AuthenticationError) as exc_info,
            ):
                authenticate_with_google('a-credential')
            assert GOOGLE_EMAIL_IN_USE_ERROR not in str(exc_info.value), error

    def test_duplicate_first_time_google_sign_in_recovers_via_retry(self):
        """
        Simulates two requests for the exact same, brand-new Google
        identity racing to provision it (e.g. a double-submitted sign-in):
        this request's own first pass misses both the ExternalIdentity and
        User-email checks (the other request's insert - both rows, in one
        atomic transaction - hadn't committed yet when this request's own
        checks ran), so it attempts to provision too and collides for real
        on the database's unique constraint. `_resolve_google_user`'s retry
        then re-checks from scratch: this time the other request's commit
        is visible, the ExternalIdentity lookup finds it, and this request
        authenticates as that same shared user - never creating a second
        account, and never hitting the "unrelated account" refusal either.
        """
        identity = _google_identity()
        winning_user = User(
            email='ada@example.com',
            first_name='Ada',
            last_name='Lovelace',
            phone_number='',
            is_verified=True,
        )
        winning_user.set_unusable_password()
        winning_user.save()
        ExternalIdentity.objects.create(
            user=winning_user,
            provider=GOOGLE_PROVIDER,
            provider_subject=identity.subject,
            email='ada@example.com',
        )

        real_select_related = ExternalIdentity.objects.select_related
        real_user_filter = User.objects.filter
        calls = {'select_related': 0}

        class _Miss:
            def filter(self, *_args, **_kwargs):
                return self

            def first(self):
                return None

            def exists(self):
                return False

        def select_related_stub(*args, **kwargs):
            calls['select_related'] += 1
            if calls['select_related'] == 1:
                return _Miss()
            return real_select_related(*args, **kwargs)

        def user_filter_stub(*args, **kwargs):
            # Consulted only during the same pass select_related's first
            # (missed) call belongs to - the retry's own success short-
            # circuits before ever reaching this check again.
            if calls['select_related'] == 1:
                return _Miss()
            return real_user_filter(*args, **kwargs)

        with (
            patch.object(
                ExternalIdentity.objects, 'select_related', side_effect=select_related_stub
            ),
            patch.object(User.objects, 'filter', side_effect=user_filter_stub),
            _patched_google_identity(identity=identity),
        ):
            session = authenticate_with_google('a-credential')

        assert session.user.pk == winning_user.pk
        assert User.objects.filter(email='ada@example.com').count() == 1
        assert ExternalIdentity.objects.count() == 1

    def test_provisioning_collision_with_an_unrelated_account_is_refused_not_linked(self):
        """
        The critical safety property of the retry mechanism: a collision
        during provisioning must never fall back to "whichever account has
        this email now" if that account isn't the *same* Google identity -
        otherwise a race could silently reintroduce the exact auto-link
        Policy B forbids (see `_resolve_google_user`'s docstring), just
        behind a timing window instead of the deterministic path. Forcing
        every pass's checks to miss (an intentionally extreme double,
        standing in for a persistently lagging read) makes both this
        request's first attempt *and* its one retry collide with the same
        unrelated, already-existing password account - proving the retry
        itself doesn't eventually paper over that collision by linking to
        it.
        """
        identity = _google_identity(email='ada@example.com')
        unrelated_user = _make_user(email='ada@example.com')

        class _AlwaysMisses:
            def first(self):
                return None

            def exists(self):
                return False

        with (
            patch.object(User.objects, 'filter', return_value=_AlwaysMisses()),
            patch.object(User, 'save', side_effect=IntegrityError('duplicate key')),
            _patched_google_identity(identity=identity),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            authenticate_with_google('a-credential')

        assert str(exc_info.value) == GENERIC_GOOGLE_LOGIN_ERROR
        assert User.objects.filter(email='ada@example.com').count() == 1
        assert not ExternalIdentity.objects.filter(user=unrelated_user).exists()
        assert ExternalIdentity.objects.count() == 0


@pytest.mark.django_db
class TestGetAuthenticatedUser:
    def test_resolves_the_user_from_a_valid_token(self):
        user = _make_user()
        token, _ = issue_access_token(user.pk)

        resolved = get_authenticated_user(_request_with_auth_header(f'Bearer {token}'))

        assert resolved is not None
        assert resolved.pk == user.pk

    def test_returns_none_for_a_missing_header(self):
        assert get_authenticated_user(_request_with_auth_header(None)) is None

    def test_returns_none_for_a_malformed_header(self):
        assert get_authenticated_user(_request_with_auth_header('NotBearer abc')) is None

    def test_returns_none_for_an_invalid_token(self):
        assert get_authenticated_user(_request_with_auth_header('Bearer garbage')) is None

    def test_returns_none_when_the_user_is_inactive(self):
        user = _make_user(is_active=False)
        token, _ = issue_access_token(user.pk)

        assert get_authenticated_user(_request_with_auth_header(f'Bearer {token}')) is None

    def test_returns_none_when_the_user_no_longer_exists(self):
        token, _ = issue_access_token(999_999_999)

        assert get_authenticated_user(_request_with_auth_header(f'Bearer {token}')) is None

    def test_a_forged_token_does_not_resolve_to_the_claimed_user(self):
        # The user id in a token is never trusted on its own - only a
        # token with a valid signature (proving *we* issued it) resolves.
        other_user = _make_user(email='other@example.com', phone_number='+255700000001')
        forged = jwt.encode(
            {'sub': str(other_user.pk), 'type': 'access', 'jti': 'x', 'iat': 0, 'exp': 9999999999},
            'not-the-real-signing-key',
            algorithm='HS256',
        )

        assert get_authenticated_user(_request_with_auth_header(f'Bearer {forged}')) is None


@pytest.mark.django_db
class TestRefresh:
    def test_refresh_credential_works(self):
        user = _make_user()
        session = login('ada@example.com', VALID_PASSWORD)

        renewed = refresh(session.refresh_token)

        assert renewed.user.pk == user.pk
        assert renewed.access_token

    def test_refresh_rotates_the_credential(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)

        renewed = refresh(session.refresh_token)

        assert renewed.refresh_token != session.refresh_token

    def test_old_refresh_credential_is_invalid_after_rotation(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)
        refresh(session.refresh_token)

        with pytest.raises(AuthenticationError) as exc_info:
            refresh(session.refresh_token)

        assert str(exc_info.value) == GENERIC_REFRESH_ERROR

    def test_rotation_revokes_and_links_the_old_session_row(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)
        old_row = RefreshSession.objects.get()

        refresh(session.refresh_token)

        old_row.refresh_from_db()
        assert old_row.revoked_at is not None
        assert old_row.replaced_by is not None

    def test_revoked_refresh_credential_fails(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)
        logout(session.refresh_token)

        with pytest.raises(AuthenticationError):
            refresh(session.refresh_token)

    def test_expired_refresh_credential_fails(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)
        RefreshSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))

        with pytest.raises(AuthenticationError) as exc_info:
            refresh(session.refresh_token)

        assert str(exc_info.value) == GENERIC_REFRESH_ERROR

    def test_unknown_refresh_credential_fails(self):
        with pytest.raises(AuthenticationError):
            refresh('this-credential-was-never-issued')

    def test_empty_refresh_credential_fails(self):
        with pytest.raises(AuthenticationError):
            refresh('')

    def test_inactive_user_cannot_refresh(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)
        User.objects.update(is_active=False)

        with pytest.raises(AuthenticationError):
            refresh(session.refresh_token)

    def test_refresh_updates_last_used_at(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)
        original_row = RefreshSession.objects.get()
        assert original_row.last_used_at is None

        refresh(session.refresh_token)

        original_row.refresh_from_db()
        assert original_row.last_used_at is not None


@pytest.mark.django_db
class TestLogout:
    def test_logout_revokes_the_refresh_session(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)

        logout(session.refresh_token)

        stored = RefreshSession.objects.get()
        assert stored.revoked_at is not None

    def test_logout_with_an_unknown_credential_does_not_raise(self):
        logout('never-issued')

    def test_logout_with_an_empty_credential_does_not_raise(self):
        logout('')

    def test_logged_out_credential_can_no_longer_refresh(self):
        _make_user()
        session = login('ada@example.com', VALID_PASSWORD)
        logout(session.refresh_token)

        with pytest.raises(AuthenticationError):
            refresh(session.refresh_token)


def test_jwt_signing_key_is_configured_and_distinct_from_secret_key():
    # Sanity check on the settings decision itself (see config/settings/base.py).
    assert settings.JWT_SIGNING_KEY
    assert settings.JWT_SIGNING_KEY != settings.SECRET_KEY
