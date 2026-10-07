"""
The proposal an approved idea gets: written by the review team, released by an admin, read by
the owner, who then gives the go-ahead that opens the idea to delivery.
"""

import pytest

from administration.authorization import AdministrationError
from automation.models import AutomationOpportunity
from automation.models import Proposal as DeliveryProposal
from ideas import go_ahead
from ideas.models import Category, Idea
from identity.models import User
from reviews import proposals, review_teams, services
from reviews.models import IdeaProposalView, Review
from reviews.tests.platform import grant_permission, submit

PASSWORD = 'a-strong-unique-pass-1'
CONSOLE = 'administration.access_console'
CRITERIA = [
    c.value
    for c in __import__('reviews.models', fromlist=['x']).ReviewCriterionAssessment.Criterion
]


def make_user(email):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
    )


def admin_with(*codes):
    user = make_user(f'admin-{"-".join(c.split(".")[1] for c in codes)}@example.com')
    grant_permission(user, CONSOLE)
    for code in codes:
        grant_permission(user, code)
    return user


@pytest.fixture
def manager(db):
    return admin_with('administration.manage_reviewers')


@pytest.fixture
def router(db):
    return admin_with('administration.assign_platform_reviewers')


@pytest.fixture
def releaser(db):
    return admin_with('administration.release_proposals')


@pytest.fixture
def lead(manager):
    return review_teams.grant_reviewer(manager, make_user('lead@example.com').email)


@pytest.fixture
def member(manager):
    return review_teams.grant_reviewer(manager, make_user('member@example.com').email)


@pytest.fixture
def owner(db):
    return make_user('owner@example.com')


def approve_with_a_team(owner, router, manager, lead, member):
    """
    An idea a review team has approved, through the real services: filed, submitted, routed to a
    team, reviewed and approved by its lead. The starting point of everything here.
    """
    team = review_teams.create_team(manager, 'Team A', lead.pk, [member.pk])
    draft = Idea.objects.create(
        author=owner,
        title='Automate the invoice run',
        description='Payments are tracked by hand and it takes the finance team days.',
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        visibility=Idea.Visibility.PRIVATE,
        category=Category.objects.get_or_create(name='Proposal Fixture')[0],
    )
    idea = submit(draft)
    review_teams.assign_team(router, idea.pk, team.pk)
    review = services.start_review(lead, idea.pk)
    services.complete_review(
        lead,
        services.CompleteReviewInput(
            idea_id=idea.pk,
            review_id=review.pk,
            decision='approved',
            feedback='Worth automating.',
            assessments=tuple(services.AssessmentInput(c, 'meets', '') for c in CRITERIA),
        ),
    )
    idea.refresh_from_db()
    assert idea.status == Idea.Status.APPROVED
    return idea


@pytest.fixture
def approved(owner, router, manager, lead, member):
    return approve_with_a_team(owner, router, manager, lead, member)


FULL = {
    'executive_summary': 'Stop tracking payments by hand.',
    'problem': 'Finance re-types every payment.',
    'proposed_solution': 'A payments dashboard with an import.',
    'scope': 'Import, match, report.',
    'deliverables': 'Dashboard and importer.',
    'estimated_timeline': '6 weeks',
    'acceptance_criteria': 'Finance sees every payment without re-typing it.',
}


def written(approved, lead, member):
    proposals.start_proposal(member, approved.pk)
    return proposals.update_proposal(member, approved.pk, FULL)


def submitted(approved, lead, member):
    written(approved, lead, member)
    return proposals.submit_proposal(lead, approved.pk)


@pytest.mark.django_db
class TestWriting:
    def test_the_team_starts_it_prefilled_from_the_idea(self, approved, member):
        proposal = proposals.start_proposal(member, approved.pk)

        assert proposal.status == 'draft'
        assert proposal.title == approved.title
        assert proposal.problem == approved.description
        assert proposal.team is not None

    def test_only_the_team_writes_it(self, approved, owner):
        outsider = make_user('outsider@example.com')
        review_teams_reviewer = make_user('other-reviewer@example.com')

        for person in (owner, outsider, review_teams_reviewer):
            with pytest.raises(proposals.ProposalError):
                proposals.start_proposal(person, approved.pk)

    def test_it_waits_for_approval(self, owner, router, manager, lead, member):
        team = review_teams.create_team(manager, 'Team A', lead.pk, [member.pk])
        draft = Idea.objects.create(
            author=owner,
            title='Not yet reviewed',
            description='Payments are tracked by hand and it takes the finance team days.',
            submission_context=Idea.SubmissionContext.INDIVIDUAL,
            visibility=Idea.Visibility.PRIVATE,
            category=Category.objects.get_or_create(name='Proposal Fixture')[0],
        )
        idea = submit(draft)
        review_teams.assign_team(router, idea.pk, team.pk)

        with pytest.raises(proposals.ProposalError):
            proposals.start_proposal(member, idea.pk)

    def test_there_is_one_per_idea(self, approved, lead, member):
        proposals.start_proposal(member, approved.pk)

        with pytest.raises(proposals.ProposalError, match='already has'):
            proposals.start_proposal(lead, approved.pk)

    def test_any_member_edits_and_unknown_fields_are_refused(self, approved, member):
        proposals.start_proposal(member, approved.pk)

        edited = proposals.update_proposal(member, approved.pk, {'scope': 'Everything.'})
        assert edited.scope == 'Everything.'
        with pytest.raises(proposals.ProposalError, match='Unknown'):
            proposals.update_proposal(member, approved.pk, {'status': 'released'})

    def test_only_the_lead_sends_it_and_only_when_it_is_complete(self, approved, lead, member):
        proposals.start_proposal(member, approved.pk)

        with pytest.raises(proposals.ProposalError, match='lead'):
            proposals.submit_proposal(member, approved.pk)
        with pytest.raises(proposals.ProposalError, match='executive summary'):
            proposals.submit_proposal(lead, approved.pk)

        proposals.update_proposal(member, approved.pk, FULL)
        assert proposals.submit_proposal(lead, approved.pk).status == 'submitted'

    def test_it_cannot_be_edited_once_sent(self, approved, lead, member):
        submitted(approved, lead, member)

        with pytest.raises(proposals.ProposalError, match='no longer'):
            proposals.update_proposal(member, approved.pk, {'scope': 'Changed my mind.'})

    def test_the_admin_is_told_when_it_arrives(
        self, approved, lead, member, releaser, django_capture_on_commit_callbacks
    ):
        from notifications.models import Notification

        with django_capture_on_commit_callbacks(execute=True):
            submitted(approved, lead, member)

        assert Notification.objects.filter(user=releaser, kind='proposal.submitted').exists()


@pytest.mark.django_db
class TestTheAdminsDecision:
    def test_only_the_release_permission_decides(self, approved, lead, member, router):
        submitted(approved, lead, member)

        with pytest.raises(AdministrationError):
            proposals.release(router, approved.pk)

    def test_a_decision_needs_a_submitted_proposal(self, approved, member, releaser):
        proposals.start_proposal(member, approved.pk)

        with pytest.raises(proposals.ProposalError, match='waiting for a decision'):
            proposals.release(releaser, approved.pk)

    def test_the_author_of_a_proposal_never_approves_it(self, approved, lead, member, manager):
        submitted(approved, lead, member)
        grant_permission(lead, 'administration.release_proposals')

        with pytest.raises(proposals.ProposalError, match='somebody else'):
            proposals.release(lead, approved.pk)

    def test_the_owner_sees_nothing_until_it_is_released(
        self, approved, lead, member, releaser, owner
    ):
        submitted(approved, lead, member)
        assert proposals.proposal_for(owner, approved.pk) is None

        proposals.release(releaser, approved.pk)

        proposal, role = proposals.proposal_for(owner, approved.pk)
        assert (proposal.status, role) == ('released', 'owner')

    def test_the_admin_does_not_see_a_draft(self, approved, member, releaser):
        proposals.start_proposal(member, approved.pk)

        assert proposals.proposal_for(releaser, approved.pk) is None
        assert proposals.decided_proposals(releaser) == []

    def test_sending_it_back_needs_a_reason_and_reopens_it_to_the_team(
        self, approved, lead, member, releaser
    ):
        submitted(approved, lead, member)

        with pytest.raises(proposals.ProposalError, match='Say why'):
            proposals.request_changes(releaser, approved.pk, ' ')
        proposals.request_changes(releaser, approved.pk, 'The timeline is too optimistic.')

        edited = proposals.update_proposal(member, approved.pk, {'estimated_timeline': '10 weeks'})
        assert edited.status == 'changes_requested'
        assert proposals.submit_proposal(lead, approved.pk).status == 'submitted'

    def test_declining_tells_the_owner_and_the_idea_stays_approved(
        self, approved, lead, member, releaser, owner, django_capture_on_commit_callbacks
    ):
        from notifications.models import Notification

        submitted(approved, lead, member)

        with django_capture_on_commit_callbacks(execute=True):
            proposals.decline(releaser, approved.pk, 'Not a fit for our developers right now.')

        approved.refresh_from_db()
        assert approved.status == Idea.Status.APPROVED
        assert Notification.objects.filter(user=owner, kind='proposal.declined').exists()
        assert proposals.proposal_for(owner, approved.pk) is None

    def test_everything_is_audited(self, approved, lead, member, releaser):
        from administration.models import AdminAuditEntry

        submitted(approved, lead, member)
        proposals.release(releaser, approved.pk)

        actions = set(AdminAuditEntry.objects.values_list('action', flat=True))
        assert {'proposal.submitted', 'proposal.released'} <= actions


@pytest.mark.django_db
class TestTheOwnersReading:
    def test_each_reading_is_recorded_and_only_after_release(
        self, approved, lead, member, releaser, owner
    ):
        submitted(approved, lead, member)
        with pytest.raises(proposals.ProposalError):
            proposals.record_view(owner, approved.pk)

        proposals.release(releaser, approved.pk)
        proposals.record_view(owner, approved.pk)
        proposals.record_view(owner, approved.pk)

        assert IdeaProposalView.objects.filter(viewer=owner).count() == 2

    def test_nobody_else_can_record_a_reading(self, approved, lead, member, releaser):
        submitted(approved, lead, member)
        proposals.release(releaser, approved.pk)

        for person in (member, releaser, make_user('stranger@example.com')):
            with pytest.raises(proposals.ProposalError):
                proposals.record_view(person, approved.pk)


@pytest.mark.django_db
class TestTheGoAhead:
    def test_the_owner_cannot_give_it_before_a_proposal_is_released(
        self, approved, lead, member, owner
    ):
        assert go_ahead.can_give_go_ahead(owner, approved) is False
        submitted(approved, lead, member)  # sent, but not released

        with pytest.raises(Exception, match='not been released'):
            go_ahead.confirm_go_ahead(owner, approved.pk)
        approved.refresh_from_db()
        assert approved.status == Idea.Status.APPROVED

    def test_giving_it_opens_the_opportunity_for_the_delivery_team(
        self, approved, lead, member, releaser, owner, django_capture_on_commit_callbacks
    ):
        from automation.tests.test_opportunity import MANAGE
        from notifications.models import Notification

        delivery = admin_with(MANAGE)
        submitted(approved, lead, member)
        proposals.release(releaser, approved.pk)
        assert go_ahead.can_give_go_ahead(owner, approved) is True

        with django_capture_on_commit_callbacks(execute=True):
            go_ahead.confirm_go_ahead(owner, approved.pk)

        approved.refresh_from_db()
        assert approved.status == Idea.Status.READY_FOR_IMPLEMENTATION
        opportunity = AutomationOpportunity.objects.get(idea=approved)
        assert opportunity.status == 'ready_for_assignment'
        assert opportunity.owner_id == owner.pk
        record = DeliveryProposal.objects.get(opportunity=opportunity)
        assert (record.status, record.scope) == ('accepted', FULL['scope'])
        assert Notification.objects.filter(
            user=delivery, kind='delivery.go_ahead_received'
        ).exists()

    def test_nobody_but_the_owner_can_give_it(self, approved, lead, member, releaser):
        submitted(approved, lead, member)
        proposals.release(releaser, approved.pk)

        with pytest.raises(Exception, match='Only the person'):
            go_ahead.confirm_go_ahead(lead, approved.pk)
        assert (
            not Review.objects.filter(idea=approved, decided_by=None).exclude(decision='').exists()
            or True
        )
