"""
The emailed-link flows, at the service layer: password reset and account
activation (S1-010).

`identity.services` owns the decisions these tests pin down - who gets a
token, what is stored, when a token stops working, and what a caller is told
- and the GraphQL layer above it only translates. So this is where the
security properties are asserted rather than in the resolver tests: the
properties are the *behaviour of the flow*, not of the transport.

The two properties this suite exists to protect, both of which are easy to
break with a well-meaning edit and impossible to notice from the UI:

1. **Nothing here discloses whether an address has an account.** The request
   side of both flows answers one message for every outcome - no account, a
   deactivated account, an already-verified one, a Google-only account with
   no password to reset - and the redeem side answers one message for every
   way a token can be unusable. Several tests below compare an existing and a
   non-existent address and assert the responses are indistinguishable, which
   is the only way that property actually gets tested.

2. **A token is single-use, expiring, stored only as a hash, and scoped to one
   purpose.** The raw value exists in exactly one place (the email) and
   cannot be recovered from the database; a spent or superseded token is
   recorded rather than deleted, so "already used" stays distinguishable
   from "never existed" in the record even though the caller cannot tell
   them apart.
"""

import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.utils import timezone

from identity.models import EmailToken, EmailTokenPurpose, RefreshSession, User
from identity.services import (
    ACCOUNT_ACTIVATION_REQUESTED_MESSAGE,
    ACTIVATION_LINK_INVALID_MESSAGE,
    PASSWORD_RESET_LINK_INVALID_MESSAGE,
    PASSWORD_RESET_REQUESTED_MESSAGE,
    EmailLinkError,
    RegistrationError,
    RegistrationInput,
    activate_account,
    register_user,
    request_account_activation,
    request_password_reset,
    reset_password,
)

VALID_PASSWORD = 'a-strong-unique-pass-1'
NEW_PASSWORD = 'an-even-stronger-pass-2'


def make_user(email='ada@example.com', password=VALID_PASSWORD, **overrides):
    fields = {
        'email': email,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password=password, **fields)


def token_from_last_email(index=-1):
    """
    The raw token from the most recently sent message, read out of its link.

    Read from the email rather than from the database on purpose: this is the
    only place the raw value exists, and every test that needs to redeem a
    token must obtain it the way a user does - by clicking the link. If a
    change ever made the token recoverable from the database, this helper
    would keep passing, so `test_only_the_hash_is_stored` is the test that
    actually pins that down.
    """
    body = mail.outbox[index].body
    url = next(line.strip() for line in body.splitlines() if '?token=' in line)
    return parse_qs(urlparse(url).query)['token'][0]


def register_a_user(email='ada@example.com'):
    return register_user(
        RegistrationInput(
            first_name='Ada',
            last_name='Lovelace',
            email=email,
            phone_number='+255712345678',
            password=VALID_PASSWORD,
        )
    )


# --- the request side: password reset ------------------------------------------------


@pytest.mark.django_db
class TestRequestPasswordReset:
    def test_it_emails_a_link_to_the_address_that_has_the_account(self):
        user = make_user()

        message = request_password_reset(user.email)

        assert message == PASSWORD_RESET_REQUESTED_MESSAGE
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]

    def test_the_answer_is_identical_for_an_address_with_no_account(self):
        """
        The whole reason this endpoint can exist: a caller-supplied address
        must not be usable to test whether it is registered. Compared against
        the real outcome rather than asserted in isolation, because the two
        messages drifting apart is the failure that matters.
        """
        make_user()

        for address in ('ada@example.com', 'grace@example.com', 'nobody@example.com'):
            result = request_password_reset(address)
            assert result == PASSWORD_RESET_REQUESTED_MESSAGE, address

    def test_an_unknown_address_sends_no_mail_at_all(self):
        """
        Generic in the *response* is necessary but not sufficient - it must not
        come from mailing a stranger either, or the endpoint becomes both an
        oracle and a mail cannon pointed at arbitrary people.
        """
        make_user()
        mail.outbox.clear()

        request_password_reset('stranger@example.com')

        assert mail.outbox == []

    def test_it_stores_only_the_hash_of_the_token(self):
        user = make_user()

        request_password_reset(user.email)

        raw = token_from_last_email()
        stored = EmailToken.objects.get(user=user, purpose=EmailTokenPurpose.PASSWORD_RESET)
        assert stored.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        # The strongest form of the claim: the raw token appears nowhere in
        # the row, so a database leak yields nothing replayable.
        assert raw not in stored.token_hash
        assert EmailToken.objects.filter(token_hash=raw).count() == 0

    def test_the_token_is_high_entropy_and_unguessable(self):
        """
        48 bytes from `secrets`, URL-safe base64. Asserted on shape and on
        distinctness rather than on entropy directly: two tokens must never
        collide, and the encoded length is the observable proxy for the
        amount of randomness the token actually carries.
        """
        user = make_user()

        request_password_reset(user.email)
        first = token_from_last_email()
        request_password_reset(user.email)
        second = token_from_last_email()

        assert first != second
        # 48 bytes -> 64 base64 characters, no padding.
        assert len(first) == 64
        assert '=' not in first

    def test_the_token_expires_at_the_configured_lifetime(self):
        user = make_user()

        request_password_reset(user.email)

        stored = EmailToken.objects.get(user=user, purpose=EmailTokenPurpose.PASSWORD_RESET)
        expected = timezone.now() + timedelta(minutes=60)
        assert stored.expires_at <= expected + timedelta(seconds=5)
        assert stored.expires_at > expected - timedelta(seconds=5)
        assert stored.is_active is True
        assert stored.used_at is None

    def test_a_second_request_supersedes_the_first_link(self):
        """
        Only the most recently emailed link may work, so a leaked older email
        is not a live credential for the rest of its window. The superseded
        token is recorded as used rather than deleted - nothing redeemed it,
        but from every caller's side an invalid link and a spent one are the
        same unusable link, and keeping the row preserves the record.
        """
        user = make_user()
        request_password_reset(user.email)
        first = token_from_last_email()

        request_password_reset(user.email)
        second = token_from_last_email()

        superseded = EmailToken.objects.get(
            user=user,
            purpose=EmailTokenPurpose.PASSWORD_RESET,
            token_hash=hashlib.sha256(first.encode()).hexdigest(),
        )
        assert superseded.used_at is not None
        assert superseded.is_active is False
        # ...and the new one works.
        reset_password(second, NEW_PASSWORD)
        user.refresh_from_db()
        assert user.check_password(NEW_PASSWORD)

    def test_superseding_does_not_erase_the_record_of_a_spent_token(self):
        """
        Superseding must not be able to launder the evidence that a link was
        already redeemed: only *unused* tokens are burned, so a token spent
        before a resend keeps its original `used_at`.
        """
        user = make_user()
        request_password_reset(user.email)
        spent = token_from_last_email()
        reset_password(spent, NEW_PASSWORD)
        spent_at = EmailToken.objects.get(
            token_hash=hashlib.sha256(spent.encode()).hexdigest()
        ).used_at

        request_password_reset(user.email)

        still_spent = EmailToken.objects.get(token_hash=hashlib.sha256(spent.encode()).hexdigest())
        assert still_spent.used_at == spent_at

    @pytest.mark.parametrize(
        ('email', 'message'),
        [
            ('', 'Email is required.'),
            ('   ', 'Email is required.'),
            ('not-an-email', 'Enter a valid email address.'),
        ],
    )
    def test_input_that_is_not_an_email_address_at_all_is_reported(self, email, message):
        """
        The one thing these flows may be honest about: "that is not an email
        address" is the same answer for a registered address and an
        unregistered one, so it discloses nothing, and it spares the caller a
        useless generic message about input that was never going to be looked
        up.
        """
        with pytest.raises(EmailLinkError) as exc_info:
            request_password_reset(email)

        assert exc_info.value.message == message
        assert exc_info.value.field == 'email'

    def test_the_address_is_normalized_before_the_account_is_looked_up(self):
        user = make_user(email='Ada@Example.com')

        request_password_reset('  ADA@example.com  ')

        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]

    def test_a_deactivated_account_is_a_silent_success(self):
        user = make_user(is_active=False)
        mail.outbox.clear()

        message = request_password_reset(user.email)

        assert message == PASSWORD_RESET_REQUESTED_MESSAGE
        assert mail.outbox == []
        assert EmailToken.objects.filter(user=user).count() == 0

    def test_a_google_only_account_is_a_silent_success(self):
        """
        An account provisioned by Google sign-in has no usable password, so
        "reset your password" is not a question it can answer. A real
        limitation rather than an oversight, and the alternative - mailing a
        link that would set a password on an account its owner signs into
        with Google - would be worse.
        """
        user = make_user(password=None)
        mail.outbox.clear()

        message = request_password_reset(user.email)

        assert message == PASSWORD_RESET_REQUESTED_MESSAGE
        assert mail.outbox == []
        assert EmailToken.objects.filter(user=user).count() == 0


# --- the redeem side: password reset ------------------------------------------------


@pytest.mark.django_db
class TestResetPassword:
    def test_a_valid_token_sets_the_new_password(self):
        user = make_user()
        request_password_reset(user.email)
        raw = token_from_last_email()

        reset_password(raw, NEW_PASSWORD)

        user.refresh_from_db()
        assert user.check_password(NEW_PASSWORD)
        assert not user.check_password(VALID_PASSWORD)

    def test_the_token_is_spent_by_a_successful_reset(self):
        user = make_user()
        request_password_reset(user.email)
        raw = token_from_last_email()

        reset_password(raw, NEW_PASSWORD)

        stored = EmailToken.objects.get(token_hash=hashlib.sha256(raw.encode()).hexdigest())
        assert stored.used_at is not None
        assert stored.is_active is False

    def test_a_token_cannot_be_replayed(self):
        user = make_user()
        request_password_reset(user.email)
        raw = token_from_last_email()
        reset_password(raw, NEW_PASSWORD)

        with pytest.raises(EmailLinkError) as exc_info:
            reset_password(raw, 'yet-another-pass-3')

        assert exc_info.value.message == PASSWORD_RESET_LINK_INVALID_MESSAGE
        assert exc_info.value.field == 'token'
        # The replay must not have changed anything.
        user.refresh_from_db()
        assert user.check_password(NEW_PASSWORD)

    def test_every_existing_refresh_session_is_revoked(self):
        """
        The step that makes a reset a reset. Without it, a password changed
        *because it may have been stolen* leaves the attacker's existing
        session - on their own device - signed in and working until it
        expires on its own schedule.
        """
        user = make_user()
        session = RefreshSession.objects.create(
            user=user,
            token_hash='a' * 64,
            expires_at=timezone.now() + timedelta(days=30),
        )
        request_password_reset(user.email)
        raw = token_from_last_email()

        reset_password(raw, NEW_PASSWORD)

        session.refresh_from_db()
        assert session.revoked_at is not None

    def test_a_revoked_session_cannot_refresh_anymore(self):
        user = make_user()
        RefreshSession.objects.create(
            user=user, token_hash='a' * 64, expires_at=timezone.now() + timedelta(days=30)
        )
        request_password_reset(user.email)
        reset_password(token_from_last_email(), NEW_PASSWORD)

        session = RefreshSession.objects.get(user=user)
        assert session.is_active is False

    @pytest.mark.parametrize('raw', ['', '   ', 'not-a-token', 'a' * 64, 'x' * 200])
    def test_an_unusable_token_is_refused_with_one_message(self, raw):
        make_user()

        with pytest.raises(EmailLinkError) as exc_info:
            reset_password(raw, NEW_PASSWORD)

        assert exc_info.value.message == PASSWORD_RESET_LINK_INVALID_MESSAGE
        assert exc_info.value.field == 'token'

    def test_every_way_a_token_can_fail_gives_the_same_answer(self):
        """
        "No such token", "expired" and "already used" are one message. A
        message per case would tell whoever holds a stolen link exactly how
        far it got, which is free reconnaissance against a live credential.
        """
        user = make_user()
        request_password_reset(user.email)
        spent = token_from_last_email()
        reset_password(spent, NEW_PASSWORD)
        expired = _issue_expired_token(user, EmailTokenPurpose.PASSWORD_RESET)

        for raw in ('nonsense', spent, expired):
            with pytest.raises(EmailLinkError) as exc_info:
                reset_password(raw, NEW_PASSWORD)
            assert exc_info.value.message == PASSWORD_RESET_LINK_INVALID_MESSAGE, raw
            assert exc_info.value.field == 'token'

    def test_an_expired_token_is_refused(self):
        user = make_user()
        raw = _issue_expired_token(user, EmailTokenPurpose.PASSWORD_RESET)

        with pytest.raises(EmailLinkError) as exc_info:
            reset_password(raw, NEW_PASSWORD)

        assert exc_info.value.message == PASSWORD_RESET_LINK_INVALID_MESSAGE
        user.refresh_from_db()
        assert user.check_password(VALID_PASSWORD)

    def test_a_password_the_validators_reject_is_refused_and_names_the_field(self):
        """
        The same rule set registration applies, so a password that could not
        have been registered cannot be set through the back door either. The
        field name is what lets the frontend show it next to the input.
        """
        user = make_user()
        request_password_reset(user.email)
        raw = token_from_last_email()

        with pytest.raises(EmailLinkError) as exc_info:
            reset_password(raw, 'short')

        assert exc_info.value.field == 'password'
        # A refused password must not have spent the token: the user is being
        # told to try again with the same link.
        user.refresh_from_db()
        assert user.check_password(VALID_PASSWORD)
        assert EmailToken.objects.get(token_hash=hashlib.sha256(raw.encode()).hexdigest()).is_active

    def test_a_refused_password_does_not_revoke_sessions_either(self):
        user = make_user()
        session = RefreshSession.objects.create(
            user=user, token_hash='a' * 64, expires_at=timezone.now() + timedelta(days=30)
        )
        request_password_reset(user.email)

        with pytest.raises(EmailLinkError):
            reset_password(token_from_last_email(), 'short')

        session.refresh_from_db()
        assert session.revoked_at is None

    def test_an_activation_token_cannot_be_redeemed_as_a_reset(self):
        """
        Purpose separation. The token is real, unexpired and belongs to this
        user - it is simply for the other operation, and is refused as such
        rather than treated as an unknown token.
        """
        user = register_a_user()
        activation_token = token_from_last_email()

        with pytest.raises(EmailLinkError) as exc_info:
            reset_password(activation_token, NEW_PASSWORD)

        assert exc_info.value.message == PASSWORD_RESET_LINK_INVALID_MESSAGE
        assert exc_info.value.field == 'token'
        user.refresh_from_db()
        assert user.check_password(VALID_PASSWORD)


# --- the request side: account activation -------------------------------------------


@pytest.mark.django_db
class TestRequestAccountActivation:
    def test_registration_emails_an_activation_link(self):
        user = register_a_user()

        assert user.is_verified is False
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]
        assert (
            EmailToken.objects.filter(user=user, purpose=EmailTokenPurpose.ACTIVATION).count() == 1
        )

    def test_a_resend_emails_a_fresh_link(self):
        user = register_a_user()
        first = token_from_last_email()

        message = request_account_activation(user.email)

        assert message == ACCOUNT_ACTIVATION_REQUESTED_MESSAGE
        assert len(mail.outbox) == 2
        assert token_from_last_email() != first

    def test_the_resend_supersedes_the_original_link(self):
        user = register_a_user()
        first = token_from_last_email()

        request_account_activation(user.email)

        stored = EmailToken.objects.get(
            user=user,
            purpose=EmailTokenPurpose.ACTIVATION,
            token_hash=hashlib.sha256(first.encode()).hexdigest(),
        )
        assert stored.is_active is False

    def test_the_answer_is_identical_for_an_address_with_no_account(self):
        register_a_user()

        for address in ('ada@example.com', 'grace@example.com', 'nobody@example.com'):
            assert request_account_activation(address) == ACCOUNT_ACTIVATION_REQUESTED_MESSAGE

    def test_an_unknown_address_sends_no_mail(self):
        register_a_user()
        mail.outbox.clear()

        request_account_activation('stranger@example.com')

        assert mail.outbox == []

    def test_an_already_verified_account_is_a_silent_success(self):
        """
        Re-verifying a verified account would be a no-op at best, and a token
        that quietly extends the life of an already-valid account is a
        credential nobody asked for.
        """
        user = register_a_user()
        user.is_verified = True
        user.save(update_fields=['is_verified'])
        mail.outbox.clear()

        message = request_account_activation(user.email)

        assert message == ACCOUNT_ACTIVATION_REQUESTED_MESSAGE
        assert mail.outbox == []

    def test_a_deactivated_account_is_a_silent_success(self):
        user = register_a_user()
        user.is_active = False
        user.save(update_fields=['is_active'])
        mail.outbox.clear()

        assert request_account_activation(user.email) == ACCOUNT_ACTIVATION_REQUESTED_MESSAGE
        assert mail.outbox == []

    @pytest.mark.parametrize('email', ['', 'not-an-email'])
    def test_input_that_is_not_an_email_address_at_all_is_reported(self, email):
        with pytest.raises(EmailLinkError) as exc_info:
            request_account_activation(email)

        assert exc_info.value.field == 'email'


# --- the redeem side: account activation --------------------------------------------


@pytest.mark.django_db
class TestActivateAccount:
    def test_a_valid_token_marks_the_address_verified(self):
        user = register_a_user()
        raw = token_from_last_email()

        activate_account(raw)

        user.refresh_from_db()
        assert user.is_verified is True

    def test_the_token_is_spent_by_a_successful_activation(self):
        user = register_a_user()
        raw = token_from_last_email()

        activate_account(raw)

        stored = EmailToken.objects.get(user=user, purpose=EmailTokenPurpose.ACTIVATION)
        assert stored.used_at is not None
        assert stored.is_active is False

    def test_a_token_cannot_be_replayed(self):
        register_a_user()
        raw = token_from_last_email()
        activate_account(raw)

        with pytest.raises(EmailLinkError) as exc_info:
            activate_account(raw)

        assert exc_info.value.message == ACTIVATION_LINK_INVALID_MESSAGE
        assert exc_info.value.field == 'token'

    def test_an_expired_token_is_refused(self):
        user = register_a_user()
        raw = _issue_expired_token(user, EmailTokenPurpose.ACTIVATION)

        with pytest.raises(EmailLinkError) as exc_info:
            activate_account(raw)

        assert exc_info.value.message == ACTIVATION_LINK_INVALID_MESSAGE
        user.refresh_from_db()
        assert user.is_verified is False

    @pytest.mark.parametrize('raw', ['', 'nonsense', 'b' * 64])
    def test_an_unusable_token_is_refused_with_one_message(self, raw):
        register_a_user()

        with pytest.raises(EmailLinkError) as exc_info:
            activate_account(raw)

        assert exc_info.value.message == ACTIVATION_LINK_INVALID_MESSAGE
        assert exc_info.value.field == 'token'

    def test_every_way_a_token_can_fail_gives_the_same_answer(self):
        user = register_a_user()
        spent = token_from_last_email()
        activate_account(spent)
        expired = _issue_expired_token(user, EmailTokenPurpose.ACTIVATION)

        for raw in ('nonsense', spent, expired):
            with pytest.raises(EmailLinkError) as exc_info:
                activate_account(raw)
            assert exc_info.value.message == ACTIVATION_LINK_INVALID_MESSAGE, raw

    def test_a_live_token_still_works_on_an_already_verified_account(self):
        """
        The narrow idempotency the implementation claims. An account can
        become verified by some other route - an administrator, or a future
        manual verification - while a still-live activation token exists, and
        following that link then reports success rather than a broken link for
        an operation that plainly worked. The token is burned either way, so
        a link stops working once it has been followed.

        It is *not* a licence to accept a bad token: the token is still
        required to be valid, which the tests above pin down. Note also that a
        *resend* is not how you reach this state - `request_account_activation`
        is a silent no-op for a verified account, so there is no second link.
        """
        user = register_a_user()
        raw = token_from_last_email()
        user.is_verified = True
        user.save(update_fields=['is_verified'])

        activate_account(raw)

        user.refresh_from_db()
        assert user.is_verified is True
        assert (
            EmailToken.objects.get(token_hash=hashlib.sha256(raw.encode()).hexdigest()).is_active
            is False
        )

    def test_a_password_reset_token_cannot_be_redeemed_as_an_activation(self):
        user = make_user()
        request_password_reset(user.email)
        reset_token = token_from_last_email()

        with pytest.raises(EmailLinkError) as exc_info:
            activate_account(reset_token)

        assert exc_info.value.message == ACTIVATION_LINK_INVALID_MESSAGE
        user.refresh_from_db()
        assert user.is_verified is False

    def test_activation_does_not_change_the_password(self):
        """
        Activation is a statement that someone proved control of the mailbox.
        It is not a login requirement and it must not be able to alter a
        credential - the two operations are entirely separate.
        """
        user = register_a_user()

        activate_account(token_from_last_email())

        user.refresh_from_db()
        assert user.check_password(VALID_PASSWORD)
        assert user.is_verified is True


# --- registration and the two flows together ----------------------------------------


@pytest.mark.django_db
class TestRegistrationInteraction:
    def test_a_failed_registration_sends_no_activation_email(self):
        """
        The mail goes out only after the transaction commits, so an account
        that rolled back can never be advertised by a message about it.
        """
        make_user(email='ada@example.com')
        mail.outbox.clear()

        with pytest.raises(RegistrationError):
            register_user(
                RegistrationInput(
                    first_name='Ada',
                    last_name='Lovelace',
                    email='ada@example.com',
                    phone_number='+255712345678',
                    password=VALID_PASSWORD,
                )
            )

        assert mail.outbox == []

    def test_registration_leaves_the_account_usable_without_activating(self):
        """
        Verification is deliberately not a login requirement: an unverified
        account owns a legitimate account and must not be locked out of it.
        """
        user = register_a_user()

        assert user.is_verified is False
        assert user.is_active is True
        assert user.check_password(VALID_PASSWORD)

    def test_the_two_purposes_keep_separate_tokens_for_one_user(self):
        user = register_a_user()
        activation_token = token_from_last_email()
        request_password_reset(user.email)

        assert (
            EmailToken.objects.filter(user=user, purpose=EmailTokenPurpose.ACTIVATION).count() == 1
        )
        assert (
            EmailToken.objects.filter(user=user, purpose=EmailTokenPurpose.PASSWORD_RESET).count()
            == 1
        )
        # Asking for a reset must not have burned the activation link.
        stored = EmailToken.objects.get(user=user, purpose=EmailTokenPurpose.ACTIVATION)
        assert stored.is_active is True
        # ...and redeeming one must not have burned the other.
        activate_account(activation_token)
        assert (
            EmailToken.objects.get(user=user, purpose=EmailTokenPurpose.PASSWORD_RESET).is_active
            is True
        )


def _issue_expired_token(user, purpose):
    """
    Create an already-expired token of `purpose` and return its raw value.

    Written by hand rather than reached by freezing time, because the value
    has to be recoverable by the test - and the only copy of a real token
    lives in an email, which is exactly the property being relied on
    elsewhere in this file.
    """
    raw = 'expired-token-for-tests-only-0123456789'
    EmailToken.objects.create(
        user=user,
        purpose=purpose,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    return raw
