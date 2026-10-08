"""
Private messages between two people, optionally about one idea.

Three entities:

    MessageThread       a conversation, anchored to an idea or not.
    MessageParticipant  who is in a thread, and how far they have read.
    Message             one message in a thread.

**Why this is not `ideas.Comment`.** A comment is public engagement on an idea:
everybody who can read the idea can read it, it is part of the discussion, and it
is addressed to whoever is following the idea. A message is addressed to named
people. Turning "Send Message" into a comment would mean a private conversation
posted where the author's colleagues can read it - so the two are different
models with different authorization, not one model with a flag.

The boundary is enforced in the only place it matters, `messaging.selectors`:
every read and write goes through a *thread*, and a thread has an explicit
participant list. There is no query in this app that answers "messages on idea
X", because "who can see a message on idea X" is not a question about the idea
- it is a question about who was invited into the conversation.

`idea` is nullable and is **context, not access**: putting a thread on an idea
gives both participants somewhere to talk about it and nothing else. Read
authorization never consults it - a participant can read their own thread even
if they cannot read the idea, which is deliberate (they were in the
conversation), while `start_thread` refuses to anchor a thread to an idea the
caller cannot read, so an idea cannot be used as a way to reach people.
"""

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

MESSAGE_MAX_LENGTH = 5000


class MessageThread(models.Model):
    """
    One conversation between two or more named participants.

    A thread with no messages is refused by `messaging.services.start_thread`,
    which creates the thread and the first message in one transaction - so
    `latest_message_at` is never null and "my threads, newest activity first"
    is a single indexed read with no join and no subquery.
    """

    idea = models.ForeignKey(
        'ideas.Idea',
        on_delete=models.CASCADE,
        related_name='message_threads',
        null=True,
        blank=True,
        help_text='Context only. Read access comes from the participant list, never from this.',
    )
    subject = models.CharField(
        max_length=200,
        blank=True,
        help_text='Optional. Defaults to the idea title, or is left blank for a direct message.',
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='started_message_threads',
    )
    latest_message_at = models.DateTimeField(
        help_text=(
            'When the last message was posted. Null until then - never null on a real thread.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-latest_message_at', '-pk']

    def __str__(self) -> str:
        return self.subject or f'Thread {self.pk}'


class MessageParticipant(models.Model):
    """
    One person in one thread, and how far they have read it.

    `last_read_at` is per-participant and is the only field on this table that
    changes - it is that reader's own business and nothing anybody else can
    observe in a way that matters. Everything else about a thread is shared.

    The `(thread, user)` uniqueness is a **database** constraint, so two
    concurrent requests to open the same conversation cannot both add somebody
    twice, whatever order they arrive in.
    """

    thread = models.ForeignKey(
        MessageThread,
        on_delete=models.CASCADE,
        related_name='participants',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_participants',
    )
    last_read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at', 'pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['thread', 'user'],
                name='unique_message_participant_per_thread',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # "What conversations am I in", newest joined first.
            models.Index(fields=['user', '-created_at'], name='msgpart_user_created_idx'),
            # The join probe behind `selectors._participant_thread_ids`: a
            # thread id list for one user, which is the only way any read in this
            # app reaches a thread.
            models.Index(fields=['thread', 'user'], name='msgpart_thread_user_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.user_id} in {self.thread_id}'

    @property
    def has_unread(self) -> bool:
        return self.last_read_at is None or self.last_read_at < self.thread.latest_message_at


class Message(models.Model):
    """
    One message, from one participant, to the thread.

    **Append-only**, like `reviews.Review` and `ideas.IdeaTransition`: a message
    somebody sent cannot be rewritten afterwards, and there is no edit. The only
    `Message` method that refuses is `save()` on a stored row and `delete()` -
    the cascade that removes a whole thread is still allowed, because removing a
    conversation removes it for everybody at once.

    `sender` must be a participant; the check is in `clean()` *and* re-asserted by
    the service, because `clean()` only guards writers that call it and the
    invariant is the security of the whole app.
    """

    thread = models.ForeignKey(
        MessageThread,
        on_delete=models.CASCADE,
        related_name='messages',
        db_index=False,  # Prefix of `messages_thread_created_idx` below.
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='sent_messages',
        db_index=False,  # Prefix of `messages_sender_created_idx` below.
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at', 'pk']
        indexes: ClassVar[list[models.Index]] = [
            # "One thread's conversation, oldest first", which is a range scan
            # on this pair; the FK index on `thread_id` alone cannot order it.
            models.Index(fields=['thread', 'created_at'], name='messages_thread_created_idx'),
            # "What has this person sent" - the sender link inside a thread is
            # select-related, but the administrative view is a range scan here.
            models.Index(fields=['sender', '-created_at'], name='messages_sender_created_idx'),
        ]

    def __str__(self) -> str:
        return f'Message(thread={self.thread_id}, sender={self.sender_id})'

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('A sent message cannot be changed.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('A sent message cannot be deleted.')

    def clean(self) -> None:
        super().clean()
        if not (self.body or '').strip():
            raise ValidationError('A message must have content.')
        if len(self.body) > MESSAGE_MAX_LENGTH:
            raise ValidationError(f'A message must be at most {MESSAGE_MAX_LENGTH} characters.')

        # Defence in depth behind `messaging.services.post_message`: whatever a
        # caller authorized, the row refuses to exist unless its sender is in its
        # own thread. A message from a non-participant is a message a non-
        # participant could read, which is the one thing this app must not allow.
        if (
            self.thread_id is not None
            and self.sender_id is not None
            and not MessageParticipant.objects.filter(
                thread_id=self.thread_id,
                user_id=self.sender_id,
            ).exists()
        ):
            raise ValidationError('You can only send messages in a conversation you are part of.')


class MessageMeta:
    """
    A thread summary for a list: the last message, the other participants, and
    the reader's own unread state.

    A value rather than a queryset because it is built from three relations -
    the thread, the other participants and the last message - and returning them
    as one object is what lets a thread list render without the caller writing
    the "who else is in this and what did they last say" queries itself. Each
    piece is select-related or prefetched by `selectors.list_threads`, so a page
    of twenty threads is three queries rather than sixty.
    """

    __slots__ = ('has_unread', 'latest_message', 'other_participants', 'thread', 'unread_count')

    def __init__(
        self,
        thread: MessageThread,
        other_participants: list,
        latest_message: 'Message | None',
        unread_count: int,
        has_unread: bool,
    ):
        self.thread = thread
        self.other_participants = other_participants
        self.latest_message = latest_message
        self.unread_count = unread_count
        self.has_unread = has_unread
