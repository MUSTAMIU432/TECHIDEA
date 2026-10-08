"""
GraphQL for proposal, assignment, project, testing, UAT, deployment and impact.

Same contract as `automation.schema`: explicit business actions, refusals as
`success: false` payloads with the field they belong to, and nothing here decides
anything - each resolver hands the authenticated user and the ids to
`automation.delivery` and reports what it says.
"""

from datetime import date, datetime
from decimal import Decimal

import strawberry

import automation.authorization as authz
import automation.delivery as delivery
import automation.impact as impact_calc
import automation.services as services
from automation.authorization import AutomationError
from automation.models import AutomationOpportunity, Project
from automation.schema import OpportunityType, _camel


def _id(value):
    return strawberry.ID(str(value)) if value is not None else None


def _build(cls, obj, **extra):
    """Fill a strawberry type from a model: ids become `ID`, everything else is copied."""
    values = {}
    for name in cls.__annotations__:
        if name in extra:
            values[name] = extra[name]
            continue
        value = getattr(obj, name)
        values[name] = _id(value) if (name == 'id' or name.endswith('_id')) else value
    return cls(**values)


# --- types ---


@strawberry.type
class ProposalType:
    id: strawberry.ID
    opportunity_id: strawberry.ID
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
    feasibility: str
    milestones: str
    financial_requirements: str
    payment_required: str
    payment_plan: str
    status: str
    review_feedback: str
    submitted_at: datetime | None
    reviewed_at: datetime | None
    updated_at: datetime


@strawberry.type
class AssignmentType:
    id: strawberry.ID
    opportunity_id: strawberry.ID
    assignee_user_id: strawberry.ID | None
    assignee_team_id: strawberry.ID | None
    assignee_name: str
    status: str
    assigned_at: datetime


@strawberry.type
class PipelineStage:
    stage: str
    state: str


@strawberry.type
class ProjectProgress:
    stages: list[PipelineStage]
    task_completion_percent: int | None
    tasks_total: int
    tasks_done: int
    tasks_blocked: int


@strawberry.type
class ProjectCapabilities:
    can_manage: bool
    can_perform_uat: bool
    can_record_impact: bool


@strawberry.type
class ProjectType:
    id: strawberry.ID
    opportunity_id: strawberry.ID
    idea_id: strawberry.ID
    title: str
    description: str
    status: str
    submission_context: str
    owner_name: str
    tenant_name: str | None
    assigned_name: str
    start_date: date | None
    target_date: date | None
    completed_at: datetime | None
    created_at: datetime
    progress: ProjectProgress
    capabilities: ProjectCapabilities


@strawberry.type
class MilestoneType:
    id: strawberry.ID
    project_id: strawberry.ID
    title: str
    description: str
    due_date: date | None
    status: str
    order: int
    completed_at: datetime | None


@strawberry.type
class TaskType:
    id: strawberry.ID
    project_id: strawberry.ID
    milestone_id: strawberry.ID | None
    title: str
    description: str
    status: str
    priority: str
    assignee_id: strawberry.ID | None
    due_date: date | None
    updated_at: datetime


@strawberry.type
class TestCaseType:
    id: strawberry.ID
    project_id: strawberry.ID
    title: str
    description: str
    expected_result: str
    actual_result: str
    status: str
    is_required: bool
    tester_id: strawberry.ID | None
    executed_at: datetime | None
    notes: str


@strawberry.type
class UatRecordType:
    id: strawberry.ID
    project_id: strawberry.ID
    scenario: str
    expected_outcome: str
    actual_outcome: str
    result: str
    is_required: bool
    reviewer_id: strawberry.ID | None
    feedback: str
    recorded_at: datetime | None
    created_at: datetime


@strawberry.type
class DeploymentType:
    id: strawberry.ID
    project_id: strawberry.ID
    environment: str
    version: str
    status: str
    deployed_by_id: strawberry.ID
    deployment_date: datetime | None
    deployment_notes: str
    rollback_information: str
    verified_at: datetime | None
    created_at: datetime


@strawberry.type
class ImpactResults:
    time_saved_percent: Decimal | None
    people_reduced: Decimal | None
    error_reduction_percent: Decimal | None
    workload_reduction_percent: Decimal | None
    cost_difference: Decimal | None
    requests_difference: Decimal | None
    estimated_hours_saved_per_week: Decimal | None
    measured_hours_saved_per_week: Decimal | None
    hours_saved_variance: Decimal | None


@strawberry.type
class ImpactType:
    id: strawberry.ID
    project_id: strawberry.ID
    status: str
    before_processing_minutes: Decimal | None
    after_processing_minutes: Decimal | None
    before_people_involved: int | None
    after_people_involved: int | None
    before_requests: int | None
    after_requests: int | None
    before_cost: Decimal | None
    after_cost: Decimal | None
    before_error_rate: Decimal | None
    after_error_rate: Decimal | None
    before_workload_hours: Decimal | None
    after_workload_hours: Decimal | None
    users_affected: int | None
    estimated_hours_saved_per_week: Decimal | None
    measured_hours_saved_per_week: Decimal | None
    estimated_cost_savings: Decimal | None
    measured_cost_savings: Decimal | None
    automation_rate: Decimal | None
    qualitative_outcome: str
    notes: str
    updated_at: datetime
    results: ImpactResults


@strawberry.type
class AssigneeOption:
    id: strawberry.ID
    name: str
    detail: str


@strawberry.type
class AssigneeOptions:
    users: list[AssigneeOption]
    teams: list[AssigneeOption]


@strawberry.type
class Traceability:
    """Idea -> opportunity -> proposal -> project -> deployment -> impact, as far as it goes."""

    idea_id: strawberry.ID
    opportunity_id: strawberry.ID | None
    proposal_id: strawberry.ID | None
    project_id: strawberry.ID | None
    deployment_id: strawberry.ID | None
    impact_id: strawberry.ID | None


# --- builders ---


def _proposal(p):
    return _build(ProposalType, p)


def _assignment(a):
    if a.assignee_user_id:
        name = a.assignee_user.get_full_name() or a.assignee_user.email
    else:
        name = a.assignee_team.name
    return _build(AssignmentType, a, assignee_name=name)


def _project(p, user):
    progress = delivery.project_progress(p)
    o = p.opportunity
    assigned = (
        (p.assigned_user.get_full_name() or p.assigned_user.email)
        if p.assigned_user_id
        else p.assigned_team.name
    )
    tenant = p.organization.name if p.organization_id else (p.team.name if p.team_id else None)
    return ProjectType(
        id=_id(p.pk),
        opportunity_id=_id(p.opportunity_id),
        idea_id=_id(o.idea_id),
        title=p.title,
        description=p.description,
        status=p.status,
        submission_context=o.submission_context,
        owner_name=p.owner.get_full_name() or p.owner.email,
        tenant_name=tenant,
        assigned_name=assigned,
        start_date=p.start_date,
        target_date=p.target_date,
        completed_at=p.completed_at,
        created_at=p.created_at,
        progress=ProjectProgress(
            stages=[PipelineStage(**s) for s in progress['stages']],
            task_completion_percent=progress['task_completion_percent'],
            tasks_total=progress['tasks_total'],
            tasks_done=progress['tasks_done'],
            tasks_blocked=progress['tasks_blocked'],
        ),
        capabilities=ProjectCapabilities(
            can_manage=authz.can_manage_project(user, p),
            can_perform_uat=authz.can_perform_uat(user, p),
            can_record_impact=authz.can_record_impact(user, p),
        ),
    )


def _impact(i):
    return _build(ImpactType, i, results=ImpactResults(**impact_calc.calculate(i)))


# --- payload types ---


@strawberry.type
class AssignmentPayload:
    success: bool
    message: str
    field: str | None = None
    assignment: AssignmentType | None = None


@strawberry.type
class ProjectPayload:
    success: bool
    message: str
    field: str | None = None
    project: ProjectType | None = None


@strawberry.type
class MilestonePayload:
    success: bool
    message: str
    field: str | None = None
    milestone: MilestoneType | None = None


@strawberry.type
class TaskPayload:
    success: bool
    message: str
    field: str | None = None
    task: TaskType | None = None


@strawberry.type
class TestCasePayload:
    success: bool
    message: str
    field: str | None = None
    test_case: TestCaseType | None = None


@strawberry.type
class UatPayload:
    success: bool
    message: str
    field: str | None = None
    record: UatRecordType | None = None


@strawberry.type
class DeploymentPayload:
    success: bool
    message: str
    field: str | None = None
    deployment: DeploymentType | None = None


@strawberry.type
class ImpactPayload:
    success: bool
    message: str
    field: str | None = None
    impact: ImpactType | None = None


def _run(payload_cls, key, action, build):
    """Run one service call and shape its outcome: a refusal is a payload, never an error."""
    try:
        result = action()
    except AutomationError as exc:
        return payload_cls(success=False, message=exc.message, field=_camel(exc.field))
    return payload_cls(success=True, message='Done.', **{key: build(result)})


# --- inputs ---


@strawberry.input
class UpdateTaskInput:
    id: strawberry.ID
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    due_date: str | None = None
    assignee_id: strawberry.ID | None = None
    milestone_id: strawberry.ID | None = None


@strawberry.input
class ImpactInput:
    before_processing_minutes: str | None = None
    after_processing_minutes: str | None = None
    before_people_involved: str | None = None
    after_people_involved: str | None = None
    before_requests: str | None = None
    after_requests: str | None = None
    before_cost: str | None = None
    after_cost: str | None = None
    before_error_rate: str | None = None
    after_error_rate: str | None = None
    before_workload_hours: str | None = None
    after_workload_hours: str | None = None
    users_affected: str | None = None
    estimated_hours_saved_per_week: str | None = None
    measured_hours_saved_per_week: str | None = None
    estimated_cost_savings: str | None = None
    measured_cost_savings: str | None = None
    automation_rate: str | None = None
    qualitative_outcome: str | None = None
    notes: str | None = None


def _given(obj, exclude=()):
    return {
        name: value
        for name, value in vars(obj).items()
        if value is not None and name not in exclude
    }


def _can_inspect(user) -> bool:
    """The console's gate and the capability to read idea content, as everywhere in it."""
    from administration.authorization import ACCESS_CONSOLE, INSPECT_IDEA_CONTENT

    return bool(
        user is not None
        and user.is_active
        and user.has_perms((ACCESS_CONSOLE, INSPECT_IDEA_CONTENT))
    )


def _project_or_none(user, project_id):
    return delivery.get_project(user, project_id)


# --- queries ---


@strawberry.type
class Query:
    @strawberry.field(description="An opportunity's proposal, or null.")
    def proposal(self, info: strawberry.Info, opportunity_id: strawberry.ID) -> ProposalType | None:
        user = info.context.user
        found = delivery.get_proposal(user, opportunity_id)
        return _proposal(found) if found else None

    @strawberry.field(
        description='Opportunities waiting for a developer. Only ready-for-assignment ones appear.'
    )
    def developer_queue(self, info: strawberry.Info) -> list[OpportunityType]:
        user = info.context.user
        return [OpportunityType.from_model(o, user) for o in delivery.developer_queue(user)[:100]]

    @strawberry.field
    def opportunity_assignments(
        self, info: strawberry.Info, opportunity_id: strawberry.ID
    ) -> list[AssignmentType]:
        opportunity = services.get_opportunity(info.context.user, opportunity_id)
        if opportunity is None:
            return []
        rows = opportunity.assignments.select_related('assignee_user', 'assignee_team')
        return [_assignment(a) for a in rows]

    @strawberry.field
    def projects(self, info: strawberry.Info, status: str | None = None) -> list[ProjectType]:
        user = info.context.user
        return [_project(p, user) for p in delivery.list_projects(user, status=status)[:100]]

    @strawberry.field
    def project(self, info: strawberry.Info, id: strawberry.ID) -> ProjectType | None:
        user = info.context.user
        found = delivery.get_project(user, id)
        return _project(found, user) if found else None

    @strawberry.field
    def project_for_opportunity(
        self, info: strawberry.Info, opportunity_id: strawberry.ID
    ) -> ProjectType | None:
        user = info.context.user
        found = delivery.project_for_opportunity(user, opportunity_id)
        return _project(found, user) if found else None

    @strawberry.field
    def project_milestones(
        self, info: strawberry.Info, project_id: strawberry.ID
    ) -> list[MilestoneType]:
        project = _project_or_none(info.context.user, project_id)
        return [_build(MilestoneType, m) for m in project.milestones.all()[:200]] if project else []

    @strawberry.field
    def project_tasks(self, info: strawberry.Info, project_id: strawberry.ID) -> list[TaskType]:
        project = _project_or_none(info.context.user, project_id)
        return [_build(TaskType, t) for t in project.tasks.all()[:500]] if project else []

    @strawberry.field
    def project_test_cases(
        self, info: strawberry.Info, project_id: strawberry.ID
    ) -> list[TestCaseType]:
        project = _project_or_none(info.context.user, project_id)
        return [_build(TestCaseType, c) for c in project.test_cases.all()[:500]] if project else []

    @strawberry.field
    def project_uat_records(
        self, info: strawberry.Info, project_id: strawberry.ID
    ) -> list[UatRecordType]:
        project = _project_or_none(info.context.user, project_id)
        return (
            [_build(UatRecordType, r) for r in project.uat_records.all()[:500]] if project else []
        )

    @strawberry.field
    def project_deployments(
        self, info: strawberry.Info, project_id: strawberry.ID
    ) -> list[DeploymentType]:
        project = _project_or_none(info.context.user, project_id)
        return (
            [_build(DeploymentType, d) for d in project.deployments.all()[:100]] if project else []
        )

    @strawberry.field(description='The recorded impact, with calculated results; null if none.')
    def project_impact(self, info: strawberry.Info, project_id: strawberry.ID) -> ImpactType | None:
        project = _project_or_none(info.context.user, project_id)
        found = getattr(project, 'impact', None) if project else None
        return _impact(found) if found else None

    @strawberry.field(
        description='Every opportunity on the platform, for the console. Empty for anyone who '
        'cannot inspect idea content.'
    )
    def admin_automation_opportunities(
        self, info: strawberry.Info, status: str | None = None
    ) -> list[OpportunityType]:
        user = info.context.user
        if not _can_inspect(user):
            return []
        rows = AutomationOpportunity.objects.select_related('idea', 'owner', 'organization', 'team')
        if status:
            rows = rows.filter(status=status)
        return [OpportunityType.from_model(o, user) for o in rows[:200]]

    @strawberry.field(
        description='Every project on the platform, for the console. Empty for anyone who '
        'cannot inspect idea content.'
    )
    def admin_automation_projects(
        self, info: strawberry.Info, status: str | None = None
    ) -> list[ProjectType]:
        user = info.context.user
        if not _can_inspect(user):
            return []
        rows = Project.objects.select_related(
            'opportunity__idea',
            'owner',
            'organization',
            'team',
            'assigned_user',
            'assigned_team',
        )
        if status:
            rows = rows.filter(status=status)
        return [_project(p, user) for p in rows[:200]]

    @strawberry.field(
        description='Who can be assigned delivery work. Only delivery managers get a list.'
    )
    def assignable_assignees(self, info: strawberry.Info) -> AssigneeOptions:
        from identity.models import User
        from teams.models import Team

        user = info.context.user
        if not authz.is_delivery_manager(user):
            return AssigneeOptions(users=[], teams=[])
        people = [
            AssigneeOption(id=_id(u.pk), name=u.get_full_name() or u.email, detail=u.email)
            for u in User.objects.filter(is_active=True).order_by('first_name', 'email')[:500]
            if authz.is_eligible_assignee(u)
        ]
        teams = [
            AssigneeOption(id=_id(t.pk), name=t.name, detail=t.slug)
            for t in Team.objects.order_by('name')[:500]
            if authz.is_eligible_team(t)
        ]
        return AssigneeOptions(users=people, teams=teams)

    @strawberry.field(description='The chain from an idea to its impact, as far as it has gone.')
    def idea_traceability(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> Traceability | None:
        user = info.context.user
        opportunity = services.opportunity_for_idea(user, idea_id)
        if opportunity is None:
            return None
        proposal = getattr(opportunity, 'proposal', None)
        project = getattr(opportunity, 'project', None)
        deployment = project.deployments.filter(status='successful').first() if project else None
        impact = getattr(project, 'impact', None) if project else None
        return Traceability(
            idea_id=idea_id,
            opportunity_id=_id(opportunity.pk),
            proposal_id=_id(proposal.pk) if proposal else None,
            project_id=_id(project.pk) if project else None,
            deployment_id=_id(deployment.pk) if deployment else None,
            impact_id=_id(impact.pk) if impact else None,
        )


# --- mutations ---


@strawberry.type
class Mutation:
    # assignment and project
    @strawberry.mutation
    def assign_opportunity(
        self,
        info: strawberry.Info,
        opportunity_id: strawberry.ID,
        assignee_user_id: strawberry.ID | None = None,
        assignee_team_id: strawberry.ID | None = None,
    ) -> AssignmentPayload:
        return _run(
            AssignmentPayload,
            'assignment',
            lambda: delivery.assign_opportunity(
                info.context.user,
                opportunity_id,
                assignee_user_id=assignee_user_id,
                assignee_team_id=assignee_team_id,
            ),
            _assignment,
        )

    @strawberry.mutation
    def create_project(
        self,
        info: strawberry.Info,
        opportunity_id: strawberry.ID,
        start_date: str | None = None,
        target_date: str | None = None,
    ) -> ProjectPayload:
        user = info.context.user
        return _run(
            ProjectPayload,
            'project',
            lambda: delivery.create_project(
                user, opportunity_id, start_date=start_date, target_date=target_date
            ),
            lambda p: _project(p, user),
        )

    @strawberry.mutation
    def start_project(self, info: strawberry.Info, id: strawberry.ID) -> ProjectPayload:
        user = info.context.user
        return _run(
            ProjectPayload,
            'project',
            lambda: delivery.start_project(user, id),
            lambda p: _project(p, user),
        )

    @strawberry.mutation
    def start_project_testing(self, info: strawberry.Info, id: strawberry.ID) -> ProjectPayload:
        user = info.context.user
        return _run(
            ProjectPayload,
            'project',
            lambda: delivery.start_testing(user, id),
            lambda p: _project(p, user),
        )

    @strawberry.mutation
    def submit_project_for_uat(self, info: strawberry.Info, id: strawberry.ID) -> ProjectPayload:
        user = info.context.user
        return _run(
            ProjectPayload,
            'project',
            lambda: delivery.submit_for_uat(user, id),
            lambda p: _project(p, user),
        )

    @strawberry.mutation
    def complete_project(self, info: strawberry.Info, id: strawberry.ID) -> ProjectPayload:
        user = info.context.user
        return _run(
            ProjectPayload,
            'project',
            lambda: delivery.complete_project(user, id),
            lambda p: _project(p, user),
        )

    # milestones and tasks
    @strawberry.mutation
    def create_milestone(
        self,
        info: strawberry.Info,
        project_id: strawberry.ID,
        title: str,
        description: str = '',
        due_date: str | None = None,
    ) -> MilestonePayload:
        return _run(
            MilestonePayload,
            'milestone',
            lambda: delivery.create_milestone(
                info.context.user,
                project_id,
                title=title,
                description=description,
                due_date=due_date,
            ),
            lambda m: _build(MilestoneType, m),
        )

    @strawberry.mutation
    def complete_milestone(self, info: strawberry.Info, id: strawberry.ID) -> MilestonePayload:
        return _run(
            MilestonePayload,
            'milestone',
            lambda: delivery.complete_milestone(info.context.user, id),
            lambda m: _build(MilestoneType, m),
        )

    @strawberry.mutation
    def create_task(
        self,
        info: strawberry.Info,
        project_id: strawberry.ID,
        title: str,
        description: str = '',
        priority: str = 'medium',
        assignee_id: strawberry.ID | None = None,
        milestone_id: strawberry.ID | None = None,
        due_date: str | None = None,
    ) -> TaskPayload:
        return _run(
            TaskPayload,
            'task',
            lambda: delivery.create_task(
                info.context.user,
                project_id,
                title=title,
                description=description,
                priority=priority,
                assignee_id=assignee_id,
                milestone_id=milestone_id,
                due_date=due_date,
            ),
            lambda t: _build(TaskType, t),
        )

    @strawberry.mutation
    def update_task(self, info: strawberry.Info, input: UpdateTaskInput) -> TaskPayload:
        values = _given(input, exclude=('id',))
        return _run(
            TaskPayload,
            'task',
            lambda: delivery.update_task(info.context.user, input.id, values),
            lambda t: _build(TaskType, t),
        )

    # testing and UAT
    @strawberry.mutation
    def create_test_case(
        self,
        info: strawberry.Info,
        project_id: strawberry.ID,
        title: str,
        description: str = '',
        expected_result: str = '',
        is_required: bool = True,
    ) -> TestCasePayload:
        return _run(
            TestCasePayload,
            'test_case',
            lambda: delivery.create_test_case(
                info.context.user,
                project_id,
                title=title,
                description=description,
                expected_result=expected_result,
                is_required=is_required,
            ),
            lambda c: _build(TestCaseType, c),
        )

    @strawberry.mutation
    def record_test_result(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
        status: str,
        actual_result: str = '',
        notes: str = '',
    ) -> TestCasePayload:
        return _run(
            TestCasePayload,
            'test_case',
            lambda: delivery.record_test_result(
                info.context.user, id, status=status, actual_result=actual_result, notes=notes
            ),
            lambda c: _build(TestCaseType, c),
        )

    @strawberry.mutation
    def create_uat_scenario(
        self,
        info: strawberry.Info,
        project_id: strawberry.ID,
        scenario: str,
        expected_outcome: str = '',
        is_required: bool = True,
    ) -> UatPayload:
        return _run(
            UatPayload,
            'record',
            lambda: delivery.create_uat_scenario(
                info.context.user,
                project_id,
                scenario=scenario,
                expected_outcome=expected_outcome,
                is_required=is_required,
            ),
            lambda r: _build(UatRecordType, r),
        )

    @strawberry.mutation
    def record_uat_result(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
        result: str,
        actual_outcome: str = '',
        feedback: str = '',
    ) -> UatPayload:
        return _run(
            UatPayload,
            'record',
            lambda: delivery.record_uat_result(
                info.context.user,
                id,
                result=result,
                actual_outcome=actual_outcome,
                feedback=feedback,
            ),
            lambda r: _build(UatRecordType, r),
        )

    # deployment and impact
    @strawberry.mutation
    def create_deployment(
        self,
        info: strawberry.Info,
        project_id: strawberry.ID,
        environment: str,
        version: str,
        notes: str = '',
        rollback_information: str = '',
    ) -> DeploymentPayload:
        return _run(
            DeploymentPayload,
            'deployment',
            lambda: delivery.create_deployment(
                info.context.user,
                project_id,
                environment=environment,
                version=version,
                notes=notes,
                rollback_information=rollback_information,
            ),
            lambda d: _build(DeploymentType, d),
        )

    @strawberry.mutation
    def record_deployment(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
        status: str,
        notes: str | None = None,
        rollback_information: str | None = None,
    ) -> DeploymentPayload:
        return _run(
            DeploymentPayload,
            'deployment',
            lambda: delivery.record_deployment(
                info.context.user,
                id,
                status=status,
                notes=notes,
                rollback_information=rollback_information,
            ),
            lambda d: _build(DeploymentType, d),
        )

    @strawberry.mutation
    def verify_deployment(self, info: strawberry.Info, id: strawberry.ID) -> DeploymentPayload:
        return _run(
            DeploymentPayload,
            'deployment',
            lambda: delivery.verify_deployment(info.context.user, id),
            lambda d: _build(DeploymentType, d),
        )

    @strawberry.mutation
    def record_impact(
        self,
        info: strawberry.Info,
        project_id: strawberry.ID,
        status: str = 'recorded',
        values: ImpactInput | None = None,
    ) -> ImpactPayload:
        given = _given(values) if values else {}
        return _run(
            ImpactPayload,
            'impact',
            lambda: delivery.record_impact(
                info.context.user, project_id, status=status, values=given
            ),
            _impact,
        )
