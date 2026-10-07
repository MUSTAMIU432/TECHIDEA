"""
GraphQL for the proposal an approved idea gets.

Who sees what is `reviews.proposals`' to decide; this module only asks and reports. A refusal
is a `success: false` payload with the field it names. The owner reads a released proposal
through `ideaProposalState` and announces each reading with `recordProposalView`, whose answer
carries what the page needs for its watermark.
"""

from datetime import datetime

import strawberry

from administration.authorization import AdministrationError
from ideas.models import Idea
from reviews import proposals
from reviews.models import IdeaProposal


@strawberry.type
class IdeaProposalType:
    idea_id: strawberry.ID
    idea_title: str
    team_name: str | None
    title: str
    executive_summary: str
    problem: str
    proposed_solution: str
    requirements_summary: str
    scope: str
    deliverables: str
    risks: str
    assumptions: str
    estimated_effort: str
    estimated_timeline: str
    acceptance_criteria: str
    status: str
    review_feedback: str
    submitted_at: datetime | None
    decided_at: datetime | None
    updated_at: datetime

    @staticmethod
    def from_model(p: IdeaProposal) -> 'IdeaProposalType':
        return IdeaProposalType(
            idea_id=strawberry.ID(str(p.idea_id)),
            idea_title=p.idea.title,
            team_name=p.team.name if p.team_id else None,
            title=p.title,
            executive_summary=p.executive_summary,
            problem=p.problem,
            proposed_solution=p.proposed_solution,
            requirements_summary=p.requirements_summary,
            scope=p.scope,
            deliverables=p.deliverables,
            risks=p.risks,
            assumptions=p.assumptions,
            estimated_effort=p.estimated_effort,
            estimated_timeline=p.estimated_timeline,
            acceptance_criteria=p.acceptance_criteria,
            status=p.status,
            review_feedback=p.review_feedback,
            submitted_at=p.submitted_at,
            decided_at=p.decided_at,
            updated_at=p.updated_at,
        )


@strawberry.type
class IdeaProposalState:
    """What the caller can do about this idea's proposal, and the proposal if they may see it."""

    viewer_role: str | None
    can_start: bool
    can_edit: bool
    can_submit: bool
    can_decide: bool
    proposal: IdeaProposalType | None


@strawberry.type
class ProposalPayload:
    success: bool
    message: str
    field: str | None = None
    proposal: IdeaProposalType | None = None


@strawberry.type
class ViewReceipt:
    """Who opened the proposal and when, for the page's watermark."""

    viewer_name: str
    viewer_email: str
    viewed_at: datetime


@strawberry.type
class ViewPayload:
    success: bool
    message: str
    receipt: ViewReceipt | None = None


@strawberry.input
class UpdateIdeaProposalInput:
    idea_id: strawberry.ID
    title: str | None = None
    executive_summary: str | None = None
    problem: str | None = None
    proposed_solution: str | None = None
    requirements_summary: str | None = None
    scope: str | None = None
    deliverables: str | None = None
    risks: str | None = None
    assumptions: str | None = None
    estimated_effort: str | None = None
    estimated_timeline: str | None = None
    acceptance_criteria: str | None = None


def _camel(name: str | None) -> str | None:
    if not name:
        return None
    head, *rest = name.split('_')
    return head + ''.join(part.capitalize() for part in rest)


def _run(action):
    try:
        proposal = action()
    except (proposals.ProposalError, AdministrationError) as exc:
        return ProposalPayload(
            success=False, message=exc.message, field=_camel(getattr(exc, 'field', None))
        )
    proposal = IdeaProposal.objects.select_related('idea', 'team').get(pk=proposal.pk)
    return ProposalPayload(
        success=True, message='Done.', proposal=IdeaProposalType.from_model(proposal)
    )


@strawberry.type
class Query:
    @strawberry.field(
        description="What the caller can do about an idea's proposal, and the proposal if they "
        'may see it. The owner sees it only once it is released.'
    )
    def idea_proposal_state(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> IdeaProposalState:
        user = info.context.user
        idea = Idea.objects.filter(pk=proposals._as_int(idea_id)).first()
        role = proposals.viewer_role(user, idea) if idea else None
        found = proposals.proposal_for(user, idea_id) if idea else None
        proposal = found[0] if found else None
        editable = (
            role == 'writer'
            and proposal is not None
            and proposal.status
            in (
                'draft',
                'changes_requested',
            )
        )
        return IdeaProposalState(
            viewer_role=role,
            can_start=bool(
                idea
                and role == 'writer'
                and idea.status == Idea.Status.APPROVED
                and proposal is None
            ),
            can_edit=editable,
            can_submit=bool(editable and proposals.is_writer_lead(user, idea)),
            can_decide=bool(role == 'admin' and proposal and proposal.status == 'submitted'),
            proposal=IdeaProposalType.from_model(proposal) if proposal else None,
        )

    @strawberry.field(
        description='Proposals that have reached the admin. Empty without the permission.'
    )
    def proposals_for_release(self, info: strawberry.Info) -> list[IdeaProposalType]:
        return [
            IdeaProposalType.from_model(p) for p in proposals.decided_proposals(info.context.user)
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation(description='Begin the proposal for an approved idea (review team only).')
    def start_idea_proposal(self, info: strawberry.Info, idea_id: strawberry.ID) -> ProposalPayload:
        return _run(lambda: proposals.start_proposal(info.context.user, idea_id))

    @strawberry.mutation
    def update_idea_proposal(
        self, info: strawberry.Info, input: UpdateIdeaProposalInput
    ) -> ProposalPayload:
        values = {
            name: value
            for name, value in vars(input).items()
            if name != 'idea_id' and value is not None
        }
        return _run(lambda: proposals.update_proposal(info.context.user, input.idea_id, values))

    @strawberry.mutation(description="The team's lead sends the finished proposal to the admin.")
    def submit_idea_proposal(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> ProposalPayload:
        return _run(lambda: proposals.submit_proposal(info.context.user, idea_id))

    @strawberry.mutation(description='Admin: approve the proposal and release it to the owner.')
    def release_idea_proposal(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> ProposalPayload:
        return _run(lambda: proposals.release(info.context.user, idea_id))

    @strawberry.mutation(description='Admin: send the proposal back to the review team.')
    def request_idea_proposal_changes(
        self, info: strawberry.Info, idea_id: strawberry.ID, feedback: str
    ) -> ProposalPayload:
        return _run(lambda: proposals.request_changes(info.context.user, idea_id, feedback))

    @strawberry.mutation(description='Admin: the idea will not be developed.')
    def decline_idea_proposal(
        self, info: strawberry.Info, idea_id: strawberry.ID, reason: str
    ) -> ProposalPayload:
        return _run(lambda: proposals.decline(info.context.user, idea_id, reason))

    @strawberry.mutation(description='Owner: note that the released proposal was opened.')
    def record_proposal_view(self, info: strawberry.Info, idea_id: strawberry.ID) -> ViewPayload:
        user = info.context.user
        try:
            view = proposals.record_view(user, idea_id)
        except proposals.ProposalError as exc:
            return ViewPayload(success=False, message=exc.message)
        return ViewPayload(
            success=True,
            message='Recorded.',
            receipt=ViewReceipt(
                viewer_name=user.get_full_name() or user.email,
                viewer_email=user.email,
                viewed_at=view.viewed_at,
            ),
        )
