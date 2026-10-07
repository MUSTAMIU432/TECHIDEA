"""Assignment -> project -> testing -> UAT -> deployment -> impact, from an opened opportunity."""

from decimal import Decimal

import pytest
from django.utils import timezone

from automation import delivery, impact, services
from automation.authorization import AutomationError
from automation.models import ImpactRecord, OpportunityEvent
from automation.tests.test_opportunity import make_org, make_user, opened
from ideas.models import Idea
from reviews.tests.platform import grant_permission
from teams import services as team_services

MANAGE = 'automation.manage_delivery'
ASSIGNABLE = 'automation.be_assignable'


@pytest.fixture
def author(db):
    return make_user('author@example.com')


@pytest.fixture
def manager(db):
    user = make_user('manager@example.com')
    grant_permission(user, MANAGE)
    return user


@pytest.fixture
def developer(db):
    user = make_user('dev@example.com')
    grant_permission(user, ASSIGNABLE)
    return user


@pytest.fixture
def stranger(db):
    return make_user('stranger@example.com')


def ready_for_assignment(author, **overrides):
    """An opportunity as the owner's go-ahead opens it: ready for a developer."""
    return opened(author, **overrides)


def project_stage(author, manager, developer):
    opportunity = ready_for_assignment(author)
    delivery.assign_opportunity(manager, opportunity.pk, assignee_user_id=developer.pk)
    return delivery.create_project(developer, opportunity.pk)


def uat_ready_project(author, manager, developer):
    project = project_stage(author, manager, developer)
    delivery.start_project(developer, project.pk)
    case = delivery.create_test_case(developer, project.pk, title='Import works')
    delivery.start_testing(developer, project.pk)
    delivery.record_test_result(developer, case.pk, status='pass', actual_result='ok')
    delivery.submit_for_uat(developer, project.pk)
    project.refresh_from_db()
    return project


def accepted_project(author, manager, developer):
    project = uat_ready_project(author, manager, developer)
    scenario = delivery.create_uat_scenario(author, project.pk, scenario='Finance sees payments')
    delivery.record_uat_result(author, scenario.pk, result='passed')
    return project


@pytest.mark.django_db
class TestDeveloperQueueAndAssignment:
    def test_only_ready_opportunities_appear_and_only_to_the_delivery_side(
        self, author, manager, developer, stranger
    ):
        gone = ready_for_assignment(author)
        services.cancel_opportunity(manager, gone.pk)  # not ready any more: must not appear
        ready = ready_for_assignment(make_user('other@example.com'))

        assert [o.pk for o in delivery.developer_queue(manager)] == [ready.pk]
        assert [o.pk for o in delivery.developer_queue(developer)] == [ready.pk]
        assert list(delivery.developer_queue(stranger)) == []
        assert list(delivery.developer_queue(author)) == []

    def test_an_eligible_developer_is_assigned(self, author, manager, developer):
        opportunity = ready_for_assignment(author)

        assignment = delivery.assign_opportunity(
            manager, opportunity.pk, assignee_user_id=developer.pk
        )

        opportunity.refresh_from_db()
        assert opportunity.status == 'assigned'
        assert assignment.assigned_by_id == manager.pk

    def test_an_ineligible_person_cannot_be_assigned(self, author, manager, stranger):
        opportunity = ready_for_assignment(author)

        with pytest.raises(AutomationError, match='cannot be assigned'):
            delivery.assign_opportunity(manager, opportunity.pk, assignee_user_id=stranger.pk)

    def test_a_team_with_an_eligible_member_can_be_assigned(self, author, manager, developer):
        team = team_services.create_team(developer, team_services.TeamInput(name='Dev Team'))
        opportunity = ready_for_assignment(author)

        assignment = delivery.assign_opportunity(manager, opportunity.pk, assignee_team_id=team.pk)

        assert assignment.assignee_team_id == team.pk

    def test_exactly_one_assignee_and_only_managers_assign(self, author, manager, developer):
        opportunity = ready_for_assignment(author)

        with pytest.raises(AutomationError, match='either a developer or a team'):
            delivery.assign_opportunity(manager, opportunity.pk)
        # Not a manager, and not assigned to a private idea's work: refused before anything else.
        with pytest.raises(AutomationError, match=r'not available|cannot assign'):
            delivery.assign_opportunity(developer, opportunity.pk, assignee_user_id=developer.pk)

    def test_a_second_assignment_is_refused(self, author, manager, developer):
        opportunity = ready_for_assignment(author)
        delivery.assign_opportunity(manager, opportunity.pk, assignee_user_id=developer.pk)

        with pytest.raises(AutomationError, match='ready for assignment'):
            delivery.assign_opportunity(manager, opportunity.pk, assignee_user_id=developer.pk)

    def test_a_project_needs_an_assignment_and_the_right_person(
        self, author, manager, developer, stranger
    ):
        opportunity = ready_for_assignment(author)
        with pytest.raises(AutomationError):
            delivery.create_project(developer, opportunity.pk)

        delivery.assign_opportunity(manager, opportunity.pk, assignee_user_id=developer.pk)
        with pytest.raises(AutomationError):
            delivery.create_project(stranger, opportunity.pk)
        project = delivery.create_project(developer, opportunity.pk)

        opportunity.refresh_from_db()
        assert opportunity.status == 'project_created'
        assert project.owner_id == author.pk
        assert project.assigned_user_id == developer.pk

    def test_the_project_keeps_the_ideas_organization(self, author, manager, developer):
        organization = make_org(author)
        opportunity = ready_for_assignment(
            author,
            organization=organization,
            submission_context=Idea.SubmissionContext.ORGANIZATION,
            visibility=Idea.Visibility.ORGANIZATION,
        )
        delivery.assign_opportunity(manager, opportunity.pk, assignee_user_id=developer.pk)

        project = delivery.create_project(developer, opportunity.pk)

        assert project.organization_id == organization.pk


@pytest.mark.django_db
class TestProjectLifecycle:
    def test_the_owner_cannot_drive_the_project(self, author, manager, developer):
        project = project_stage(author, manager, developer)

        with pytest.raises(AutomationError, match='cannot move'):
            delivery.start_project(author, project.pk)

    def test_a_step_cannot_be_skipped(self, author, manager, developer):
        project = project_stage(author, manager, developer)

        with pytest.raises(AutomationError, match='cannot go to'):
            delivery.start_testing(developer, project.pk)

    def test_tasks_and_milestones(self, author, manager, developer):
        project = project_stage(author, manager, developer)
        delivery.start_project(developer, project.pk)
        milestone = delivery.create_milestone(developer, project.pk, title='MVP')
        task = delivery.create_task(
            developer,
            project.pk,
            title='Build import',
            milestone_id=milestone.pk,
            assignee_id=developer.pk,
        )

        delivery.update_task(developer, task.pk, {'status': 'done'})
        delivery.complete_milestone(developer, milestone.pk)

        task.refresh_from_db()
        assert task.status == 'done'
        progress = delivery.project_progress(project)
        assert progress['task_completion_percent'] == 100

    def test_a_task_cannot_go_to_an_outsider_or_another_projects_milestone(
        self, author, manager, developer, stranger
    ):
        project = project_stage(author, manager, developer)
        with pytest.raises(AutomationError, match='not part of this project'):
            delivery.create_task(developer, project.pk, title='T', assignee_id=stranger.pk)
        with pytest.raises(AutomationError, match='milestone from this project'):
            delivery.create_task(developer, project.pk, title='T', milestone_id=99999)

    def test_progress_reflects_real_state_only(self, author, manager, developer):
        project = project_stage(author, manager, developer)

        stages = {s['stage']: s['state'] for s in delivery.project_progress(project)['stages']}

        assert stages['requirements'] == 'done'
        assert stages['proposal'] == 'done'
        assert stages['assignment'] == 'done'
        assert stages['development'] == 'current'
        assert stages['testing'] == 'pending'
        assert stages['impact'] == 'pending'
        assert delivery.project_progress(project)['task_completion_percent'] is None


@pytest.mark.django_db
class TestTestingAndUAT:
    def test_a_failed_required_test_blocks_uat_and_says_why(self, author, manager, developer):
        project = project_stage(author, manager, developer)
        delivery.start_project(developer, project.pk)
        good = delivery.create_test_case(developer, project.pk, title='Good')
        bad = delivery.create_test_case(developer, project.pk, title='Bad')
        delivery.start_testing(developer, project.pk)
        delivery.record_test_result(developer, good.pk, status='pass')
        delivery.record_test_result(developer, bad.pk, status='fail', actual_result='crash')

        with pytest.raises(AutomationError, match='1 required test case has failed'):
            delivery.submit_for_uat(developer, project.pk)

    def test_an_optional_failure_does_not_block_and_untested_does(self, author, manager, developer):
        project = project_stage(author, manager, developer)
        delivery.start_project(developer, project.pk)
        required = delivery.create_test_case(developer, project.pk, title='Req')
        optional = delivery.create_test_case(developer, project.pk, title='Nice', is_required=False)
        delivery.start_testing(developer, project.pk)
        delivery.record_test_result(developer, optional.pk, status='fail')

        with pytest.raises(AutomationError, match='not passed yet'):
            delivery.submit_for_uat(developer, project.pk)
        delivery.record_test_result(developer, required.pk, status='pass')
        assert delivery.submit_for_uat(developer, project.pk).status == 'uat'

    def test_uat_needs_some_required_test(self, author, manager, developer):
        project = project_stage(author, manager, developer)
        delivery.start_project(developer, project.pk)
        delivery.start_testing(developer, project.pk)

        with pytest.raises(AutomationError, match='no required test cases'):
            delivery.submit_for_uat(developer, project.pk)

    def test_only_the_stakeholder_accepts_and_the_builder_cannot(self, author, manager, developer):
        project = uat_ready_project(author, manager, developer)
        scenario = delivery.create_uat_scenario(developer, project.pk, scenario='Check payments')

        with pytest.raises(AutomationError, match='Only the owner'):
            delivery.record_uat_result(developer, scenario.pk, result='passed')
        with pytest.raises(AutomationError, match='Only the owner'):
            delivery.record_uat_result(manager, scenario.pk, result='passed')
        delivery.record_uat_result(author, scenario.pk, result='passed')

    def test_a_failure_needs_feedback_returns_to_development_and_is_kept(
        self, author, manager, developer
    ):
        project = uat_ready_project(author, manager, developer)
        scenario = delivery.create_uat_scenario(author, project.pk, scenario='Check payments')

        with pytest.raises(AutomationError, match='Feedback is required'):
            delivery.record_uat_result(author, scenario.pk, result='failed')
        delivery.record_uat_result(author, scenario.pk, result='failed', feedback='Totals wrong.')

        project.refresh_from_db()
        scenario.refresh_from_db()
        assert project.status == 'active'
        assert scenario.result == 'failed'
        assert scenario.feedback == 'Totals wrong.'
        with pytest.raises(AutomationError, match='already been decided'):
            delivery.record_uat_result(author, scenario.pk, result='passed')

    def test_a_second_round_is_judged_on_its_own(self, author, manager, developer):
        project = uat_ready_project(author, manager, developer)
        first = delivery.create_uat_scenario(author, project.pk, scenario='Round one')
        delivery.record_uat_result(author, first.pk, result='failed', feedback='No.')
        delivery.start_testing(developer, project.pk)
        delivery.submit_for_uat(developer, project.pk)
        second = delivery.create_uat_scenario(author, project.pk, scenario='Round two')
        delivery.record_uat_result(author, second.pk, result='passed')

        deployment = delivery.create_deployment(
            developer, project.pk, environment='production', version='1.0'
        )

        assert deployment.status == 'pending'
        assert project.uat_records.count() == 2  # the failed round is still there


@pytest.mark.django_db
class TestDeployment:
    def test_deployment_needs_passed_uat(self, author, manager, developer):
        project = uat_ready_project(author, manager, developer)

        with pytest.raises(AutomationError, match='no acceptance scenario'):
            delivery.create_deployment(developer, project.pk, environment='prod', version='1')

        delivery.create_uat_scenario(author, project.pk, scenario='Pending one')
        with pytest.raises(AutomationError, match='1 required acceptance scenario has not passed'):
            delivery.create_deployment(developer, project.pk, environment='prod', version='1')

    def test_a_project_is_deployed_only_by_a_successful_record(self, author, manager, developer):
        project = accepted_project(author, manager, developer)
        deployment = delivery.create_deployment(
            developer, project.pk, environment='prod', version='1.0'
        )
        project.refresh_from_db()
        assert project.status == 'uat'

        with pytest.raises(AutomationError, match='cannot become'):
            delivery.record_deployment(developer, deployment.pk, status='successful')
        delivery.record_deployment(developer, deployment.pk, status='in_progress')
        delivery.record_deployment(developer, deployment.pk, status='successful')

        project.refresh_from_db()
        deployment.refresh_from_db()
        assert project.status == 'deployed'
        assert deployment.deployment_date is not None

    def test_a_failed_deployment_leaves_the_project_where_it_was(self, author, manager, developer):
        project = accepted_project(author, manager, developer)
        deployment = delivery.create_deployment(
            developer, project.pk, environment='prod', version='1.0'
        )
        delivery.record_deployment(developer, deployment.pk, status='in_progress')
        delivery.record_deployment(developer, deployment.pk, status='failed', notes='DB down')

        project.refresh_from_db()
        assert project.status == 'uat'

    def test_a_rollback_sends_it_back_to_development(self, author, manager, developer):
        project = accepted_project(author, manager, developer)
        deployment = delivery.create_deployment(
            developer, project.pk, environment='prod', version='1.0'
        )
        delivery.record_deployment(developer, deployment.pk, status='in_progress')
        delivery.record_deployment(developer, deployment.pk, status='successful')
        delivery.record_deployment(
            developer, deployment.pk, status='rolled_back', rollback_information='Reverted.'
        )

        project.refresh_from_db()
        assert project.status == 'active'

    def test_only_the_delivery_side_deploys(self, author, manager, developer):
        project = accepted_project(author, manager, developer)

        with pytest.raises(AutomationError, match='cannot deploy'):
            delivery.create_deployment(author, project.pk, environment='prod', version='1')


@pytest.mark.django_db
class TestImpactAndCompletion:
    def _deployed(self, author, manager, developer):
        project = accepted_project(author, manager, developer)
        deployment = delivery.create_deployment(
            developer, project.pk, environment='prod', version='1.0'
        )
        delivery.record_deployment(developer, deployment.pk, status='in_progress')
        delivery.record_deployment(developer, deployment.pk, status='successful')
        return project, deployment

    def test_completion_needs_a_verified_deployment_and_an_impact_decision(
        self, author, manager, developer
    ):
        project, deployment = self._deployed(author, manager, developer)

        with pytest.raises(AutomationError, match='Verify the deployment'):
            delivery.complete_project(developer, project.pk)
        delivery.verify_deployment(developer, deployment.pk)
        with pytest.raises(AutomationError, match='Record the impact'):
            delivery.complete_project(developer, project.pk)

        delivery.record_impact(developer, project.pk, status='pending')
        completed = delivery.complete_project(developer, project.pk)

        assert completed.status == 'completed'
        assert completed.completed_at is not None
        assert completed.opportunity.status == 'completed'

    def test_impact_is_never_forced_or_invented(self, author, manager, developer):
        project, _ = self._deployed(author, manager, developer)

        with pytest.raises(AutomationError, match='at least one measure'):
            delivery.record_impact(developer, project.pk, status='recorded')
        unavailable = delivery.record_impact(developer, project.pk, status='unavailable')
        assert unavailable.before_processing_minutes is None

    def test_impact_cannot_be_recorded_before_deployment(self, author, manager, developer):
        project = uat_ready_project(author, manager, developer)

        with pytest.raises(AutomationError, match='once the automation is deployed'):
            delivery.record_impact(developer, project.pk, status='pending')

    def test_results_use_stored_values_and_keep_estimated_apart_from_measured(
        self, author, manager, developer
    ):
        project, _ = self._deployed(author, manager, developer)

        record = delivery.record_impact(
            author,
            project.pk,
            values={
                'before_processing_minutes': '240',
                'after_processing_minutes': '30',
                'before_people_involved': '5',
                'after_people_involved': '2',
                'before_error_rate': '12',
                'after_error_rate': '2',
                'estimated_hours_saved_per_week': '20',
                'measured_hours_saved_per_week': '17.5',
            },
        )

        results = impact.calculate(record)
        assert results['time_saved_percent'] == Decimal('87.5')
        assert results['people_reduced'] == 3
        assert results['error_reduction_percent'] == Decimal('83.3')
        assert results['hours_saved_variance'] == Decimal('-2.5')
        assert results['estimated_hours_saved_per_week'] == Decimal('20')
        assert results['measured_hours_saved_per_week'] == Decimal('17.5')

    def test_a_missing_half_means_no_result(self):
        record = ImpactRecord(before_processing_minutes=Decimal('240'))
        results = impact.calculate(record)

        assert results['time_saved_percent'] is None
        assert results['people_reduced'] is None
        assert results['hours_saved_variance'] is None

    def test_bad_numbers_are_refused(self, author, manager, developer):
        project, _ = self._deployed(author, manager, developer)

        with pytest.raises(AutomationError, match='Enter a number'):
            delivery.record_impact(author, project.pk, values={'before_cost': 'lots'})
        with pytest.raises(AutomationError, match='makes sense'):
            delivery.record_impact(author, project.pk, values={'before_error_rate': '250'})
        with pytest.raises(AutomationError, match='whole number'):
            delivery.record_impact(author, project.pk, values={'users_affected': '2.5'})

    def test_a_stranger_cannot_record_impact(self, author, manager, developer, stranger):
        project, _ = self._deployed(author, manager, developer)

        with pytest.raises(AutomationError):
            delivery.record_impact(stranger, project.pk, status='pending')


@pytest.mark.django_db
class TestIsolationAndAudit:
    def test_a_project_is_as_private_as_its_idea(self, author, manager, developer, stranger):
        project = project_stage(author, manager, developer)

        assert delivery.get_project(author, project.pk) is not None
        assert delivery.get_project(developer, project.pk) is not None  # delivery side
        assert delivery.get_project(stranger, project.pk) is None  # an individual idea is private

    def test_an_assigned_developer_can_open_a_private_ideas_work_and_nobody_else_can(
        self, author, manager, developer, stranger
    ):
        opportunity = ready_for_assignment(author)
        assert services.get_opportunity(developer, opportunity.pk) is None  # not yet assigned

        delivery.assign_opportunity(manager, opportunity.pk, assignee_user_id=developer.pk)

        assert services.get_opportunity(developer, opportunity.pk) is not None
        assert services.get_opportunity(stranger, opportunity.pk) is None
        assert opportunity.pk in [o.pk for o in services.list_opportunities(developer)]
        assert opportunity.pk not in [o.pk for o in services.list_opportunities(stranger)]

    def test_the_whole_journey_is_audited_in_order(self, author, manager, developer):
        project = accepted_project(author, manager, developer)

        actions = list(
            OpportunityEvent.objects.filter(opportunity=project.opportunity).values_list(
                'action', flat=True
            )
        )
        for expected in (
            'opportunity_created',
            'assignment_created',
            'project_created',
            'project_status_changed',
            'test_executed',
            'uat_recorded',
        ):
            assert expected in actions

    def test_the_idea_is_traceable_from_the_project(self, author, manager, developer):
        project = project_stage(author, manager, developer)

        assert project.opportunity.idea_id is not None
        assert project.opportunity.idea.author_id == author.pk
        assert timezone.now() >= project.created_at
