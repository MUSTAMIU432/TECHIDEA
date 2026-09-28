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

# The decision values *are* the idea statuses a completed review moves its idea
# to, taken from the idea's own enum so the two cannot drift: a
# `CHANGES_REQUESTED` review leaves its idea in `CHANGES_REQUESTED`, and so on.
REVIEW_DECISION_CHOICES = tuple(
    (value, label)
    for value, label in Idea.Status.choices
    if value
    in (
        Idea.Status.CHANGES_REQUESTED,
        Idea.Status.APPROVED,
        Idea.Status.REJECTED,
    )
)

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
    review after a resubmission.
    """

    class Decision(models.TextChoices):
        (
            CHANGES_REQUESTED,
            REJECTED,
            APPROVED,
        ) = REVIEW_DECISION_CHOICES

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
    round = models.PositiveSmallIntegerField(
        help_text="1 for an idea's first review, then one more per resubmission.",
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
        ordering: ClassVar[list[str]] = ['idea', 'round']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['idea', 'round'],
                name='review_round_unique_per_idea',
            ),
            # At most one review in progress per idea: the database backstop
            # for two reviewers claiming the same idea at once.
            models.UniqueConstraint(
                fields=['idea'],
                condition=models.Q(completed_at__isnull=True),
                name='review_one_open_per_idea',
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
