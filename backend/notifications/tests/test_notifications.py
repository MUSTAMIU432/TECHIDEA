"""
Notifications: one business event, two channels, neither of them business.

The claims in `notifications.models` and `notifications.services`, and each of
them is a claim somebody could quietly break:

1. **One event, two channels.** `deliver` writes the row and sends the email
   from one call. There is no "email notification" record and no second event, so
   nothing downstream reconciles them - and the list can never show an approval
   the email does not mention.
2. **Neither channel is inside the decision.** Both are registered on
   `on_commit`, so a decision that rolls back notifies nobody.
3. **Channel failure is not business failure.** The row write and the send are
   both swallowed. A mail outage must not become a failed approval, and a broken
   notification list must not undo somebody's go-ahead.
4. **A notification is not something a person wrote.** `title` and `body` are
   the platform's words about *what happened*, length-capped, and never the
   content of a review or a report - that stays behind authentication.
5. **It is yours or nothing.** There is no tenancy question to get wrong here,
   because the row carries the audience.

Which kinds may carry a report link is asserted too, since that is the
distinction between "a decision happened, here is the report" and "there is
something waiting in your queue".
"""

import pytest
from django.core import mail
from django.db.utils import IntegrityError

from ideas import services as idea_services
from ideas.models import Category, Idea
from identity.models import User
from notifications import selectors, services
from notifications.models import DECISION_KINDS, Notification

# `transaction=True` for the whole module, and not as a convenience: almost every
# claim in this file is about work that happens **after** the transaction that
# caused the event has committed, and `on_commit` callbacks never fire inside the
# rollback-at-the-end transaction a normal `django_db` test runs in. With a real
# commit, the tests below exercise the code the product actually runs - and
# `TestOnCommit` can prove the negative (a rolled-back decision notifies nobody)
# without needing a fixture to execute callbacks by hand.
pytestmark = pytest.mark.django_db(transaction=True)

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password=VALID_PASSWORD,
    )


@pytest.fixture
def recipient():
    return make_user('recipient@example.com')


@pytest.fixture
def colleague():
    return make_user('colleague@example.com')


@pytest.fixture
def idea():
    category = Category.objects.create(name='Notifications Fixture')
    return Idea.objects.create(
        title='An idea worth notifying about',
        description=DESCRIPTION,
        category=category,
        visibility=Idea.Visibility.PUBLIC,
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        author=make_user('author@example.com'),
    )


def deliver_now(**kwargs):
    """`deliver`, and the post-commit work it registers.

    A plain call: with `transaction=True` there is no enclosing transaction, so
    each `deliver` commits immediately and its `on_commit` hooks run before this
    returns. That is what makes the count assertions below about delivered
    notifications rather than about queued work.
    """
    return services.deliver(**kwargs)


# --- the happy path ---------------------------------------------------------------------


class TestOneEventTwoChannels:
    def test_it_writes_a_row_and_sends_an_email(self, recipient, idea, mailoutbox):
        deliver_now(
            recipients=recipient,
            kind='idea.platform_approved',
            title='"An idea worth notifying about" was approved',
            body='The platform review is complete. Read the report and decide.',
            idea=idea,
        )

        notification = Notification.objects.get()
        assert notification.user_id == recipient.pk
        assert notification.idea_id == idea.pk
        assert notification.is_read is False
        assert notification.report_id is None

        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == [recipient.email]
        # The subject is the notification's own title: one message for the event,
        # not one per notification.
        assert mailoutbox[0].subject == notification.title

    def test_it_returns_nothing_and_never_raises(self, recipient):
        """
        The return value is the notifications that were written - which is empty,
        because the write has not happened yet - and never an exception. Callers
        therefore have no failure case to handle, which is what stops a mail
        outage from becoming a failed approval somewhere else.
        """
        assert (
            deliver_now(recipients=recipient, kind='idea.platform_approved', title='t', body='b')
            == []
        )

    def test_everybody_in_the_list_is_told_once(self, recipient, colleague, mailoutbox):
        deliver_now(
            recipients=[recipient, colleague],
            kind='idea.platform_approved',
            title='Approved',
            body='Read the report.',
        )

        assert Notification.objects.count() == 2
        assert len(mailoutbox) == 2

    def test_a_single_user_does_not_have_to_be_wrapped_in_a_list(self, recipient):
        deliver_now(recipients=recipient, kind='review.assigned', title='Assigned', body='Open it.')

        assert Notification.objects.get().user_id == recipient.pk

    def test_nobody_to_tell_is_not_an_error(self, mailoutbox):
        for recipients in ([], (), None, [None]):
            assert (
                deliver_now(
                    recipients=recipients, kind='review.platform_queue', title='t', body='b'
                )
                == []
            )

        assert Notification.objects.count() == 0
        assert mailoutbox == []

    def test_a_deactivated_recipient_is_skipped(self, recipient, mailoutbox):
        recipient.is_active = False
        recipient.save(update_fields=['is_active'])

        deliver_now(recipients=recipient, kind='review.platform_queue', title='t', body='b')

        assert Notification.objects.count() == 0
        assert mailoutbox == []

    def test_send_email_false_still_writes_the_row(self, recipient, mailoutbox):
        """
        For the kinds where an email would be noise. The in-app notification is
        the record; the email is only ever a nudge at it.
        """
        deliver_now(
            recipients=recipient,
            kind='message.received',
            title='New message',
            body='Somebody sent you a message.',
            send_email=False,
        )

        assert Notification.objects.count() == 1
        assert mailoutbox == []

    def test_one_email_per_recipient_rather_than_one_per_notification(self, recipient, mailoutbox):
        """A platform approval with four unread items is one email, not four."""
        for index in range(4):
            deliver_now(
                recipients=recipient,
                kind='idea.platform_approved',
                title=f'Approved {index}',
                body='Read the report.',
            )

        assert Notification.objects.count() == 4
        assert len(mailoutbox) == 4
        # The last one sent names the newest notification, because each email is
        # written from the unread list at the moment it is sent.
        assert mailoutbox[-1].subject == 'Approved 3'


# --- post-commit ------------------------------------------------------------------------


class TestOnCommit:
    def test_a_rolled_back_decision_notifies_nobody(self, recipient, mailoutbox):
        """
        The whole reason delivery is on `on_commit`: an approval that rolled back
        must not have told anybody it was approved.
        """
        from django.db import transaction

        def approve_then_fail():
            services.deliver(
                recipients=recipient,
                kind='idea.platform_approved',
                title='Approved',
                body='Read the report.',
            )
            raise RuntimeError('the decision rolled back')

        with pytest.raises(RuntimeError), transaction.atomic():
            approve_then_fail()

        assert Notification.objects.count() == 0
        assert mailoutbox == []

    def test_the_email_is_sent_from_the_committed_rows(self, recipient, mailoutbox):
        """
        It reads the unread list when it runs, not a list captured earlier - so a
        notification that was somehow marked read in between produces no email
        claiming there is something waiting.
        """
        from django.db import transaction

        with transaction.atomic():
            services.deliver(
                recipients=recipient,
                kind='idea.platform_approved',
                title='Approved',
                body='Read the report.',
            )

        assert Notification.objects.count() == 1
        assert len(mailoutbox) == 1

    def test_deliver_itself_writes_nothing(self, recipient):
        """
        The separation, stated directly: calling `deliver` registers work; it does
        not do it. Asserted without committing so the "after" state is visible.
        """
        from django.db import transaction

        with transaction.atomic():
            services.deliver(
                recipients=recipient, kind='review.assigned', title='Assigned', body='Open it.'
            )

            assert Notification.objects.count() == 0


# --- failure is never business failure ---------------------------------------------------


class TestFailures:
    def test_a_broken_mail_backend_does_not_take_the_notification_with_it(
        self, recipient, monkeypatch
    ):
        """
        If the mail backend is down the notification still exists. A person who
        cannot be emailed is still somebody who was told.
        """
        from django.core.mail import EmailMessage

        monkeypatch.setattr(
            EmailMessage, 'send', lambda *a, **k: (_ for _ in ()).throw(OSError('no route'))
        )

        deliver_now(recipients=recipient, kind='idea.platform_approved', title='t', body='b')

        assert Notification.objects.count() == 1

    def test_a_broken_row_write_does_not_raise_into_the_decision(self, recipient, monkeypatch):
        """
        The other half: if the notification list cannot be written the business
        event still stands. `deliver` raises nothing, so the caller - an approval
        service, a go-ahead - cannot fail because a convenience table did.
        """
        monkeypatch.setattr(
            Notification.objects,
            'bulk_create',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('table is locked')),
        )

        assert (
            deliver_now(recipients=recipient, kind='idea.platform_approved', title='t', body='b')
            == []
        )
        assert Notification.objects.count() == 0

    def test_a_malformed_kind_is_refused_by_the_database(self, recipient):
        """
        A closed vocabulary, checked by the database rather than by the caller
        being careful - the same rule the ideas and organizations vocabularies
        follow, and for the same reason: `choices` alone only covers `full_clean`.
        """
        with pytest.raises(IntegrityError):
            Notification.objects.create(user=recipient, kind='idea.exploded', title='t', body='b')


# --- what may be in a notification ---------------------------------------------------------


class TestWhatGoesIn:
    def test_a_long_body_is_truncated_rather_than_refused(self, recipient):
        """
        The limit is enforced at the boundary, not trusted. A caller that pastes a
        review's feedback in gets a truncation rather than a full report in
        somebody's inbox - and the decision it was reporting is not held up while
        somebody argues about a column length.
        """
        deliver_now(
            recipients=recipient,
            kind='idea.platform_changes_requested',
            title='t',
            body='x' * 900,
        )

        assert len(Notification.objects.get().body) == services.BODY_MAX_LENGTH

    def test_a_long_title_is_truncated_too(self, recipient):
        deliver_now(recipients=recipient, kind='review.assigned', title='y' * 400, body='b')

        assert len(Notification.objects.get().title) == services.TITLE_MAX_LENGTH

    def test_only_a_decision_kind_may_carry_a_report(self):
        """
        The distinction that stops a "somebody is waiting in your queue" nudge from
        pointing at an approval report. Asserted against the vocabulary rather than
        against a call, because it is a property of the kinds.
        """
        assert 'idea.platform_approved' in DECISION_KINDS
        assert 'review.platform_queue' not in DECISION_KINDS
        assert 'review.organization_queue' not in DECISION_KINDS
        assert 'review.assigned' not in DECISION_KINDS
        assert not any(kind.startswith('review.') for kind in DECISION_KINDS)

    def test_a_report_link_needs_an_idea(self, recipient):
        """
        One direction only: a report always belongs to an idea, but an idea with
        no report is ordinary (the email may have failed, or the review may not
        have been an approval).
        """
        with pytest.raises(IntegrityError):
            Notification.objects.create(
                user=recipient,
                kind='idea.platform_approved',
                title='t',
                body='b',
                report_id=999999,
            )

    def test_an_invitation_notification_needs_no_idea_at_all(self, recipient):
        # pylint: disable=too-many-locals
        """
        `idea` is nullable for exactly this: "you have been invited" is about
        something that is not an idea, and `SET_NULL` means deleting the idea later
        does not un-tell the recipient.
        """
        deliver_now(
            recipients=recipient, kind='invitation.received', title='Invited', body='Open it.'
        )

        notification = Notification.objects.get()
        assert notification.idea_id is None

        idea = idea_services.create_idea_in_context(
            recipient,
            idea_services.IdeaInput(
                title='Mine',
                description=DESCRIPTION,
                visibility=Idea.Visibility.PUBLIC,
                category_id=Category.objects.create(name='Later').pk,
            ),
            submission_context=Idea.SubmissionContext.INDIVIDUAL,
        )
        idea.delete()

        assert Notification.objects.filter(pk=notification.pk).exists()


# --- reading it back ---------------------------------------------------------------------


class TestSelectors:
    def test_a_person_reads_their_own_newest_first(self, recipient):
        for index in range(3):
            deliver_now(
                recipients=recipient, kind='review.assigned', title=f'Seen {index}', body='b'
            )

        page = selectors.list_notifications(recipient)

        assert [item.title for item in page.items] == ['Seen 2', 'Seen 1', 'Seen 0']

    def test_somebody_elses_notification_is_not_resolvable_by_its_id(self, recipient, colleague):
        deliver_now(recipients=recipient, kind='review.assigned', title='Mine', body='b')
        notification = Notification.objects.get()

        assert selectors.get_notification(recipient, notification.pk) is not None
        assert selectors.get_notification(colleague, notification.pk) is None
        assert selectors.list_notifications(colleague).items == []

    def test_unread_only_narrows_and_never_widens(self, recipient, colleague):
        """
        The flag is applied *after* the recipient filter, so it can only ever make
        the list shorter. Pinned with somebody else's notification in the table,
        because a filter that widened instead would show up as a row belonging to
        a person who was never asked for it.
        """
        for title in ('First', 'Second'):
            deliver_now(recipients=recipient, kind='review.assigned', title=title, body='b')
        deliver_now(recipients=colleague, kind='review.assigned', title='Theirs', body='b')

        newest = Notification.objects.filter(user=recipient, title='Second').get()
        selectors.mark_read(recipient, newest.pk)

        assert selectors.unread_count(recipient) == 1
        assert [
            item.title for item in selectors.list_notifications(recipient, unread_only=True).items
        ] == ['First']
        assert [item.title for item in selectors.list_notifications(recipient).items] == [
            'Second',
            'First',
        ]
        # The colleague's own unread row is untouched and unseeable.
        assert selectors.unread_count(colleague) == 1
        assert 'Theirs' not in {
            item.title for item in selectors.list_notifications(recipient, unread_only=True).items
        }

    def test_marking_read_is_idempotent(self, recipient):
        deliver_now(recipients=recipient, kind='review.assigned', title='Mine', body='b')
        notification = Notification.objects.get()

        first = selectors.mark_read(recipient, notification.pk)
        moment = first.is_read_at
        second = selectors.mark_read(recipient, notification.pk)

        # Writing the moment again would move the instant somebody "noticed" it,
        # which is a small lie about a past event.
        assert second.is_read_at == moment
        assert moment is not None

    def test_somebody_elses_notification_cannot_be_marked_read_by_id(self, recipient, colleague):
        deliver_now(recipients=recipient, kind='review.assigned', title='Mine', body='b')
        notification = Notification.objects.get()

        assert selectors.mark_read(colleague, notification.pk) is None
        assert Notification.objects.get(pk=notification.pk).is_read_at is None
        assert services.mark_all_read(colleague) == 0
        assert Notification.objects.get(pk=notification.pk).is_read_at is None

    def test_mark_all_read_is_one_intent(self, recipient, colleague):
        for _ in range(3):
            deliver_now(recipients=recipient, kind='review.assigned', title='Mine', body='b')
        deliver_now(recipients=colleague, kind='review.assigned', title='Theirs', body='b')

        assert services.mark_all_read(recipient) == 3
        assert selectors.unread_count(recipient) == 0
        assert selectors.unread_count(colleague) == 1

    def test_an_anonymous_or_inactive_caller_reads_nothing(self, recipient):
        deliver_now(recipients=recipient, kind='review.assigned', title='Mine', body='b')
        notification = Notification.objects.get()
        recipient.is_active = False
        recipient.save(update_fields=['is_active'])

        assert selectors.list_notifications(None).items == []
        assert selectors.list_notifications(recipient).items == []
        assert selectors.unread_count(None) == 0
        assert selectors.unread_count(recipient) == 0
        assert selectors.get_notification(None, notification.pk) is None
        assert selectors.mark_read(recipient, notification.pk) is None
        assert services.mark_all_read(None) == 0
        assert services.mark_all_read(recipient) == 0

    @pytest.mark.parametrize('notification_id', ['nope', None, ''])
    def test_malformed_ids_are_refused_rather_than_raising(self, recipient, notification_id):
        assert selectors.get_notification(recipient, notification_id) is None


@pytest.fixture
def mailoutbox():
    """The outgoing mail, emptied on both sides so no test inherits another's."""
    outbox = mail.outbox
    outbox.clear()
    yield outbox
    outbox.clear()
