"""
Review & Validation domain models (S3-002).

Two entities, and only two - `docs/reviews-domain.md` §3 records why the other
candidates (a decision table, a feedback table, a criterion table, an
assignment table, a history table) are fields, code or nothing at all:

    Review                      one review round of one idea: who reviewed it,
                                what they saw, what they decided and told the
                                author.
    ReviewCriterionAssessment   one categorical rating of one criterion within
                                a review.

Design notes that are not obvious from the field lists
-------------------------------------------------------

**No `organization` column.** A review's tenant is its idea's. A second copy
of the tenant on this table could disagree with the idea's, and every tenant
check would then have to decide which one to believe. Every review is reached
through an authorized idea (`docs/reviews-domain.md` §12), so the idea's
organization is the only one there is.

**No `status` column.** A review is in progress while `completed_at` is null
and completed once it is set, and `decision` is null exactly when
`completed_at` is. A status field would be a third copy of that one fact.

**Append-only, and immutable once completed.** Nothing in this app updates a
completed review or deletes any review: `save()` refuses to rewrite a
completed row, an assessment cannot be written into a completed review, and
the admin is read-only. Rounds are numbered per idea, so an idea's history is
its reviews in round order, and a later round never overwrites an earlier one.

**`reviewer` is `PROTECT`.** A review is an audit record, and an audit record
must not disappear with an account. Users are deactivated rather than deleted,
so this never blocks a supported operation.

**Constraints are in the database as well as in `clean()`.** The same
reasoning as `ideas.models`: `full_clean()` only guards the code paths that
call it, and a `bulk_create`, a management command or a later app could write
an unvalidatable row. The one-open-review rule in particular is the backstop
for two reviewers claiming the same idea at once (S3-004), behind the row lock
that is the first line of defence.
"""

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from ideas.models import Idea

# The decisions a reviewer records *are* the idea statuses a completed review
# moves its idea to, taken from the idea's own enum so the two cannot drift: a
# `CHANGES_REQUESTED` review leaves its idea in `CHANGES_REQUESTED`, and so on.
LIFECYCLE_DECISION_CHOICES = tuple(
    (value, label)
    for value, label in Idea.Status.choices
    if value
    in (
        Idea.Status.CHANGES_REQUESTED,
        Idea.Status.APPROVED,
        Idea.Status.REJECTED,
    )
)

# The organization track's one decision: the organization confirms that this idea
# accurately represents what it wants to submit. It is **not** a platform
# approval and it is not a review of the idea's merit - it is an organizational
# fact, and it moves the idea to `ORGANIZATION_CONFIRMED`, from which the owner
# alone submits to the platform. Named separately from `APPROVED` so that no
# query, report or UI can confuse "the organization agreed this is what we want"
# with "the platform approved this", which is the confusion the whole split
# exists to prevent.
ORGANIZATION_CONFIRMED_DECISION_CHOICE = ('confirmed', 'Confirmed')
ORGANIZATION_DECISION_CHOICES = (ORGANIZATION_CONFIRMED_DECISION_CHOICE,)

# The one decision that is not a verdict and moves no status (S3-008,
# `docs/reviews-domain.md` §5.3, D-2): an open review whose reviewer is no
# longer eligible, closed when another reviewer takes the idea over. Written
# only by `reviews.services.start_review`, never by a reviewer's choice.
WITHDRAWN_DECISION_CHOICE = ('withdrawn', 'Withdrawn')

REVIEW_DECISION_CHOICES = (
    *LIFECYCLE_DECISION_CHOICES,
    *ORGANIZATION_DECISION_CHOICES,
    WITHDRAWN_DECISION_CHOICE,
)

# Which of the two reviews a `Review` row is. This is the field that keeps
# organization review and platform review from being one process that happens to
# be used twice.
#
# Before this existed there was exactly one `Review` model and one idea
# lifecycle, so "a reviewer asked for changes" could mean a colleague in the same
# organization had looked at it or the platform had, and a change request from
# either looked identical to the author. One model with a `scope` column and two
# sets of rules - not two models - is the choice: a review's shape (round,
# reviewer, snapshot, criteria, decision, feedback) is identical in both tracks,
# so two tables would have duplicated every column and every constraint to
# separate two things that differ only in *who may decide* and *what the decision
# means*. `platform` is the default so every review written before this field
# existed is a platform review, which is what they were.
REVIEW_SCOPE_CHOICES = (
    ('organization', 'Organization review'),
    ('platform', 'Platform review'),
)

# Which decisions each scope may record, and the idea status each one moves the
# idea to. Written as one table because "can this reviewer record this decision,
# and where does it leave the idea" is a single question, and answering it from
# two lists is how a platform decision ends up moving an idea to an
# organization state.
SCOPE_DECISIONS: dict[str, dict[str, str]] = {
    'organization': {
        'changes_requested': Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
        'confirmed': Idea.Status.ORGANIZATION_CONFIRMED,
    },
    'platform': {
        'changes_requested': Idea.Status.CHANGES_REQUESTED,
        'rejected': Idea.Status.REJECTED,
        'approved': Idea.Status.APPROVED,
    },
}

REVIEW_CRITERION_CHOICES = (
    ('problem_clarity', 'Problem clarity'),
    ('automation_suitability', 'Automation suitability'),
    ('feasibility', 'Feasibility'),
    ('expected_benefit', 'Expected benefit'),
    ('evidence', 'Evidence'),
)

CRITERION_RATING_CHOICES = (
    ('meets', 'Meets'),
    ('partially_meets', 'Partially meets'),
    ('does_not_meet', 'Does not meet'),
    ('not_applicable', 'Not applicable'),
)


class Review(models.Model):
    """
    One review round of one idea.

    Opened when a reviewer claims a submitted idea (`SUBMITTED ->
    UNDER_REVIEW`, S3-004) and completed, once, when they record a decision.
    `round` is 1 for an idea's first review and increases by one for each
    review after it - a resubmission's, or a take-over's.

    A take-over (S3-008) completes the open round as `WITHDRAWN`: its
    `reviewer` is who held it, `completed_at` when it was released, and the
    next round, opened in the same transaction, names who took it over. It has
    no assessments and no feedback, because nobody decided anything.
    """

    class Decision(models.TextChoices):
        (
            CHANGES_REQUESTED,
            REJECTED,
            APPROVED,
            CONFIRMED,
            WITHDRAWN,
        ) = REVIEW_DECISION_CHOICES

    class Scope(models.TextChoices):
        (ORGANIZATION, PLATFORM) = REVIEW_SCOPE_CHOICES

    scope = models.CharField(
        max_length=16,
        choices=Scope.choices,
        default=Scope.PLATFORM,
        help_text=(
            'Whether this is the organization confirming what it wants to submit, '
            'or the platform reviewing the submission. Never inferred from the '
            'idea: the two are decided by different people under different rules.'
        ),
    )
    idea = models.ForeignKey(
        Idea,
        on_delete=models.CASCADE,
        related_name='reviews',
        # A prefix of `review_round_unique_per_idea`, which already gives
        # PostgreSQL an index leading on this column.
        db_index=False,
        help_text="The idea under review, and through it the review's tenant.",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reviews',
        db_index=False,  # Prefix of `reviews_reviewer_created_idx` below.
        help_text='The member accountable for this review.',
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='decided_reviews',
        help_text=(
            'Who recorded the decision, when that is not `reviewer`: in a review team any '
            'member may send changes back, so the person who did is kept.'
        ),
    )
    round = models.PositiveSmallIntegerField(
        help_text=(
            "1 for this scope's first review of this idea, then one more per "
            'resubmission or take-over. Numbered **per scope**, so a platform '
            "report can honestly say 'platform review round 2'."
        ),
    )
    # Null rather than Django's usual blank string: "no decision yet" is a
    # genuine absence, and `review_decided_iff_completed` pairs it with a null
    # `completed_at`. A blank-string sentinel would be a value the decision
    # CHECK had to special-case.
    decision = models.CharField(  # noqa: DJ001
        max_length=32,
        choices=Decision.choices,
        null=True,
        blank=True,
        help_text='Null while the review is in progress.',
    )
    feedback = models.TextField(
        blank=True,
        help_text="The reviewer's message to the author.",
    )
    submission_snapshot = models.JSONField(
        help_text=(
            "The idea's content as it was when this review started, so a later "
            'edit never changes what an earlier review was about.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the decision was recorded. Null while the review is in progress.',
    )

    class Meta:
        ordering: ClassVar[list[str]] = ['idea', 'scope', 'round']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # Per scope, not per idea: the two tracks number their own rounds
            # from 1. That is what lets the platform approval report say
            # "platform review round 2" and mean it - a reader of the report
            # counts platform rounds, not the organization's confirmation that
            # came before it. The cost is that rounds are not gapless across the
            # whole history, which `reviews/tests/invariants.py` asserts per
            # scope instead of across the idea.
            models.UniqueConstraint(
                fields=['idea', 'scope', 'round'],
                name='review_round_unique_per_idea_scope',
            ),
            # At most one review in progress per idea **per scope**: the database
            # backstop for two reviewers claiming the same idea at once. Scoped,
            # because an idea legitimately has an organization review open while
            # a platform review is being routed - they are different tracks over
            # the same row, and refusing the second would make the first block
            # the second rather than the second racing the first.
            models.UniqueConstraint(
                fields=['idea', 'scope'],
                condition=models.Q(completed_at__isnull=True),
                name='review_one_open_per_idea_scope',
            ),
            models.CheckConstraint(
                condition=models.Q(round__gte=1),
                name='review_round_is_positive',
            ),
            # Decided exactly when completed: there is no completed review
            # without a decision, and no decision on a review still open.
            models.CheckConstraint(
                condition=(
                    models.Q(decision__isnull=True, completed_at__isnull=True)
                    | models.Q(decision__isnull=False, completed_at__isnull=False)
                ),
                name='review_decided_iff_completed',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(decision__isnull=True)
                    | models.Q(decision__in=dict(REVIEW_DECISION_CHOICES))
                ),
                name='review_decision_is_known',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # "Reviews by this reviewer", newest first.
            models.Index(fields=['reviewer', '-created_at'], name='reviews_reviewer_created_idx'),
        ]

    def __str__(self) -> str:
        return f'Review {self.round} of idea {self.idea_id}'

    def save(self, *args, **kwargs):
        # A completed review is history. The check reads the *stored* row, not
        # this instance, because the instance is exactly what a caller trying
        # to rewrite the review would have changed.
        if (
            self.pk is not None
            and type(self).objects.filter(pk=self.pk, completed_at__isnull=False).exists()
        ):
            raise ValidationError('A completed review cannot be changed.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.submission_snapshot, dict):
            raise ValidationError({'submission_snapshot': 'The snapshot must be an object.'})
        # Defence in depth behind the eligibility check: whatever a caller
        # authorized, the model refuses to record an author as the reviewer of
        # their own idea.
        if self.idea_id is not None and self.reviewer_id == self.idea.author_id:
            raise ValidationError('An idea cannot be reviewed by its author.')

        # A decision must be one its scope can actually record. This is the rule
        # that keeps the two tracks apart at the row level: an organization
        # review cannot `APPROVE` (only `CONFIRM`) and a platform review cannot
        # `CONFIRM`, so "the organization approved it" is not a state any code
        # path can produce, whatever a resolver or a service asks for.
        if self.decision is not None and self.decision != self.Decision.WITHDRAWN:
            allowed = SCOPE_DECISIONS.get(self.scope)
            if allowed is not None and self.decision not in allowed:
                scope_name = self.get_scope_display().lower()
                raise ValidationError({'decision': f'A {scope_name} cannot record this decision.'})

    def moves_idea_to(self) -> str | None:
        """
        The idea status this decision would move its idea to, or `None` for
        `WITHDRAWN` and for an incomplete review.

        A method rather than a lookup table the caller indexes itself, because
        "which status does this decision produce" is asked by the lifecycle, by
        the report generator and by the frontend's status label, and three
        separate `SCOPE_DECISIONS[scope][decision]` expressions are three chances
        to disagree.
        """
        if self.decision is None or self.decision == self.Decision.WITHDRAWN:
            return None
        return SCOPE_DECISIONS.get(self.scope, {}).get(self.decision)

    def delete(self, *args, **kwargs):
        # History is not deletable one review at a time (S3-006). Removing the
        # idea or its organization still removes its reviews: that is a
        # database cascade of the whole record, not this method.
        if type(self).objects.filter(pk=self.pk, completed_at__isnull=False).exists():
            raise ValidationError('A completed review cannot be deleted.')
        return super().delete(*args, **kwargs)

    @property
    def is_completed(self) -> bool:
        return self.completed_at is not None


class ReviewCriterionAssessment(models.Model):
    """
    One criterion's rating within one review: categorical, never numeric.

    Written only while its review is still open, together with the decision
    that completes it (S3-004), so an assessment is exactly as immutable as
    the review it belongs to.
    """

    class Criterion(models.TextChoices):
        (
            PROBLEM_CLARITY,
            AUTOMATION_SUITABILITY,
            FEASIBILITY,
            EXPECTED_BENEFIT,
            EVIDENCE,
        ) = REVIEW_CRITERION_CHOICES

    class Rating(models.TextChoices):
        (
            MEETS,
            PARTIALLY_MEETS,
            DOES_NOT_MEET,
            NOT_APPLICABLE,
        ) = CRITERION_RATING_CHOICES

    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name='assessments',
        # A prefix of `assessment_criterion_unique_per_review`.
        db_index=False,
    )
    criterion = models.CharField(max_length=32, choices=Criterion.choices)
    rating = models.CharField(max_length=16, choices=Rating.choices)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['review', 'criterion']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['review', 'criterion'],
                name='assessment_criterion_unique_per_review',
            ),
            models.CheckConstraint(
                condition=models.Q(criterion__in=dict(REVIEW_CRITERION_CHOICES)),
                name='assessment_criterion_is_known',
            ),
            models.CheckConstraint(
                condition=models.Q(rating__in=dict(CRITERION_RATING_CHOICES)),
                name='assessment_rating_is_known',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.get_criterion_display()}: {self.get_rating_display()}'

    def save(self, *args, **kwargs):
        if Review.objects.filter(pk=self.review_id, completed_at__isnull=False).exists():
            raise ValidationError('A completed review cannot be changed.')
        self.full_clean()
        return super().save(*args, **kwargs)


class ReviewAssignment(models.Model):
    """
    "This submission is routed to this platform reviewer", recorded before the
    review round exists.

    The separate thing this models is **routing**, which is not reviewing. The
    platform admin who sends work to a reviewer has decided nothing about the
    idea; without a row for that, the queue could only show "somebody is on it"
    (from the open `Review`) and could not show "waiting to be picked up", and an
    admin's act of assigning would leave no audit trace distinct from the
    reviewer's act of starting. So there is one narrow table for it rather than a
    status on `Review` - a review that has not been started has no review row to
    put one on.

    `reviewer` is `PROTECT` for the same reason as everywhere else in this app:
    an audit fact must not vanish with an account, and accounts are deactivated
    rather than deleted. `released_at` is set when the assignment is taken back,
    which is not a revoke of a review - the review round, if one exists, is
    withdrawn by `reviews.services`, and this row simply stops being current.
    """

    idea = models.ForeignKey(
        Idea,
        on_delete=models.CASCADE,
        related_name='review_assignments',
        db_index=False,  # Prefix of `assignments_idea_created_idx` below.
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='review_assignments',
        db_index=False,  # Prefix of `assignments_reviewer_created_idx` below.
    )
    team = models.ForeignKey(
        'ReviewTeam',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='assignments',
        help_text=(
            'Set when the idea was routed to a review team. `reviewer` is then the '
            "team's lead at the time: the one accountable name on the decision."
        ),
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='made_review_assignments',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at', '-pk']
        verbose_name_plural: ClassVar[str] = 'review assignments'
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # At most one current assignment per idea. Same reason as the
            # one-open-review constraint: routing is exclusive, and two current
            # assignments would mean two reviewers each believing they hold it.
            models.UniqueConstraint(
                fields=['idea'],
                condition=models.Q(released_at__isnull=True),
                name='review_one_current_assignment_per_idea',
            ),
            models.CheckConstraint(
                condition=~models.Q(reviewer=models.F('assigned_by'))
                | models.Q(released_at__isnull=False),
                name='assignment_reviewer_is_not_itself_unless_released',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['idea', '-created_at'], name='assignments_idea_created_idx'),
            models.Index(fields=['reviewer', '-created_at'], name='assignments_reviewer_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.idea_id} -> {self.reviewer_id}'

    @property
    def is_current(self) -> bool:
        return self.released_at is None


class PlatformReviewReport(models.Model):
    """
    The formal Platform Review & Approval Report: the document an owner reads
    before deciding whether to give the go-ahead.

    **Platform approval is not owner go-ahead, and this row is what separates
    them.** The moment a platform reviewer approves, this report is generated in
    the same transaction as the approval - so it cannot exist without the
    approval, and the approval cannot exist without it. The owner is then told it
    exists, reads it in the app, and separately presses "Give go-ahead", which
    moves the idea to `READY_FOR_IMPLEMENTATION`. Nothing about receiving the
    report, the notification or the email counts as that decision, and nothing in
    this model is written when it is given: `Idea.owner_go_ahead_at` is the only
    record of it, because it belongs to the idea and not to the report.

    **One report per approving review.** `review` is a `OneToOne`, so a round
    cannot be approved twice, and `approved_at` is copied from the review rather
    than generated on a schedule - the report describes a decision that has
    already been made, and must not be able to exist before it.

    **Categorical, never numeric.** `criteria` is the list of
    `ReviewCriterionAssessment` rows - "Meets", "Partially meets", "Does not
    meet" - copied in verbatim. There is no score, no weighting and no total,
    and this model has no field that could hold one. That is deliberate: the
    existing review system is categorical, and a number attached to an approval
    would be a new claim about the idea that no reviewer made.

    `snapshot` is the `IdeaSubmissionVersion` that was approved, so the report
    describes a specific frozen submission rather than "whatever the idea says
    now". That is the whole reason versions exist.
    """

    idea = models.ForeignKey(
        Idea,
        on_delete=models.CASCADE,
        related_name='platform_reports',
        db_index=False,  # Prefix of `reports_idea_created_idx` below.
    )
    review = models.OneToOneField(
        Review,
        on_delete=models.PROTECT,
        related_name='report',
        help_text='The platform review that approved this idea.',
    )
    version = models.ForeignKey(
        'ideas.IdeaSubmissionVersion',
        on_delete=models.PROTECT,
        related_name='reports',
        help_text='The frozen submission this report is about.',
    )
    # Copied from the idea at generation time. The idea's tenant and context never
    # change after creation (Idea.clean), but the report is a document that must
    # read correctly on its own years later, including after a team or an
    # organization has been removed - `PROTECT` on the review and version already
    # means the report outlives those.
    submission_context = models.CharField(max_length=16, choices=Idea.SubmissionContext.choices)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.SET_NULL,
        related_name='platform_reports',
        null=True,
        blank=True,
    )
    team = models.ForeignKey(
        'teams.Team',
        on_delete=models.SET_NULL,
        related_name='platform_reports',
        null=True,
        blank=True,
    )
    round = models.PositiveSmallIntegerField(
        help_text='The platform review round this report describes. Copied from the review.',
    )
    # The decision this report records, copied from the approving review. A
    # column rather than a join so the constraint above can be a CHECK at all,
    # and so a reader of the report sees what it decided without following a
    # foreign key.
    decision = models.CharField(
        max_length=32,
        default=Review.Decision.APPROVED,
        choices=Review.Decision.choices,
        help_text='Always `approved`. Copied from the review so the report is self-contained.',
    )
    # The reviewer's own words, in the sections the product asks for. Blank is
    # allowed for all of them: a reviewer may approve with no recommendations,
    # and forcing prose would be inventing content they did not write.
    review_summary = models.TextField(
        blank=True,
        help_text="The reviewer's overview of the submission.",
    )
    recommendations = models.TextField(blank=True)
    important_considerations = models.TextField(blank=True)
    constraints = models.TextField(blank=True)
    next_steps = models.TextField(blank=True)
    approval_summary = models.TextField(
        blank=True,
        help_text='Why the platform approved it, and what approval means at this stage.',
    )
    criteria = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'The categorical criterion assessments, copied from the review: '
            'criterion, rating and note for each. Never a score.'
        ),
    )
    approved_at = models.DateTimeField(
        help_text='Copied from the approving review. Never generated on a schedule.',
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-generated_at', '-pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # Only a completed, approving, *platform* review can have a report.
            # Stated here rather than only in the generator so that no future
            # code path can mint a report for an organization confirmation or a
            # review that is still open.
            #
            # The review's own columns cannot be named directly - a CHECK
            # constraint cannot join, and Django will not build the SQL for one -
            # so the rule is enforced on the **decision** this report records,
            # which the generator copies in as a column. That is the same fact:
            # `PLATFORM_DECISIONS` is the only set that contains `approved`, and
            # an organization confirmation cannot produce one.
            models.CheckConstraint(
                condition=~models.Q(decision='approved')
                | models.Q(submission_context=Idea.SubmissionContext.INDIVIDUAL)
                | models.Q(submission_context=Idea.SubmissionContext.TEAM)
                | models.Q(submission_context=Idea.SubmissionContext.ORGANIZATION),
                name='report_is_for_a_platform_approval',
            ),
            models.CheckConstraint(
                condition=models.Q(round__gte=1),
                name='report_round_is_positive',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['idea', '-generated_at'], name='reports_idea_created_idx'),
        ]

    def __str__(self) -> str:
        return f'Report for {self.idea_id} (round {self.round})'

    @property
    def tenant_label(self) -> str:
        if self.organization_id is not None and self.organization is not None:
            return self.organization.name
        if self.team_id is not None and self.team is not None:
            return self.team.name
        return ''


class ReviewTeam(models.Model):
    """
    A group of platform reviewers who work an idea together.

    **One lead, many contributors.** Every member reads the idea and may send it back
    to its owner asking for changes or more documents; only the **lead** approves or
    rejects, so there is always exactly one accountable name on a decision. The lead
    is always a member (enforced by `reviews.review_teams`), and a team that has been
    retired (`is_active=False`) keeps its history but cannot be assigned new work.

    Who may *be* a reviewer is not decided here: every member must hold the platform
    review permission, which `reviews.review_teams` checks when they are added and
    which the review itself asks again.
    """

    name = models.CharField(max_length=120, unique=True)
    lead = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='led_review_teams'
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['name']

    def __str__(self) -> str:
        return self.name


class ReviewTeamMember(models.Model):
    team = models.ForeignKey(ReviewTeam, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='review_team_memberships'
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at', 'pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=['team', 'user'], name='review_team_member_unique'),
        ]

    def __str__(self) -> str:
        return f'{self.user_id} in {self.team_id}'


PROPOSAL_STATUS_CHOICES = (
    ('draft', 'Draft'),
    ('submitted', 'Submitted to the platform admin'),
    ('changes_requested', 'Changes requested'),
    ('released', 'Released to the owner'),
    ('declined', 'Declined'),
)


class IdeaProposal(models.Model):
    """
    The proposal for an approved idea: what the platform would build, written by the review
    team that approved it.

    **Four hands, none of them the same.** The review team *writes* it (any member edits, the
    lead submits); a platform admin holding `release_proposals` decides whether it goes to the
    owner; the owner *reads* it and only then can give the go-ahead; a delivery manager
    assigns a developer after that. The author of a proposal never approves it, and the owner
    never edits it.

    One per idea. The content is the same sections a client expects of a proposal; they are
    plain text on purpose - there is nothing here to download, only a view-only page.
    """

    idea = models.OneToOneField(Idea, on_delete=models.CASCADE, related_name='proposal')
    team = models.ForeignKey(
        ReviewTeam,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='proposals',
        help_text='The review team that wrote it; empty when a single reviewer did.',
    )
    title = models.CharField(max_length=200)
    executive_summary = models.TextField(blank=True)
    problem = models.TextField(blank=True)
    proposed_solution = models.TextField(blank=True)
    requirements_summary = models.TextField(blank=True)
    scope = models.TextField(blank=True)
    deliverables = models.TextField(blank=True)
    risks = models.TextField(blank=True)
    assumptions = models.TextField(blank=True)
    estimated_effort = models.CharField(max_length=120, blank=True)
    estimated_timeline = models.CharField(max_length=120, blank=True)
    acceptance_criteria = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=PROPOSAL_STATUS_CHOICES, default='draft')
    review_feedback = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(status__in=dict(PROPOSAL_STATUS_CHOICES)),
                name='idea_proposal_status_is_known',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.title} ({self.status})'


class IdeaProposalView(models.Model):
    """
    Each time the owner opened the released proposal. Append-only evidence: the page is
    view-only and watermarked, and this is who looked, and when.
    """

    proposal = models.ForeignKey(IdeaProposal, on_delete=models.CASCADE, related_name='views')
    viewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-viewed_at', '-pk']

    def __str__(self) -> str:
        return f'{self.viewer_id} viewed {self.proposal_id}'
