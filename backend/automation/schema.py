"""
GraphQL for the automation delivery lifecycle.

Explicit business actions, never a free `updateStatus`: each mutation names one
thing that happens and the service behind it decides whether it may. A business
refusal is a `success: false` payload with the field it belongs to, the same
contract every other domain here uses; only a bug raises.
"""

from datetime import datetime

import strawberry

import automation.authorization as authz
import automation.services as services
from automation.authorization import AutomationError
from automation.models import (
    AutomationOpportunity,
    OpportunityEvent,
    Requirement,
    SolutionScope,
)


@strawberry.type
class RequirementType:
    id: strawberry.ID
    opportunity_id: strawberry.ID
    title: str
    description: str
    type: str
    priority: str
    status: str
    acceptance_criteria: str
    created_by_id: strawberry.ID
    created_at: datetime
    updated_at: datetime

    @staticmethod
    def from_model(r: Requirement) -> 'RequirementType':
        return RequirementType(
            id=strawberry.ID(str(r.pk)),
            opportunity_id=strawberry.ID(str(r.opportunity_id)),
            title=r.title,
            description=r.description,
            type=r.type,
            priority=r.priority,
            status=r.status,
            acceptance_criteria=r.acceptance_criteria,
            created_by_id=strawberry.ID(str(r.created_by_id)),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )


@strawberry.type
class SolutionType:
    opportunity_id: strawberry.ID
    summary: str
    business_workflow: str
    in_scope: str
    out_of_scope: str
    systems_integrations: str
    technical_considerations: str
    assumptions: str
    constraints: str
    risks: str
    expected_output: str
    updated_at: datetime

    @staticmethod
    def from_model(s: SolutionScope) -> 'SolutionType':
        return SolutionType(
            opportunity_id=strawberry.ID(str(s.opportunity_id)),
            summary=s.summary,
            business_workflow=s.business_workflow,
            in_scope=s.in_scope,
            out_of_scope=s.out_of_scope,
            systems_integrations=s.systems_integrations,
            technical_considerations=s.technical_considerations,
            assumptions=s.assumptions,
            constraints=s.constraints,
            risks=s.risks,
            expected_output=s.expected_output,
            updated_at=s.updated_at,
        )


@strawberry.type
class OpportunityEventType:
    id: strawberry.ID
    action: str
    entity_type: str
    entity_id: strawberry.ID | None
    from_status: str
    to_status: str
    actor_id: strawberry.ID
    created_at: datetime

    @staticmethod
    def from_model(e: OpportunityEvent) -> 'OpportunityEventType':
        return OpportunityEventType(
            id=strawberry.ID(str(e.pk)),
            action=e.action,
            entity_type=e.entity_type,
            entity_id=strawberry.ID(str(e.entity_id)) if e.entity_id is not None else None,
            from_status=e.from_status,
            to_status=e.to_status,
            actor_id=strawberry.ID(str(e.actor_id)),
            created_at=e.created_at,
        )


@strawberry.type
class OpportunityCapabilities:
    """What the *caller* may do here - for showing buttons; the server decides regardless."""

    can_manage_requirements: bool
    can_edit_solution: bool
    can_transition: bool


@strawberry.type
class OpportunityType:
    id: strawberry.ID
    idea_id: strawberry.ID
    idea_title: str
    title: str
    summary: str
    problem_statement: str
    automation_goal: str
    expected_benefit: str
    priority: str
    status: str
    submission_context: str
    owner_name: str
    tenant_name: str | None
    approved_at: datetime | None
    ready_at: datetime | None
    created_at: datetime
    updated_at: datetime
    capabilities: OpportunityCapabilities

    @staticmethod
    def from_model(o: AutomationOpportunity, user) -> 'OpportunityType':
        tenant = o.organization.name if o.organization_id else (o.team.name if o.team_id else None)
        return OpportunityType(
            id=strawberry.ID(str(o.pk)),
            idea_id=strawberry.ID(str(o.idea_id)),
            idea_title=o.idea.title,
            title=o.title,
            summary=o.summary,
            problem_statement=o.problem_statement,
            automation_goal=o.automation_goal,
            expected_benefit=o.expected_benefit,
            priority=o.priority,
            status=o.status,
            submission_context=o.submission_context,
            owner_name=o.owner.get_full_name() or o.owner.email,
            tenant_name=tenant,
            approved_at=o.approved_at,
            ready_at=o.ready_at,
            created_at=o.created_at,
            updated_at=o.updated_at,
            capabilities=OpportunityCapabilities(
                can_manage_requirements=authz.can_manage_requirements(user, o),
                can_edit_solution=authz.can_edit_solution(user, o),
                can_transition=authz.can_transition(user, o),
            ),
        )


@strawberry.type
class OpportunityPayload:
    success: bool
    message: str
    field: str | None = None
    opportunity: OpportunityType | None = None


@strawberry.type
class RequirementPayload:
    success: bool
    message: str
    field: str | None = None
    requirement: RequirementType | None = None


@strawberry.type
class SolutionPayload:
    success: bool
    message: str
    field: str | None = None
    solution: SolutionType | None = None


@strawberry.input
class CreateOpportunityInput:
    idea_id: strawberry.ID


@strawberry.input
class UpdateOpportunityInput:
    id: strawberry.ID
    title: str | None = None
    summary: str | None = None
    priority: str | None = None


@strawberry.input
class RequirementInput:
    title: str | None = None
    description: str | None = None
    type: str | None = None
    priority: str | None = None
    status: str | None = None
    acceptance_criteria: str | None = None


@strawberry.input
class UpdateSolutionInput:
    opportunity_id: strawberry.ID
    summary: str | None = None
    business_workflow: str | None = None
    in_scope: str | None = None
    out_of_scope: str | None = None
    systems_integrations: str | None = None
    technical_considerations: str | None = None
    assumptions: str | None = None
    constraints: str | None = None
    risks: str | None = None
    expected_output: str | None = None


def _camel(name: str | None) -> str | None:
    if not name:
        return None
    head, *rest = name.split('_')
    return head + ''.join(part.capitalize() for part in rest)


def _opportunity_result(info, action) -> OpportunityPayload:
    user = info.context.user
    try:
        opportunity = action()
    except AutomationError as exc:
        return OpportunityPayload(success=False, message=exc.message, field=_camel(exc.field))
    return OpportunityPayload(
        success=True, message='Done.', opportunity=OpportunityType.from_model(opportunity, user)
    )


@strawberry.type
class Query:
    @strawberry.field(description='Automation opportunities the caller may read, newest first.')
    def automation_opportunities(
        self, info: strawberry.Info, status: str | None = None
    ) -> list[OpportunityType]:
        user = info.context.user
        return [
            OpportunityType.from_model(o, user)
            for o in services.list_opportunities(user, status=status)[:100]
        ]

    @strawberry.field(description='One opportunity, or null when it cannot be shown to the caller.')
    def automation_opportunity(
        self, info: strawberry.Info, id: strawberry.ID
    ) -> OpportunityType | None:
        user = info.context.user
        opportunity = services.get_opportunity(user, id)
        return OpportunityType.from_model(opportunity, user) if opportunity else None

    @strawberry.field(description="The idea's live opportunity: the way from an idea to delivery.")
    def automation_opportunity_for_idea(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> OpportunityType | None:
        user = info.context.user
        opportunity = services.opportunity_for_idea(user, idea_id)
        return OpportunityType.from_model(opportunity, user) if opportunity else None

    @strawberry.field(description="An opportunity's requirements, oldest first.")
    def requirements(
        self, info: strawberry.Info, opportunity_id: strawberry.ID
    ) -> list[RequirementType]:
        opportunity = services.get_opportunity(info.context.user, opportunity_id)
        if opportunity is None:
            return []
        return [RequirementType.from_model(r) for r in opportunity.requirements.all()[:200]]

    @strawberry.field(description="An opportunity's solution and scope, or null if none yet.")
    def opportunity_solution(
        self, info: strawberry.Info, opportunity_id: strawberry.ID
    ) -> SolutionType | None:
        opportunity = services.get_opportunity(info.context.user, opportunity_id)
        solution = getattr(opportunity, 'solution', None) if opportunity else None
        return SolutionType.from_model(solution) if solution else None

    @strawberry.field(description="An opportunity's audit trail, oldest first.")
    def opportunity_activity(
        self, info: strawberry.Info, opportunity_id: strawberry.ID
    ) -> list[OpportunityEventType]:
        opportunity = services.get_opportunity(info.context.user, opportunity_id)
        if opportunity is None:
            return []
        return [OpportunityEventType.from_model(e) for e in opportunity.events.all()[:200]]


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            "Open an idea's opportunity by hand (delivery managers only). Normally the owner's "
            'go-ahead opens it.'
        )
    )
    def create_automation_opportunity(
        self, info: strawberry.Info, input: CreateOpportunityInput
    ) -> OpportunityPayload:
        return _opportunity_result(
            info, lambda: services.create_opportunity(info.context.user, input.idea_id)
        )

    @strawberry.mutation(description="Edit an opportunity's title, summary or priority.")
    def update_automation_opportunity(
        self, info: strawberry.Info, input: UpdateOpportunityInput
    ) -> OpportunityPayload:
        data = services.OpportunityInput(
            title=input.title, summary=input.summary, priority=input.priority
        )
        return _opportunity_result(
            info, lambda: services.update_opportunity(info.context.user, input.id, data)
        )

    @strawberry.mutation
    def cancel_automation_opportunity(
        self, info: strawberry.Info, id: strawberry.ID
    ) -> OpportunityPayload:
        return _opportunity_result(info, lambda: services.cancel_opportunity(info.context.user, id))

    @strawberry.mutation
    def create_requirement(
        self, info: strawberry.Info, opportunity_id: strawberry.ID, input: RequirementInput
    ) -> RequirementPayload:
        try:
            requirement = services.create_requirement(
                info.context.user, opportunity_id, services.RequirementInput(**vars(input))
            )
        except AutomationError as exc:
            return RequirementPayload(success=False, message=exc.message, field=_camel(exc.field))
        return RequirementPayload(
            success=True,
            message='Requirement added.',
            requirement=RequirementType.from_model(requirement),
        )

    @strawberry.mutation
    def update_requirement(
        self, info: strawberry.Info, id: strawberry.ID, input: RequirementInput
    ) -> RequirementPayload:
        try:
            requirement = services.update_requirement(
                info.context.user, id, services.RequirementInput(**vars(input))
            )
        except AutomationError as exc:
            return RequirementPayload(success=False, message=exc.message, field=_camel(exc.field))
        return RequirementPayload(
            success=True,
            message='Requirement saved.',
            requirement=RequirementType.from_model(requirement),
        )

    @strawberry.mutation(description="Create or update an opportunity's solution and scope.")
    def update_opportunity_solution(
        self, info: strawberry.Info, input: UpdateSolutionInput
    ) -> SolutionPayload:
        values = {f: getattr(input, f) for f in services.SOLUTION_FIELDS}
        try:
            solution = services.update_solution(info.context.user, input.opportunity_id, values)
        except AutomationError as exc:
            return SolutionPayload(success=False, message=exc.message, field=_camel(exc.field))
        return SolutionPayload(
            success=True, message='Solution saved.', solution=SolutionType.from_model(solution)
        )
