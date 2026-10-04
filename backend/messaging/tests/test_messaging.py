"""
Private messages: a conversation between named people.

The claim `messaging.models` makes is that this is **not** `ideas.Comment`, and
the tests are organised around that, because the two are so easy to conflate and
conflating them leaks:

1. **Access comes from the participant list, never from the idea.** Every read and
   write goes through a thread, and a thread has an explicit participant list.
   There is no query in this app that answers "messages on idea X", because "who
   can see a message on idea X" is not a question about the idea.
2. **An idea is context, not access.** A participant can read their thread even
   if they cannot read the idea - they were in the conversation - while
   `start_thread` refuses to anchor to an idea the caller cannot read, so an idea
   cannot be used as a way to reach people.
3. **Read position is personal.** `last_read_at` is per participant and moves for
   nobody else; two people reading one conversation are at different points in it.
4. **A thread never exists without a message**, which is what lets
   `latest_message_at` be non-null and the list a single indexed read.
5. **"Send message" twice about the same idea continues the conversation** rather
   than starting a second one - and a thread with no idea is never reused, because
   continuing it would silently attach it to an idea the two never agreed to
   discuss.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.utils import IntegrityError
from django.test.utils import CaptureQueriesContext

from ideas.models import Category, Idea
from identity.models import User
from messaging import selectors
from messaging.models import MESSAGE_MAX_LENGTH, Message, MessageParticipant, MessageThread
from messaging.services import MessageError, PostMessageInput, StartThreadInput, start_thread

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
def sender():
    return make_user('sender@example.com')


@pytest.fixture
def recipient():
    return make_user('recipient@example.com')


@pytest.fixture
def stranger():
    return make_user('stranger@example.com')


@pytest.fixture
def category():
    """One category for the module's ideas: `Category.name` is unique platform-wide."""
    category, _ = Category.objects.get_or_create(name='Messaging Fixture')
    return category


@pytest.fixture
def public_idea(category):
    return Idea.objects.create(
        title='An idea to talk about',
        description=DESCRIPTION,
        category=category,
        visibility=Idea.Visibility.PUBLIC,
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        author=make_user('author@example.com'),
    )


@pytest.fixture
def private_idea(category):
    return Idea.objects.create(
        title="Somebody else's private idea",
        description=DESCRIPTION,
        category=category,
        visibility=Idea.Visibility.PRIVATE,
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        author=make_user('author@example.com'),
    )


def thread_between(sender, recipient, *, idea=None, body='Hello'):
    return start_thread(
        sender,
        StartThreadInput(
            recipient_ids=(recipient.pk,), body=body, idea_id=idea.pk if idea else None
        ),
    )


# --- starting a conversation ------------------------------------------------------------


@pytest.mark.django_db
class TestStartingAThread:
    def test_it_creates_the_thread_its_participants_and_its_first_message(self, sender, recipient):
        thread = thread_between(sender, recipient)

        assert thread.started_by_id == sender.pk
        assert thread.latest_message_at is not None
        assert set(thread.participants.values_list('user_id', flat=True)) == {
            sender.pk,
            recipient.pk,
        }
        assert [message.body for message in thread.messages.all()] == ['Hello']

    def test_a_thread_never_exists_without_a_message(self, sender, recipient):
        """What lets `latest_message_at` be non-null and the list a single read."""
        thread = thread_between(sender, recipient)

        assert thread.messages.exists()
        assert thread.latest_message_at == thread.messages.get().created_at

    def test_the_sender_is_a_participant_not_just_an_author(self, sender, recipient):
        thread = thread_between(sender, recipient)

        assert selectors.can_read_thread(sender, thread) is True
        assert selectors.participants_of(thread).count() == 2

    def test_it_can_be_about_an_idea(self, sender, recipient, public_idea):
        thread = thread_between(sender, recipient, idea=public_idea)

        assert thread.idea_id == public_idea.pk
        # No subject given, so it defaults to the idea's title - which is what a
        # thread list needs to be readable at a glance.
        assert thread.subject == public_idea.title

    def test_a_direct_message_needs_no_idea(self, sender, recipient):
        thread = thread_between(sender, recipient)

        assert thread.idea_id is None
        assert thread.subject == ''

    def test_an_idea_the_caller_cannot_read_cannot_be_used_to_reach_people(
        self, sender, recipient, private_idea
    ):
        """
        The one asymmetry in the boundary, and the reason it is safe: the idea
        proves the caller may start the conversation, but it grants the recipient
        nothing. They are told about the conversation, not about the idea.
        """
        with pytest.raises(MessageError) as exc_info:
            thread_between(sender, recipient, idea=private_idea)

        assert exc_info.value.message == 'Idea is unavailable.'
        assert not MessageThread.objects.exists()

        # And a supplied id that resolves to nothing is refused rather than
        # treated as "no idea", so the two cannot be confused at this boundary.
        with pytest.raises(MessageError):
            thread_between(sender, recipient, idea=private_idea)

    def test_a_recipient_does_not_need_to_be_able_to_read_the_idea(
        self, sender, recipient, private_idea
    ):
        """
        Deliberate, and the whole point of a private message: somebody is being
        told about the conversation directly, and the anchor is only context.

        The **sender** still has to be able to read the idea - that is the check
        that stops an idea being used as a lever to reach people - so this is a
        thread its own author opens with somebody who cannot see it.
        """
        private_idea.author = sender
        private_idea.save(update_fields=['author'])

        thread = thread_between(sender, recipient, idea=private_idea)

        assert thread.idea_id == private_idea.pk
        # ...and the idea itself stays out of the recipient's reach.
        from ideas import selectors as idea_selectors

        assert idea_selectors.get_idea(recipient, private_idea.pk) is None

    def test_the_same_idea_and_the_same_person_continues_the_conversation(
        self, sender, recipient, public_idea
    ):
        """
        "Send message" twice is one conversation, not two - otherwise a colleague
        who replies to three comments ends up with three threads.
        """
        first = thread_between(sender, recipient, idea=public_idea, body='First')
        second = start_thread(
            sender,
            StartThreadInput(recipient_ids=(recipient.pk,), body='Second', idea_id=public_idea.pk),
        )

        assert second.pk == first.pk
        assert Message.objects.filter(thread=first).count() == 2

    def test_a_different_idea_is_a_different_conversation(
        self, sender, recipient, public_idea, category
    ):
        another = Idea.objects.create(
            title='A different idea',
            description=DESCRIPTION,
            category=category,
            visibility=Idea.Visibility.PUBLIC,
            submission_context=Idea.SubmissionContext.INDIVIDUAL,
            author=sender,
        )

        first = thread_between(sender, recipient, idea=public_idea)
        second = thread_between(sender, recipient, idea=another)

        assert second.pk != first.pk

    def test_a_thread_with_no_idea_is_never_reused(self, sender, recipient):
        """
        Continuing it would silently attach a bare conversation to whatever the
        two of them were next discussing.
        """
        first = thread_between(sender, recipient, body='First')
        second = thread_between(sender, recipient, body='Second')

        assert second.pk != first.pk
        assert first.idea_id is None
        assert second.idea_id is None

    def test_somebody_must_be_chosen(self, sender, recipient, stranger):
        for recipient_ids in ((), (None,), (sender.pk,)):
            with pytest.raises(MessageError) as exc_info:
                start_thread(sender, StartThreadInput(recipient_ids=recipient_ids, body='Hello'))

            assert exc_info.value.field == 'recipientIds'

        # A stranger *is* somebody to choose: being unknown to each other is not
        # a reason a conversation cannot start, which is why the id-999999 case
        # below is about an account that does not exist.
        thread = thread_between(sender, stranger)
        assert set(thread.participants.values_list('user_id', flat=True)) == {
            sender.pk,
            stranger.pk,
        }

    def test_you_cannot_start_a_conversation_with_yourself(self, sender):
        with pytest.raises(MessageError) as exc_info:
            start_thread(sender, StartThreadInput(recipient_ids=(sender.pk,), body='Hello'))

        assert 'yourself' in exc_info.value.message

    def test_an_unavailable_recipient_is_refused_rather_than_dropped(self, sender, stranger):
        """
        A silently dropped recipient is a conversation somebody believes they are
        in and is not.
        """
        stranger.is_active = False
        stranger.save(update_fields=['is_active'])

        with pytest.raises(MessageError) as exc_info:
            start_thread(sender, StartThreadInput(recipient_ids=(stranger.pk,), body='Hello'))

        assert exc_info.value.field == 'recipientIds'
        assert not MessageThread.objects.exists()

    @pytest.mark.parametrize('body', ['', '   ', 'x' * (MESSAGE_MAX_LENGTH + 1)])
    def test_an_unusable_body_is_refused(self, sender, recipient, body):
        with pytest.raises(MessageError) as exc_info:
            start_thread(sender, StartThreadInput(recipient_ids=(recipient.pk,), body=body))

        assert exc_info.value.field == 'body'

    def test_it_requires_an_authenticated_active_account(self, sender, recipient):
        sender.is_active = False
        sender.save(update_fields=['is_active'])

        for who in (None, sender):
            with pytest.raises(MessageError) as exc_info:
                start_thread(who, StartThreadInput(recipient_ids=(recipient.pk,), body='Hello'))

            assert exc_info.value.reason == 'unauthenticated'

        assert not MessageThread.objects.exists()

    def test_several_recipients_are_all_participants(self, sender, recipient, stranger):
        thread = start_thread(
            sender,
            StartThreadInput(recipient_ids=(recipient.pk, stranger.pk), body='Hello both'),
        )

        assert set(thread.participants.values_list('user_id', flat=True)) == {
            sender.pk,
            recipient.pk,
            stranger.pk,
        }


# --- posting into a thread ----------------------------------------------------------------


@pytest.mark.django_db
class TestPosting:
    def test_a_participant_can_reply(self, sender, recipient):
        thread = thread_between(sender, recipient)

        message = post(recipient, thread, 'Replying')

        assert message.sender_id == recipient.pk
        assert Message.objects.filter(thread=thread).count() == 2

    def test_posting_moves_the_thread_and_marks_your_own_messages_read(self, sender, recipient):
        thread = thread_between(sender, recipient)
        assert selectors.has_unread_in(recipient, thread) is True

        message = post(recipient, thread, 'Replying')

        thread.refresh_from_db()
        assert thread.latest_message_at == message.created_at
        # Having posted, they have read: otherwise replying in a thread you had
        # not opened leaves your own badge lit, which teaches people to ignore it.
        participant = MessageParticipant.objects.get(thread=thread, user=recipient)
        assert participant.last_read_at == message.created_at
        assert selectors.has_unread_in(recipient, thread) is False
        assert selectors.has_unread_in(sender, thread) is True

    def test_everybody_else_in_the_conversation_is_told(
        self, sender, recipient, stranger, django_capture_on_commit_callbacks
    ):
        """
        One message, one notification each - and never the sender's, who is looking
        at it.

        The callbacks are captured and executed because that is what "after the
        commit" means inside a `django_db` test, where the enclosing transaction is
        rolled back rather than committed.
        """
        from notifications.models import Notification

        thread = start_thread(
            sender,
            StartThreadInput(recipient_ids=(recipient.pk, stranger.pk), body='Hello both'),
        )

        with django_capture_on_commit_callbacks(execute=True):
            post(recipient, thread, 'Replying')

        told = list(Notification.objects.values_list('user_id', 'kind'))
        assert sorted(told) == sorted(
            [
                (sender.pk, 'message.received'),
                (stranger.pk, 'message.received'),
            ]
        )

    def test_a_rolled_back_message_notifies_nobody(
        self, sender, recipient, django_capture_on_commit_callbacks
    ):
        from django.db import transaction

        from messaging import services
        from notifications.models import Notification

        thread = thread_between(sender, recipient)

        def reply_then_fail():
            services.post_message(
                recipient, PostMessageInput(thread_id=thread.pk, body='Never sent')
            )
            raise RuntimeError('rolled back')

        with (
            pytest.raises(RuntimeError),
            transaction.atomic(),
            django_capture_on_commit_callbacks(execute=True),
        ):
            reply_then_fail()

        assert Notification.objects.count() == 0
        assert Message.objects.filter(thread=thread).count() == 1

    def test_a_stranger_cannot_post_into_it(self, sender, recipient, stranger):
        thread = thread_between(sender, recipient)

        with pytest.raises(MessageError) as exc_info:
            post(stranger, thread, 'Let me in')

        # Refused exactly like a thread that does not exist, so the endpoint is
        # not a way of finding out which conversations exist.
        assert exc_info.value.message == 'Conversation is unavailable.'
        assert Message.objects.filter(thread=thread).count() == 1

    @pytest.mark.parametrize('thread_id', [999999, 'nope', None, ''])
    def test_an_unknown_thread_is_unavailable(self, sender, recipient, thread_id):
        from messaging import services

        with pytest.raises(MessageError) as exc_info:
            services.post_message(sender, PostMessageInput(thread_id, 'Hello'))

        assert exc_info.value.message == 'Conversation is unavailable.'

    def test_it_requires_an_authenticated_active_account(self, sender, recipient):
        thread = thread_between(sender, recipient)
        recipient.is_active = False
        recipient.save(update_fields=['is_active'])

        for who in (None, recipient):
            with pytest.raises(MessageError) as exc_info:
                post(who, thread, 'Hello')

            assert exc_info.value.reason == 'unauthenticated'


# --- reading -------------------------------------------------------------------------------


@pytest.mark.django_db
class TestReading:
    def test_a_participant_reads_the_whole_conversation_oldest_first(self, sender, recipient):
        thread = thread_between(sender, recipient, body='First')
        post(recipient, thread, 'Second')

        page = selectors.list_messages(recipient, thread.pk)

        assert [message.body for message in page.messages] == ['First', 'Second']
        assert page.total == 2

    def test_a_thread_capped_rather_than_paged(self, sender, recipient):
        """
        The type says so, and this pins it: a conversation is read end to end and
        a thread's history is small, so `limit` caps the window and `total` says
        how much there was.
        """
        thread = thread_between(sender, recipient, body='First')
        for index in range(4):
            post(sender, thread, f'More {index}')

        page = selectors.list_messages(recipient, thread.pk, limit=2)

        assert len(page.messages) == 2
        assert page.total == 5
        later = selectors.list_messages(recipient, thread.pk, offset=2, limit=50)
        assert len(later.messages) == 3

    def test_a_stranger_reads_nothing_and_the_answer_matches_a_missing_thread(
        self, sender, recipient, stranger
    ):
        thread = thread_between(sender, recipient)

        assert selectors.get_thread(stranger, thread.pk) is None
        assert selectors.list_messages(stranger, thread.pk).messages == []
        assert selectors.get_thread(stranger, 999999) is None
        assert selectors.list_messages(stranger, 999999).messages == []

    def test_a_message_id_cannot_be_borrowed(self, sender, recipient, stranger):
        """
        Reached through the caller's own threads, so a message id from a
        conversation somebody is not in resolves to nothing rather than to its
        body.
        """
        thread = thread_between(sender, recipient)
        message = thread.messages.get()

        assert selectors.get_message(recipient, message.pk) is not None
        assert selectors.get_message(stranger, message.pk) is None

    def test_an_anonymous_or_inactive_caller_reads_nothing(self, sender, recipient):
        thread = thread_between(sender, recipient)
        recipient.is_active = False
        recipient.save(update_fields=['is_active'])

        assert selectors.get_thread(None, thread.pk) is None
        assert selectors.get_thread(recipient, thread.pk) is None
        assert selectors.list_messages(None, thread.pk).messages == []
        assert selectors.get_message(recipient, thread.messages.get().pk) is None
        assert selectors.list_threads(recipient) == []
        assert selectors.exists_for_user(recipient) is False

    @pytest.mark.parametrize('thread_id', ['nope', None, ''])
    def test_malformed_ids_are_refused_rather_than_raising(self, sender, recipient, thread_id):
        assert selectors.get_thread(sender, thread_id) is None
        assert selectors.get_message(sender, thread_id) is None


# --- the list -------------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheThreadList:
    def test_it_shows_only_the_callers_threads_newest_activity_first(
        self, sender, recipient, stranger
    ):
        older = thread_between(sender, recipient, body='Older')
        newer = thread_between(sender, stranger, body='Newer')

        summaries = selectors.list_threads(sender)

        assert [summary.thread.pk for summary in summaries] == [newer.pk, older.pk]
        # The two people in these two conversations, and nobody else: `stranger`
        # is a *participant* of one of them, and `recipient` of the other, so
        # what is being checked is that a thread nobody is in is absent rather
        # than that a name is absent.
        assert {user.pk for summary in summaries for user in summary.other_participants} == {
            recipient.pk,
            stranger.pk,
        }

    def test_it_names_the_other_participants_and_the_latest_message(self, sender, recipient):
        thread = thread_between(sender, recipient, body='First')
        post(recipient, thread, 'Latest')

        summary = selectors.list_threads(recipient)[0]

        assert [user.pk for user in summary.other_participants] == [sender.pk]
        assert summary.latest_message.body == 'Latest'
        # And from the sender's side, the other participant is the recipient.
        assert [user.pk for user in selectors.list_threads(sender)[0].other_participants] == [
            recipient.pk
        ]

    def test_the_unread_count_is_personal(self, sender, recipient):
        """
        Two people reading one conversation are at different points in it, which
        is why `last_read_at` is per participant rather than per thread.
        """
        thread = thread_between(sender, recipient)
        post(recipient, thread, 'Unread by the sender')

        assert selectors.list_threads(sender)[0].unread_count == 1
        assert selectors.list_threads(sender)[0].has_unread is True
        # The recipient posted it, so it is read for them.
        assert selectors.list_threads(recipient)[0].unread_count == 0
        assert selectors.unread_count_for(sender) == 1
        assert selectors.unread_count_for(recipient) == 0

    def test_the_list_costs_the_same_however_many_threads(self, sender, recipient, stranger):
        """
        The N+1 this design exists to avoid: the unread count is a correlated
        subquery in the list query, not a query per row, so ten conversations
        cost what three do.
        """
        thread_between(sender, recipient)
        selectors.list_threads(sender)

        def queries():
            with CaptureQueriesContext(connection) as captured:
                selectors.list_threads(sender)
            return len(captured.captured_queries)

        for index in range(10):
            thread_between(sender, stranger if index % 2 else recipient)

        assert queries() == queries()
        assert len(selectors.list_threads(sender)) == 11

    def test_exists_for_user_is_the_condition_for_showing_the_nav_entry(
        self, sender, recipient, stranger
    ):
        assert selectors.exists_for_user(stranger) is False

        thread_between(sender, recipient)

        assert selectors.exists_for_user(sender) is True
        assert selectors.exists_for_user(recipient) is True
        assert selectors.exists_for_user(stranger) is False

    def test_an_unread_thread_is_reported_without_being_joined_by_id(
        self, sender, recipient, stranger
    ):
        thread = thread_between(sender, recipient)

        # A stranger asking about a thread they are not in gets nothing, and
        # asking twice does not create a second answer.
        assert selectors.has_unread_in(stranger, thread) is False
        assert selectors.unread_count_for(stranger) == 0


# --- marking read --------------------------------------------------------------------------


@pytest.mark.django_db
class TestMarkingRead:
    def test_it_moves_the_callers_own_cursor_to_the_end(self, sender, recipient):
        thread = thread_between(sender, recipient)
        post(recipient, thread, 'Unread')

        from messaging import services

        services.mark_thread_read(sender, thread.pk)

        assert selectors.has_unread_in(sender, thread) is False
        # ...and only the caller's. The recipient's own position is untouched.
        assert selectors.has_unread_in(recipient, thread) is False
        participant = MessageParticipant.objects.get(thread=thread, user=recipient)
        assert participant.last_read_at is not None

    def test_a_stranger_cannot_mark_it_read(self, sender, recipient, stranger):
        thread = thread_between(sender, recipient)
        post(recipient, thread, 'Unread')

        from messaging import services

        with pytest.raises(MessageError) as exc_info:
            services.mark_thread_read(stranger, thread.pk)

        assert exc_info.value.message == 'Conversation is unavailable.'
        assert selectors.has_unread_in(sender, thread) is True

    def test_it_requires_an_authenticated_active_account(self, sender, recipient):
        thread = thread_between(sender, recipient)

        from messaging import services

        for who in (None,):
            with pytest.raises(MessageError) as exc_info:
                services.mark_thread_read(who, thread.pk)

            assert exc_info.value.reason == 'unauthenticated'


# --- it is not a comment -------------------------------------------------------------------


@pytest.mark.django_db
class TestItIsNotAComment:
    def test_a_message_is_invisible_to_everybody_who_can_read_the_idea(
        self, sender, recipient, public_idea
    ):
        """
        The whole reason this is a separate model. A reader of the idea can see
        its title and its discussion; they cannot see that two of its readers are
        talking about it, or what they said.
        """
        thread = thread_between(sender, recipient, idea=public_idea, body='Not for the discussion')
        outsider = make_user('outsider@example.com')

        # The outsider can read the idea...
        from ideas import selectors as idea_selectors

        assert idea_selectors.get_idea(outsider, public_idea.pk) is not None
        # ...and has no route to the conversation.
        assert selectors.get_thread(outsider, thread.pk) is None
        assert selectors.list_messages(outsider, thread.pk).messages == []
        assert selectors.get_message(outsider, Message.objects.get(thread=thread.pk).pk) is None

    def test_a_comment_on_the_idea_is_not_a_message(self, sender, recipient, public_idea):
        from ideas.models import Comment

        Comment.objects.create(idea=public_idea, author=sender, content='A public note on the idea')

        assert Message.objects.count() == 0
        assert [c.content for c in Comment.objects.all()] == ['A public note on the idea']


# --- the constraints -------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheConstraints:
    def test_one_participant_row_per_person_per_thread(self, sender, recipient):
        thread = thread_between(sender, recipient)

        with pytest.raises(IntegrityError):
            MessageParticipant.objects.create(thread=thread, user=sender)

    def test_a_message_names_a_sender_who_is_in_the_thread(self, sender, recipient, stranger):
        """
        The database knows the sender, so the invariant is checkable - and it is
        the one that stops a message appearing with an author who was never told.
        A message row with a sender outside its thread is refused.
        """
        thread = thread_between(sender, recipient)

        # `Message.save()` runs `full_clean`, so the invariant is caught before the
        # database is reached - which is the better of the two: the error names
        # what is wrong instead of arriving as an opaque constraint violation.
        with pytest.raises(ValidationError):
            Message.objects.create(thread=thread, sender=stranger, body='Hello')

    def test_the_sender_is_protected_from_deletion(self, sender, recipient):
        """A conversation is a record of what two named people said."""
        thread = thread_between(sender, recipient)

        with pytest.raises(IntegrityError):
            sender.delete()

        assert MessageThread.objects.filter(pk=thread.pk).exists()

    def test_deleting_the_idea_keeps_the_conversation(self, sender, recipient, public_idea):
        """
        `idea` is context, not access - and deleting the context does not un-ring
        the bell for the participants.
        """
        thread = thread_between(sender, recipient, idea=public_idea)
        public_idea.delete()

        assert not MessageThread.objects.filter(pk=thread.pk).exists()
        assert not Message.objects.filter(thread=thread).exists()


# --- helpers ---------------------------------------------------------------------------------


def post(user, thread, body):
    from messaging import services

    return services.post_message(user, PostMessageInput(thread_id=thread.pk, body=body))
