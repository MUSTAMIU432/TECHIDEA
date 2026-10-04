"""
Message operations: start a conversation, reply to one, mark it read.

The write side, in the project's usual `(message, field, reason)` shape. Three
operations and one rule they all enforce:

    **A message is only ever posted by a participant of its thread.**

That is asserted on every write rather than assumed, and it is checked *inside*
the transaction against the row as it now is. It matters more than it looks: the
`Message.clean()` backstop only fires on writers that call `full_clean`, so a
service that created a message without re-checking participation would produce a
message a non-participant could read - which is precisely the thing this app
exists to prevent. Two independent checks, one of them in the model, because the
failure is a privacy leak rather than a validation error.

Starting a conversation
-----------------------
`start_thread` refuses to anchor a thread to an idea the caller cannot read.
Reusing an existing thread between the same two people *about the same idea*
happens instead of starting a second conversation, so "Send Message" twice is a
continuation. A thread with no idea is never reused: it is anchored to nothing,
so continuing it would silently attach it to an idea the two of them never
agreed to discuss.

It also refuses to open a thread with yourself, and refuses to include somebody
who cannot read the idea the thread is anchored to - so a thread cannot be used
to reach somebody through an idea they have no access to.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from ideas.models import Idea
from identity.models import User
from messaging import selectors
from messaging.models import MESSAGE_MAX_LENGTH, Message, MessageParticipant, MessageThread

logger = logging.getLogger(__name__)


class MessageError(Exception):
    """A refused messaging operation, in the project's `(message, field, reason)` shape."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message)
        self.message = message
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class StartThreadInput:
    recipient_ids: tuple[int, ...]
    body: str
    idea_id: object | None = None
    subject: str = ''


@dataclass(frozen=True)
class PostMessageInput:
    thread_id: object
    body: str


def _require_active_user(user: User | None) -> User:
    if user is None or not user.is_active:
        raise MessageError('You must be signed in to send messages.', reason='unauthenticated')
    return user


def _validate_body(body: str | None) -> str:
    normalized = (body or '').strip()
    if not normalized:
        raise MessageError('Write a message first.', field='body')
    if len(normalized) > MESSAGE_MAX_LENGTH:
        raise MessageError(
            f'A message must be at most {MESSAGE_MAX_LENGTH} characters.', field='body'
        )
    return normalized


def _resolve_idea(user: User, idea_id: object | None) -> Idea | None:
    """
    The idea this conversation is about, resolved through the Ideas visibility
    rule - or `None` if there is no idea.

    `idea_id is None` means "no idea", which is allowed: a direct message with no
    context is a normal thing to want. A supplied id that resolves to nothing is
    **not** the same as no id, and is refused - otherwise "start a thread about
    idea 99999" and "start a thread about nothing" would be indistinguishable at
    the authorization boundary, and an idea the caller cannot read would become
    indistinguishable from one that does not exist.
    """
    if idea_id is None or idea_id == '':
        return None

    from ideas import selectors as idea_selectors

    idea = idea_selectors.get_idea(user, idea_id)
    if idea is None:
        raise MessageError('Idea is unavailable.', reason='forbidden')
    return idea


def _resolve_recipients(
    user: User, recipient_ids: tuple[int, ...], idea: Idea | None
) -> list[User]:
    """
    The other people in the conversation, as active users.

    Every id is resolved through one query rather than one per id, and an id that
    does not resolve to an active account is refused rather than skipped: a
    silently dropped recipient is a conversation somebody believes they are in
    and is not.
    """
    if not recipient_ids:
        raise MessageError('Choose somebody to send this to.', field='recipientIds')

    # Ids arrive as strings from GraphQL, so they are parsed here. One that does
    # not parse - a null, a blank string's neighbour - is not silently dropped: it
    # stays in the set as something unresolvable, so the "not available" refusal
    # below reports it rather than the caller finding out later that the person
    # they chose was never in the conversation.
    unique_ids: set[int] = set()
    for raw in recipient_ids or ():
        if str(raw).strip() in ('', 'None'):
            continue
        try:
            unique_ids.add(int(str(raw)))
        except (TypeError, ValueError):
            continue
    unique_ids.discard(user.pk)

    if not unique_ids:
        raise MessageError('You cannot start a conversation with yourself.', field='recipientIds')

    recipients = list(User.objects.filter(pk__in=unique_ids, is_active=True))
    if len(recipients) != len(unique_ids):
        raise MessageError('One of the people you chose is not available.', field='recipientIds')

    # An idea is context, not access: the thread's *creator* must be able to read
    # the idea, which `idea_selectors.get_idea` above already established. The
    # recipients are not required to be able to read it - they are being told
    # about the conversation directly, which is the entire point of a private
    # message - but if the idea is anchored, they must at least be told what it
    # is, so the caller names it in the message.
    if idea is not None:
        logger.info(
            'Message thread anchored to an idea (idea=%s, recipients=%s).',
            idea.pk,
            [recipient.pk for recipient in recipients],
        )

    return recipients


@transaction.atomic
def start_thread(user: User | None, data: StartThreadInput) -> MessageThread:
    """
    Open a conversation, or continue the one `user` already has with these people
    about this idea.

    One transaction for the thread, its participants and its first message, so a
    thread never exists without a message in it - which is what lets
    `MessageThread.latest_message_at` be non-null and the thread list a single
    indexed read.

    **Always returns the thread**, on both paths. It used to return the *message*
    when it continued an existing conversation, so `startMessageThread` resolved
    `thread.pk` - a message id - and answered a successful "Message sent." with
    `thread: null`. A caller that wants the message has the thread.
    """
    active_user = _require_active_user(user)
    body = _validate_body(data.body)

    idea = _resolve_idea(active_user, data.idea_id)

    if idea is not None:
        existing = selectors.thread_exists_for_pair(
            active_user, _first_recipient_hint(data.recipient_ids, active_user), idea
        )
        if existing is not None:
            post_message(active_user, PostMessageInput(existing.pk, body))
            return existing

    recipients = _resolve_recipients(active_user, data.recipient_ids, idea)

    thread = MessageThread.objects.create(
        idea=idea,
        subject=(data.subject or (idea.title if idea is not None else ''))[:200],
        started_by=active_user,
        # Set from the message below rather than from the clock: "the last time
        # anything was said in this thread" has to be a moment a message actually
        # carries, or a thread sorted by it can order differently from its own
        # contents. Stamped after the create, in the same transaction.
        latest_message_at=timezone.now(),
    )
    MessageParticipant.objects.bulk_create(
        [
            MessageParticipant(thread=thread, user=active_user),
            *[MessageParticipant(thread=thread, user=recipient) for recipient in recipients],
        ]
    )
    message = Message.objects.create(thread=thread, sender=active_user, body=body)
    thread.latest_message_at = message.created_at
    thread.save(update_fields=['latest_message_at'])

    # The starter has read their own conversation, by the same rule `post_message`
    # applies to its sender. Without it a brand-new thread opens with the author's
    # own first message counted as unread, which is a badge lying to the only
    # person who has certainly read it.
    MessageParticipant.objects.filter(thread=thread, user=active_user).update(
        last_read_at=message.created_at
    )

    logger.info(
        'Message thread started (thread=%s, idea=%s, participants=%s).',
        thread.pk,
        idea.pk if idea is not None else None,
        [active_user.pk, *(recipient.pk for recipient in recipients)],
    )
    return thread


def _first_recipient_hint(recipient_ids: tuple[int, ...], user: User) -> User:
    """
    A `User` used only to find an existing two-person thread.

    `thread_exists_for_pair` needs a user object and the code above looks for the
    one the caller has already exchanged messages with. Resolving an arbitrary
    id here would raise for an id that is not a real account, before
    `_resolve_recipients` has produced its better message for the same problem -
    so a missing id is simply "no existing thread", and the later resolution
    reports it.
    """
    for pk in recipient_ids or ():
        try:
            normalized = int(str(pk))
        except (TypeError, ValueError):
            continue
        if normalized == user.pk:
            continue
        candidate = User.objects.filter(pk=normalized).only('pk', 'email').first()
        if candidate is not None:
            return candidate
    return user


@transaction.atomic
def post_message(user: User | None, data: PostMessageInput) -> Message:
    """
    Post a message into an existing thread the caller participates in.

    The thread is resolved through `selectors.get_thread`, so a thread the caller
    is not in is refused identically to one that does not exist - and the
    participation check is repeated on the locked row, because the selector's
    answer was true when the request arrived and the transaction may wait.

    Also marks the sender's own messages read: having posted, they have read.
    Without that, replying in a thread you had not opened leaves your own unread
    badge lit, which is exactly the kind of small wrongness that teaches people
    to ignore the badge.
    """
    active_user = _require_active_user(user)
    body = _validate_body(data.body)

    thread = selectors.get_thread(active_user, data.thread_id)
    if thread is None:
        raise MessageError('Conversation is unavailable.')

    locked = (
        MessageThread.objects.select_for_update(of=('self',))
        .filter(pk=thread.pk, participants__user=active_user)
        .first()
    )
    if locked is None:
        raise MessageError('Conversation is unavailable.')

    message = Message.objects.create(thread=locked, sender=active_user, body=body)
    _notify_participants(locked.pk, active_user)

    locked.latest_message_at = message.created_at
    # Only `latest_message_at`: `MessageThread` has no `updated_at`, so naming it
    # here raised `ValueError: The following fields do not exist in this model`
    # and no message could ever be posted.
    locked.save(update_fields=['latest_message_at'])

    MessageParticipant.objects.filter(thread=locked, user=active_user).update(
        last_read_at=message.created_at
    )

    logger.info(
        'Message posted (thread=%s, sender=%s, message=%s).',
        locked.pk,
        active_user.pk,
        message.pk,
    )
    return message


def _notify_participants(thread_id: int, sender: User) -> None:
    """
    Tell everybody else in the conversation that there is a message in it.

    One event, one notification each, after the commit - and the sender is never
    notified, because they are looking at it. Registered with `on_commit` from
    inside the transaction that wrote the message, so a rolled-back post notifies
    nobody, and it never raises: the message is in the thread either way, and
    somebody who is not told will find it when they open the conversation.
    """
    from notifications import services as notification_services

    thread = MessageThread.objects.filter(pk=thread_id).select_related('started_by', 'idea').first()
    if thread is None:
        return

    others = [
        participant.user
        for participant in MessageParticipant.objects.filter(thread=thread)
        .select_related('user')
        .exclude(user_id=sender.pk)
    ]
    if not others:
        return

    about = f' about "{thread.idea.title}"' if thread.idea_id and thread.idea else ''
    notification_services.deliver(
        recipients=others,
        kind='message.received',
        title=f'{sender.first_name} sent you a message{about}',
        body='Open the conversation to read it.',
    )


@transaction.atomic
def mark_thread_read(user: User | None, thread_id: object) -> MessageThread:
    """
    Move the caller's read position to the end of a thread they participate in.

    "Read" is a personal cursor, not a mutation of the conversation: no other
    participant sees it, and it can never mark a message as read for anybody
    else. Called when a thread is opened, so the badge reflects having looked
    rather than having replied.
    """
    active_user = _require_active_user(user)

    thread = selectors.get_thread(active_user, thread_id)
    if thread is None:
        raise MessageError('Conversation is unavailable.')

    MessageParticipant.objects.filter(thread=thread, user=active_user).update(
        last_read_at=thread.latest_message_at
    )
    return thread
