"""
Everything after the opportunity's solution: proposal, assignment, project,
tasks and milestones, testing, UAT, deployment and impact.

The same rules as `automation.services`: authenticated user in, ids in, state and
authority re-checked under a row lock, change and audit event in one transaction,
people told afterwards, and no client-supplied status, owner, team or assignee
accepted without verification. A status only moves through the action that owns
it, and each gate states what is missing in plain words.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.utils import timezone

from automation import authorization as authz
from automation.authorization import AutomationError
from automation.models import (
    DEPLOYMENT_STATUS_CHOICES,
    IMPACT_STATUS_CHOICES,
    PRIORITY_CHOICES,
    TASK_STATUS_CHOICES,
    Assignment,
    AutomationOpportunity,
    Deployment,
    ImpactRecord,
    Milestone,
    Project,
    Proposal,
    Task,
    TestCase,
    UATRecord,
)
from automation.services import (
    UNAVAILABLE,
    _choice,
    _clean,
    _locked_opportunity,
    _notify,
    _owner_if_not,
    _record,
)
from identity.models import User

# --- small shared helpers ---


def _pk(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise AutomationError(UNAVAILABLE) from None


def _delivery_recipients(project: Project) -> list[User]:
    """The people doing the work: the assigned developer, or the assigned team's members."""
    from teams.models import TeamMembership

    if project.assigned_user_id:
        return [project.assigned_user]
    members = TeamMembership.objects.filter(
        team_id=project.assigned_team_id, status=TeamMembership.Status.ACTIVE
    ).select_related('user')
    return [m.user for m in members]


def _tell(project, kind, title, body, *, owner=True, delivery=False, actor=None):
    people = []
    if owner:
        people.append(project.owner)
    if delivery:
        people.extend(_delivery_recipients(project))
    seen = set()
    unique = []
    for person in people:
        if person.pk not in seen and (actor is None or person.pk != actor.pk):
            seen.add(person.pk)
            unique.append(person)
    _notify(unique, kind, title, body, project.opportunity.idea)


def _date(value, field: str):
    if value in (None, ''):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise AutomationError('Enter a valid date.', field=field) from None


def _decimal(value, field: str, *, maximum: Decimal) -> Decimal | None:
    if value is None or str(value).strip() == '':
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise AutomationError('Enter a number.', field=field) from None
    if not number.is_finite() or number < 0 or number > maximum:
        raise AutomationError('Enter a number that makes sense for this measure.', field=field)
    return number


def _integer(value, field: str) -> int | None:
    number = _decimal(value, field, maximum=Decimal(10**9))
    if number is None:
        return None
    if number != number.to_integral_value():
        raise AutomationError('Enter a whole number.', field=field)
    return int(number)


# --- the proposal (read-only here) ---------------------------------------------------------


def get_proposal(user, opportunity_id):
    """
    The proposal this opportunity carries: a copy of the one the review team wrote and the
    owner accepted. Nothing in this module writes it - it is the record of what was agreed.
    """
    opportunity = _get(user, opportunity_id)
    return getattr(opportunity, 'proposal', None) if opportunity else None


def _get(user, opportunity_id):
    from automation.services import get_opportunity

    return get_opportunity(user, opportunity_id)


# --- the developer queue and assignment ---


def developer_queue(user):
    """Opportunities waiting for a developer. Nothing else ever appears here."""
    if user is None or not user.is_active:
        return AutomationOpportunity.objects.none()
    if not (authz.is_delivery_manager(user) or authz.is_eligible_assignee(user)):
        return AutomationOpportunity.objects.none()
    return AutomationOpportunity.objects.filter(status='ready_for_assignment').select_related(
        'idea', 'owner', 'organization', 'team'
    )


@transaction.atomic
def assign_opportunity(
    user, opportunity_id, *, assignee_user_id=None, assignee_team_id=None
) -> Assignment:
    opportunity = _locked_opportunity(user, opportunity_id)
    if not authz.can_assign(user, opportunity):
        raise AutomationError('You cannot assign this opportunity.')
    if opportunity.status != 'ready_for_assignment':
        raise AutomationError('Only an opportunity that is ready for assignment can be assigned.')
    if bool(assignee_user_id) == bool(assignee_team_id):
        raise AutomationError('Choose either a developer or a team.', field='assignee')

    assignee_user = assignee_team = None
    if assignee_user_id:
        assignee_user = User.objects.filter(pk=_pk(assignee_user_id), is_active=True).first()
        if assignee_user is None or not authz.is_eligible_assignee(assignee_user):
            raise AutomationError('That person cannot be assigned delivery work.', field='assignee')
    else:
        from teams.models import Team

        assignee_team = Team.objects.filter(pk=_pk(assignee_team_id)).first()
        if assignee_team is None or not authz.is_eligible_team(assignee_team):
            raise AutomationError('That team cannot be assigned delivery work.', field='assignee')

    try:
        with transaction.atomic():
            assignment = Assignment.objects.create(
                opportunity=opportunity,
                assignee_user=assignee_user,
                assignee_team=assignee_team,
                assigned_by=user,
            )
    except IntegrityError:
        raise AutomationError('This opportunity is already assigned.') from None

    opportunity.status = 'assigned'
    opportunity.save(update_fields=['status', 'updated_at'])
    _record(
        opportunity,
        user,
        'assignment_created',
        'assignment',
        assignment.pk,
        from_status='ready_for_assignment',
        to_status='assigned',
        assignee_user=assignee_user.pk if assignee_user else None,
        assignee_team=assignee_team.pk if assignee_team else None,
    )
    stand_in = Project(assigned_user=assignee_user, assigned_team=assignee_team)
    stand_in.assigned_user_id = assignee_user.pk if assignee_user else None
    stand_in.assigned_team_id = assignee_team.pk if assignee_team else None
    _notify(
        _delivery_recipients(stand_in),
        'automation.opportunity_assigned',
        'You have been assigned an automation opportunity',
        f'"{opportunity.title}" is yours to deliver.',
        opportunity.idea,
    )
    _notify(
        _owner_if_not(opportunity, user),
        'automation.opportunity_assigned',
        'Your opportunity was assigned',
        f'"{opportunity.title}" now has someone delivering it.',
        opportunity.idea,
    )
    return assignment


# --- the project ---


def _locked_project(user, project_id):
    pk = _pk(project_id)
    project = (
        Project.objects.select_for_update(of=('self',))
        .select_related(
            'opportunity__idea', 'owner', 'organization', 'team', 'assigned_user', 'assigned_team'
        )
        .filter(pk=pk)
        .first()
    )
    if project is None or not authz.can_view_project(user, project):
        raise AutomationError(UNAVAILABLE)
    return project


@transaction.atomic
def create_project(user, opportunity_id, *, start_date=None, target_date=None) -> Project:
    opportunity = _locked_opportunity(user, opportunity_id)
    assignment = opportunity.assignments.filter(status='active').first()
    stand_in = None
    if assignment is not None:
        stand_in = Project(
            assigned_user_id=assignment.assignee_user_id,
            assigned_team_id=assignment.assignee_team_id,
        )
    allowed = authz.is_delivery_manager(user) or (
        stand_in is not None and authz.is_assignee(user, stand_in)
    )
    if not allowed:
        raise AutomationError('You cannot create the project for this opportunity.')
    if opportunity.status != 'assigned' or assignment is None:
        raise AutomationError('A project can be created once the opportunity is assigned.')

    start, target = _date(start_date, 'start_date'), _date(target_date, 'target_date')
    if start and target and target < start:
        raise AutomationError(
            'The target date cannot be before the start date.', field='target_date'
        )

    project = Project.objects.create(
        opportunity=opportunity,
        assignment=assignment,
        title=opportunity.title,
        description=opportunity.summary or opportunity.problem_statement,
        owner=opportunity.owner,
        organization=opportunity.organization,
        team=opportunity.team,
        assigned_user=assignment.assignee_user,
        assigned_team=assignment.assignee_team,
        start_date=start,
        target_date=target,
    )
    opportunity.status = 'project_created'
    opportunity.save(update_fields=['status', 'updated_at'])
    _record(
        opportunity,
        user,
        'project_created',
        'project',
        project.pk,
        from_status='assigned',
        to_status='project_created',
    )
    project.opportunity = opportunity
    _tell(
        project,
        'automation.project_created',
        'A project was created',
        f'Delivery of "{project.title}" is now a project.',
        actor=user,
    )
    return project


def _project_edge(
    user, project_id, from_status, to_status, *, check=None, who=None, kind=None, body=''
):
    project = _locked_project(user, project_id)
    if not (who or authz.can_manage_project)(user, project):
        raise AutomationError('You cannot move this project forward.')
    if project.status != from_status:
        raise AutomationError(
            f'A project that is {project.status.replace("_", " ")} cannot go to '
            f'{to_status.replace("_", " ")}.'
        )
    if check is not None:
        check(project)
    project.status = to_status
    return project


def _save_project_move(user, project, before, kind=None, title='', body='', **tell):
    project.save(update_fields=['status', 'start_date', 'completed_at', 'updated_at'])
    _record(
        project.opportunity,
        user,
        'project_status_changed',
        'project',
        project.pk,
        from_status=before,
        to_status=project.status,
    )
    if kind:
        _tell(project, kind, title, body, actor=user, **tell)


@transaction.atomic
def start_project(user, project_id) -> Project:
    project = _project_edge(user, project_id, 'planning', 'active')
    if project.start_date is None:
        project.start_date = timezone.localdate()
    _save_project_move(
        user,
        project,
        'planning',
        'automation.project_started',
        'Your project has started',
        f'Development of "{project.title}" has begun.',
    )
    return project


@transaction.atomic
def start_testing(user, project_id) -> Project:
    project = _project_edge(user, project_id, 'active', 'testing')
    _save_project_move(user, project, 'active')
    return project


def _required_test_gate(project: Project) -> None:
    required = project.test_cases.filter(is_required=True)
    if not required.exists():
        raise AutomationError(
            'UAT cannot begin because there are no required test cases. Add and pass at least one.'
        )
    failed = required.filter(status='fail').count()
    if failed:
        noun = 'case has' if failed == 1 else 'cases have'
        raise AutomationError(f'UAT cannot begin because {failed} required test {noun} failed.')
    unfinished = required.exclude(status='pass').count()
    if unfinished:
        noun = 'case has' if unfinished == 1 else 'cases have'
        raise AutomationError(
            f'UAT cannot begin because {unfinished} required test {noun} not passed yet.'
        )


@transaction.atomic
def submit_for_uat(user, project_id) -> Project:
    project = _project_edge(user, project_id, 'testing', 'uat', check=_required_test_gate)
    _save_project_move(
        user,
        project,
        'testing',
        'automation.ready_for_uat',
        'Please accept the delivered work',
        f'"{project.title}" has passed testing and is ready for your acceptance.',
    )
    return project


def _uat_round(project: Project):
    """The scenarios of the current round: those created since the project last entered UAT."""
    entered = (
        project.opportunity.events.filter(
            action='project_status_changed', entity_id=project.pk, to_status='uat'
        )
        .order_by('-created_at', '-pk')
        .first()
    )
    records = project.uat_records.all()
    return records.filter(created_at__gte=entered.created_at) if entered else records.none()


def _uat_gate(project: Project) -> None:
    required = _uat_round(project).filter(is_required=True)
    if not required.exists():
        raise AutomationError(
            'Deployment cannot happen because no acceptance scenario has been defined.'
        )
    unfinished = required.exclude(result='passed').count()
    if unfinished:
        raise AutomationError(
            f'Deployment cannot happen because {unfinished} required acceptance '
            f'scenario{"" if unfinished == 1 else "s"} {"has" if unfinished == 1 else "have"} '
            'not passed.'
        )


@transaction.atomic
def complete_project(user, project_id) -> Project:
    def gate(project):
        deployments = project.deployments.filter(status='successful')
        if not deployments.exists():
            raise AutomationError('Complete a successful deployment before completing the project.')
        if not deployments.filter(verified_at__isnull=False).exists():
            raise AutomationError('Verify the deployment before completing the project.')
        if not ImpactRecord.objects.filter(project=project).exists():
            raise AutomationError(
                'Record the impact, or mark it pending or unavailable, before completing.',
                field='impact',
            )

    project = _project_edge(user, project_id, 'deployed', 'completed', check=gate)
    project.completed_at = timezone.now()
    opportunity = project.opportunity
    opportunity.status = 'completed'
    opportunity.save(update_fields=['status', 'updated_at'])
    _save_project_move(
        user,
        project,
        'deployed',
        'automation.project_completed',
        'Your project is complete',
        f'"{project.title}" has been delivered.',
        delivery=True,
    )
    return project


# --- milestones and tasks ---

_OPEN_PROJECT = frozenset({'planning', 'active', 'testing', 'uat'})


def _open_project(user, project_id) -> Project:
    project = _locked_project(user, project_id)
    if not authz.can_manage_project(user, project):
        raise AutomationError('You cannot change this project.')
    if project.status not in _OPEN_PROJECT:
        raise AutomationError('This project is closed to this kind of change.')
    return project


@transaction.atomic
def create_milestone(user, project_id, *, title, description='', due_date=None) -> Milestone:
    project = _open_project(user, project_id)
    milestone = Milestone.objects.create(
        project=project,
        title=_clean(title, 'title', required=True, limit=200),
        description=_clean(description, 'description'),
        due_date=_date(due_date, 'due_date'),
        order=project.milestones.count(),
    )
    _record(project.opportunity, user, 'milestone_created', 'milestone', milestone.pk)
    return milestone


@transaction.atomic
def complete_milestone(user, milestone_id) -> Milestone:
    milestone = Milestone.objects.filter(pk=_pk(milestone_id)).first()
    if milestone is None:
        raise AutomationError(UNAVAILABLE)
    project = _open_project(user, milestone.project_id)
    milestone = Milestone.objects.select_for_update().get(pk=milestone.pk)
    if milestone.status == 'completed':
        raise AutomationError('This milestone is already complete.')
    milestone.status = 'completed'
    milestone.completed_at = timezone.now()
    milestone.save(update_fields=['status', 'completed_at'])
    _record(project.opportunity, user, 'milestone_completed', 'milestone', milestone.pk)
    _tell(
        project,
        'automation.milestone_completed',
        'A milestone was completed',
        f'"{milestone.title}" is done on "{project.title}".',
        actor=user,
    )
    return milestone


def _task_assignee(project, assignee_id):
    if assignee_id in (None, ''):
        return None
    person = User.objects.filter(pk=_pk(assignee_id), is_active=True).first()
    if person is None or not authz.can_manage_project(person, project):
        raise AutomationError('That person is not part of this project.', field='assignee')
    return person


def _task_milestone(project, milestone_id):
    if milestone_id in (None, ''):
        return None
    milestone = project.milestones.filter(pk=_pk(milestone_id)).first()
    if milestone is None:
        raise AutomationError('Choose a milestone from this project.', field='milestone')
    return milestone


@transaction.atomic
def create_task(
    user,
    project_id,
    *,
    title,
    description='',
    priority='medium',
    assignee_id=None,
    milestone_id=None,
    due_date=None,
) -> Task:
    project = _open_project(user, project_id)
    task = Task.objects.create(
        project=project,
        milestone=_task_milestone(project, milestone_id),
        title=_clean(title, 'title', required=True, limit=200),
        description=_clean(description, 'description'),
        priority=_choice(priority, PRIORITY_CHOICES, 'priority', default='medium'),
        assignee=_task_assignee(project, assignee_id),
        due_date=_date(due_date, 'due_date'),
    )
    _record(project.opportunity, user, 'task_created', 'task', task.pk)
    return task


@transaction.atomic
def update_task(user, task_id, values: dict) -> Task:
    task = Task.objects.filter(pk=_pk(task_id)).first()
    if task is None:
        raise AutomationError(UNAVAILABLE)
    project = _open_project(user, task.project_id)
    task = Task.objects.select_for_update().get(pk=task.pk)
    before = task.status
    changed = []
    simple = {
        'title': lambda v: _clean(v, 'title', required=True, limit=200),
        'description': lambda v: _clean(v, 'description'),
        'status': lambda v: _choice(v, TASK_STATUS_CHOICES, 'status'),
        'priority': lambda v: _choice(v, PRIORITY_CHOICES, 'priority'),
        'due_date': lambda v: _date(v, 'due_date'),
    }
    for field, clean in simple.items():
        if field in values and values[field] is not None:
            setattr(task, field, clean(values[field]))
            changed.append(field)
    if 'assignee_id' in values:
        task.assignee = _task_assignee(project, values['assignee_id'])
        changed.append('assignee')
    if 'milestone_id' in values:
        task.milestone = _task_milestone(project, values['milestone_id'])
        changed.append('milestone')
    if changed:
        task.save(update_fields=[*changed, 'updated_at'])
    if task.status != before:
        _record(
            project.opportunity,
            user,
            'task_status_changed',
            'task',
            task.pk,
            from_status=before,
            to_status=task.status,
        )
    return task


# --- testing ---


@transaction.atomic
def create_test_case(
    user, project_id, *, title, description='', expected_result='', is_required=True
) -> TestCase:
    project = _open_project(user, project_id)
    case = TestCase.objects.create(
        project=project,
        title=_clean(title, 'title', required=True, limit=200),
        description=_clean(description, 'description'),
        expected_result=_clean(expected_result, 'expected_result'),
        is_required=bool(is_required),
    )
    _record(project.opportunity, user, 'test_case_created', 'test_case', case.pk)
    return case


@transaction.atomic
def record_test_result(user, test_case_id, *, status, actual_result='', notes='') -> TestCase:
    case = TestCase.objects.filter(pk=_pk(test_case_id)).first()
    if case is None:
        raise AutomationError(UNAVAILABLE)
    project = _locked_project(user, case.project_id)
    if not authz.can_manage_project(user, project):
        raise AutomationError('You cannot record test results for this project.')
    if project.status not in {'active', 'testing'}:
        raise AutomationError('Tests are run while the project is in development or testing.')
    result = _choice(status, (('pass', ''), ('fail', ''), ('blocked', '')), 'status')

    case = TestCase.objects.select_for_update().get(pk=case.pk)
    case.status = result
    case.actual_result = _clean(actual_result, 'actual_result')
    case.notes = _clean(notes, 'notes')
    case.tester = user
    case.executed_at = timezone.now()
    case.save(update_fields=['status', 'actual_result', 'notes', 'tester', 'executed_at'])
    _record(project.opportunity, user, 'test_executed', 'test_case', case.pk, result=result)
    if result == 'fail' and case.is_required:
        _tell(
            project,
            'automation.testing_failed',
            'A required test failed',
            f'"{case.title}" failed on "{project.title}".',
            owner=False,
            delivery=True,
            actor=user,
        )
    return case


# --- UAT ---


@transaction.atomic
def create_uat_scenario(
    user, project_id, *, scenario, expected_outcome='', is_required=True
) -> UATRecord:
    project = _locked_project(user, project_id)
    if not (authz.can_manage_project(user, project) or authz.can_perform_uat(user, project)):
        raise AutomationError('You cannot add acceptance scenarios to this project.')
    if project.status != 'uat':
        raise AutomationError('Acceptance scenarios belong to the UAT stage.')
    record = UATRecord.objects.create(
        project=project,
        scenario=_clean(scenario, 'scenario', required=True),
        expected_outcome=_clean(expected_outcome, 'expected_outcome'),
        is_required=bool(is_required),
    )
    _record(project.opportunity, user, 'uat_recorded', 'uat', record.pk, result='pending')
    return record


@transaction.atomic
def record_uat_result(user, uat_id, *, result, actual_outcome='', feedback='') -> UATRecord:
    record = UATRecord.objects.filter(pk=_pk(uat_id)).first()
    if record is None:
        raise AutomationError(UNAVAILABLE)
    project = _locked_project(user, record.project_id)
    if not authz.can_perform_uat(user, project):
        raise AutomationError('Only the owner of the idea can accept or reject the work.')
    if record.result != 'pending':
        # A decided scenario is history. Another round is new scenarios.
        raise AutomationError('This scenario has already been decided.')
    if project.status != 'uat':
        raise AutomationError('Acceptance can only be recorded while the project is in UAT.')
    outcome = _choice(result, (('passed', ''), ('failed', '')), 'result')

    record = UATRecord.objects.select_for_update().get(pk=record.pk)
    if record.result != 'pending':
        # A decided scenario is history. Another round is new scenarios.
        raise AutomationError('This scenario has already been decided.')
    text = _clean(feedback, 'feedback', required=outcome == 'failed')
    record.result = outcome
    record.actual_outcome = _clean(actual_outcome, 'actual_outcome')
    record.feedback = text
    record.reviewer = user
    record.recorded_at = timezone.now()
    record.save(update_fields=['result', 'actual_outcome', 'feedback', 'reviewer', 'recorded_at'])
    _record(project.opportunity, user, 'uat_recorded', 'uat', record.pk, result=outcome)

    if outcome == 'passed':
        _tell(
            project,
            'automation.uat_passed',
            'Acceptance passed',
            f'A scenario passed on "{project.title}".',
            owner=False,
            delivery=True,
            actor=user,
        )
    else:
        if record.is_required:
            # Back to development, with the failed round kept as history.
            project.status = 'active'
            _save_project_move(user, project, 'uat')
        _tell(
            project,
            'automation.uat_failed',
            'Acceptance failed',
            f'A scenario failed on "{project.title}": {text}',
            owner=False,
            delivery=True,
            actor=user,
        )
    return record


# --- deployment ---

_DEPLOYMENT_EDGES = {
    'pending': {'in_progress', 'failed'},
    'in_progress': {'successful', 'failed'},
    'successful': {'rolled_back'},
}


@transaction.atomic
def create_deployment(
    user, project_id, *, environment, version, notes='', rollback_information=''
) -> Deployment:
    project = _locked_project(user, project_id)
    if not authz.can_manage_project(user, project):
        raise AutomationError('You cannot deploy this project.')
    if project.status != 'uat':
        raise AutomationError('A project can be deployed once it has passed acceptance.')
    _uat_gate(project)
    deployment = Deployment.objects.create(
        project=project,
        environment=_clean(environment, 'environment', required=True, limit=60),
        version=_clean(version, 'version', required=True, limit=60),
        deployment_notes=_clean(notes, 'notes'),
        rollback_information=_clean(rollback_information, 'rollback_information'),
        deployed_by=user,
    )
    _record(
        project.opportunity,
        user,
        'deployment_recorded',
        'deployment',
        deployment.pk,
        status='pending',
    )
    return deployment


@transaction.atomic
def record_deployment(
    user, deployment_id, *, status, notes=None, rollback_information=None
) -> Deployment:
    deployment = Deployment.objects.filter(pk=_pk(deployment_id)).first()
    if deployment is None:
        raise AutomationError(UNAVAILABLE)
    project = _locked_project(user, deployment.project_id)
    if not authz.can_manage_project(user, project):
        raise AutomationError('You cannot record deployments for this project.')
    new_status = _choice(status, DEPLOYMENT_STATUS_CHOICES, 'status')

    deployment = Deployment.objects.select_for_update().get(pk=deployment.pk)
    if new_status not in _DEPLOYMENT_EDGES.get(deployment.status, set()):
        raise AutomationError(
            f'A {deployment.status.replace("_", " ")} deployment cannot become '
            f'{new_status.replace("_", " ")}.'
        )
    if notes is not None:
        deployment.deployment_notes = _clean(notes, 'notes')
    if rollback_information is not None:
        deployment.rollback_information = _clean(rollback_information, 'rollback_information')
    before = deployment.status
    deployment.status = new_status

    if new_status == 'successful':
        if project.status != 'uat':
            raise AutomationError('Only a project that passed acceptance can be deployed.')
        deployment.deployment_date = timezone.now()
        project.status = 'deployed'
        _save_project_move(
            user,
            project,
            'uat',
            'automation.deployment_completed',
            'Your automation is live',
            f'"{project.title}" was deployed to {deployment.environment}.',
            delivery=True,
        )
    elif new_status == 'rolled_back' and project.status == 'deployed':
        if not project.deployments.exclude(pk=deployment.pk).filter(status='successful').exists():
            project.status = 'active'
            _save_project_move(user, project, 'deployed')

    deployment.save()
    _record(
        project.opportunity,
        user,
        'deployment_recorded',
        'deployment',
        deployment.pk,
        from_status=before,
        to_status=new_status,
    )
    return deployment


@transaction.atomic
def verify_deployment(user, deployment_id) -> Deployment:
    deployment = Deployment.objects.filter(pk=_pk(deployment_id)).first()
    if deployment is None:
        raise AutomationError(UNAVAILABLE)
    project = _locked_project(user, deployment.project_id)
    if not authz.can_manage_project(user, project):
        raise AutomationError('You cannot verify deployments for this project.')
    deployment = Deployment.objects.select_for_update().get(pk=deployment.pk)
    if deployment.status != 'successful':
        raise AutomationError('Only a successful deployment can be verified.')
    if deployment.verified_at is None:
        deployment.verified_at = timezone.now()
        deployment.save(update_fields=['verified_at'])
        _record(project.opportunity, user, 'deployment_verified', 'deployment', deployment.pk)
    return deployment


# --- impact ---

_IMPACT_DECIMALS = {
    'before_processing_minutes': Decimal('9999999'),
    'after_processing_minutes': Decimal('9999999'),
    'before_cost': Decimal('99999999999'),
    'after_cost': Decimal('99999999999'),
    'before_error_rate': Decimal('100'),
    'after_error_rate': Decimal('100'),
    'before_workload_hours': Decimal('9999999'),
    'after_workload_hours': Decimal('9999999'),
    'estimated_hours_saved_per_week': Decimal('9999999'),
    'measured_hours_saved_per_week': Decimal('9999999'),
    'estimated_cost_savings': Decimal('99999999999'),
    'measured_cost_savings': Decimal('99999999999'),
    'automation_rate': Decimal('100'),
}
_IMPACT_INTEGERS = (
    'before_people_involved',
    'after_people_involved',
    'before_requests',
    'after_requests',
    'users_affected',
)
IMPACT_FIELDS = (*_IMPACT_DECIMALS, *_IMPACT_INTEGERS, 'qualitative_outcome', 'notes')


@transaction.atomic
def record_impact(
    user, project_id, *, status='recorded', values: dict | None = None
) -> ImpactRecord:
    """
    Record, update, or explicitly defer impact. Only what is sent is touched, a blank
    clears a figure, and `recorded` needs at least one real value - nothing is
    invented to fill a gap.
    """
    project = _locked_project(user, project_id)
    if not authz.can_record_impact(user, project):
        raise AutomationError('You cannot record impact for this project.')
    if project.status not in {'deployed', 'completed'}:
        raise AutomationError('Impact is recorded once the automation is deployed.')
    new_status = _choice(status, IMPACT_STATUS_CHOICES, 'status', default='recorded')
    values = values or {}
    unknown = set(values) - set(IMPACT_FIELDS)
    if unknown:
        raise AutomationError('Unknown impact field.', field=sorted(unknown)[0])

    impact, _created = ImpactRecord.objects.select_for_update().get_or_create(
        project=project, defaults={'recorded_by': user, 'status': new_status}
    )
    for field, value in values.items():
        if field in _IMPACT_DECIMALS:
            setattr(impact, field, _decimal(value, field, maximum=_IMPACT_DECIMALS[field]))
        elif field in _IMPACT_INTEGERS:
            setattr(impact, field, _integer(value, field))
        else:
            setattr(impact, field, _clean(value, field))
    if new_status == 'recorded' and not _has_any_value(impact):
        raise AutomationError(
            'Enter at least one measure, or mark the impact pending or unavailable.',
            field='status',
        )
    impact.status = new_status
    impact.recorded_by = user
    impact.save()
    _record(project.opportunity, user, 'impact_recorded', 'impact', impact.pk, status=new_status)
    if new_status == 'recorded':
        _tell(
            project,
            'automation.impact_recorded',
            'Impact was recorded',
            f'The results of "{project.title}" have been recorded.',
            delivery=True,
            actor=user,
        )
    return impact


def _has_any_value(impact: ImpactRecord) -> bool:
    return any(
        getattr(impact, f) not in (None, '') for f in (*_IMPACT_DECIMALS, *_IMPACT_INTEGERS)
    ) or bool(impact.qualitative_outcome.strip())


# --- reads ---


def get_project(user, project_id):
    try:
        pk = int(str(project_id))
    except (TypeError, ValueError):
        return None
    project = (
        Project.objects.select_related(
            'opportunity__idea', 'owner', 'organization', 'team', 'assigned_user', 'assigned_team'
        )
        .filter(pk=pk)
        .first()
    )
    return project if project and authz.can_view_project(user, project) else None


def list_projects(user, *, status=None):
    from automation.services import list_opportunities

    readable = list_opportunities(user).values('pk')
    queryset = Project.objects.select_related(
        'opportunity__idea', 'owner', 'organization', 'team', 'assigned_user', 'assigned_team'
    ).filter(opportunity_id__in=readable)
    return queryset.filter(status=status) if status else queryset


def project_for_opportunity(user, opportunity_id):
    opportunity = _get(user, opportunity_id)
    return (
        get_project(user, opportunity.project.pk)
        if opportunity and hasattr(opportunity, 'project')
        else None
    )


def project_progress(project: Project) -> dict:
    """
    Where a project really is, from its own records. Each stage is `done`, `current`
    or `pending` according to what exists - never a percentage somebody typed in.
    """
    opportunity = project.opportunity
    status = project.status
    order = ['planning', 'active', 'testing', 'uat', 'deployed', 'completed']
    reached = order.index(status) if status in order else 0
    impact = ImpactRecord.objects.filter(project=project).first()
    impact_done = impact is not None and impact.status == 'recorded'

    def stage(name, done, current):
        return {'stage': name, 'state': 'done' if done else 'current' if current else 'pending'}

    solution = getattr(opportunity, 'solution', None)
    agreed = Proposal.objects.filter(opportunity=opportunity, status='accepted').first()
    # The accepted proposal carries the requirements and the solution the owner agreed to, so
    # either the delivery team's own records or the proposal's sections count - both are data.
    has_requirements = opportunity.requirements.exists() or bool(
        agreed and agreed.requirements_summary.strip()
    )
    has_solution = bool(solution and solution.summary.strip()) or bool(
        agreed and agreed.proposed_solution.strip()
    )
    stages = [
        stage('requirements', has_requirements, False),
        stage('solution', has_solution, False),
        stage('proposal', agreed is not None, False),
        stage('assignment', True, False),
        stage('development', reached >= 2, status in {'planning', 'active'}),
        stage('testing', reached >= 3, status == 'testing'),
        stage('uat', reached >= 4, status == 'uat'),
        stage(
            'deployment',
            reached >= 4 and project.deployments.filter(status='successful').exists(),
            False,
        ),
        stage('impact', impact_done, status in {'deployed', 'completed'} and not impact_done),
    ]
    tasks = project.tasks.count()
    done_tasks = project.tasks.filter(status='done').count()
    return {
        'stages': stages,
        'task_completion_percent': round(done_tasks / tasks * 100) if tasks else None,
        'tasks_total': tasks,
        'tasks_done': done_tasks,
        'tasks_blocked': project.tasks.filter(status='blocked').count(),
    }
