"""Platform reviewers, review teams, and how a team works one idea."""

import pytest

from administration.authorization import AdministrationError
from ideas import services as idea_services
from ideas.models import Category, Idea
from identity.models import User
from reviews import review_teams, services
from reviews.models import Review, ReviewAssignment, ReviewCriterionAssessment
from reviews.tests.platform import grant_permission, grant_platform_reviewer, submit

PASSWORD = 'a-strong-unique-pass-1'
CRITERIA = [c.value for c in ReviewCriterionAssessment.Criterion]
MANAGE = 'administration.manage_reviewers'
ASSIGN = 'administration.assign_platform_reviewers'
CONSOLE = 'administration.access_console'


def make_user(email):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
    )


def make_reviewer(email):
    user = make_user(email)
    grant_platform_reviewer(user)
    return user


@pytest.fixture
def manager(db):
    user = make_user('manager@example.com')
    grant_permission(user, CONSOLE)
    grant_permission(user, MANAGE)
    return user


@pytest.fixture
def router(db):
    user = make_user('router@example.com')
    grant_permission(user, CONSOLE)
    grant_permission(user, ASSIGN)
    return user


@pytest.fixture
def lead(manager):
    return review_teams.grant_reviewer(manager, make_user('lead@example.com').email)


@pytest.fixture
def member(manager):
    return review_teams.grant_reviewer(manager, make_user('member@example.com').email)


@pytest.fixture
def team(manager, lead, member):
    return review_teams.create_team(manager, 'Platform Review A', lead.pk, [member.pk])


@pytest.fixture
def idea(db):
    author = make_user('author@example.com')
    draft = Idea.objects.create(
        author=author,
        title='Automate the invoice run',
        description='Payments are tracked by hand and it takes the finance team days.',
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        visibility=Idea.Visibility.PRIVATE,
        category=Category.objects.get_or_create(name='Review Team Fixture')[0],
    )
    return submit(draft)


def finish(idea, review, decision, feedback='A reason.'):
    return services.CompleteReviewInput(
        idea_id=idea.pk,
        review_id=review.pk,
        decision=decision,
        feedback=feedback,
        assessments=tuple(services.AssessmentInput(c, 'meets', '') for c in CRITERIA),
    )


@pytest.mark.django_db
class TestMakingReviewers:
    def test_only_the_manage_permission_creates_a_reviewer(self, manager, router):
        person = make_user('new@example.com')

        with pytest.raises(AdministrationError):
            review_teams.grant_reviewer(router, person.email)  # routing is not building
        review_teams.grant_reviewer(manager, person.email)

        assert review_teams.is_reviewer(person) is True

    def test_an_unknown_or_deactivated_account_is_refused(self, manager):
        with pytest.raises(AdministrationError, match='No account'):
            review_teams.grant_reviewer(manager, 'nobody@example.com')
        gone = make_user('gone@example.com')
        User.objects.filter(pk=gone.pk).update(is_active=False)
        with pytest.raises(AdministrationError, match='deactivated'):
            review_teams.grant_reviewer(manager, gone.email)

    def test_the_list_is_empty_for_anyone_without_the_permission(self, manager, router, lead):
        assert lead in review_teams.list_reviewers(manager)
        assert review_teams.list_reviewers(router) == []
        assert review_teams.list_reviewers(None) == []

    def test_a_lead_cannot_be_removed_until_somebody_else_leads(self, manager, team, lead):
        with pytest.raises(AdministrationError, match='lead'):
            review_teams.revoke_reviewer(manager, lead.pk)

    def test_removing_a_reviewer_takes_them_out_of_their_teams(self, manager, team, member):
        review_teams.revoke_reviewer(manager, member.pk)

        assert review_teams.is_reviewer(member) is False
        assert not team.members.filter(user=member).exists()


@pytest.mark.django_db
class TestFormingTeams:
    def test_the_lead_is_always_a_member(self, manager, lead):
        team = review_teams.create_team(manager, 'Solo', lead.pk, [])

        assert list(team.members.values_list('user_id', flat=True)) == [lead.pk]

    def test_only_reviewers_can_be_members(self, manager, lead):
        outsider = make_user('outsider@example.com')

        with pytest.raises(AdministrationError, match='not a platform reviewer'):
            review_teams.create_team(manager, 'Bad', lead.pk, [outsider.pk])

    def test_a_name_is_required_and_unique(self, manager, lead, team):
        with pytest.raises(AdministrationError, match='name'):
            review_teams.create_team(manager, ' ', lead.pk, [])
        with pytest.raises(AdministrationError, match='already exists'):
            review_teams.create_team(manager, 'platform review a', lead.pk, [])

    def test_a_router_cannot_form_teams(self, router, lead):
        with pytest.raises(AdministrationError):
            review_teams.create_team(router, 'Nope', lead.pk, [])

    def test_changing_the_lead_keeps_the_new_lead_a_member(self, manager, team, member):
        updated = review_teams.update_team(manager, team.pk, lead_id=member.pk, member_ids=[])

        assert updated.lead_id == member.pk
        assert updated.members.filter(user=member).exists()


@pytest.mark.django_db
class TestRoutingToATeam:
    def test_the_idea_is_assigned_with_the_lead_as_the_accountable_reviewer(
        self, router, team, lead, idea
    ):
        assignment = review_teams.assign_team(router, idea.pk, team.pk)

        assert assignment.team_id == team.pk
        assert assignment.reviewer_id == lead.pk
        assert ReviewAssignment.objects.filter(idea=idea, released_at__isnull=True).count() == 1

    def test_only_the_routing_permission_assigns(self, manager, team, idea):
        with pytest.raises(AdministrationError):
            review_teams.assign_team(manager, idea.pk, team.pk)

    def test_a_retired_team_cannot_be_given_work(self, router, manager, team, idea):
        review_teams.update_team(manager, team.pk, is_active=False)

        with pytest.raises(AdministrationError, match='not available'):
            review_teams.assign_team(router, idea.pk, team.pk)

    def test_a_team_that_includes_the_author_cannot_review_it(self, router, manager, team, idea):
        grant_platform_reviewer(idea.author)
        review_teams.update_team(manager, team.pk, member_ids=[idea.author_id])

        with pytest.raises(AdministrationError, match='wrote this idea'):
            review_teams.assign_team(router, idea.pk, team.pk)

    def test_an_admin_cannot_hand_work_to_their_own_team(self, manager, team, lead, idea):
        grant_permission(lead, ASSIGN)

        with pytest.raises(AdministrationError, match='lead this team'):
            review_teams.assign_team(lead, idea.pk, team.pk)

    def test_an_idea_already_under_review_cannot_be_reassigned(self, router, team, lead, idea):
        services.start_review(lead, idea.pk)

        with pytest.raises(AdministrationError, match='already being reviewed'):
            review_teams.assign_team(router, idea.pk, team.pk)


@pytest.mark.django_db
class TestHowTheTeamDecides:
    @pytest.fixture
    def routed(self, router, team, idea):
        review_teams.assign_team(router, idea.pk, team.pk)
        idea.refresh_from_db()
        return idea

    def test_a_reviewer_outside_the_team_cannot_touch_a_routed_idea(self, routed):
        stranger = make_reviewer('stranger@example.com')

        with pytest.raises(services.ReviewError, match='not allowed'):
            services.start_review(stranger, routed.pk)

    def test_an_unrouted_idea_is_still_open_to_any_reviewer(self, idea):
        anyone = make_reviewer('anyone@example.com')

        assert services.start_review(anyone, idea.pk).reviewer_id == anyone.pk

    def test_any_member_can_send_it_back_and_the_deciding_member_is_recorded(
        self, routed, lead, member
    ):
        services.start_review(lead, routed.pk)
        review = Review.objects.get(idea=routed, completed_at=None)

        done = services.complete_review(
            member,
            finish(routed, review, 'changes_requested', "Please attach last month's invoices."),
        )

        routed.refresh_from_db()
        assert routed.status == Idea.Status.CHANGES_REQUESTED
        assert done.reviewer_id == lead.pk
        assert done.decided_by_id == member.pk

    def test_a_member_cannot_approve_but_the_lead_can(self, routed, lead, member):
        services.start_review(member, routed.pk)
        review = Review.objects.get(idea=routed, completed_at=None)
        approve = finish(routed, review, 'approved', 'Looks good.')

        with pytest.raises(services.ReviewError, match="team's lead"):
            services.complete_review(member, approve)
        routed.refresh_from_db()
        assert routed.status == Idea.Status.UNDER_REVIEW

        services.complete_review(lead, approve)
        routed.refresh_from_db()
        assert routed.status == Idea.Status.APPROVED

    def test_a_member_cannot_reject_either(self, routed, member):
        services.start_review(member, routed.pk)
        review = Review.objects.get(idea=routed, completed_at=None)

        with pytest.raises(services.ReviewError, match="team's lead"):
            services.complete_review(
                member,
                finish(routed, review, 'rejected', 'No.'),
            )

    def test_an_outsider_cannot_complete_the_teams_round(self, routed, lead):
        services.start_review(lead, routed.pk)
        review = Review.objects.get(idea=routed, completed_at=None)
        stranger = make_reviewer('stranger@example.com')

        with pytest.raises(services.ReviewError):
            services.complete_review(
                stranger,
                finish(routed, review, 'changes_requested', 'x'),
            )

    def test_the_owner_resubmits_and_the_same_team_picks_it_up_again(self, routed, lead, member):
        services.start_review(lead, routed.pk)
        review = Review.objects.get(idea=routed, completed_at=None)
        services.complete_review(
            lead,
            finish(routed, review, 'changes_requested', 'Add volumes.'),
        )
        idea_services.submit_idea(routed.author, routed.pk)
        routed.refresh_from_db()
        assert routed.status == Idea.Status.SUBMITTED

        second = services.start_review(member, routed.pk)

        assert second.round == 2
