"""
Platform administration models.

One entity, and it is an audit record: `AdminAuditEntry`. The administration
console owns no business data of its own - users, organizations, ideas,
reviews and categories stay in the apps that own them, and the console reads
and changes them only through those apps' rules. What it adds is the record of
who used administrative power, on what, and with which result.

**Why this is also where the platform permissions are declared.** Platform
administration is a *platform-scoped* authorization concern, and the project
already has exactly one platform-scoped permission mechanism: Django's own
`auth.Permission`, reached through `identity.User`'s `PermissionsMixin`
(`user.has_perm`, groups, per-user grants, `is_superuser`). The organization
`Role`/`Permission` tables are deliberately *organization*-scoped - every
`Role` belongs to one organization - so they cannot express "may act across
every tenant" without inventing a fake platform organization, and an
organization Owner must never become a platform administrator by holding a
role there. Declaring the console's permissions in `Meta.permissions` here
reuses Django's mechanism instead of building a second framework, and gives
the codes a stable home: `administration.<codename>`.

`default_permissions = ()` because the default add/change/delete/view codes
would describe editing the audit log, which is not an operation that exists.

**Why a new audit table.** The existing audit trail, `ideas.IdeaTransition`,
records one thing - an idea's status moving - and has an idea foreign key and
status columns that mean nothing for "an administrator deactivated an
account". Reusing it would mean loosening its constraints for every row; so
this is a second, narrow append-only table with the same guarantees, not a
generic event store.
"""

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

ADMIN_AUDIT_ACTION_CHOICES = (
    ('user.activated', 'User activated'),
    ('user.deactivated', 'User deactivated'),
    ('membership_role.assigned', 'Organization role assigned'),
    ('membership_role.removed', 'Organization role removed'),
    ('category.created', 'Category created'),
    ('category.updated', 'Category updated'),
    ('category.activated', 'Category activated'),
    ('category.deactivated', 'Category deactivated'),
    ('attachment.downloaded', 'Attachment downloaded'),
    ('platform_admin.granted', 'Platform administration granted'),
    ('platform_admin.revoked', 'Platform administration revoked'),
    # The platform review track (submission-context phase). These are the
    # platform's own business events, audited in the same append-only table as
    # the console's administrative use of power, because they are the same kind
    # of fact: an account-independent decision about an idea somebody else
    # wrote.
    ('platform_reviewer.assigned', 'Platform reviewer assigned'),
    ('platform_reviewer.unassigned', 'Platform reviewer unassigned'),
    ('platform_submission.locked', 'Platform submission locked'),
    ('platform_review.approved', 'Platform review approved'),
    ('platform_review.changes_requested', 'Platform review requested changes'),
    ('platform_review.rejected', 'Platform review rejected'),
    ('report.generated', 'Platform review report generated'),
    ('idea.owner_go_ahead', 'Idea owner gave go-ahead'),
    # Who may review, and in which team (the review-team phase).
    ('reviewer.granted', 'Platform reviewer created'),
    ('reviewer.revoked', 'Platform reviewer removed'),
    ('review_team.created', 'Review team created'),
    ('review_team.updated', 'Review team changed'),
    ('review_team.assigned', 'Idea assigned to a review team'),
    # The proposal a review team writes after approval, and the admin's decision on it.
    ('proposal.submitted', 'Proposal submitted to the platform admin'),
    ('proposal.changes_requested', 'Proposal sent back to the review team'),
    ('proposal.released', 'Proposal released to its owner'),
    ('proposal.declined', 'Proposal declined'),
)

ADMIN_AUDIT_RESULT_CHOICES = (
    ('succeeded', 'Succeeded'),
    ('refused', 'Refused'),
)


class AdminAuditEntry(models.Model):
    """
    One use of administrative power: who, what, on which target, when, and
    whether it went through.

    **Append-only**, like `ideas.IdeaTransition`: `save()` refuses to rewrite a
    stored row and `delete()` refuses outright, and the Django admin is
    read-only. There is no service or mutation that changes one.

    **Never a secret.** `metadata` carries identifiers and the before/after of
    the fields that changed - never a password, token, credential or file
    content. `administration.services` is the only writer and builds it from a
    fixed set of keys.

    `actor` is nullable only for operations run from the command line
    (`grant_platform_admin`), where there is no authenticated platform user;
    every console operation records the authenticated caller. `PROTECT`, for
    the same reason `reviews.Review.reviewer` is: an audit record must not
    disappear with an account, and accounts are deactivated, not deleted.

    `target_label` is a snapshot (an email, a category name) so the entry still
    reads sensibly if the target is later renamed; `target_type`/`target_id`
    are the stable reference.
    """

    class Action(models.TextChoices):
        (
            USER_ACTIVATED,
            USER_DEACTIVATED,
            MEMBERSHIP_ROLE_ASSIGNED,
            MEMBERSHIP_ROLE_REMOVED,
            CATEGORY_CREATED,
            CATEGORY_UPDATED,
            CATEGORY_ACTIVATED,
            CATEGORY_DEACTIVATED,
            ATTACHMENT_DOWNLOADED,
            PLATFORM_ADMIN_GRANTED,
            PLATFORM_ADMIN_REVOKED,
            PLATFORM_REVIEWER_ASSIGNED,
            PLATFORM_REVIEWER_UNASSIGNED,
            PLATFORM_SUBMISSION_LOCKED,
            PLATFORM_REVIEW_APPROVED,
            PLATFORM_REVIEW_CHANGES_REQUESTED,
            PLATFORM_REVIEW_REJECTED,
            REPORT_GENERATED,
            IDEA_OWNER_GO_AHEAD,
            REVIEWER_GRANTED,
            REVIEWER_REVOKED,
            REVIEW_TEAM_CREATED,
            REVIEW_TEAM_UPDATED,
            REVIEW_TEAM_ASSIGNED,
            PROPOSAL_SUBMITTED,
            PROPOSAL_CHANGES_REQUESTED,
            PROPOSAL_RELEASED,
            PROPOSAL_DECLINED,
        ) = ADMIN_AUDIT_ACTION_CHOICES

    class Result(models.TextChoices):
        SUCCEEDED, REFUSED = ADMIN_AUDIT_RESULT_CHOICES

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='admin_audit_entries',
        help_text='The authenticated administrator. Null only for command-line operations.',
    )
    action = models.CharField(max_length=64, choices=Action.choices)
    result = models.CharField(max_length=16, choices=Result.choices)
    target_type = models.CharField(max_length=32, help_text="e.g. 'user', 'category'.")
    target_id = models.CharField(max_length=64)
    target_label = models.CharField(max_length=255, blank=True)
    # A plain id rather than a foreign key: the entry must outlive the
    # organization (a cascade would erase the record of what was done to it),
    # and SET_NULL would erase exactly the fact the entry exists to keep.
    organization_id = models.BigIntegerField(null=True, blank=True)
    message = models.CharField(
        max_length=255,
        blank=True,
        help_text='For a refusal, the reason given to the administrator.',
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at', '-pk']
        verbose_name_plural = 'admin audit entries'
        default_permissions = ()
        permissions: ClassVar[list[tuple[str, str]]] = [
            ('access_console', 'Can access the platform administration console'),
            (
                'inspect_idea_content',
                'Can inspect idea content, discussion, evidence and review feedback '
                'across organizations',
            ),
            ('manage_user_accounts', 'Can activate and deactivate user accounts'),
            ('manage_organization_roles', 'Can assign and remove organization roles'),
            ('manage_categories', 'Can create, edit and retire idea categories'),
            # --- the platform review track -------------------------------------
            # Split in two on purpose, because intake and deciding are different
            # acts with different consequences. INSPECT is enough to open a
            # submission and read it; REVIEW is what lets somebody approve,
            # request changes or reject one. It lives here - on a platform-scoped
            # model, reachable only through Django's permission backend - so it
            # is structurally impossible to grant by holding an organization
            # Owner or Reviewer role, or a team Owner role. That is the whole of
            # "platform reviewers are independent".
            (
                'review_platform_submissions',
                'Can inspect, review and decide ideas submitted to the platform',
            ),
            (
                'assign_platform_reviewers',
                'Can assign and remove the platform reviewer of a submission',
            ),
            # Creating reviewers and forming review teams is a different power from
            # routing work to them, so the two can be held by different people.
            (
                'manage_reviewers',
                'Can create and remove platform reviewers and form review teams',
            ),
            # The admin who decides whether an idea's proposal goes to its owner. Held
            # by neither reviewers (who write it) nor owners (who read it).
            (
                'release_proposals',
                "Can approve a review team's proposal and release it to the idea owner",
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(action__in=dict(ADMIN_AUDIT_ACTION_CHOICES)),
                name='admin_audit_action_is_known',
            ),
            models.CheckConstraint(
                condition=models.Q(result__in=dict(ADMIN_AUDIT_RESULT_CHOICES)),
                name='admin_audit_result_is_known',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # The console's two reads: the recent feed, and one target's history.
            models.Index(fields=['-created_at'], name='admin_audit_created_idx'),
            models.Index(
                fields=['target_type', 'target_id', '-created_at'],
                name='admin_audit_target_idx',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.action} {self.target_type}:{self.target_id} ({self.result})'

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('A recorded audit entry cannot be changed.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('A recorded audit entry cannot be deleted.')
