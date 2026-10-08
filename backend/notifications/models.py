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
    # --- automation delivery (Sprint 4) ----------------------------------------
    ('proposal.submitted', 'A proposal is waiting for your decision'),
    ('proposal.writing_opened', 'You can now write the proposal'),
    ('proposal.owner_declined', 'The owner decided not to go ahead'),
    ('proposal.changes_requested', 'Your proposal was sent back'),
    ('proposal.released', 'Your proposal is ready to read'),
    ('proposal.declined', 'Your idea will not be developed'),
    ('delivery.go_ahead_received', 'An approved idea is waiting for a developer'),
    ('automation.opportunity_created', 'An automation opportunity was opened'),
    ('automation.requirements_ready', 'Requirements are ready'),
    ('automation.proposal_submitted', 'A proposal was submitted'),
    ('automation.proposal_changes_requested', 'Changes were requested on a proposal'),
    ('automation.ready_for_assignment', 'An opportunity is ready for assignment'),
    ('automation.opportunity_assigned', 'An opportunity was assigned'),
    ('automation.project_created', 'A project was created'),
    ('automation.project_started', 'A project has started'),
    ('automation.ready_for_uat', 'A project is ready for acceptance testing'),
    ('automation.testing_failed', 'A required test failed'),
    ('automation.uat_passed', 'Acceptance testing passed'),
    ('automation.uat_failed', 'Acceptance testing failed'),
    ('automation.deployment_completed', 'A deployment completed'),
    ('automation.milestone_completed', 'A milestone was completed'),
    ('automation.project_completed', 'A project was completed'),
    ('automation.impact_recorded', 'Impact was recorded'),
    # --- the review queues -----------------------------------------------------
    ('review.assigned', 'An idea was assigned to you'),
    ('review.organization_queue', 'An idea is waiting for organization review'),
    ('review.platform_queue', 'An idea is waiting for platform review'),
    ('review.decision_awaiting_release', 'A decision is waiting to be sent to its owner'),
    # --- invitations and messaging ---------------------------------------------
    ('invitation.received', 'You have been invited'),
    ('invitation.accepted', 'Your invitation was accepted'),
    ('invitation.declined', 'Your invitation was declined'),
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


#: Where a notification of each kind takes you, as a path **inside the frontend**.
#:
#: Stored nowhere on purpose. The destination is a fact about the product's
#: routes, and a second copy in the database is a second thing that can go stale
#: when a route is renamed - so it is derived here, from the kind and the ids the
#: row already carries, and the same mapping is used for the in-app link and for
#: any push payload built from the same row. One mapping, two channels.
#:
#: `None` means "there is nowhere specific to go", and the client falls back to
#: the notifications list. That is a real answer and not a gap: an invitation for
#: an address with no account, or a notification about something that no longer
#: exists, has no page to open.
NOTIFICATION_LABELS = {
    'idea.organization_changes_requested': 'Changes requested by your organization',
    'idea.organization_confirmed': 'Your organization confirmed your idea',
    'idea.submitted_to_platform': 'Your idea was submitted to the platform',
    'idea.platform_changes_requested': 'The platform asked for changes',
    'idea.platform_rejected': 'The platform did not approve your idea',
    'idea.platform_approved': 'Platform review completed',
    'idea.owner_go_ahead': 'Your idea is ready for implementation',
    'proposal.submitted': 'A proposal is waiting for your decision',
    'proposal.writing_opened': 'You can now write the proposal',
    'proposal.owner_declined': 'The owner decided not to go ahead',
    'proposal.changes_requested': 'Your proposal was sent back',
    'proposal.released': 'Your proposal is ready to read',
    'proposal.declined': 'Your idea will not be developed',
    'delivery.go_ahead_received': 'An approved idea is waiting for a developer',
    'automation.opportunity_created': 'An automation opportunity was opened',
    'automation.requirements_ready': 'Requirements are ready',
    'automation.proposal_submitted': 'A proposal was submitted',
    'automation.proposal_changes_requested': 'Changes were requested on a proposal',
    'automation.ready_for_assignment': 'An opportunity is ready for assignment',
    'automation.opportunity_assigned': 'An opportunity was assigned',
    'automation.project_created': 'A project was created',
    'automation.project_started': 'A project has started',
    'automation.ready_for_uat': 'A project is ready for acceptance testing',
    'automation.testing_failed': 'A required test failed',
    'automation.uat_passed': 'Acceptance testing passed',
    'automation.uat_failed': 'Acceptance testing failed',
    'automation.deployment_completed': 'A deployment completed',
    'automation.milestone_completed': 'A milestone was completed',
    'automation.project_completed': 'A project was completed',
    'automation.impact_recorded': 'Impact was recorded',
    'review.assigned': 'An idea was assigned to you',
    'review.organization_queue': 'An idea is waiting for organization review',
    'review.platform_queue': 'An idea is waiting for platform review',
    'review.decision_awaiting_release': 'A decision is waiting to be sent to its owner',
    'invitation.received': 'You have been invited',
    'invitation.accepted': 'Your invitation was accepted',
    'invitation.declined': 'Your invitation was declined',
    'message.received': 'You have a new message',
}

#: The paths themselves. Kept apart from the labels above so this module holds
#: no routing vocabulary and a rename is one edit in this one table.
NOTIFICATIONS_PATH = '/app/notifications'
IDEA_PATH = '/app/ideas'
IDEA_REPORT_PATH = '/app/ideas/{idea_id}/report'
MESSAGES_PATH = '/app/messages'
DECISIONS_PATH = '/app/admin/decisions'
ADMIN_PROPOSALS_PATH = '/app/admin/proposals'
#: Where each kind sends its reader to act - the page that owns the work, not just the idea.
#: `{idea_id}` is filled from the row. Kinds not listed fall back to the idea, or the list.
WORK_PATHS = {
    # The reviewer's queue, opened on the idea.
    'review.assigned': '/app/reviews?idea={idea_id}',
    'review.platform_queue': '/app/reviews?idea={idea_id}',
    'review.organization_queue': '/app/reviews?idea={idea_id}',
    # The review team's proposal workspace.
    'proposal.writing_opened': '/app/reviews/proposals/{idea_id}',
    'proposal.changes_requested': '/app/reviews/proposals/{idea_id}',
    'proposal.owner_declined': '/app/reviews/proposals/{idea_id}',
    # The owner's reading of the released proposal.
    'proposal.released': '/app/ideas/{idea_id}/proposal',
    # The administrators' console pages.
    'review.decision_awaiting_release': DECISIONS_PATH,
    'proposal.submitted': ADMIN_PROPOSALS_PATH,
    # The owner's idea page, opened on the section the notification is about.
    'idea.platform_changes_requested': '/app/ideas/{idea_id}#respond',
    'idea.organization_changes_requested': '/app/ideas/{idea_id}#respond',
    'idea.platform_approved': '/app/ideas/{idea_id}#decision-letter',
    'idea.platform_rejected': '/app/ideas/{idea_id}#decision-letter',
    'proposal.declined': '/app/ideas/{idea_id}#proposal',
    # Delivery: the developer queue for the people who assign it.
    'delivery.go_ahead_received': '/app/automation/queue',
}

#: Delivery notifications open the idea's project, on the tab the news is about.
PROJECT_TABS = {
    'automation.project_created': 'overview',
    'automation.project_started': 'overview',
    'automation.project_completed': 'overview',
    'automation.milestone_completed': 'work',
    'automation.testing_failed': 'testing',
    'automation.ready_for_uat': 'uat',
    'automation.uat_passed': 'uat',
    'automation.uat_failed': 'uat',
    'automation.deployment_completed': 'deployment',
    'automation.impact_recorded': 'impact',
}
#: ...and opportunity news opens the opportunity, on its tab.
OPPORTUNITY_TABS = {
    'automation.opportunity_assigned': 'assignment',
    'automation.ready_for_assignment': 'assignment',
    'automation.opportunity_created': 'overview',
}


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
    def action_path(self) -> str | None:
        """
        The in-app destination for this notification, or `None`.

        Derived from the kind and the ids the row already holds, so it cannot
        disagree with the row and needs no column to fall out of date. The rules
        are deliberately conservative:

        - A notification carrying a `report` goes to that idea's report page -
          the one place a decision is spelled out. `DECISION_KINDS` is the gate,
          so a queue nudge cannot send somebody to an approval report.
        - A notification carrying an `idea` goes to that idea, **only when this
          recipient may actually read it**. A notification outlives the access
          it was written under: an author who was removed from an organization,
          or a deactivated account reading on a shared device, must not be handed
          a link to something they can no longer open.
        - Everything else - an invitation, a message, a queue nudge - goes to the
          list, or nowhere at all. In particular **an invitation notification has
          no accept link**: acceptance needs the plaintext token, which exists
          only in the email because only its digest is stored, so there is
          nothing here that could honestly produce one.
        """
        if self.report_id is not None and self.kind in DECISION_KINDS:
            return IDEA_REPORT_PATH.format(idea_id=self.idea_id)
        delivery_path = self._delivery_path()
        if delivery_path is not None:
            return delivery_path
        work_path = WORK_PATHS.get(self.kind)
        if work_path is not None and (self.idea_id is not None or '{' not in work_path):
            path = work_path.format(idea_id=self.idea_id)
            if not path.startswith(f'{IDEA_PATH}/'):
                # A workspace or console page, which checks its reader itself.
                return path
            # A section of the idea's page: only for somebody who can still read the idea.
            from ideas import selectors as idea_selectors

            return path if idea_selectors.can_view_idea(self.user, self.idea) else None
        if self.idea_id is not None:
            from ideas import selectors as idea_selectors

            if idea_selectors.can_view_idea(self.user, self.idea):
                return f'{IDEA_PATH}/{self.idea_id}'
        if self.kind == 'message.received':
            return MESSAGES_PATH
        return None

    def _delivery_path(self) -> str | None:
        """The project or opportunity page, on its tab, for delivery news about this idea."""
        if self.idea_id is None or (
            self.kind not in PROJECT_TABS and self.kind not in OPPORTUNITY_TABS
        ):
            return None
        from automation.models import AutomationOpportunity, Project

        if self.kind in PROJECT_TABS:
            project = Project.objects.filter(opportunity__idea_id=self.idea_id).first()
            if project is not None:
                return f'/app/automation/projects/{project.pk}?tab={PROJECT_TABS[self.kind]}'
        opportunity = (
            AutomationOpportunity.objects.filter(idea_id=self.idea_id)
            .exclude(status='cancelled')
            .first()
        )
        if opportunity is None:
            return None
        tab = OPPORTUNITY_TABS.get(self.kind, 'project')
        return f'/app/automation/opportunities/{opportunity.pk}?tab={tab}'

    @property
    def is_read(self) -> bool:
        return self.is_read_at is not None
