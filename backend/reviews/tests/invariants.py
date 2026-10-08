"""
What must be true of one idea's review and audit records after *any* sequence of
operations - asserted at the end of the end-to-end flows and after every race,
where "exactly one valid result" is otherwise easy to under-check.

**Both tracks, and that is the point.** An idea now has an *organization* review
round and *platform* review rounds on one history, and each track **numbers its
own rounds from 1** - so "platform review round 2" in the approval report means
the platform's second look at the idea, not the third time anybody looked at it.
Everything here therefore treats the two sequences separately where a rule is
about one of them, and together where a rule is about the idea. The rules are
split into small functions because "one function that checks everything" stops
being readable the moment the domain grows a second thing to check - which is
what happened here.
"""

from itertools import pairwise

from ideas.lifecycle import TRANSITIONS
from ideas.models import Idea, IdeaTransition
from reviews.models import Review, ReviewCriterionAssessment
from reviews.services import VERDICT_DECISIONS

CRITERIA = sorted(ReviewCriterionAssessment.Criterion.values)
RATINGS = set(ReviewCriterionAssessment.Rating.values)

ORGANIZATION = Review.Scope.ORGANIZATION
PLATFORM = Review.Scope.PLATFORM

# Every decision that ends a review round, across both tracks. `VERDICT_DECISIONS`
# is the platform set (it is what `reviews.services` accepts); an organization
# review is *decided* by `CONFIRMED` as well as by a changes request, and that
# difference is precisely why the tracks cannot share one list.
ALL_DECISIONS = frozenset(VERDICT_DECISIONS) | {Review.Decision.CONFIRMED}

# The decisions each track may record. Read from the model, so this file cannot
# assert a vocabulary the code does not have.
ORGANIZATION_DECISIONS = frozenset({Review.Decision.CHANGES_REQUESTED, Review.Decision.CONFIRMED})
PLATFORM_DECISIONS = frozenset(
    {
        Review.Decision.CHANGES_REQUESTED,
        Review.Decision.APPROVED,
        Review.Decision.REJECTED,
    }
)

# The only (from, to) pair whose arrival *opens* a review round.
#
# Only the platform track has one: claiming a submission moves the idea into
# `UNDER_REVIEW`, which is a transition. Opening an organization review moves
# nothing - the idea is already `SUBMITTED_TO_ORGANIZATION` and stays there until
# somebody decides - so that round is recorded by its `Review` row alone and
# produces no `IdeaTransition` at all. Deliberately, and worth stating: an
# organization reviewer picking something up is not a decision about it.
_PLATFORM_ROUND_STARTS = ((Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW),)

# The statuses a review decision may be recorded from, per track. An organization
# decision moves the idea straight out of the queue; a platform one out of
# `UNDER_REVIEW`.
_ORGANIZATION_DECIDED_FROM = (Idea.Status.SUBMITTED_TO_ORGANIZATION,)
_PLATFORM_DECIDED_FROM = (Idea.Status.UNDER_REVIEW,)


def assert_review_records_consistent(idea: Idea) -> None:
    """Every rule in this module, for one idea, after any sequence of operations."""
    idea.refresh_from_db()
    reviews = list(
        Review.objects.filter(idea=idea).order_by('round').prefetch_related('assessments')
    )
    transitions = list(IdeaTransition.objects.filter(idea=idea).order_by('created_at', 'pk'))

    _assert_one_round_sequence(reviews)
    _assert_open_reviews(idea, reviews)
    _assert_never_the_author(reviews)
    _assert_review_rows(reviews)
    _assert_decisions_belong_to_their_track(reviews)
    _assert_transitions_are_a_legal_chain(transitions, idea)
    _assert_starts_and_verdicts_are_recorded(reviews, transitions)


def _assert_one_round_sequence(reviews: list[Review]) -> None:
    """
    Each track's rounds are 1..n with no gap - one sequence **per scope**, not
    per idea, which is what lets the report say "platform review round 2".
    """
    for scope in (ORGANIZATION, PLATFORM):
        rounds = [review.round for review in reviews if review.scope == scope]
        assert rounds == list(range(1, len(rounds) + 1)), f'{scope} rounds are not gapless'


def _assert_open_reviews(idea: Idea, reviews: list[Review]) -> None:
    """
    At most one open review **per track**, and a platform review is open exactly
    while its idea is `UNDER_REVIEW`.

    Scoped, because an idea legitimately has an organization review open while a
    platform review is being routed: they are different tracks over one row, and
    an unscoped "at most one open" would be a rule about a situation the domain
    does not produce.
    """
    for scope in (ORGANIZATION, PLATFORM):
        open_for_scope = [
            review for review in reviews if review.scope == scope and review.completed_at is None
        ]
        assert len(open_for_scope) <= 1, f'two open {scope} reviews'

    open_reviews = [review for review in reviews if review.completed_at is None]
    if idea.status == Idea.Status.UNDER_REVIEW:
        assert open_reviews == [reviews[-1]]
        assert open_reviews[0].scope == PLATFORM
    else:
        assert open_reviews == []


def _assert_never_the_author(reviews: list[Review]) -> None:
    """No review, in either track, is ever reviewed by the idea's author."""
    for review in reviews:
        assert review.reviewer_id != review.idea.author_id


def _assert_review_rows(reviews: list[Review]) -> None:
    """Per-row rules: decided-ness, criteria, round ordering and take-overs."""
    for previous, review in zip([None, *reviews], reviews, strict=False):
        assert (review.decision is None) == (review.completed_at is None)

        criteria = sorted(assessment.criterion for assessment in review.assessments.all())
        ratings = {assessment.rating for assessment in review.assessments.all()}

        if review.decision in ALL_DECISIONS:
            # A platform verdict carries all five criteria. An organization review
            # is a much lighter thing - an organizational confirmation rather
            # than an assessment of merit - so it may carry none at all, but
            # anything it does carry is still a known criterion with a known
            # rating.
            if review.scope == PLATFORM:
                assert criteria == CRITERIA
            assert set(criteria) <= set(CRITERIA)
            assert ratings <= RATINGS
            assert review.completed_at >= review.created_at
        else:
            # Open, or withdrawn by a take-over: nothing was assessed.
            assert criteria == []

        if previous is not None and review.scope == previous.scope:
            # Only *within* a track is the ordering meaningful: the two tracks
            # interleave on one idea's history, so an organization's confirmation
            # may sit between two platform rounds without being "out of order".
            assert previous.completed_at is not None, f'{review.scope} round {review.round}'
            assert previous.completed_at <= review.created_at

        if review.decision == Review.Decision.WITHDRAWN:
            assert review is not reviews[-1], 'a withdrawn round is always followed by the next'
            assert review.scope == PLATFORM, 'only a platform review is ever taken over'


def _assert_decisions_belong_to_their_track(reviews: list[Review]) -> None:
    """
    A decision belongs to its scope, and each scope moves its idea somewhere real.

    This is "an organization cannot approve and the platform cannot confirm",
    asserted on the rows rather than on a service: `Review.clean()` refuses the
    mismatch on write, and this is what proves no path around it exists.
    """
    for review in reviews:
        if review.decision is None or review.decision == Review.Decision.WITHDRAWN:
            continue

        allowed = ORGANIZATION_DECISIONS if review.scope == ORGANIZATION else PLATFORM_DECISIONS
        assert review.decision in allowed, f'{review.scope} recorded {review.decision}'
        assert review.moves_idea_to() is not None, f'{review.decision} moves nowhere'


def _assert_transitions_are_a_legal_chain(transitions: list[IdeaTransition], idea: Idea) -> None:
    """The audit trail is one unbroken chain of legal moves, ending at the status."""
    for earlier, later in pairwise(transitions):
        assert earlier.to_status == later.from_status
    for transition in transitions:
        assert (transition.from_status, transition.to_status) in TRANSITIONS
    if transitions:
        assert transitions[-1].to_status == idea.status


def _assert_starts_and_verdicts_are_recorded(
    reviews: list[Review], transitions: list[IdeaTransition]
) -> None:
    """
    Every round start and every verdict has its audit row, made by that round's
    reviewer; a take-over completes a round without moving a status, so it
    produces no transition row at all.
    """
    # A list, not a dict: two `SUBMITTED -> UNDER_REVIEW` moves are two platform
    # rounds, and keying by the pair would collapse the first claim and its
    # take-over into one row.
    recorded_starts = [
        transition.actor_id
        for transition in transitions
        if (transition.from_status, transition.to_status) in _PLATFORM_ROUND_STARTS
    ]
    platform_started_rounds = [
        review.reviewer_id
        for previous, review in zip([None, *reviews], reviews, strict=False)
        if review.scope == PLATFORM
        and (previous is None or previous.decision != Review.Decision.WITHDRAWN)
    ]
    assert recorded_starts == platform_started_rounds

    recorded_verdicts = [
        (transition.to_status, transition.actor_id)
        for transition in transitions
        if transition.from_status in (*_ORGANIZATION_DECIDED_FROM, *_PLATFORM_DECIDED_FROM)
    ]
    decided_rounds = [
        (review.moves_idea_to(), review.reviewer_id)
        for review in sorted(reviews, key=lambda r: (r.scope, r.round))
        if review.decision in ALL_DECISIONS
    ]
    assert recorded_verdicts == decided_rounds
