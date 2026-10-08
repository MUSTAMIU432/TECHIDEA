"""
Outgoing transactional email: the transport layer (S1-010).

`identity.email` has no decisions in it - it is handed a user and a token
that `identity.services` has already decided to send, and turns that into a
message. What still deserves tests is everything that decision *depends on*
being true:

- the link in the body is the one the frontend actually serves, built from
  the configured frontend origin, and is a complete absolute URL rather than
  a relative path that would sail through into a message that resolves to
  nothing;
- the recipient is the *stored* address, which is what makes it safe to mail
  an arbitrary submitted address at all;
- the body renders the template and the template's context, so a rename of
  either fails here rather than as a blank line in somebody's inbox;
- the lifetime in the message is derived from the setting, so shortening the
  window cannot leave the email claiming a duration the code no longer
  honours;
- a delivery failure is logged and swallowed rather than raised, and never
  logs the token - which is the whole reason this module can be called from
  inside a flow that must answer generically.

Django's test environment swaps the email backend for the in-memory one, so
`django.core.mail.outbox` is what "the message that was sent" means
throughout. `test_the_test_suite_really_is_capturing_mail` asserts that
directly, because every other test here depends on it and a silent failure
of that assumption would make the whole file pass for the wrong reason.
"""

import logging
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import mail
from django.core.exceptions import ImproperlyConfigured

from identity.email import (
    ACTIVATION_PATH,
    ACTIVATION_SUBJECT,
    PASSWORD_RESET_PATH,
    PASSWORD_RESET_SUBJECT,
    build_action_url,
    format_lifetime,
    send_activation_email,
    send_password_reset_email,
)
from identity.models import User

RAW_TOKEN = 'a-raw-token-value_1234567890'


def make_user(email='ada@example.com', **overrides):
    fields = {
        'email': email,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password='a-strong-unique-pass-1', **fields)


@pytest.fixture
def smtp(settings):
    """
    A deployed-shaped mail configuration: an https frontend origin to build
    links from, a real sender address, and two distinct lifetimes so a test
    that confuses the two flows' settings fails rather than passing by
    coincidence.
    """
    settings.FRONTEND_URL = 'https://app.example.test'
    settings.DEFAULT_FROM_EMAIL = 'no-reply@example.test'
    settings.PASSWORD_RESET_TOKEN_LIFETIME = timedelta(minutes=45)
    settings.ACTIVATION_TOKEN_LIFETIME = timedelta(hours=48)
    return settings


@pytest.mark.django_db
def test_the_test_suite_really_is_capturing_mail(smtp):
    """
    Every assertion about "the message that was sent" below is only meaningful
    if the outbox is real. Pinned first, and explicitly, so that if the test
    environment ever stops swapping in the in-memory backend the rest of this
    file fails loudly instead of quietly asserting against an empty list.
    """
    send_password_reset_email(make_user(), RAW_TOKEN)

    assert len(mail.outbox) == 1


class TestBuildActionUrl:
    def test_it_builds_an_absolute_url_from_the_frontend_origin(self, smtp):
        url = build_action_url(PASSWORD_RESET_PATH, RAW_TOKEN)

        assert url.startswith('https://app.example.test/reset-password?')
        assert 'token=' in url

    def test_it_composes_the_origin_and_the_frontend_s_own_path(self, smtp):
        """
        The frontend route is a path *in the browser app*, not an API path:
        the link lands on a page, and that page then calls the mutation with
        the token. So the path is one this project's router owns, and it is
        the one the router is tested against - a link to an API path would
        resolve to nothing a person can use.

        The origin arrives already normalized (a trailing slash is stripped
        once, where the setting is read - see
        `tests/test_email_configuration.py::test_a_trailing_slash_on_the_frontend_url_is_stripped`),
        which is why composing the two here is enough.
        """
        url = build_action_url(ACTIVATION_PATH, RAW_TOKEN)

        assert url == (f'https://app.example.test{ACTIVATION_PATH}?token={RAW_TOKEN}')
        # The frontend serves both of these routes.
        assert PASSWORD_RESET_PATH == '/reset-password'
        assert ACTIVATION_PATH == '/activate-account'

    def test_a_missing_frontend_url_is_refused_rather_than_producing_a_dead_link(self, smtp):
        """
        A relative link would look correct in a test and be useless in an
        inbox. Every environment sets FRONTEND_URL (local.py defaults it,
        production.py requires an https:// one), so an empty value here is a
        misconfiguration worth failing on.
        """
        smtp.FRONTEND_URL = ''

        with pytest.raises(ImproperlyConfigured) as exc_info:
            build_action_url(PASSWORD_RESET_PATH, RAW_TOKEN)

        assert 'FRONTEND_URL' in str(exc_info.value)

    def test_the_token_is_carried_as_a_query_parameter(self, smtp):
        url = build_action_url(ACTIVATION_PATH, RAW_TOKEN)

        assert parse_qs(urlparse(url).query) == {'token': [RAW_TOKEN]}


class TestFormatLifetime:
    @pytest.mark.parametrize(
        ('lifetime', 'expected'),
        [
            (timedelta(minutes=45), '45 minutes'),
            (timedelta(minutes=1), '1 minute'),
            (timedelta(hours=2), '2 hours'),
            (timedelta(hours=1), '1 hour'),
            (timedelta(hours=1, minutes=30), '1 hour 30 minutes'),
        ],
    )
    def test_it_renders_a_duration_in_words(self, lifetime, expected):
        """
        Derived from the setting rather than written into the template, so a
        deployment that shortens the window cannot leave the message claiming
        a duration the code no longer honours.
        """
        assert format_lifetime(lifetime) == expected


@pytest.mark.django_db
class TestSendPasswordResetEmail:
    def test_it_sends_one_plain_text_message_to_the_stored_address(self, smtp):
        send_password_reset_email(make_user(), RAW_TOKEN)

        message = mail.outbox[0]
        assert message.to == ['ada@example.com']
        assert message.from_email == 'no-reply@example.test'
        assert message.subject == PASSWORD_RESET_SUBJECT

    def test_the_body_carries_the_working_link(self, smtp):
        send_password_reset_email(make_user(), RAW_TOKEN)

        body = mail.outbox[0].body
        assert f'https://app.example.test{PASSWORD_RESET_PATH}?token={RAW_TOKEN}' in body

    def test_the_body_states_the_lifetime_the_code_will_honour(self, smtp):
        send_password_reset_email(make_user(), RAW_TOKEN)

        assert '45 minutes' in mail.outbox[0].body

    def test_the_greeting_uses_the_stored_first_name(self, smtp):
        send_password_reset_email(make_user(first_name='Ada'), RAW_TOKEN)

        assert mail.outbox[0].body.startswith('Hello Ada,')

    def test_it_says_what_to_do_if_the_request_was_not_theirs(self, smtp):
        """
        A reset mail that a stranger receives is only reassuring if it tells
        them that doing nothing is the safe answer.
        """
        send_password_reset_email(make_user(), RAW_TOKEN)

        body = mail.outbox[0].body
        assert 'did not ask for a new password' in body
        assert 'ignore this email' in body

    def test_the_body_carries_no_markup_and_no_remote_content(self, smtp):
        """
        Plain text on purpose: these messages exist to deliver one link, and
        HTML would add remote images, a tracking pixel and a link whose text
        can differ from its target - the things that make a phishing mail
        convincing, in a mail whose whole purpose is "click this".
        """
        send_password_reset_email(make_user(), RAW_TOKEN)

        body = mail.outbox[0].body.lower()
        assert '<html' not in body
        assert '<a ' not in body
        assert '<img' not in body
        # The action link is the only URL in the message.
        assert body.count('http') == 1

    def test_the_subject_does_not_name_the_account(self, smtp):
        """
        An inbox preview is visible to more than its owner - a lock-screen
        notification, a shared machine - so the subject says what the mail is
        without saying whose it is.
        """
        send_password_reset_email(make_user(email='private-person@example.com'), RAW_TOKEN)

        subject = mail.outbox[0].subject
        assert 'ada@example.com' not in subject
        assert 'private-person@example.com' not in subject
        assert 'Lovelace' not in subject

    def test_the_recipient_is_the_stored_address_not_the_submitted_one(self, smtp):
        """
        The property that makes mailing a caller-supplied address safe at all:
        the only address a message can go to is the one on the user record
        this send was handed.
        """
        send_password_reset_email(make_user(email='real-owner@example.com'), RAW_TOKEN)

        assert mail.outbox[0].to == ['real-owner@example.com']


@pytest.mark.django_db
class TestSendActivationEmail:
    def test_it_sends_one_message_to_the_stored_address(self, smtp):
        send_activation_email(make_user(), RAW_TOKEN)

        message = mail.outbox[0]
        assert message.to == ['ada@example.com']
        assert message.subject == ACTIVATION_SUBJECT

    def test_the_body_carries_the_working_link(self, smtp):
        send_activation_email(make_user(), RAW_TOKEN)

        body = mail.outbox[0].body
        assert f'https://app.example.test{ACTIVATION_PATH}?token={RAW_TOKEN}' in body

    def test_the_body_states_the_lifetime(self, smtp):
        send_activation_email(make_user(), RAW_TOKEN)

        assert '48 hours' in mail.outbox[0].body

    def test_it_carries_no_markup(self, smtp):
        send_activation_email(make_user(), RAW_TOKEN)

        body = mail.outbox[0].body.lower()
        assert '<html' not in body
        assert '<a ' not in body

    def test_it_tells_an_unexpected_recipient_that_ignoring_it_is_safe(self, smtp):
        send_activation_email(make_user(), RAW_TOKEN)

        assert 'ignore this email' in mail.outbox[0].body


@pytest.mark.django_db
class TestDeliveryFailure:
    """
    A send that fails is logged, never raised.

    Every caller here sits inside an operation that reports a *generic*
    result on purpose, so a broken mail configuration is invisible at the API
    boundary by construction: the request "succeeds" and the user waits for a
    message that was never sent. Raising instead would either leak account
    existence (the error would differ per address) or force every caller to
    reimplement the catch. The cost of swallowing is that a broken mail setup
    is found in the logs rather than in a support ticket - which is why the
    failure is logged at ERROR, and why production.py refuses to start on the
    settings that cause the commonest version of it.
    """

    @pytest.mark.parametrize('boom', [OSError('connection refused'), TimeoutError('timed out')])
    def test_a_transport_failure_never_reaches_the_caller(self, smtp, monkeypatch, boom):
        def explode(self, *args, **kwargs):
            raise boom

        monkeypatch.setattr('identity.email.EmailMessage.send', explode)

        send_password_reset_email(make_user(), RAW_TOKEN)  # must not raise

    def test_the_failure_is_logged_at_error_level(self, smtp, monkeypatch, caplog):
        def explode(self, *args, **kwargs):
            raise OSError('connection refused')

        monkeypatch.setattr('identity.email.EmailMessage.send', explode)

        with caplog.at_level(logging.ERROR, logger='identity.email'):
            send_password_reset_email(make_user(), RAW_TOKEN)

        assert caplog.records, 'a failed send must be logged'
        assert caplog.records[0].levelno == logging.ERROR
        assert 'Could not send transactional email' in caplog.text

    def test_the_token_never_appears_in_the_logs(self, smtp, monkeypatch, caplog):
        """
        The log line identifies the user by primary key precisely so that the
        one thing nobody may write down - the live credential - is not in it.
        """

        def explode(self, *args, **kwargs):
            raise OSError('connection refused')

        monkeypatch.setattr('identity.email.EmailMessage.send', explode)

        with caplog.at_level(logging.DEBUG):
            send_password_reset_email(make_user(), RAW_TOKEN)

        assert RAW_TOKEN not in caplog.text

    def test_an_unexpected_exception_type_is_swallowed_too(self, smtp, monkeypatch):
        """
        Every backend raises its own type, and an enumerated allow-list would
        be a new failure mode of its own: the one exception type missing from
        the list would escape and turn a delivery problem into a 500 that
        *does* vary with whether the address has an account.
        """

        def explode(self, *args, **kwargs):
            raise RuntimeError('something nobody anticipated')

        monkeypatch.setattr('identity.email.EmailMessage.send', explode)

        send_activation_email(make_user(), RAW_TOKEN)  # must not raise

    def test_a_failed_send_still_leaves_the_flow_reporting_its_own_outcome(self, smtp, monkeypatch):
        """
        The consequence the design accepts, asserted so it stays a decision
        rather than becoming an accident: the caller gets the generic success
        it would have got anyway, and the account is untouched.
        """
        from identity.services import request_password_reset

        def explode(self, *args, **kwargs):
            raise OSError('connection refused')

        monkeypatch.setattr('identity.email.EmailMessage.send', explode)

        message = request_password_reset(make_user().email)

        assert message
        assert User.objects.count() == 1
