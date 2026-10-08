"""
The automation delivery lifecycle (Sprint 4): what happens to an idea after the
owner's go-ahead.

An **opportunity** is one idea entering implementation planning. It is created
only from an idea in `READY_FOR_IMPLEMENTATION`, and it carries the idea's
ownership context (individual / team / organization) *by copy at creation*, never
by recomputation, so the chain idea -> opportunity -> project can never quietly
change hands. The idea link is `PROTECT`: an idea that has become an opportunity
cannot be deleted out from under it.

Append-only `OpportunityEvent` is this domain's audit trail, for the reason
`administration.AdminAuditEntry` gives for being its own table: the existing
trail (`ideas.IdeaTransition`) records one thing - an idea's status moving - and
loosening its constraints to hold "a requirement was edited" would weaken every
row. It is narrow, append-only and written in the same transaction as the change
it records.

Platform permissions (who may run delivery) are declared on `OpportunityEvent`
for the same reason the console's are declared on `AdminAuditEntry`: a
platform-scoped `auth.Permission` is the project's one mechanism for "may act
across tenants", and an organization role must never become one.
"""

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

PRIORITY_CHOICES = (('high', 'High'), ('medium', 'Medium'), ('low', 'Low'))

OPPORTUNITY_STATUS_CHOICES = (
    ('draft', 'Draft'),
    ('discovery', 'Discovery'),
    ('requirements', 'Requirements'),
    ('solution_design', 'Solution design'),
    ('proposal', 'Proposal'),
    ('ready_for_assignment', 'Ready for assignment'),
    ('assigned', 'Assigned'),
    ('project_created', 'Project created'),
    ('completed', 'Completed'),
    ('cancelled', 'Cancelled'),
)

REQUIREMENT_TYPE_CHOICES = (
    ('business', 'Business'),
    ('functional', 'Functional'),
    ('technical', 'Technical'),
    ('security', 'Security'),
    ('integration', 'Integration'),
    ('data', 'Data'),
    ('compliance', 'Compliance'),
)

REQUIREMENT_STATUS_CHOICES = (
    ('open', 'Open'),
    ('in_progress', 'In progress'),
    ('satisfied', 'Satisfied'),
    ('rejected', 'Rejected'),
)

EVENT_ACTION_CHOICES = (
    ('opportunity_created', 'Opportunity created'),
    ('opportunity_updated', 'Opportunity updated'),
    ('opportunity_status_changed', 'Opportunity status changed'),
    ('requirement_created', 'Requirement created'),
    ('requirement_updated', 'Requirement updated'),
    ('solution_updated', 'Solution updated'),
    ('proposal_created', 'Proposal created'),
    ('proposal_updated', 'Proposal updated'),
    ('proposal_status_changed', 'Proposal status changed'),
    ('assignment_created', 'Assignment created'),
    ('project_created', 'Project created'),
    ('project_status_changed', 'Project status changed'),
    ('milestone_created', 'Milestone created'),
    ('milestone_completed', 'Milestone completed'),
    ('task_created', 'Task created'),
    ('task_status_changed', 'Task status changed'),
    ('test_case_created', 'Test case created'),
    ('test_executed', 'Test executed'),
    ('uat_recorded', 'UAT recorded'),
    ('deployment_recorded', 'Deployment recorded'),
    ('deployment_verified', 'Deployment verified'),
    ('impact_recorded', 'Impact recorded'),
)

PROPOSAL_STATUS_CHOICES = (
    ('draft', 'Draft'),
    ('ready', 'Ready'),
    ('submitted', 'Submitted'),
    ('accepted', 'Accepted'),
    ('changes_requested', 'Changes requested'),
    ('rejected', 'Rejected'),
)
ASSIGNMENT_STATUS_CHOICES = (('active', 'Active'), ('released', 'Released'))
PROJECT_STATUS_CHOICES = (
    ('planning', 'Planning'),
    ('active', 'Active'),
    ('testing', 'Testing'),
    ('uat', 'UAT'),
    ('deployed', 'Deployed'),
    ('completed', 'Completed'),
    ('on_hold', 'On hold'),
    ('cancelled', 'Cancelled'),
)
MILESTONE_STATUS_CHOICES = (
    ('pending', 'Pending'),
    ('in_progress', 'In progress'),
    ('completed', 'Completed'),
)
TASK_STATUS_CHOICES = (
    ('todo', 'To do'),
    ('in_progress', 'In progress'),
    ('blocked', 'Blocked'),
    ('done', 'Done'),
)
TEST_STATUS_CHOICES = (
    ('not_run', 'Not run'),
    ('pass', 'Pass'),
    ('fail', 'Fail'),
    ('blocked', 'Blocked'),
)
UAT_RESULT_CHOICES = (('pending', 'Pending'), ('passed', 'Passed'), ('failed', 'Failed'))
DEPLOYMENT_STATUS_CHOICES = (
    ('pending', 'Pending'),
    ('in_progress', 'In progress'),
    ('successful', 'Successful'),
    ('failed', 'Failed'),
    ('rolled_back', 'Rolled back'),
)
IMPACT_STATUS_CHOICES = (
    ('recorded', 'Recorded'),
    ('pending', 'Pending'),
    ('unavailable', 'Unavailable'),
)

#: Statuses in which an opportunity still counts as the idea's one live one.
#: Only `cancelled` frees the idea to start again.
TERMINAL_STATUSES = ('cancelled',)


class AutomationOpportunity(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft'
        DISCOVERY = 'discovery'
        REQUIREMENTS = 'requirements'
        SOLUTION_DESIGN = 'solution_design'
        PROPOSAL = 'proposal'
        READY_FOR_ASSIGNMENT = 'ready_for_assignment'
        ASSIGNED = 'assigned'
        PROJECT_CREATED = 'project_created'
        COMPLETED = 'completed'
        CANCELLED = 'cancelled'

    class Priority(models.TextChoices):
        HIGH = 'high'
        MEDIUM = 'medium'
        LOW = 'low'

    idea = models.ForeignKey(
        'ideas.Idea', on_delete=models.PROTECT, related_name='automation_opportunities'
    )
    title = models.CharField(max_length=200)
    summary = models.TextField(blank=True)
    problem_statement = models.TextField(blank=True)
    automation_goal = models.TextField(blank=True)
    expected_benefit = models.TextField(blank=True)
    priority = models.CharField(max_length=8, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=24, choices=OPPORTUNITY_STATUS_CHOICES, default='draft')

    # --- ownership context, copied from the idea and never changed -------------
    submission_context = models.CharField(max_length=16)
    organization = models.ForeignKey(
        'organizations.Organization',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='automation_opportunities',
    )
    team = models.ForeignKey(
        'teams.Team',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='automation_opportunities',
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='owned_automation_opportunities',
        help_text="The idea's author, who stays its stakeholder throughout delivery.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_automation_opportunities',
    )

    approved_at = models.DateTimeField(
        null=True, blank=True, help_text="When the owner's go-ahead was given."
    )
    ready_at = models.DateTimeField(
        null=True, blank=True, help_text='When it became ready for assignment.'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at', '-pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # The duplicate guard, in the database: one live opportunity per idea,
            # whatever the service layer did or did not catch under concurrency.
            models.UniqueConstraint(
                fields=['idea'],
                condition=~models.Q(status='cancelled'),
                name='opportunity_one_live_per_idea',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=dict(OPPORTUNITY_STATUS_CHOICES)),
                name='opportunity_status_is_known',
            ),
            models.CheckConstraint(
                condition=models.Q(priority__in=dict(PRIORITY_CHOICES)),
                name='opportunity_priority_is_known',
            ),
            # Exactly the tenant the context names, so the chain cannot be
            # re-homed by writing the wrong column.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        submission_context='individual',
                        organization__isnull=True,
                        team__isnull=True,
                    )
                    | models.Q(
                        submission_context='team',
                        team__isnull=False,
                        organization__isnull=True,
                    )
                    | models.Q(
                        submission_context='organization',
                        organization__isnull=False,
                        team__isnull=True,
                    )
                ),
                name='opportunity_context_matches_tenant',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['status', '-created_at'], name='opp_status_created_idx'),
            models.Index(fields=['organization', '-created_at'], name='opp_org_created_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.title} ({self.status})'


class Requirement(models.Model):
    opportunity = models.ForeignKey(
        AutomationOpportunity, on_delete=models.CASCADE, related_name='requirements'
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    type = models.CharField(max_length=16, choices=REQUIREMENT_TYPE_CHOICES, default='functional')
    priority = models.CharField(max_length=8, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=16, choices=REQUIREMENT_STATUS_CHOICES, default='open')
    acceptance_criteria = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at', 'pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(type__in=dict(REQUIREMENT_TYPE_CHOICES)),
                name='requirement_type_is_known',
            ),
            models.CheckConstraint(
                condition=models.Q(priority__in=dict(PRIORITY_CHOICES)),
                name='requirement_priority_is_known',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=dict(REQUIREMENT_STATUS_CHOICES)),
                name='requirement_status_is_known',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['opportunity', 'created_at'], name='req_opp_created_idx'),
        ]

    def __str__(self) -> str:
        return self.title


class SolutionScope(models.Model):
    """The solution and its scope: one per opportunity, edited in place."""

    opportunity = models.OneToOneField(
        AutomationOpportunity, on_delete=models.CASCADE, related_name='solution'
    )
    summary = models.TextField(blank=True)
    business_workflow = models.TextField(blank=True)
    in_scope = models.TextField(blank=True)
    out_of_scope = models.TextField(blank=True)
    systems_integrations = models.TextField(blank=True)
    technical_considerations = models.TextField(blank=True)
    assumptions = models.TextField(blank=True)
    constraints = models.TextField(blank=True)
    risks = models.TextField(blank=True)
    expected_output = models.TextField(blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f'Solution for opportunity {self.opportunity_id}'


class OpportunityEvent(models.Model):
    """One audited event in an opportunity's life. Append-only."""

    opportunity = models.ForeignKey(
        AutomationOpportunity, on_delete=models.CASCADE, related_name='events'
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='+',
        help_text='The authenticated caller; never an input.',
    )
    action = models.CharField(max_length=32, choices=EVENT_ACTION_CHOICES)
    entity_type = models.CharField(max_length=32)
    entity_id = models.BigIntegerField(null=True, blank=True)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at', 'pk']
        default_permissions = ()
        permissions: ClassVar[list[tuple[str, str]]] = [
            (
                'manage_delivery',
                'Can run the automation delivery lifecycle across organizations',
            ),
            (
                'be_assignable',
                'Can be assigned automation delivery work',
            ),
        ]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(action__in=dict(EVENT_ACTION_CHOICES)),
                name='opportunity_event_action_is_known',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['opportunity', 'created_at'], name='oppevent_opp_created_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.action} on opportunity {self.opportunity_id}'

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('An opportunity event is append-only and cannot be changed.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('An opportunity event is append-only and cannot be deleted.')


def _known(field: str, choices, name: str) -> models.CheckConstraint:
    return models.CheckConstraint(condition=models.Q(**{f'{field}__in': dict(choices)}), name=name)


PAYMENT_CHOICES = (('yes', 'The owner pays'), ('no', 'No charge to the owner'))


class Proposal(models.Model):
    """The delivery team's proposal for an opportunity. One per opportunity, edited in place."""

    opportunity = models.OneToOneField(
        AutomationOpportunity, on_delete=models.CASCADE, related_name='proposal'
    )
    title = models.CharField(max_length=200)
    executive_summary = models.TextField(blank=True)
    problem = models.TextField(blank=True)
    proposed_solution = models.TextField(blank=True)
    requirements_summary = models.TextField(blank=True)
    scope = models.TextField(blank=True)
    deliverables = models.TextField(blank=True)
    risks = models.TextField(blank=True)
    assumptions = models.TextField(blank=True)
    estimated_effort = models.CharField(max_length=120, blank=True)
    estimated_timeline = models.CharField(max_length=120, blank=True)
    acceptance_criteria = models.TextField(blank=True)
    feasibility = models.TextField(blank=True)
    milestones = models.TextField(blank=True, help_text='The timeline, phase by phase.')
    financial_requirements = models.TextField(blank=True)
    payment_required = models.CharField(
        max_length=3,
        choices=PAYMENT_CHOICES,
        blank=True,
        help_text='Whether the owner pays for it; empty until the team answers.',
    )
    payment_plan = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=PROPOSAL_STATUS_CHOICES, default='draft')
    review_feedback = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            _known('status', PROPOSAL_STATUS_CHOICES, 'proposal_status_is_known'),
        ]

    def __str__(self) -> str:
        return f'{self.title} ({self.status})'


class Assignment(models.Model):
    """Who is delivering an opportunity: one developer **or** one team, never both."""

    opportunity = models.ForeignKey(
        AutomationOpportunity, on_delete=models.CASCADE, related_name='assignments'
    )
    assignee_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='delivery_assignments',
    )
    assignee_team = models.ForeignKey(
        'teams.Team',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='delivery_assignments',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    status = models.CharField(max_length=12, choices=ASSIGNMENT_STATUS_CHOICES, default='active')
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-assigned_at', '-pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # Two people assigned at once is a conflict, so the database refuses it.
            models.UniqueConstraint(
                fields=['opportunity'],
                condition=models.Q(status='active'),
                name='assignment_one_active_per_opportunity',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(assignee_user__isnull=False, assignee_team__isnull=True)
                    | models.Q(assignee_user__isnull=True, assignee_team__isnull=False)
                ),
                name='assignment_exactly_one_assignee',
            ),
            _known('status', ASSIGNMENT_STATUS_CHOICES, 'assignment_status_is_known'),
        ]

    def __str__(self) -> str:
        return f'Assignment of opportunity {self.opportunity_id}'


class Project(models.Model):
    """Delivery of one opportunity. Ownership context is copied from it and never changes."""

    opportunity = models.OneToOneField(
        AutomationOpportunity, on_delete=models.PROTECT, related_name='project'
    )
    assignment = models.ForeignKey(Assignment, on_delete=models.PROTECT, related_name='projects')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='owned_projects'
    )
    organization = models.ForeignKey(
        'organizations.Organization',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='automation_projects',
    )
    team = models.ForeignKey(
        'teams.Team',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='automation_projects',
    )
    assigned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='delivery_projects',
    )
    assigned_team = models.ForeignKey(
        'teams.Team',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='delivery_projects',
    )
    status = models.CharField(max_length=12, choices=PROJECT_STATUS_CHOICES, default='planning')
    start_date = models.DateField(null=True, blank=True)
    target_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at', '-pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            _known('status', PROJECT_STATUS_CHOICES, 'project_status_is_known'),
            models.CheckConstraint(
                condition=(
                    models.Q(assigned_user__isnull=False, assigned_team__isnull=True)
                    | models.Q(assigned_user__isnull=True, assigned_team__isnull=False)
                ),
                name='project_exactly_one_assignee',
            ),
            models.CheckConstraint(
                condition=models.Q(status='completed', completed_at__isnull=False)
                | ~models.Q(status='completed'),
                name='project_completed_has_timestamp',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['status', '-created_at'], name='project_status_created_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.title} ({self.status})'


class Milestone(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=MILESTONE_STATUS_CHOICES, default='pending')
    order = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['order', 'pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            _known('status', MILESTONE_STATUS_CHOICES, 'milestone_status_is_known'),
        ]

    def __str__(self) -> str:
        return self.title


class Task(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    milestone = models.ForeignKey(
        Milestone, null=True, blank=True, on_delete=models.SET_NULL, related_name='tasks'
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=TASK_STATUS_CHOICES, default='todo')
    priority = models.CharField(max_length=8, choices=PRIORITY_CHOICES, default='medium')
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at', 'pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            _known('status', TASK_STATUS_CHOICES, 'task_status_is_known'),
            _known('priority', PRIORITY_CHOICES, 'task_priority_is_known'),
        ]

    def __str__(self) -> str:
        return self.title


class TestCase(models.Model):
    """A technical test. A failed required one blocks the move to UAT."""

    __test__ = False  # not a pytest class, despite the name

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='test_cases')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    expected_result = models.TextField(blank=True)
    actual_result = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=TEST_STATUS_CHOICES, default='not_run')
    is_required = models.BooleanField(default=True)
    tester = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    executed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            _known('status', TEST_STATUS_CHOICES, 'test_case_status_is_known'),
        ]

    def __str__(self) -> str:
        return self.title


class UATRecord(models.Model):
    """
    One user-acceptance scenario. Once decided it is never edited: a failed round
    is history, and another round is new scenarios.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='uat_records')
    scenario = models.TextField()
    expected_outcome = models.TextField(blank=True)
    actual_outcome = models.TextField(blank=True)
    result = models.CharField(max_length=8, choices=UAT_RESULT_CHOICES, default='pending')
    is_required = models.BooleanField(default=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    feedback = models.TextField(blank=True)
    recorded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at', 'pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            _known('result', UAT_RESULT_CHOICES, 'uat_result_is_known'),
        ]

    def __str__(self) -> str:
        return f'UAT: {self.scenario[:40]}'


class Deployment(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='deployments')
    environment = models.CharField(max_length=60)
    version = models.CharField(max_length=60)
    status = models.CharField(max_length=12, choices=DEPLOYMENT_STATUS_CHOICES, default='pending')
    deployed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    deployment_date = models.DateTimeField(null=True, blank=True)
    deployment_notes = models.TextField(blank=True)
    rollback_information = models.TextField(blank=True)
    verified_at = models.DateTimeField(
        null=True, blank=True, help_text='When the successful deployment was verified in place.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at', '-pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            _known('status', DEPLOYMENT_STATUS_CHOICES, 'deployment_status_is_known'),
        ]

    def __str__(self) -> str:
        return f'{self.version} to {self.environment} ({self.status})'


class ImpactRecord(models.Model):
    """
    What changed because of the automation. Nothing is required and nothing is
    invented: every number is whatever somebody entered, **estimated** and
    **measured** are separate columns, and a result is only calculated from a pair
    that exists (see `automation.impact`).
    """

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name='impact')
    status = models.CharField(max_length=12, choices=IMPACT_STATUS_CHOICES, default='pending')

    before_processing_minutes = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    after_processing_minutes = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    before_people_involved = models.PositiveIntegerField(null=True, blank=True)
    after_people_involved = models.PositiveIntegerField(null=True, blank=True)
    before_requests = models.PositiveIntegerField(null=True, blank=True)
    after_requests = models.PositiveIntegerField(null=True, blank=True)
    before_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    after_cost = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    before_error_rate = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    after_error_rate = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    before_workload_hours = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    after_workload_hours = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    users_affected = models.PositiveIntegerField(null=True, blank=True)
    estimated_hours_saved_per_week = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    measured_hours_saved_per_week = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    estimated_cost_savings = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    measured_cost_savings = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    automation_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    qualitative_outcome = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            _known('status', IMPACT_STATUS_CHOICES, 'impact_status_is_known'),
        ]

    def __str__(self) -> str:
        return f'Impact for project {self.project_id} ({self.status})'
