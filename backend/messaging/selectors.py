"""
Reading private messages.

Every read goes through a *thread*, and a thread is reachable only through
`MessageParticipant`. There is deliberately no "messages on idea X" query in this
module, and that absence is the design: who may read a message is a question
about who was invited into the conversation, not about the idea it hangs from.
`start_thread` refuses to anchor a thread to an idea the caller cannot read, so
an idea cannot be used as a lever to reach people.

Everything here returns `None`, `[]` or an empty page for a caller who may not
see the thing, identically for "does not exist" and "not yours", so a thread or
message id cannot be used to discover that either exists.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from django.db.models import (
    Count,
    DateTimeField,
    IntegerField,
    OuterRef,
    Prefetch,
    QuerySet,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce

from identity.models import User
from messaging.models import Message, MessageParticipant, MessageThread


def _participant_thread_ids(user: User) -> QuerySet[MessageThread]:
    """The ids of every thread `user` is a participant of. The one access rule."""
    return MessageThread.objects.filter(participants__user=user).values('pk')


def get_thread(user: User | None, thread_id: object) -> MessageThread | None:
    """One thread `user` participates in, or `None`."""
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(thread_id))
    except (TypeError, ValueError):
        return None

    return (
        MessageThread.objects.select_related('idea', 'started_by')
        .prefetch_related(
            Prefetch(
                'participants',
                queryset=MessageParticipant.objects.select_related('user').order_by(
                    'created_at', 'pk'
                ),
            )
        )
        .filter(pk=normalized_id, pk__in=_participant_thread_ids(user))
        .first()
    )


def get_message(user: User | None, message_id: object) -> Message | None:
    """One message `user` may read, reached through their own threads only."""
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(message_id))
    except (TypeError, ValueError):
        return None

    return (
        Message.objects.select_related('sender', 'thread')
        .filter(pk=normalized_id, thread__in=_participant_thread_ids(user))
        .first()
    )


@dataclass(frozen=True)
class MessagePage:
    """One page of a thread's messages. Deliberately not Django's `Page`: a
    conversation is read end to end and a thread's whole history is small, so it
    is capped rather than paged, and saying so in the type keeps a caller from
    growing an assumption that messages are infinite."""

    messages: list[Message]
    total: int


def list_messages(
    user: User | None,
    thread_id: object,
    *,
    offset: object = 0,
    limit: object = 50,
) -> MessagePage:
    """
    One thread's conversation, oldest first, for a participant.

    An empty page for anybody who is not a participant - and for a thread that
    does not exist - so a thread id cannot be used to find out whether a
    conversation exists.
    """

    # Built here rather than through `ideas.pagination.empty_page`: that returns a
    # `Page`, and `MessagePage` is deliberately not a `Page` - it carries `total`
    # and no page info, because a conversation is read end to end. Asking
    # `empty_page` for it was a `TypeError`, so *every* read of a thread by
    # somebody who is not a participant raised rather than answering.
    def nothing() -> MessagePage:
        return MessagePage(messages=[], total=0)

    if user is None or not user.is_active:
        return nothing()

    thread = get_thread(user, thread_id)
    if thread is None:
        return nothing()

    try:
        normalized_offset = max(int(str(offset or 0)), 0)
    except (TypeError, ValueError):
        normalized_offset = 0
    try:
        normalized_limit = max(int(str(limit)), 1)
    except (TypeError, ValueError):
        normalized_limit = 50

    queryset = (
        Message.objects.select_related('sender').filter(thread=thread).order_by('created_at', 'pk')
    )
    total = queryset.count()
    rows = list(queryset[normalized_offset : normalized_offset + normalized_limit])
    return MessagePage(messages=rows, total=total)


#: The floor for "has this reader read anything in this thread". `created_at >
#: NULL` is NULL, which the database answers as *false*, so without a floor a
#: participant who has never opened a thread would have nothing unread in it -
#: precisely backwards. With the epoch as the floor, "never read" means the whole
#: conversation is waiting.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _unread_count_subquery() -> Coalesce:
    """
    How many messages in *this* thread the *viewer* has not read.

    One correlated subquery per thread, inside the list query - which is the whole
    point: a loop would make a message list cost one query per row, and the badge
    is read on every page.

    The comparison is against the viewer's own `last_read_at` for this thread,
    carried in as the `viewer_last_read_at` annotation. It is per participant
    rather than per thread because two people reading one conversation are at
    different points in it, and a shared cursor would make the badge lie to
    whichever of them was behind.
    """
    return Coalesce(
        Subquery(
            Message.objects.filter(
                thread=OuterRef('pk'),
                created_at__gt=Coalesce(OuterRef('viewer_last_read_at'), Value(_EPOCH)),
            )
            .order_by()
            .values('thread')
            .annotate(total=Count('*'))
            .values('total')[:1],
            output_field=IntegerField(),
        ),
        0,
    )


@dataclass(frozen=True)
class ThreadSummary:
    """One row of somebody's message list: who, what last, and whether unread."""

    thread: MessageThread
    other_participants: list[User]
    latest_message: Message | None
    unread_count: int
    has_unread: bool


def list_threads(user: User | None) -> list[ThreadSummary]:
    """
    Every thread `user` participates in, newest activity first.

    Three queries for any number of threads, and the annotation for the unread
    count is a correlated subquery rather than a loop - which is the difference
    between a message list that costs one round trip and one that costs one per
    row.

    The unread subquery counts messages in the thread posted after *this*
    participant's `last_read_at`, which is why `last_read_at` is per participant
    rather than per thread: two people reading the same conversation are at
    different points in it.
    """
    if user is None or not user.is_active:
        return []

    rows = (
        MessageThread.objects.filter(participants__user=user)
        .select_related('idea', 'started_by')
        .prefetch_related(
            Prefetch(
                'participants',
                queryset=MessageParticipant.objects.select_related('user').order_by(
                    'created_at', 'pk'
                ),
            ),
            Prefetch(
                'messages',
                queryset=Message.objects.select_related('sender')
                .order_by('-created_at', '-pk')
                .only('id', 'thread_id', 'sender_id', 'body', 'created_at'),
                to_attr='recent_messages',
            ),
        )
        .annotate(
            # Two annotations rather than one subquery nested inside the other's
            # filter. Nesting worked syntactically and counted the *wrong*
            # participant's read position for everybody after the first, because
            # `OuterRef` inside a nested subquery used as a filter *value* binds to
            # the inner query rather than the thread. Referencing the earlier
            # annotation keeps the correlation where it can be seen.
            viewer_last_read_at=Subquery(
                MessageParticipant.objects.filter(thread=OuterRef('pk'), user=user).values(
                    'last_read_at'
                )[:1],
                output_field=DateTimeField(),
            ),
            unread_count=_unread_count_subquery(),
            viewer_participant_id=Subquery(
                MessageParticipant.objects.filter(thread=OuterRef('pk'), user=user).values('id')[
                    :1
                ],
                output_field=IntegerField(),
            ),
        )
        .order_by('-latest_message_at', '-pk')
    )

    summaries: list[ThreadSummary] = []
    for thread in rows:
        participants = list(thread.participants.all())
        others = [link.user for link in participants if link.user_id != user.pk]
        recent = getattr(thread, 'recent_messages', [])
        summaries.append(
            ThreadSummary(
                thread=thread,
                other_participants=others,
                latest_message=recent[0] if recent else None,
                unread_count=thread.unread_count or 0,
                has_unread=(thread.unread_count or 0) > 0,
            )
        )
    return summaries


def can_read_thread(user: User | None, thread: MessageThread | None) -> bool:
    """
    Whether `user` is a participant in `thread`.

    The predicate form of the module's only rule, for callers that need the
    answer in Python - chiefly `messaging.services`, which asks it again inside
    the transaction that posts.
    """
    if user is None or not user.is_active or thread is None:
        return False
    return MessageParticipant.objects.filter(thread=thread, user=user).exists()


def participants_of(thread: MessageThread) -> QuerySet[User]:
    """The users in `thread`. `thread` must already be authorized."""
    return User.objects.filter(message_participants__thread=thread).order_by('email')


def thread_is_anchored_to(thread: MessageThread, idea) -> bool:
    """Whether `thread` was opened about `idea`. Used to keep a thread link honest."""
    return thread.idea_id == getattr(idea, 'pk', None) and thread.idea_id is not None


def thread_exists_for_pair(user: User, other: User, idea) -> MessageThread | None:
    """
    The thread `user` and `other` already have about `idea`, if there is one.

    Used by `start_thread` so "Send Message" twice to the same person about the
    same idea continues the conversation instead of starting a second one. A
    thread with no idea is never reused: it is not anchored to anything, so
    continuing it would silently attach it to an idea the two of them never
    agreed to discuss.
    """
    if idea is None or idea.pk is None:
        return None

    return (
        MessageThread.objects.filter(
            pk__in=_participant_thread_ids(user),
            idea_id=idea.pk,
            participants__user=other,
        )
        .distinct()
        .order_by('-latest_message_at', '-pk')
        .first()
    )


def unread_count_for(user: User | None) -> int:
    """How many threads `user` has unread messages in. Cheap: the list path's own count."""
    if user is None or not user.is_active:
        return 0
    return sum(1 for summary in list_threads(user) if summary.has_unread)


def has_unread_in(user: User | None, thread: MessageThread) -> bool:
    """Whether `user` has unread messages in `thread`. Requires a participant check."""
    if not can_read_thread(user, thread):
        return False
    participant = MessageParticipant.objects.filter(thread=thread, user=user).first()
    if participant is None:  # pragma: no cover - can_read_thread already answered this
        return False
    if participant.last_read_at is None:
        return thread.messages.exists()
    return Message.objects.filter(thread=thread, created_at__gt=participant.last_read_at).exists()


def exists_for_user(user: User | None) -> bool:
    """Whether `user` is in any thread at all - the condition for showing the nav entry."""
    if user is None or not user.is_active:
        return False
    return MessageParticipant.objects.filter(user=user).exists()
