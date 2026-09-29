"""
Review operations (S3-004): starting a review and completing it.

Each is one transaction that writes the review record *and* makes the idea's
status move, so the two commit together or not at all. The status move is
made by `ideas.lifecycle.apply_review_transition` - the lifecycle remains the
only code that changes `Idea.status` - and that function refuses to run outside
the transaction this module opens.

Locking
-------
Both operations lock the idea row first (`ideas.selectors.get_idea_for_update`,
the same visibility-filtered locked read `transition_idea` uses) and then, for
completion, the review row. One order everywhere, so the two cannot deadlock
each other. Every authorization and state check runs *after* the lock, on the
row as it now is:

- two reviewers starting the same idea at once: the second waits for the
  first to commit, re-reads `UNDER_REVIEW`, and is refused. The one-open-review
  constraint is the database backstop if anything ever skipped the lock;
- a reviewer completing twice: the second call waits, re-reads a completed
  review, and is refused;
- two reviewers taking over the same stalled review: the second waits,
  re-reads an open review whose reviewer (the first) is eligible, and is
  refused.

Take-over (S3-008)
------------------
`docs/reviews-domain.md` §5.3 / D-2: if the reviewer holding an open review
stops being eligible - left the organization, lost `idea.review`, was
deactivated, can no longer read the idea - the idea would sit in
`UNDER_REVIEW` with nobody able to decide it. Another eligible reviewer then
takes it over with the same `startReview`: claiming a stalled idea is still
claiming it, so there is no assignment operation. The open round is completed
as `WITHDRAWN` and round n+1 opens for the new reviewer, in one transaction.
The idea stays `UNDER_REVIEW`, so no lifecycle move is made and no
`IdeaTransition` is written; the two review rows are the record. An eligible
reviewer's open review is never taken: the gate is the holder's *current*
ineligibility, asked after the lock.

Refusals
--------
`ReviewError` carries the project's `(message, field, reason)` triple, like
`IdeaError`. The order of the checks keeps the operations from being oracles:
an idea the caller cannot read is "unavailable", exactly as a nonexistent one;
a caller who can read it but is not its reviewer is told they may not review
it, and learns nothing about any review; only the idea's own reviewers are
told about the state of a review. The client-side capability flags are never
consulted - every rule is asked again here.
"""

import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from ideas import lifecycle
from ideas import selectors as idea_selectors
from ideas.models import Idea
from ideas.services import IdeaError
from identity.models import User
from organizations.authorization import AuthorizationError
from reviews import eligibility, notifications
from reviews.models import LIFECYCLE_DECISION_CHOICES, Review, ReviewCriterionAssessment

logger = logging.getLogger(__name__)

MAX_FEEDBACK_LENGTH = 5000
MAX_NOTE_LENGTH = 1000

# Decisions that must explain themselves to the author (D-3): an author sent
# back or turned down needs to know why. An approval may carry a note but does
# not have to.
FEEDBACK_REQUIRED_DECISIONS = frozenset(
    {Review.Decision.CHANGES_REQUESTED, Review.Decision.REJECTED}
)

# What a reviewer may choose. `WITHDRAWN` is a decision value but not a choice:
# only a take-over writes it.
VERDICT_DECISIONS = frozenset(value for value, _label in LIFECYCLE_DECISION_CHOICES)


class ReviewError(AuthorizationError):
    """Any refusal from this module; rendered by `reviews/schema.py`."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message, reason=reason, field=field)


@dataclass(frozen=True)
class AssessmentInput:
    criterion: str
    rating: str
    note: str = ''


@dataclass(frozen=True)
class CompleteReviewInput:
    idea_id: object
    review_id: object
    decision: str
    feedback: str
    assessments: tuple[AssessmentInput, ...]


def _require_active_user(user: User | None) -> User:
    if user is None or not user.is_active:
        raise ReviewError('You must be signed in to review ideas.', reason='unauthenticated')
    return user


def _lock_readable_idea(user: User, idea_id: object) -> Idea:
    idea = idea_selectors.get_idea_for_update(user, idea_id)
    if idea is None:
        # Nonexistent, another tenant's, or not shared with this reader: one
        # answer for all three.
        raise ReviewError('Idea is unavailable.')
    return idea


def _require_reviewer(user: User, idea: Idea) -> None:
    # `can_review` asks the membership and `idea.review` again, on the locked
    # idea: leaving the organization, losing the role or being the author all
    # take effect here, whatever the client was shown earlier.
    if not eligibility.can_review(user, idea):
        raise ReviewError('You are not allowed to review this idea.')


def _submission_snapshot(idea: Idea) -> dict:
    """What the reviewer is looking at, frozen at the moment they start."""
    return {
        'title': idea.title,
        'description': idea.description,
        'category': (
            {'id': idea.category_id, 'name': idea.category.name} if idea.category_id else None
        ),
        'visibility': idea.visibility,
        'submitted_at': idea.submitted_at.isoformat() if idea.submitted_at else None,
    }


def _lifecycle(call):
    """Run a lifecycle call, turning its refusal into this module's."""
    try:
        return call()
    except IdeaError as exc:
        raise ReviewError(exc.message, field=exc.field, reason=exc.reason) from None


def start_review(user: User | None, idea_id: object) -> Review:
    """
    Claim an idea for review and open its next review round with a snapshot
    of the idea.

    A `SUBMITTED` idea moves to `UNDER_REVIEW`. An `UNDER_REVIEW` idea can be
    claimed only by taking over a review whose reviewer is no longer eligible
    (see the module docstring); it stays `UNDER_REVIEW`.
    """
    active_user = _require_active_user(user)

    with transaction.atomic():
        idea = _lock_readable_idea(active_user, idea_id)
        _require_reviewer(active_user, idea)

        if idea.status == Idea.Status.UNDER_REVIEW:
            return _take_over(active_user, idea)
        if idea.status != Idea.Status.SUBMITTED:
            # Covers the losing side of two concurrent starts: it read the
            # status after the winner committed.
            raise ReviewError('This idea is not waiting for review.')

        review = _open_next_round(active_user, idea)

        _lifecycle(
            lambda: lifecycle.apply_review_transition(active_user, idea, Idea.Status.UNDER_REVIEW)
        )

    return review


def _open_next_round(reviewer: User, idea: Idea) -> Review:
    """Round n+1 for `reviewer`, on an idea the caller has locked."""
    last_round = Review.objects.filter(idea=idea).aggregate(last=Max('round'))['last'] or 0
    try:
        # A savepoint, so a constraint violation leaves the outer transaction
        # usable for the refusal rather than poisoned.
        with transaction.atomic():
            return Review.objects.create(
                idea=idea,
                reviewer=reviewer,
                round=last_round + 1,
                submission_snapshot=_submission_snapshot(idea),
            )
    except (IntegrityError, ValidationError):
        # `full_clean()` reports the one-open-review constraint as a
        # ValidationError; a write that skipped it would hit the database's
        # IntegrityError. Either way another review is open.
        raise ReviewError('This idea is already being reviewed.') from None


def _take_over(user: User, idea: Idea) -> Review:
    """
    Withdraw the open review of a locked `UNDER_REVIEW` idea whose reviewer is
    no longer eligible, and open the next round for `user`.

    `user` has already been found eligible. Refused - with the same message as
    starting an idea that is not waiting, so the answer says nothing about
    who holds it - when the open review's reviewer is still eligible, which
    includes `user` themselves.
    """
    open_review = (
        # `of=('self',)`: lock the review, not the reviewer's account row the
        # join also reads.
        Review.objects.select_for_update(of=('self',))
        .select_related('reviewer')
        .filter(idea=idea, completed_at__isnull=True)
        .first()
    )
    if open_review is None or eligibility.can_review(open_review.reviewer, idea):
        raise ReviewError('This idea is not waiting for review.')

    # Completed first: the one-open-review constraint admits the new round
    # only once this one is closed.
    open_review.decision = Review.Decision.WITHDRAWN
    open_review.completed_at = timezone.now()
    open_review.save()

    review = _open_next_round(user, idea)
    logger.info(
        'Review taken over (idea=%s, withdrawn_review=%s, previous_reviewer=%s, '
        'review=%s, reviewer=%s).',
        idea.pk,
        open_review.pk,
        open_review.reviewer_id,
        review.pk,
        user.pk,
    )
    return review


def _validate_completion(data: CompleteReviewInput) -> tuple[str, str, list[AssessmentInput]]:
    """Everything about the input that needs no database, checked up front."""
    decision = str(data.decision or '').strip().lower()
    if decision not in VERDICT_DECISIONS:
        raise ReviewError('Choose a decision.', field='decision')

    feedback = (data.feedback or '').strip()
    if len(feedback) > MAX_FEEDBACK_LENGTH:
        raise ReviewError(
            f'Feedback must be at most {MAX_FEEDBACK_LENGTH} characters.', field='feedback'
        )
    if decision in FEEDBACK_REQUIRED_DECISIONS and not feedback:
        raise ReviewError('Explain this decision to the author.', field='feedback')

    criteria = set(ReviewCriterionAssessment.Criterion.values)
    ratings = set(ReviewCriterionAssessment.Rating.values)
    seen: set[str] = set()
    assessments = []
    for item in data.assessments:
        criterion = str(item.criterion or '').strip().lower()
        rating = str(item.rating or '').strip().lower()
        note = (item.note or '').strip()
        if criterion not in criteria:
            raise ReviewError('That is not a review criterion.', field='assessments')
        if criterion in seen:
            raise ReviewError('Assess each criterion once.', field='assessments')
        if rating not in ratings:
            raise ReviewError('Choose a rating for every criterion.', field='assessments')
        if len(note) > MAX_NOTE_LENGTH:
            raise ReviewError(
                f'Criterion notes must be at most {MAX_NOTE_LENGTH} characters.',
                field='assessments',
            )
        seen.add(criterion)
        assessments.append(AssessmentInput(criterion=criterion, rating=rating, note=note))

    if seen != criteria:
        raise ReviewError('Rate every criterion before deciding.', field='assessments')

    return decision, feedback, assessments


def complete_review(user: User | None, data: CompleteReviewInput) -> Review:
    """
    Record the decision on the caller's own open review - every criterion's
    rating and note, the feedback, the decision, `completed_at` - and move the
    idea to the status the decision names. One transaction: all of it, or none.
    """
    active_user = _require_active_user(user)
    decision, feedback, assessments = _validate_completion(data)

    try:
        review_pk = int(str(data.review_id))
    except (TypeError, ValueError):
        raise ReviewError('Review is unavailable.') from None

    with transaction.atomic():
        idea = _lock_readable_idea(active_user, data.idea_id)
        _require_reviewer(active_user, idea)

        review = Review.objects.select_for_update().filter(pk=review_pk, idea=idea).first()
        if review is None or review.reviewer_id != active_user.pk:
            # Not a review of this idea, or somebody else's: the same answer, so
            # a guessed id reveals nothing and nobody completes another
            # reviewer's review.
            raise ReviewError('Review is unavailable.')
        if review.completed_at is not None:
            raise ReviewError('This review has already been completed.')
        if idea.status != Idea.Status.UNDER_REVIEW:
            raise ReviewError('This idea is not under review.')

        # Assessments first, while the review is still open - the model refuses
        # to add one to a completed review.
        for item in assessments:
            ReviewCriterionAssessment.objects.create(
                review=review, criterion=item.criterion, rating=item.rating, note=item.note
            )

        review.decision = decision
        review.feedback = feedback
        review.completed_at = timezone.now()
        review.save()

        _lifecycle(lambda: lifecycle.apply_review_transition(active_user, idea, decision))

        # After the commit, never before: a completion that rolls back must not
        # tell the author about a decision that was never recorded. The email
        # never raises (see `reviews.notifications`).
        review_pk = review.pk
        transaction.on_commit(lambda: notifications.send_review_decision_email(review_pk))

    return Review.objects.prefetch_related('assessments').get(pk=review.pk)
