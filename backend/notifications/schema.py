"""
The Notifications GraphQL adapter.

The notification list, the unread count, and marking one read. There is no
mutation that *creates* a notification: notifications are written by the domains
that decided something (`notifications.services.deliver`), and a client that
could create one could put words in somebody else's inbox.

Two ideas kept apart here, because the product asks for it and it is easy to lose:

- **The list is a record of what the platform told this person**, and the only
  thing a reader may change about it is their own `isRead`. Reading is not
  approving, opening a report is not a go-ahead, and neither is recorded here.
- **The review report is not in the body.** A notification carries a short,
  non-sensitive summary and a link. The report's criteria, assessments and
  recommendations stay behind authentication, which is why `Notification.body`
  is bounded and why no field of this type exposes anything the email does not.
"""

import strawberry

from notifications import selectors, services
from notifications.models import NOTIFICATION_LABELS, Notification


@strawberry.type(description='One thing the platform told you.')
class NotificationType:
    id: strawberry.ID
    kind: str
    label: str = strawberry.field(
        description='The kind in words, so a client renders the same wording the server does '
        'instead of splitting a dotted string and hoping it guessed the capitals right.'
    )
    title: str
    body: str
    idea_id: strawberry.ID | None
    report_id: strawberry.ID | None
    is_read: bool
    action_path: str | None = strawberry.field(
        description=(
            'Where this takes you in the app, decided by the server, or null when '
            'there is nowhere specific to go. Present so a client never has to '
            'guess a destination from the kind and an id - and so an '
            'in-app link and a push payload can be built from one mapping.'
        )
    )
    created_at: str

    @staticmethod
    def from_model(notification: Notification) -> 'NotificationType':
        return NotificationType(
            id=strawberry.ID(str(notification.pk)),
            kind=notification.kind,
            label=NOTIFICATION_LABELS.get(notification.kind, notification.kind),
            title=notification.title,
            body=notification.body,
            idea_id=strawberry.ID(str(notification.idea_id)) if notification.idea_id else None,
            report_id=strawberry.ID(str(notification.report_id))
            if notification.report_id
            else None,
            is_read=notification.is_read_at is not None,
            action_path=notification.action_path,
            created_at=notification.created_at.isoformat(),
        )


@strawberry.type(description='Result of marking a notification read.')
class NotificationPayload:
    success: bool
    message: str
    notification: NotificationType | None = None


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            'Your notifications, newest first. Yours only - there is no way to '
            'ask for anybody else`s, and an id you do not own resolves to null.'
        )
    )
    def notifications(
        self,
        info: strawberry.Info,
        offset: int | None = None,
        limit: int | None = None,
        unread_only: bool = False,
    ) -> list[NotificationType]:
        page = selectors.list_notifications(
            info.context.user, offset=offset, limit=limit, unread_only=unread_only
        )
        return [NotificationType.from_model(row) for row in page.items]

    @strawberry.field(
        description=(
            'How many notifications you have not read. One indexed count, which '
            'is why the badge does not come out of the list itself.'
        )
    )
    def unread_notification_count(self, info: strawberry.Info) -> int:
        return selectors.unread_count(info.context.user)

    @strawberry.field(
        description=(
            'Whether you have any unread notification. For a caller that needs '
            'to know whether to draw a badge but not how many are unread - a dot '
            'rather than a count - since this is one indexed EXISTS where '
            '`unreadNotificationCount` is one indexed COUNT.'
        )
    )
    def has_unread_notifications(self, info: strawberry.Info) -> bool:
        return selectors.unread_count(info.context.user) > 0


@strawberry.type
class Mutation:
    @strawberry.mutation(description='Mark one of your notifications read.')
    def mark_notification_read(
        self, info: strawberry.Info, id: strawberry.ID
    ) -> NotificationPayload:
        notification = selectors.mark_read(info.context.user, id)
        if notification is None:
            return NotificationPayload(success=False, message='That notification is not available.')
        return NotificationPayload(
            success=True,
            message='Marked as read.',
            notification=NotificationType.from_model(notification),
        )

    @strawberry.mutation(description='Mark all of your notifications read.')
    def mark_all_notifications_read(self, info: strawberry.Info) -> NotificationPayload:
        services.mark_all_read(info.context.user)
        return NotificationPayload(success=True, message='All notifications marked as read.')
