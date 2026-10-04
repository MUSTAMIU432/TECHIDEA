"""
In-app system notifications.

One entity: `Notification`. It is a *delivery of a business fact to one person*
- "your idea was approved", "a reviewer asked for changes" - and it is kept
deliberately separate from every other thing a person can receive:

    Comment               public engagement, on an idea, visible to its readers
    Review feedback       formal, inside `reviews.Review`, addressed to the author
    Private message       `messaging.Message`, two named participants
    System notification   this model: the platform telling somebody what happened
    Email                 a delivery channel, not a kind of thing

The distinction matters because four of those five can be mistaken for one
another and the product asks that they not be. A comment saying "looks good to
me" is not an approval; a vote is not an approval; an email saying a review is
finished is not the report. So this model stores only things the *platform*
decided, never anything a user wrote.

**One business event, two channels.** `notifications.services.deliver` writes
this row and sends an email from the same call. It is one event with two
deliveries, not two events - there is no "email notification" record and no
second business event, so nothing downstream has to reconcile them, and the
notification list can never show an approval the email does not mention or the
other way round.

**Channel failure is not business failure.** Both the row write and the send
happen after the transaction that caused the event has committed, and neither
raises. If the mail backend is down the notification still exists; if the row
write fails the approval is still valid and the author still sees the report in
the app. Nothing here is inside the decision's transaction - see
`services.deliver`'s docstring.

`Notification` is not an audit record: `ideas.IdeaTransition` and
`administration.AdminAuditEntry` are, and this is the surface a person reads.
Nothing is ever updated except `is_read_at`, which is that reader's own business.
"""

from typing import ClassVar

from django.conf import settings
from django.db import models

NOTIFICATION_KIND_CHOICES = (
    # --- the idea journey ------------------------------------------------------
    ('idea.organization_changes_requested', 'Changes requested by your organization'),
    ('idea.organization_confirmed', 'Your organization confirmed your idea'),
    ('idea.submitted_to_platform', 'Your idea was submitted to the platform'),
    ('idea.platform_changes_requested', 'The platform asked for changes'),
    ('idea.platform_rejected', 'The platform did not approve your idea'),
    ('idea.platform_approved', 'Platform review completed'),
    ('idea.owner_go_ahead', 'Your idea is ready for implementation'),
    # --- the review queues -----------------------------------------------------
    ('review.assigned', 'An idea was assigned to you'),
    ('review.organization_queue', 'An idea is waiting for organization review'),
    ('review.platform_queue', 'An idea is waiting for platform review'),
    # --- invitations and messaging ---------------------------------------------
    ('invitation.received', 'You have been invited'),
    ('invitation.accepted', 'Your invitation was accepted'),
    ('message.received', 'You have a new message'),
)

# The kinds that are *about a decision* rather than a queue. Only these render a
# review report link, and only these may be produced by a review service - the
# distinction is what stops a "somebody is waiting for you" nudge from pointing
# at an approval report.
DECISION_KINDS = frozenset(
    {
        'idea.organization_changes_requested',
        'idea.organization_confirmed',
        'idea.platform_changes_requested',
        'idea.platform_rejected',
        'idea.platform_approved',
        'idea.owner_go_ahead',
    }
)


class Notification(models.Model):
    """
    One thing the platform has told one person.

    `recipient` is a `PROTECT`: a notification is a statement made *to somebody*,
    and deleting their account should not rewrite history by deleting the record
    that they were told something. Users are deactivated, not deleted.

    `idea` is nullable so a notification can be about something that is not an
    idea yet - "you have been invited". It is `SET_NULL` rather than `CASCADE`
    for the same reason: the recipient was told something, and removing the thing
    they were told about does not un-tell them.

    `body` is short, plain text written by the platform, never by a user, and is
    the only thing an email carries from here. The **full** content of whatever
    the notification is about is never copied into it: the email says a review
    report is ready and links to it, and the report itself stays behind
    authentication. That is the same rule `reviews.notifications` follows, and it
    is why `body` is a `CharField` with a length limit rather than a `TextField`.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='notifications',
        help_text='The person being told.',
    )
    kind = models.CharField(max_length=64, choices=NOTIFICATION_KIND_CHOICES)
    title = models.CharField(max_length=200)
    body = models.CharField(
        max_length=500,
        help_text='A short, non-sensitive summary. The full content stays behind authentication.',
    )
    idea = models.ForeignKey(
        'ideas.Idea',
        on_delete=models.SET_NULL,
        related_name='notifications',
        null=True,
        blank=True,
    )
    # Set on the kinds that produced a PlatformReviewReport, so the client can
    # link straight to it without fetching the report to find out whether there
    # is one. Null for every other kind.
    report = models.ForeignKey(
        'reviews.PlatformReviewReport',
        on_delete=models.SET_NULL,
        related_name='notifications',
        null=True,
        blank=True,
    )
    is_read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at', '-pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(kind__in=dict(NOTIFICATION_KIND_CHOICES)),
                name='notification_kind_is_known',
            ),
            # A report link only ever points at a report: the other direction
            # (a report with no notification) is legitimate - the email may have
            # failed - so only this half is constrained.
            models.CheckConstraint(
                # `idea_id__isnull`, never `idea__isnull`: a CHECK constraint
                # cannot join, and Django will not build the SQL for one.
                condition=models.Q(report_id__isnull=True) | models.Q(idea_id__isnull=False),
                name='notification_report_needs_idea',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # The notification list, and the unread badge count beside it.
            # Leading on `user` because both reads are always one person's.
            models.Index(fields=['user', '-created_at'], name='notif_user_created_idx'),
            models.Index(fields=['user', 'is_read_at', '-created_at'], name='notif_unread_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.kind} -> {self.user_id}'

    @property
    def is_read(self) -> bool:
        return self.is_read_at is not None
