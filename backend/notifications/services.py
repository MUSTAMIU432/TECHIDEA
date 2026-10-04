"""
Notifying somebody that the platform decided something.

`deliver` is the whole of this module's public surface, and it exists because
the product asks for a **single business event reaching a person through more
than one channel**. Platform approval, for instance, must arrive as an in-app
notification *and* as an email; the two are not two approvals and must never be
allowed to become two records.

So `deliver` takes the event once and does both:

    deliver(...)  ->  writes notifications.Notification
                  ->  registers an email with transaction.on_commit

**Nothing here runs inside the decision's transaction**, and that is the design
rather than an accident. It is what makes "if email fails, platform approval
remains valid" true structurally instead of aspirationally:

- The `Notification` row and the email are both registered on `on_commit`, so a
  decision that rolls back notifies nobody.
- The row write is wrapped so a failure is logged and swallowed. The decision is
  committed; the author still sees the report in the app, and the notification
  list is a convenience, not the record.
- The send (`notifications.email`) never raises either, for the same reason.

A caller therefore cannot accidentally make delivery part of the decision: it is
not in the transaction, and it cannot raise into it.

**What goes in the body.** `title` and `body` are written by the calling domain
and must not carry sensitive content - no reviewer feedback, no criteria, no
report contents. They say *what happened* and *what to do next*, and the email
links to a page that requires authentication. `BODY_MAX_LENGTH` is enforced here
rather than trusted, so a caller that pastes a review's feedback into the body
gets a truncation rather than a full report in somebody's inbox.
"""

import logging

from django.db import transaction

from identity.models import User
from notifications.models import Notification

logger = logging.getLogger(__name__)

# The model's `body` column is 500 characters; the service enforces the same
# limit on the way in so a too-long summary is truncated at the boundary rather
# than refused inside a notification the decision already depends on.
BODY_MAX_LENGTH = 500
TITLE_MAX_LENGTH = 200


def deliver(
    *,
    recipients: User | list[User] | tuple[User, ...],
    kind: str,
    title: str,
    body: str,
    idea=None,
    report=None,
    send_email: bool = True,
) -> list[Notification]:
    """
    Tell each of `recipients` that something happened, in the app and by email.

    Returns the notifications that were written, which is empty when the write
    failed or when there was nobody to tell - never an exception. Callers do not
    need to handle the failure case at all, which is what stops a mail outage
    from becoming a failed approval somewhere else in the code.

    `send_email=False` is for kinds where an email would be noise: a queue nudge,
    or a notification about a message the recipient is already looking at.
    """
    targets = _as_list(recipients)

    with transaction.atomic():
        # Register first: if the send itself raises (it is written not to, but
        # a template error would), the transaction still commits its row.
        transaction.on_commit(lambda: _write_all(targets, kind, title, body, idea, report))
        if send_email:
            transaction.on_commit(lambda: _email_all(targets, idea))

    return []


def _as_list(recipients: User | list[User] | tuple[User, ...]) -> list[User]:
    """
    The active recipients, whichever shape they arrived in.

    One filter for both shapes on purpose. It used to short-circuit for a single
    `User`, which meant a deactivated account was written a notification and
    emailed when the caller passed one recipient, and quietly skipped when it
    passed a list of one - so "we do not notify deactivated accounts" was true of
    the code and false of the platform, depending on the call site's spelling.
    """
    candidates = [recipients] if isinstance(recipients, User) else list(recipients or [])
    return [user for user in candidates if user is not None and user.is_active]


def _write_all(
    targets: list[User],
    kind: str,
    title: str,
    body: str,
    idea,
    report,
) -> None:
    rows = [
        Notification(
            user=user,
            kind=kind,
            title=title[:TITLE_MAX_LENGTH],
            body=body[:BODY_MAX_LENGTH],
            idea=idea,
            report=report,
        )
        for user in targets
    ]
    if not rows:
        return

    try:
        Notification.objects.bulk_create(rows)
    except Exception:
        # Broad on purpose, exactly as `identity.email._send` is: the business
        # event has already been committed and is valid regardless. A
        # notification list that missed an entry is a much smaller problem than
        # an approval that raised.
        logger.exception(
            'Could not write notifications (kind=%s, recipients=%s). The event '
            'stands; the recipients were not notified in the app.',
            kind,
            [user.pk for user in targets],
        )


def _email_all(targets: list[User], idea) -> None:
    from notifications import email as notification_email

    for user in targets:
        notification_email.send_system_email(user, idea)


def mark_all_read(user: User | None) -> int:
    """
    Mark every one of `user`'s unread notifications read, and return how many.

    A single UPDATE rather than a loop, because "mark everything read" is one
    intent and one statement. Scoped to the recipient, so it cannot touch anybody
    else's inbox - and there is no "mark as read" path here for a notification the
    caller does not own, because `selectors.get_notification` resolves through the
    recipient before anything is written.
    """
    from django.utils import timezone

    if user is None or not user.is_active:
        return 0

    return Notification.objects.filter(user=user, is_read_at__isnull=True).update(
        is_read_at=timezone.now()
    )
