"""
What must be true of one idea's review and audit records after *any* sequence
of operations (S3-008) - asserted at the end of the end-to-end flows and after
every race, where "exactly one valid result" is otherwise easy to under-check.
"""

from itertools import pairwise

from ideas.lifecycle import TRANSITIONS
from ideas.models import Idea, IdeaTransition
from reviews.models import Review, ReviewCriterionAssessment
from reviews.services import VERDICT_DECISIONS

CRITERIA = sorted(ReviewCriterionAssessment.Criterion.values)
RATINGS = set(ReviewCriterionAssessment.Rating.values)


def assert_review_records_consistent(idea: Idea) -> None:
    idea.refresh_from_db()
    reviews = list(
        Review.objects.filter(idea=idea).order_by('round').prefetch_related('assessments')
    )
    transitions = list(IdeaTransition.objects.filter(idea=idea).order_by('created_at', 'pk'))

    # Rounds are 1..n with no gap, and at most the last one is open - exactly
    # when the idea is under review.
    assert [r.round for r in reviews] == list(range(1, len(reviews) + 1))
    open_reviews = [r for r in reviews if r.completed_at is None]
    assert len(open_reviews) <= 1
    if idea.status == Idea.Status.UNDER_REVIEW:
        assert open_reviews == [reviews[-1]]
    else:
        assert open_reviews == []

    for previous, review in zip([None, *reviews], reviews, strict=False):
        assert (review.decision is None) == (review.completed_at is None)
        assert review.reviewer_id != idea.author_id
        criteria = sorted(a.criterion for a in review.assessments.all())
        if review.decision in VERDICT_DECISIONS:
            # A verdict carries all five criteria, each with a known rating.
            assert criteria == CRITERIA
            assert {a.rating for a in review.assessments.all()} <= RATINGS
            assert review.completed_at >= review.created_at
        else:
            # Open, or withdrawn by a take-over: nothing was assessed.
            assert criteria == []
        if previous is not None:
            assert previous.completed_at is not None
            assert previous.completed_at <= review.created_at
        if review.decision == Review.Decision.WITHDRAWN:
            assert review is not reviews[-1], 'a withdrawn round is always followed by the next'

    # The audit trail is one unbroken chain of legal moves ending at the status.
    for earlier, later in pairwise(transitions):
        assert earlier.to_status == later.from_status
    for transition in transitions:
        assert (transition.from_status, transition.to_status) in TRANSITIONS
    if transitions:
        assert transitions[-1].to_status == idea.status

    # Every start and every verdict has its audit row, made by that round's
    # reviewer; a take-over (a round after a withdrawn one) moves no status.
    starts = [
        t.actor_id
        for t in transitions
        if (t.from_status, t.to_status) == (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW)
    ]
    started_rounds = [
        r.reviewer_id
        for previous, r in zip([None, *reviews], reviews, strict=False)
        if previous is None or previous.decision != Review.Decision.WITHDRAWN
    ]
    assert starts == started_rounds
    verdicts = [
        (t.to_status, t.actor_id) for t in transitions if t.from_status == Idea.Status.UNDER_REVIEW
    ]
    assert verdicts == [
        (r.decision, r.reviewer_id) for r in reviews if r.decision in VERDICT_DECISIONS
    ]
