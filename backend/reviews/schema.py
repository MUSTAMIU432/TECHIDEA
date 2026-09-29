"""
The Reviews GraphQL adapter (S3-003 reads, S3-004 operations).

The same shape as `ideas/schema.py`: types with `from_model`, payloads
carrying `(success, message, field)`, and resolvers that only move data
between the schema and `reviews/selectors.py` / `reviews/eligibility.py` /
`reviews/services.py`, with no rule decided here. The two mutations,
`startReview` and `completeReview`, are the only way to make the review moves
of the idea lifecycle; `transitionIdea` refuses them.

What is exposed, and what is not
--------------------------------
- `reviewQueue` returns the existing `IdeaPage`, paged by the existing
  `offset`/`limit` convention (`ideas.pagination`), so the queue is not a
  second list shape for the client to handle.
- `ideaReviews` is the only way to read a review. There is no `review(id)`:
  a review is reached through an idea the caller may read, never by guessing
  its id.
- `ReviewType` carries `reviewerId` and no nested user, like every other type
  a `PUBLIC` idea can reach. The reviewer's id is visible to the author
  (accountability, `docs/reviews-domain.md` D-9); other readers see no
  review at all.
- `submissionSnapshot` is null unless the viewer is a reviewer of the idea.
  The author already has the idea itself; the snapshot is reviewer context.
"""

import strawberry
from strawberry.scalars import JSON

from ideas.schema import IdeaPage, IdeaType, PageInfo
from reviews import eligibility, selectors, services
from reviews.models import Review, ReviewCriterionAssessment

ReviewDecision = strawberry.enum(Review.Decision, name='ReviewDecision')
ReviewCriterion = strawberry.enum(ReviewCriterionAssessment.Criterion, name='ReviewCriterion')
CriterionRating = strawberry.enum(ReviewCriterionAssessment.Rating, name='CriterionRating')


@strawberry.type(description="One criterion's categorical rating within a review.")
class CriterionAssessmentType:
    criterion: ReviewCriterion
    rating: CriterionRating
    note: str

    @staticmethod
    def from_model(assessment: ReviewCriterionAssessment) -> 'CriterionAssessmentType':
        return CriterionAssessmentType(
            criterion=ReviewCriterion(assessment.criterion),
            rating=CriterionRating(assessment.rating),
            note=assessment.note,
        )


@strawberry.type(
    description=(
        'One review round of an idea. `decision` and `completedAt` are null '
        'while the review is in progress, which only reviewers ever see. '
        'WITHDRAWN is not a verdict: the round was closed when another reviewer '
        'took over from a reviewer who could no longer review, and the next '
        'round is theirs. '
        '`reviewerId` rather than a nested user, for the same reason '
        '`IdeaType` carries `authorId`.'
    )
)
class ReviewType:
    id: strawberry.ID
    idea_id: strawberry.ID
    round: int
    reviewer_id: strawberry.ID
    decision: ReviewDecision | None
    feedback: str
    assessments: list[CriterionAssessmentType]
    created_at: str
    completed_at: str | None
    submission_snapshot: JSON | None = strawberry.field(
        description=(
            'The idea as it was when this review started. Reviewers only; null for the author.'
        )
    )

    @staticmethod
    def from_model(review: Review, *, include_snapshot: bool) -> 'ReviewType':
        return ReviewType(
            id=strawberry.ID(str(review.pk)),
            idea_id=strawberry.ID(str(review.idea_id)),
            round=review.round,
            reviewer_id=strawberry.ID(str(review.reviewer_id)),
            decision=ReviewDecision(review.decision) if review.decision else None,
            feedback=review.feedback,
            # `.all()` reads the prefetch made by `list_idea_reviews`.
            assessments=[
                CriterionAssessmentType.from_model(assessment)
                for assessment in review.assessments.all()
            ],
            created_at=review.created_at.isoformat(),
            completed_at=review.completed_at.isoformat() if review.completed_at else None,
            submission_snapshot=review.submission_snapshot if include_snapshot else None,
        )


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            'Ideas waiting for review in one organization, oldest submission '
            'first, for a reviewer there. An empty page for anybody else - '
            'including a reviewer of another organization - which is also the '
            'answer for an organization that does not exist. Reading the queue '
            'never starts a review.'
        )
    )
    def review_queue(
        self,
        info: strawberry.Info,
        organization_id: strawberry.ID,
        offset: int | None = None,
        limit: int | None = None,
    ) -> IdeaPage:
        user = info.context.user
        page = selectors.review_queue(user, organization_id, offset=offset, limit=limit)
        return IdeaPage(
            items=[IdeaType.from_model(idea, user) for idea in page.items],
            page_info=PageInfo(
                offset=page.offset,
                limit=page.limit,
                total_count=page.total_count,
                has_next_page=page.has_next_page,
                has_previous_page=page.has_previous_page,
            ),
        )

    @strawberry.field(
        description=(
            "An idea's review history, in round order. Reviewers of the idea's "
            'organization see every round; its author sees completed rounds; '
            'anybody else - including readers of a PUBLIC idea - sees none. An '
            'empty list for an idea the caller cannot read or that does not exist.'
        )
    )
    def idea_reviews(self, info: strawberry.Info, idea_id: strawberry.ID) -> list[ReviewType]:
        history = selectors.list_idea_reviews(info.context.user, idea_id)
        return [
            ReviewType.from_model(review, include_snapshot=history.viewer_is_reviewer)
            for review in history.reviews
        ]

    @strawberry.field(
        description=(
            'Whether the current user reviews ideas in this organization - for '
            'deciding whether to offer the review queue. A convenience: the '
            'queue itself is authorized on every request.'
        )
    )
    def viewer_can_review_in(self, info: strawberry.Info, organization_id: strawberry.ID) -> bool:
        return eligibility.is_reviewer_in(info.context.user, organization_id)


@strawberry.input(description="One criterion's rating, and an optional note on it.")
class CriterionAssessmentInput:
    criterion: ReviewCriterion
    rating: CriterionRating
    note: str = ''


@strawberry.input(
    description=(
        "Complete the caller's own open review of an idea. Every criterion must "
        'be rated exactly once; feedback is required for CHANGES_REQUESTED and '
        'REJECTED. WITHDRAWN is refused: only a take-over records it.'
    )
)
class CompleteReviewInput:
    idea_id: strawberry.ID
    review_id: strawberry.ID
    decision: ReviewDecision
    assessments: list[CriterionAssessmentInput]
    feedback: str = ''


@strawberry.type(
    description=(
        'Result of a review operation. On success `review` is the review as '
        'the caller now sees it and `idea` is the idea in its new state.'
    )
)
class ReviewPayload:
    success: bool
    message: str
    field: str | None = None
    review: ReviewType | None = None
    idea: IdeaType | None = None


def _review_payload(info: strawberry.Info, review: Review, message: str) -> ReviewPayload:
    user = info.context.user
    return ReviewPayload(
        success=True,
        message=message,
        # The caller is this review's reviewer, so they see its snapshot.
        review=ReviewType.from_model(review, include_snapshot=True),
        idea=IdeaType.from_model(review.idea, user),
    )


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            'Start reviewing a submitted idea: open its next review round and '
            "move it to UNDER_REVIEW. Only a reviewer in the idea's organization "
            'who can read it and did not write it; refused if somebody else has '
            'already started. On an idea UNDER_REVIEW whose reviewer can no '
            'longer review it, takes the review over: that round is WITHDRAWN and '
            "the next one is the caller's."
        )
    )
    def start_review(self, info: strawberry.Info, idea_id: strawberry.ID) -> ReviewPayload:
        try:
            review = services.start_review(info.context.user, idea_id)
        except services.ReviewError as exc:
            return ReviewPayload(success=False, message=exc.message, field=exc.field)
        return _review_payload(info, review, 'Review started.')

    @strawberry.mutation(
        description=(
            'Record the decision on your own open review - every criterion, the '
            'feedback and the decision - and move the idea to the status the '
            'decision names. All of it, or none of it.'
        )
    )
    def complete_review(self, info: strawberry.Info, input: CompleteReviewInput) -> ReviewPayload:
        try:
            review = services.complete_review(
                info.context.user,
                services.CompleteReviewInput(
                    idea_id=input.idea_id,
                    review_id=input.review_id,
                    decision=input.decision.value,
                    feedback=input.feedback,
                    assessments=tuple(
                        services.AssessmentInput(
                            criterion=item.criterion.value,
                            rating=item.rating.value,
                            note=item.note,
                        )
                        for item in input.assessments
                    ),
                ),
            )
        except services.ReviewError as exc:
            return ReviewPayload(success=False, message=exc.message, field=exc.field)
        return _review_payload(info, review, f'Review completed: {review.get_decision_display()}.')
