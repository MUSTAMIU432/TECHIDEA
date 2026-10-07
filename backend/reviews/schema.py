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
from reviews import eligibility, organization_review, platform_review, selectors, services
from reviews.models import PlatformReviewReport, Review, ReviewAssignment, ReviewCriterionAssessment

ReviewDecision = strawberry.enum(Review.Decision, name='ReviewDecision')
ReviewScope = strawberry.enum(Review.Scope, name='ReviewScope')
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
    # Which review this is. Never inferred from the idea and never chosen by the
    # client: an organization confirmation and a platform approval are different
    # decisions by different people, and a client that cannot tell them apart
    # will render "your idea was approved" for both.
    scope: ReviewScope
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
            scope=ReviewScope(review.scope),
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
            'One page of the ideas waiting for **organization** review in one '
            'organization, oldest submission first, for a reviewer there.\n\n'
            'Organization-context ideas only - a team or individual idea has no '
            "organization to confirm it - and never the caller's own, which they "
            'could not review anyway.\n\n'
            'An empty page for anybody else, including a reviewer of another '
            'organization and an organization that does not exist, which are all '
            'the same answer. Reading the queue never starts a review.\n\n'
            'The **platform** queue is `platformReviewQueue`, and it is not '
            'tenant-scoped: the platform reviews submissions from every '
            'organization.'
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
            'Whether the current user organization-reviews ideas in this '
            'organization - for deciding whether to offer the organization '
            'review queue.\n\n'
            '**Not** a statement about platform review: this is the '
            '`idea.review` permission inside one organization, and it grants '
            'nothing on the platform track. A convenience - the queue itself is '
            'authorized on every request.'
        )
    )
    def viewer_can_review_in(self, info: strawberry.Info, organization_id: strawberry.ID) -> bool:
        return eligibility.is_organization_reviewer_in(info.context.user, organization_id)


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
    # Only the three platform verdicts are accepted here. `CONFIRMED` is an
    # organization decision with a different meaning, and `WITHDRAWN` is written
    # only by a take-over - both are refused by `reviews.models.Review.clean()`
    # for a platform review, so naming one here cannot produce a row.
    decision: ReviewDecision
    assessments: list[CriterionAssessmentInput]
    feedback: str = ''


@strawberry.input(
    description=(
        'Complete your own open **organization** review of an idea. Two '
        'decisions only: `CONFIRMED` ("yes, this is what our organization wants '
        'to submit") and `CHANGES_REQUESTED`. Feedback is required for a changes '
        'request. There is no `APPROVED` here, and the server refuses one.'
    )
)
class CompleteOrganizationReviewInput:
    idea_id: strawberry.ID
    review_id: strawberry.ID
    decision: ReviewDecision
    feedback: str = ''
    assessments: list[CriterionAssessmentInput] = strawberry.field(default_factory=list)


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


# --- the organization track ---------------------------------------------------------


@strawberry.type(
    description=(
        'The Platform Review & Approval Report for an idea: what the reviewer '
        'looked at, what they assessed, what they recommend, and what approval '
        'means at this stage.\n\n'
        "**Categorical, never numeric.** `criteria` carries the review's own "
        "assessments - 'Meets', 'Partially meets', 'Does not meet' - verbatim. "
        'There is no score, no total and no weighting, and this type has no field '
        'that could hold one: the review system is categorical and the report '
        'does not introduce a claim nobody made.\n\n'
        'Reading this is **not** giving the go-ahead. The owner has to decide '
        'separately, and `IdeaType.ownerGoAheadAt` is null until they do.'
    )
)
class PlatformReviewReportType:
    id: strawberry.ID
    idea_id: strawberry.ID
    review_id: strawberry.ID
    round: int
    decision: str
    submission_context: str
    organization_id: strawberry.ID | None
    team_id: strawberry.ID | None
    tenant_name: str
    reviewer_id: strawberry.ID
    reviewer_first_name: str
    criteria: JSON
    review_summary: str
    feedback: str
    recommendations: str
    important_considerations: str
    constraints: str
    next_steps: str
    approval_summary: str
    approved_at: str
    generated_at: str
    # The frozen submission this report is about, as a version number. The report
    # describes one specific submission rather than "whatever the idea says now",
    # which is the whole reason submission versions exist.
    version: int

    @staticmethod
    def from_model(report: PlatformReviewReport) -> 'PlatformReviewReportType':
        return PlatformReviewReportType(
            id=strawberry.ID(str(report.pk)),
            idea_id=strawberry.ID(str(report.idea_id)),
            review_id=strawberry.ID(str(report.review_id)),
            round=report.round,
            decision=report.decision,
            submission_context=report.submission_context,
            organization_id=(
                strawberry.ID(str(report.organization_id)) if report.organization_id else None
            ),
            team_id=strawberry.ID(str(report.team_id)) if report.team_id else None,
            tenant_name=report.tenant_label,
            reviewer_id=strawberry.ID(str(report.review.reviewer_id)),
            reviewer_first_name=report.review.reviewer.first_name,
            criteria=report.criteria,
            review_summary=report.review_summary,
            feedback=report.review.feedback,
            recommendations=report.recommendations,
            important_considerations=report.important_considerations,
            constraints=report.constraints,
            next_steps=report.next_steps,
            approval_summary=report.approval_summary,
            approved_at=report.approved_at.isoformat(),
            generated_at=report.generated_at.isoformat(),
            version=report.version.version,
        )


@strawberry.type(description='The frozen submission the platform reviewed.')
class SubmissionVersionType:
    version: int
    submission_context: str
    submitted_by_id: strawberry.ID
    submitted_at: str
    is_current: bool
    title: str

    @staticmethod
    def from_model(version) -> 'SubmissionVersionType':
        return SubmissionVersionType(
            version=version.version,
            submission_context=version.submission_context,
            submitted_by_id=strawberry.ID(str(version.submitted_by_id)),
            submitted_at=version.created_at.isoformat(),
            is_current=version.is_current,
            title=str((version.snapshot or {}).get('title', '')),
        )


@strawberry.type(description='A submission routed to a platform reviewer.')
class ReviewAssignmentType:
    id: strawberry.ID
    idea_id: strawberry.ID
    reviewer_id: strawberry.ID
    reviewer_first_name: str
    assigned_by_id: strawberry.ID
    created_at: str
    is_current: bool

    @staticmethod
    def from_model(assignment: ReviewAssignment) -> 'ReviewAssignmentType':
        return ReviewAssignmentType(
            id=strawberry.ID(str(assignment.pk)),
            idea_id=strawberry.ID(str(assignment.idea_id)),
            reviewer_id=strawberry.ID(str(assignment.reviewer_id)),
            reviewer_first_name=assignment.reviewer.first_name,
            assigned_by_id=strawberry.ID(str(assignment.assigned_by_id)),
            created_at=assignment.created_at.isoformat(),
            is_current=assignment.is_current,
        )


@strawberry.type(description='Result of routing a submission to a platform reviewer.')
class ReviewAssignmentPayload:
    success: bool
    message: str
    field: str | None = None
    assignment: ReviewAssignmentType | None = None


@strawberry.input(description='Route a submitted idea to a platform reviewer.')
class AssignPlatformReviewerInput:
    idea_id: strawberry.ID
    reviewer_id: strawberry.ID


@strawberry.type
class PlatformTrackQuery:
    @strawberry.field(
        description=(
            'Ideas waiting for **organization** review in one organization, '
            'oldest submission first. For a reviewer there: somebody in their '
            'own organization holding the permission to review, who did not '
            "write them. The viewer's own ideas are never in it.\n\n"
            'An empty list for anybody else - including a reviewer of another '
            'organization, an organization owner whose idea it is, and an '
            'organization that does not exist, which are all the same answer.'
        )
    )
    def organization_review_queue(
        self, info: strawberry.Info, organization_id: strawberry.ID
    ) -> list[IdeaType]:
        return [
            IdeaType.from_model(idea, info.context.user)
            for idea in organization_review.organization_queue_for(
                info.context.user, organization_id
            )
        ]

    @strawberry.field(
        description=(
            'Team ideas waiting for the caller to verify, oldest first. Empty unless the '
            "caller is one of that team's reviewers, and never includes their own ideas."
        )
    )
    def team_review_queue(self, info: strawberry.Info, team_id: strawberry.ID) -> list[IdeaType]:
        return [
            IdeaType.from_model(idea, info.context.user)
            for idea in organization_review.team_queue_for(info.context.user, team_id)
        ]

    @strawberry.field(
        description=(
            'Submissions waiting for **platform** review: the ones nobody has '
            'picked up first, then the ones already under review. For a platform '
            'reviewer only.\n\n'
            'Empty for everybody else. Holding an organization Owner or Reviewer '
            'role does not appear here and grants nothing here: platform review '
            'is authorized by a platform permission and nothing else.'
        )
    )
    def platform_review_queue(self, info: strawberry.Info) -> list[IdeaType]:
        return [
            IdeaType.from_model(idea, info.context.user)
            for idea in platform_review.platform_queue(info.context.user)
        ]

    @strawberry.field(
        description=(
            'Everything sitting in **platform intake**: submitted to the '
            'platform, not yet under review.\n\n'
            "This is the platform admin's triage list, not a reviewer's, so it "
            'is gated on the permission to assign reviewers rather than on the '
            'permission to review. Both are platform-scoped and neither is '
            'reachable from an organization or team role.'
        )
    )
    def platform_intake(self, info: strawberry.Info) -> list[IdeaType]:
        return [
            IdeaType.from_model(idea, info.context.user)
            for idea in platform_review.platform_intake(info.context.user)
        ]

    @strawberry.field(
        description=(
            "An idea's **platform review and approval report**, or null.\n\n"
            "The idea's author reads it to decide whether to give the go-ahead, "
            'so it is readable by the author and by platform reviewers and by '
            'nobody else - not by an organization reviewer who happens to be a '
            'colleague, and not by a team Owner. Null is also the answer for an '
            'idea with no report yet.'
        )
    )
    def idea_review_report(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> PlatformReviewReportType | None:
        from ideas import selectors as idea_selectors

        user = info.context.user
        idea = idea_selectors.get_idea(user, idea_id)
        if idea is None:
            return None

        report = platform_review.report_for_idea(idea)
        if report is None:
            return None
        if idea.author_id != getattr(user, 'pk', None) and not eligibility.can_review(user, idea):
            return None
        return PlatformReviewReportType.from_model(report)

    @strawberry.field(
        description=(
            "An idea's frozen platform submissions, oldest version first.\n\n"
            'Each version is the content as it was submitted; the current one is '
            'what the platform is holding. Answering a request for changes '
            'produces the next version and leaves the earlier ones in place, so '
            'this is the auditable submission history.\n\n'
            "Readers of an idea may read it - the version is the idea's own "
            'content - so this needs no separate permission and returns an empty '
            'list for an idea the caller cannot read.'
        )
    )
    def idea_submission_versions(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> list[SubmissionVersionType]:
        from ideas import selectors as idea_selectors
        from ideas import versions as idea_versions

        idea = idea_selectors.get_idea(info.context.user, idea_id)
        if idea is None:
            return []
        return [
            SubmissionVersionType.from_model(version)
            for version in idea_versions.version_history(idea)
        ]

    @strawberry.field(description='Who a submitted idea is currently routed to, or null.')
    def idea_review_assignment(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> ReviewAssignmentType | None:
        from ideas import selectors as idea_selectors

        idea = idea_selectors.get_idea(info.context.user, idea_id)
        if idea is None:
            return None
        assignment = platform_review.assignment_for(idea)
        return ReviewAssignmentType.from_model(assignment) if assignment is not None else None

    @strawberry.field(
        description=(
            'Whether the current user may start the **organization** review of '
            'this idea. Their own idea is never theirs to review.'
        )
    )
    def viewer_can_start_organization_review(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> bool:
        from ideas import selectors as idea_selectors

        idea = idea_selectors.get_idea(info.context.user, idea_id)
        return eligibility.can_start_organization_review(info.context.user, idea)


@strawberry.type
class PlatformTrackMutation:
    @strawberry.mutation(
        description=(
            'Open the organization review round for an idea your organization is '
            'putting forward.\n\n'
            "Requires being an active member of the idea's own organization "
            'holding the permission to review, able to read it, and **not** its '
            'author. The idea must be waiting for organization review; '
            'organization confirmation is a decision somebody makes, not a button '
            'the author presses.'
        )
    )
    def start_organization_review(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> ReviewPayload:
        try:
            review = organization_review.start_organization_review(info.context.user, idea_id)
        except organization_review.OrganizationReviewError as exc:
            return ReviewPayload(success=False, message=exc.message, field=exc.field)
        return _review_payload(info, review, 'Organization review started.')

    @strawberry.mutation(
        description=(
            'Record your organization review decision - confirm the idea, or ask '
            'for changes with feedback - and move the idea. All of it, or none '
            'of it.\n\n'
            '`CONFIRMED` means "this idea accurately represents what our '
            'organization wants to submit". It does **not** mean platform '
            'approval, and it does not submit anything: afterwards the owner sees '
            '"Submit to Platform".\n\n'
            'Previous review history is preserved; a changes request can be '
            'answered and the idea resubmitted, and the earlier round stays where '
            'it is.'
        )
    )
    def complete_organization_review(
        self, info: strawberry.Info, input: CompleteOrganizationReviewInput
    ) -> ReviewPayload:
        try:
            review = organization_review.complete_organization_review(
                info.context.user,
                organization_review.CompleteOrganizationReviewInput(
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
        except organization_review.OrganizationReviewError as exc:
            return ReviewPayload(success=False, message=exc.message, field=exc.field)
        return _review_payload(info, review, f'Review completed: {review.get_decision_display()}.')

    @strawberry.mutation(
        description=(
            'Route a submitted idea to a platform reviewer.\n\n'
            'Requires the platform permission to assign reviewers - not the '
            'permission to review, and not any organization or team role. The '
            'person named must be able to review platform submissions, checked '
            'before the assignment exists so the platform cannot strand an idea '
            'with somebody who could never decide it.'
        )
    )
    def assign_platform_reviewer(
        self, info: strawberry.Info, input: AssignPlatformReviewerInput
    ) -> ReviewAssignmentPayload:
        try:
            assignment = platform_review.assign_reviewer(
                info.context.user, input.idea_id, input.reviewer_id
            )
        except platform_review.PlatformReviewError as exc:
            return ReviewAssignmentPayload(success=False, message=exc.message, field=exc.field)
        except Exception as exc:  # AdministrationError and its relatives.
            message = getattr(exc, 'message', 'That person cannot be assigned.')
            return ReviewAssignmentPayload(success=False, message=message)
        return ReviewAssignmentPayload(
            success=True,
            message='Platform reviewer assigned.',
            assignment=ReviewAssignmentType.from_model(assignment),
        )

    @strawberry.mutation(
        description=(
            'Take back an assignment without withdrawing a review. If a round is '
            'already open, the reviewer simply stops holding the assignment.'
        )
    )
    def release_platform_reviewer(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> ReviewAssignmentPayload:
        try:
            platform_review.release_assignment(info.context.user, idea_id)
        except platform_review.PlatformReviewError as exc:
            return ReviewAssignmentPayload(success=False, message=exc.message, field=exc.field)
        except Exception as exc:
            return ReviewAssignmentPayload(
                success=False,
                message=getattr(exc, 'message', 'That assignment cannot be released.'),
            )
        return ReviewAssignmentPayload(success=True, message='Assignment released.')
