"""
The automation delivery lifecycle's business operations.

Every function takes the authenticated user and the ids the operation is about,
re-reads what it needs under a lock, checks the entry rule, the caller's
authority and the current state, writes the change *and its audit event in one
transaction*, and tells people afterwards. Nothing accepts a status, an owner, a
team or an organization from a client: the ownership context is copied from the
idea, and a status only moves through the named actions below.

Phase 1 covers opening an opportunity, its requirements and its solution, and
the early lifecycle (draft -> discovery -> requirements -> solution design ->
proposal). Everything from the proposal's acceptance onward (readiness for
assignment, assignment, project) is added by the later phases and has no
callable path here yet, so it cannot be reached by guessing a status.
"""

import logging
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from automation import authorization as authz
from automation.authorization import AutomationError
from automation.models import (
    PRIORITY_CHOICES,
    REQUIREMENT_STATUS_CHOICES,
    REQUIREMENT_TYPE_CHOICES,
    AutomationOpportunity,
    OpportunityEvent,
    Proposal,
    Requirement,
    SolutionScope,
)
from ideas import selectors as idea_selectors
from ideas.models import Idea
from identity.models import User

logger = logging.getLogger(__name__)

NOT_READY = 'Only an idea that is ready for implementation can become an automation opportunity.'
UNAVAILABLE = 'This is not available.'
ALREADY_OPEN = 'This idea already has an automation opportunity.'
CLOSED = 'This opportunity is closed and can no longer be changed.'

#: An opportunity is born ready for assignment: the proposal it carries was written by the review
#: team, released by a platform admin and read and accepted by the owner (their go-ahead). What is
#: left is to assign a developer. Until a project exists it can still be cancelled.
_CANCELLABLE = frozenset({'ready_for_assignment', 'assigned'})

#: Statuses in which requirements and the solution may still be edited: the delivery team's
#: working material, from the moment the opportunity opens until the project is under way.
_EDITABLE = frozenset({'ready_for_assignment', 'assigned', 'project_created'})


@dataclass
class OpportunityInput:
    title: str | None = None
    summary: str | None = None
    priority: str | None = None


def _clean(value: str | None, field: str, *, required: bool = False, limit: int | None = None):
    text = (value or '').strip()
    if required and not text:
        raise AutomationError(f'{field.replace("_", " ").capitalize()} is required.', field=field)
    if limit is not None and len(text) > limit:
        raise AutomationError(
            f'{field.replace("_", " ").capitalize()} must be {limit} characters or fewer.',
            field=field,
        )
    return text


def _choice(value: str | None, choices, field: str, default: str | None = None) -> str:
    normalized = (value or '').strip().lower() or default
    if normalized not in dict(choices):
        raise AutomationError(f'Choose a valid {field.replace("_", " ")}.', field=field)
    return normalized


def _record(
    opportunity, actor, action, entity_type, entity_id=None, from_status='', to_status='', **meta
):
    OpportunityEvent.objects.create(
        opportunity=opportunity,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        from_status=from_status,
        to_status=to_status,
        metadata=meta,
    )


def _notify(recipients, kind, title, body, idea):
    from notifications.services import deliver

    people = [u for u in recipients if u is not None]
    if people:
        deliver(recipients=people, kind=kind, title=title, body=body, idea=idea, send_email=False)


def _owner_if_not(opportunity, actor):
    return [opportunity.owner] if opportunity.owner_id != actor.pk else []


def _locked_opportunity(user: User | None, opportunity_id: object):
    """The opportunity under a row lock, or the one refusal that hides whether it exists."""
    try:
        pk = int(str(opportunity_id))
    except (TypeError, ValueError):
        raise AutomationError(UNAVAILABLE, reason='forbidden') from None
    opportunity = (
        AutomationOpportunity.objects.select_for_update(of=('self',))
        .select_related('idea', 'owner', 'organization', 'team')
        .filter(pk=pk)
        .first()
    )
    if opportunity is None or not authz.can_view(user, opportunity):
        raise AutomationError(UNAVAILABLE, reason='forbidden')
    return opportunity


def _require_editable(opportunity):
    if opportunity.status not in _EDITABLE:
        raise AutomationError(CLOSED)


# --- the opportunity ------------------------------------------------------------


def delivery_managers() -> list[User]:
    """Active accounts that run delivery: by group, directly, or as superuser."""
    codename = authz.MANAGE_DELIVERY.split('.', 1)[1]
    return list(
        User.objects.filter(is_active=True)
        .filter(
            Q(
                user_permissions__content_type__app_label='automation',
                user_permissions__codename=codename,
            )
            | Q(
                groups__permissions__content_type__app_label='automation',
                groups__permissions__codename=codename,
            )
            | Q(is_superuser=True)
        )
        .distinct()
    )


def _open(idea: Idea, proposal, created_by: User) -> AutomationOpportunity:
    """
    Open the opportunity for `idea`, ready for a developer, carrying the accepted proposal.

    The partial unique constraint is the backstop for "one live opportunity per idea"; its
    violation is reported as the same friendly refusal.
    """
    try:
        with transaction.atomic():
            opportunity = AutomationOpportunity.objects.create(
                idea=idea,
                title=proposal.title or idea.title,
                summary=proposal.executive_summary,
                problem_statement=proposal.problem or idea.description,
                automation_goal=idea.desired_outcome or idea.improvement_goal,
                expected_benefit=idea.expected_benefit,
                priority='medium',
                status='ready_for_assignment',
                submission_context=idea.submission_context,
                organization=idea.organization if idea.organization_id else None,
                team=idea.team if idea.team_id else None,
                owner=idea.author,
                created_by=created_by,
                approved_at=idea.owner_go_ahead_at,
                ready_at=timezone.now(),
            )
    except IntegrityError:
        raise AutomationError(ALREADY_OPEN, field='idea') from None

    # The proposal travels with the opportunity as the record of what was agreed. It is
    # accepted by definition: the owner read it and gave the go-ahead on it.
    Proposal.objects.create(
        opportunity=opportunity,
        title=proposal.title,
        executive_summary=proposal.executive_summary,
        problem=proposal.problem,
        proposed_solution=proposal.proposed_solution,
        requirements_summary=proposal.requirements_summary,
        scope=proposal.scope,
        deliverables=proposal.deliverables,
        risks=proposal.risks,
        assumptions=proposal.assumptions,
        estimated_effort=proposal.estimated_effort,
        estimated_timeline=proposal.estimated_timeline,
        acceptance_criteria=proposal.acceptance_criteria,
        feasibility=proposal.feasibility,
        milestones=proposal.milestones,
        financial_requirements=proposal.financial_requirements,
        payment_required=proposal.payment_required,
        payment_plan=proposal.payment_plan,
        status='accepted',
        created_by=proposal.created_by,
        reviewed_by=idea.author,
        submitted_at=proposal.submitted_at,
        reviewed_at=timezone.now(),
    )
    _record(opportunity, created_by, 'opportunity_created', 'opportunity', opportunity.pk)
    return opportunity


def _released_proposal(idea: Idea):
    from reviews.models import IdeaProposal

    return IdeaProposal.objects.filter(idea=idea, status='released').first()


def open_from_go_ahead(idea: Idea) -> AutomationOpportunity:
    """
    The owner's go-ahead opens the opportunity, so the delivery team sees it at once.

    Called by `ideas.go_ahead` inside the go-ahead's own transaction, so an idea cannot become
    ready without its opportunity, or the other way round. Nobody is asked to press a button
    and nobody is authorized here: the authorization was the owner's go-ahead, on a proposal
    an admin released.
    """
    proposal = _released_proposal(idea)
    if proposal is None:
        raise AutomationError('There is no released proposal to start from.')
    opportunity = _open(idea, proposal, idea.author)
    _notify(
        delivery_managers(),
        'delivery.go_ahead_received',
        f'"{opportunity.title}" is waiting for a developer',
        'The owner read the proposal and gave the go-ahead. Assign a developer or a team.',
        idea,
    )
    return opportunity


@transaction.atomic
def create_opportunity(user: User | None, idea_id: object) -> AutomationOpportunity:
    """
    Open the opportunity for a ready idea by hand: the delivery manager's way to recover an
    idea whose opportunity was not opened at the go-ahead.

    Needs a released proposal, exactly as the automatic path does.
    """
    if user is None or not user.is_active:
        raise AutomationError('Sign in to continue.', reason='unauthenticated')
    try:
        pk = int(str(idea_id))
    except (TypeError, ValueError):
        raise AutomationError(UNAVAILABLE) from None

    idea = (
        Idea.objects.select_for_update(of=('self',))
        .select_related('organization', 'team')
        .filter(pk=pk)
        .first()
    )
    if idea is None or not authz.can_create_for(user, idea):
        raise AutomationError(UNAVAILABLE)
    if idea.status != Idea.Status.READY_FOR_IMPLEMENTATION:
        raise AutomationError(NOT_READY)
    proposal = _released_proposal(idea)
    if proposal is None:
        raise AutomationError('This idea has no released proposal to start from.')
    return _open(idea, proposal, user)


@transaction.atomic
def update_opportunity(
    user: User | None, opportunity_id: object, data: OpportunityInput
) -> AutomationOpportunity:
    opportunity = _locked_opportunity(user, opportunity_id)
    if not authz.can_manage_requirements(user, opportunity):
        raise AutomationError('You cannot change this opportunity.')
    _require_editable(opportunity)

    changed = []
    if data.title is not None:
        opportunity.title = _clean(data.title, 'title', required=True, limit=200)
        changed.append('title')
    if data.summary is not None:
        opportunity.summary = _clean(data.summary, 'summary')
        changed.append('summary')
    if data.priority is not None:
        opportunity.priority = _choice(data.priority, PRIORITY_CHOICES, 'priority')
        changed.append('priority')
    if changed:
        opportunity.save(update_fields=[*changed, 'updated_at'])
        _record(
            opportunity, user, 'opportunity_updated', 'opportunity', opportunity.pk, fields=changed
        )
    return opportunity


@transaction.atomic
def cancel_opportunity(user, opportunity_id) -> AutomationOpportunity:
    opportunity = _locked_opportunity(user, opportunity_id)
    if not authz.can_transition(user, opportunity):
        raise AutomationError('You cannot cancel this opportunity.')
    if opportunity.status not in _CANCELLABLE:
        raise AutomationError(CLOSED)
    before = opportunity.status
    opportunity.status = 'cancelled'
    opportunity.save(update_fields=['status', 'updated_at'])
    _record(
        opportunity,
        user,
        'opportunity_status_changed',
        'opportunity',
        opportunity.pk,
        from_status=before,
        to_status='cancelled',
    )
    return opportunity


# --- requirements ---------------------------------------------------------------


@dataclass
class RequirementInput:
    title: str | None = None
    description: str | None = None
    type: str | None = None
    priority: str | None = None
    status: str | None = None
    acceptance_criteria: str | None = None


@transaction.atomic
def create_requirement(user, opportunity_id, data: RequirementInput) -> Requirement:
    opportunity = _locked_opportunity(user, opportunity_id)
    if not authz.can_manage_requirements(user, opportunity):
        raise AutomationError('You cannot add requirements to this opportunity.')
    _require_editable(opportunity)

    requirement = Requirement.objects.create(
        opportunity=opportunity,
        title=_clean(data.title, 'title', required=True, limit=200),
        description=_clean(data.description, 'description'),
        type=_choice(data.type, REQUIREMENT_TYPE_CHOICES, 'type', default='functional'),
        priority=_choice(data.priority, PRIORITY_CHOICES, 'priority', default='medium'),
        acceptance_criteria=_clean(data.acceptance_criteria, 'acceptance_criteria'),
        created_by=user,
    )
    _record(opportunity, user, 'requirement_created', 'requirement', requirement.pk)
    return requirement


def _requirement_changes(data: 'RequirementInput') -> dict:
    """The fields the caller sent, each with the cleaner that validates it."""
    table = {
        'title': lambda v: _clean(v, 'title', required=True, limit=200),
        'description': lambda v: _clean(v, 'description'),
        'type': lambda v: _choice(v, REQUIREMENT_TYPE_CHOICES, 'type'),
        'priority': lambda v: _choice(v, PRIORITY_CHOICES, 'priority'),
        'status': lambda v: _choice(v, REQUIREMENT_STATUS_CHOICES, 'status'),
        'acceptance_criteria': lambda v: _clean(v, 'acceptance_criteria'),
    }
    return {
        field: (getattr(data, field), clean)
        for field, clean in table.items()
        if getattr(data, field) is not None
    }


@transaction.atomic
def update_requirement(user, requirement_id, data: RequirementInput) -> Requirement:
    try:
        pk = int(str(requirement_id))
    except (TypeError, ValueError):
        raise AutomationError(UNAVAILABLE) from None
    owning = Requirement.objects.filter(pk=pk).values_list('opportunity_id', flat=True).first()
    if owning is None:
        raise AutomationError(UNAVAILABLE)
    opportunity = _locked_opportunity(user, owning)
    if not authz.can_manage_requirements(user, opportunity):
        raise AutomationError('You cannot change this requirement.')
    _require_editable(opportunity)

    requirement = Requirement.objects.select_for_update().get(pk=pk)
    changed = []
    for field, (value, clean) in _requirement_changes(data).items():
        setattr(requirement, field, clean(value))
        changed.append(field)
    if changed:
        requirement.save(update_fields=[*changed, 'updated_at'])
        _record(
            opportunity, user, 'requirement_updated', 'requirement', requirement.pk, fields=changed
        )
    return requirement


# --- the solution ---------------------------------------------------------------

SOLUTION_FIELDS = (
    'summary',
    'business_workflow',
    'in_scope',
    'out_of_scope',
    'systems_integrations',
    'technical_considerations',
    'assumptions',
    'constraints',
    'risks',
    'expected_output',
)


@transaction.atomic
def update_solution(user, opportunity_id, values: dict[str, str | None]) -> SolutionScope:
    """Create or update the solution; only the fields passed are touched."""
    opportunity = _locked_opportunity(user, opportunity_id)
    if not authz.can_edit_solution(user, opportunity):
        raise AutomationError('You cannot edit the solution for this opportunity.')
    _require_editable(opportunity)

    unknown = set(values) - set(SOLUTION_FIELDS)
    if unknown:
        raise AutomationError('Unknown solution field.', field=sorted(unknown)[0])
    solution, _created = SolutionScope.objects.get_or_create(
        opportunity=opportunity, defaults={'updated_by': user}
    )
    changed = []
    for field, value in values.items():
        if value is not None:
            setattr(solution, field, (value or '').strip())
            changed.append(field)
    if changed:
        solution.updated_by = user
        solution.save(update_fields=[*changed, 'updated_by', 'updated_at'])
        _record(opportunity, user, 'solution_updated', 'solution', solution.pk, fields=changed)
    return solution


# --- reads ----------------------------------------------------------------------


def get_opportunity(user: User | None, opportunity_id: object) -> AutomationOpportunity | None:
    """The opportunity, or `None` for every reason it cannot be handed over."""
    try:
        pk = int(str(opportunity_id))
    except (TypeError, ValueError):
        return None
    opportunity = (
        AutomationOpportunity.objects.select_related('idea', 'owner', 'organization', 'team')
        .filter(pk=pk)
        .first()
    )
    return opportunity if opportunity and authz.can_view(user, opportunity) else None


def list_opportunities(user: User | None, *, status: str | None = None):
    """Opportunities the caller may read, newest first. Delivery managers see all."""
    if user is None or not user.is_active:
        return AutomationOpportunity.objects.none()
    queryset = AutomationOpportunity.objects.select_related('idea', 'owner', 'organization', 'team')
    if not authz.is_delivery_manager(user):
        from django.db.models import Q

        from teams.authorization import active_team_ids

        readable = idea_selectors.list_ideas(user).values('pk')
        queryset = queryset.filter(
            Q(idea_id__in=readable)
            | Q(assignments__status='active', assignments__assignee_user=user)
            | Q(
                assignments__status='active',
                assignments__assignee_team_id__in=active_team_ids(user),
            )
        ).distinct()
    if status:
        queryset = queryset.filter(status=status)
    return queryset


def opportunity_for_idea(user: User | None, idea_id: object) -> AutomationOpportunity | None:
    """The idea's live opportunity, if the caller may read it - the way back from an idea."""
    try:
        pk = int(str(idea_id))
    except (TypeError, ValueError):
        return None
    opportunity = (
        AutomationOpportunity.objects.select_related('idea', 'owner', 'organization', 'team')
        .filter(idea_id=pk)
        .exclude(status='cancelled')
        .first()
    )
    return opportunity if opportunity and authz.can_view(user, opportunity) else None


def now():  # pragma: no cover - indirection kept for the test clock
    return timezone.now()
