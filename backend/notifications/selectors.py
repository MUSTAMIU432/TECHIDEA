"""
Reading your own notifications.

A deliberately tiny module, because the whole authorization story is "yours or
nothing": a notification belongs to one recipient, it is never visible to
anybody else, and there is no tenancy question because a notification is not a
tenant's data - it is a statement made *to a person*.

So every function filters on `user` first and resolves ids through it, and every
one of them returns `None`, `[]` or `0` for anybody else. There is no
organization check here to get wrong, because there is nothing to check: the row
that carries the audience is the thing being read.

Paged through the existing `ideas.pagination.Page`, rather than a second
pagination convention, so the client handles one page shape.
"""

from ideas.pagination import Page, empty_page, paginate
from identity.models import User
from notifications.models import Notification


def list_notifications(
    user: User | None,
    *,
    offset: object = 0,
    limit: object = None,
    unread_only: bool = False,
) -> Page[Notification]:
    """
    One page of `user`'s notifications, newest first.

    `unread_only` narrows the list and never widens it - the `user` filter is
    applied before it and is not something a flag can remove. `-pk` as the
    tie-breaker for the same microsecond-resolution reason every other list in the
    platform uses one, so a page boundary cannot show one row twice and skip
    another.
    """
    if user is None or not user.is_active:
        return empty_page(offset, limit)

    queryset = Notification.objects.filter(user=user)
    if unread_only:
        queryset = queryset.filter(is_read_at__isnull=True)

    return paginate(queryset.order_by('-created_at', '-pk'), offset=offset, limit=limit)


def unread_count(user: User | None) -> int:
    """
    How many of `user`'s notifications are unread.

    One `COUNT(*)` against `notif_unread_idx`, which leads on exactly this
    predicate. Kept separate from the list rather than derived from it, because
    the badge is read on every page and the list is not.
    """
    if user is None or not user.is_active:
        return 0

    return Notification.objects.filter(user=user, is_read_at__isnull=True).count()


def get_notification(user: User | None, notification_id: object) -> Notification | None:
    """
    One of `user`'s notifications, or `None`.

    Resolved through `user=user` rather than by id alone, so an id belonging to
    somebody else is null rather than readable.
    """
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(notification_id))
    except (TypeError, ValueError):
        return None

    return Notification.objects.filter(pk=normalized_id, user=user).first()


def mark_read(user: User | None, notification_id: object) -> Notification | None:
    """
    Mark one of `user`'s notifications read, or `None`.

    Idempotent: marking an already-read notification returns it, unchanged.
    Writing `is_read_at` again would move the moment a reader "noticed" it, which
    is a small lie about a past event - and there is nothing to gain by it.

    Returns the row so the caller can render the updated list item without a
    second read.
    """
    notification = get_notification(user, notification_id)
    if notification is None:
        return None

    if notification.is_read_at is None:
        from django.utils import timezone

        notification.is_read_at = timezone.now()
        notification.save(update_fields=['is_read_at'])
    return notification
