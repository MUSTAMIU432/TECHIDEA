"""
The in-app notification's email channel.

Same contract as `identity.email` and `reviews.notifications`: plain text, from
`DEFAULT_FROM_EMAIL`, to the *stored* address, after the commit, and **never
raises**. The event that produced it is already committed and valid; a mail
failure is a delivery problem, not a business one.

**Deliberately generic and deliberately thin.** This message is not a template
per kind - it says that there is something new waiting in the app and links to
the notifications list, and the list itself carries the specific wording. That
is not laziness, it is the security rule working: an inbox is readable by more
than its owner and outlives the account's access, so the one thing this email
must not do is reproduce the thing it is announcing. For a platform approval
that means the report's criteria, the reviewer's assessments and the
recommendations stay behind authentication, and the email says only that a
review is complete and where to read it.

The one exception is the subject, which is the notification's own `title` -
already written by the domain to be short and non-sensitive
(`notifications.services.BODY_MAX_LENGTH` bounds the body the same way).
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from identity.models import User
from notifications.models import Notification

logger = logging.getLogger(__name__)

NOTIFICATIONS_PATH = '/app/notifications'


def send_system_email(user: User, idea=None) -> None:
    """
    Mail `user` that they have notifications waiting.

    Sends one message for all of them rather than one per notification: a
    platform approval with four unread items should be one email, not four, and
    an unread count is not sensitive.
    """
    unread = list(
        Notification.objects.filter(user=user, is_read_at__isnull=True)
        .order_by('-created_at', '-pk')
        .values_list('id', flat=True)[:10]
    )
    if not unread:
        return

    latest = Notification.objects.filter(pk=unread[0]).first()
    if latest is None:  # pragma: no cover - the list and this read cannot disagree
        return

    notifications_url = build_action_url_path(NOTIFICATIONS_PATH)

    body = render_to_string(
        'notifications/email/system_notification.txt',
        {
            'app_name': 'Automation Platform',
            'first_name': user.first_name,
            'title': latest.title,
            'summary': latest.body,
            'count': len(unread),
            'notifications_url': notifications_url,
        },
    )

    message = EmailMessage(
        subject=latest.title,
        body=body,
        to=[user.email],
        from_email=settings.DEFAULT_FROM_EMAIL,
    )

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception(
            'Could not send the notification email (user=%s). The notifications exist in '
            'the app; the email was not delivered.',
            user.pk,
        )


def build_action_url_path(path: str) -> str:
    """The absolute frontend URL for a page with no token, or the path alone."""
    return f'{settings.FRONTEND_URL}{path}' if settings.FRONTEND_URL else path
