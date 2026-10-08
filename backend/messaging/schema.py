"""
The Messaging GraphQL adapter.

The private-message surface: your threads, one thread's messages, and sending.
Kept in its own domain and its own tables precisely so that "Send Message" cannot
become another way to write a public comment - see `messaging/models.py`'s module
docstring for why that boundary is structural.

**Every read and write is authorized by participation**, resolved inside
`messaging.services` / `messaging.selectors`. There is no `message(id)` field on
this schema at all: a message is reached through a thread the caller is in, never
by guessing an id, and a thread the caller is not in is null rather than an
error. So the schema has no shape in which a private conversation could be read
by somebody who was not invited into it.
"""

import strawberry

from identity.schema import UserType
from messaging import selectors, services
from messaging.models import Message, MessageThread


@strawberry.type(description='One message in a conversation you are part of.')
class MessageType:
    id: strawberry.ID
    thread_id: strawberry.ID
    sender_id: strawberry.ID
    sender_first_name: str
    body: str
    created_at: str

    @staticmethod
    def from_model(message: Message) -> 'MessageType':
        return MessageType(
            id=strawberry.ID(str(message.pk)),
            thread_id=strawberry.ID(str(message.thread_id)),
            sender_id=strawberry.ID(str(message.sender_id)),
            sender_first_name=message.sender.first_name if message.sender_id else '',
            body=message.body,
            created_at=message.created_at.isoformat(),
        )


@strawberry.type(description='One conversation, and who is in it.')
class MessageThreadType:
    id: strawberry.ID
    subject: str
    idea_id: strawberry.ID | None
    idea_title: str
    started_by_id: strawberry.ID
    participants: list[UserType]
    latest_message_at: str

    @staticmethod
    def from_model(thread: MessageThread) -> 'MessageThreadType':
        return MessageThreadType(
            id=strawberry.ID(str(thread.pk)),
            subject=thread.subject,
            idea_id=strawberry.ID(str(thread.idea_id)) if thread.idea_id else None,
            idea_title=thread.idea.title if thread.idea_id else '',
            started_by_id=strawberry.ID(str(thread.started_by_id)),
            participants=[UserType.from_model(link.user) for link in thread.participants.all()],
            latest_message_at=thread.latest_message_at.isoformat(),
        )


@strawberry.type(description='A conversation in your list, with its unread state.')
class MessageThreadSummaryType:
    thread: MessageThreadType
    other_participants: list[UserType]
    latest_message: MessageType | None
    unread_count: int
    has_unread: bool

    @staticmethod
    def from_model(summary: selectors.ThreadSummary) -> 'MessageThreadSummaryType':
        return MessageThreadSummaryType(
            thread=MessageThreadType.from_model(summary.thread),
            other_participants=[UserType.from_model(user) for user in summary.other_participants],
            latest_message=MessageType.from_model(summary.latest_message)
            if summary.latest_message
            else None,
            unread_count=summary.unread_count,
            has_unread=summary.has_unread,
        )


@strawberry.type(description='One page of a conversation, oldest first.')
class MessagePageType:
    messages: list[MessageType]
    total: int


@strawberry.type(description='Result of a messaging operation.')
class MessagePayload:
    success: bool
    message: str
    field: str | None = None
    thread: MessageThreadType | None = None
    sent_message: MessageType | None = None


@strawberry.input(description='Start a private conversation, optionally about an idea.')
class StartMessageThreadInput:
    recipient_ids: list[strawberry.ID]
    body: str
    idea_id: strawberry.ID | None = None
    subject: str = ''


@strawberry.input(description='Reply in a conversation you are part of.')
class PostMessageInput:
    thread_id: strawberry.ID
    body: str


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            'Your conversations, most recently active first, with unread counts. '
            'Yours only: a thread you were not invited into is not in this list, '
            'and `messageThread` returns null for it.'
        )
    )
    def message_threads(self, info: strawberry.Info) -> list[MessageThreadSummaryType]:
        return [
            MessageThreadSummaryType.from_model(summary)
            for summary in selectors.list_threads(info.context.user)
        ]

    @strawberry.field(
        description=(
            'One conversation you are part of, or null. Null is also the answer '
            'for a conversation that does not exist and for one you are not in.'
        )
    )
    def message_thread(self, info: strawberry.Info, id: strawberry.ID) -> MessageThreadType | None:
        thread = selectors.get_thread(info.context.user, id)
        return MessageThreadType.from_model(thread) if thread is not None else None

    @strawberry.field(
        description=(
            "One conversation's messages, oldest first. Empty for anybody who is "
            'not a participant, and for a thread that does not exist - the same '
            'answer, so a thread id cannot be used to find out that a '
            'conversation exists.'
        )
    )
    def thread_messages(self, info: strawberry.Info, thread_id: strawberry.ID) -> MessagePageType:
        page = selectors.list_messages(info.context.user, thread_id)
        return MessagePageType(
            messages=[MessageType.from_model(message) for message in page.messages],
            total=page.total,
        )

    @strawberry.field(
        description=(
            'How many of your conversations have unread messages. Whether to '
            'show the navigation badge.'
        )
    )
    def unread_thread_count(self, info: strawberry.Info) -> int:
        return selectors.unread_count_for(info.context.user)


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            'Send a private message. Starts a conversation with the people you '
            'name, optionally about an idea you can read - and if you already '
            'have a conversation with them about that idea, this continues it '
            'rather than starting a second one. These messages are private to '
            'the participants; they are not comments and are not visible to '
            'anybody who can read the idea.'
        )
    )
    def start_message_thread(
        self, info: strawberry.Info, input: StartMessageThreadInput
    ) -> MessagePayload:
        try:
            thread = services.start_thread(
                info.context.user,
                services.StartThreadInput(
                    recipient_ids=tuple(str(pk) for pk in input.recipient_ids),
                    body=input.body,
                    idea_id=input.idea_id,
                    subject=input.subject,
                ),
            )
        except services.MessageError as exc:
            return MessagePayload(success=False, message=exc.message, field=exc.field)

        resolved = selectors.get_thread(info.context.user, thread.pk)
        return MessagePayload(
            success=True,
            message='Message sent.',
            thread=MessageThreadType.from_model(resolved) if resolved is not None else None,
        )

    @strawberry.mutation(description='Reply in a conversation you are part of.')
    def post_message(self, info: strawberry.Info, input: PostMessageInput) -> MessagePayload:
        try:
            message = services.post_message(
                info.context.user,
                services.PostMessageInput(thread_id=input.thread_id, body=input.body),
            )
        except services.MessageError as exc:
            return MessagePayload(success=False, message=exc.message, field=exc.field)

        thread = selectors.get_thread(info.context.user, message.thread_id)
        return MessagePayload(
            success=True,
            message='Message sent.',
            thread=MessageThreadType.from_model(thread) if thread is not None else None,
            sent_message=MessageType.from_model(message),
        )

    @strawberry.mutation(
        description=(
            'Mark a conversation read, moving your own read position to its end. '
            'It is a personal cursor: no other participant is affected.'
        )
    )
    def mark_thread_read(self, info: strawberry.Info, thread_id: strawberry.ID) -> MessagePayload:
        try:
            thread = services.mark_thread_read(info.context.user, thread_id)
        except services.MessageError as exc:
            return MessagePayload(success=False, message=exc.message, field=exc.field)
        resolved = selectors.get_thread(info.context.user, thread.pk)
        return MessagePayload(
            success=True,
            message='Marked as read.',
            thread=MessageThreadType.from_model(resolved) if resolved is not None else None,
        )
