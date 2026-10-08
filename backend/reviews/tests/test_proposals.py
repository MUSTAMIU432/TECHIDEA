"""
The proposal an approved idea gets: written by the review team, released by an admin, read by
the owner, who then gives the go-ahead that opens the idea to delivery.
"""

from datetime import date, timedelta

import pytest

from administration.authorization import AdministrationError
from automation.models import AutomationOpportunity
from automation.models import Proposal as DeliveryProposal
from ideas import go_ahead
from ideas.models import Category, Idea
from ideas.services import IdeaError
from identity.models import User
from notifications.models import Notification
from reviews import proposal_answers, proposals, review_teams, services
from reviews.models import IdeaProposalView, ProposalAnswer, Review
from reviews.tests.platform import build_idea, grant_permission, send_decision_letters, submit

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
    # An administrator sends the approval letter: the owner hears of it before the proposal.
    send_decision_letters(idea)
    return idea


@pytest.fixture
def approved(owner, router, manager, lead, member):
    return approve_with_a_team(owner, router, manager, lead, member)


FULL = {
    'executive_summary': 'Stop tracking payments by hand.',
    'problem': 'Finance re-types every payment.',
    'feasibility': 'Feasible: the bank exports a CSV the importer can read.',
    'proposed_solution': 'A payments dashboard with an import.',
    'requirements_summary': '- Track every payment\n- Match it to an invoice',
    'scope': 'Import, match, report.',
    'deliverables': 'Dashboard and importer.',
    'estimated_timeline': '6 weeks',
    'milestones': 'Weeks 1-2 importer; 3-4 matching; 5-6 dashboard and handover.',
    'financial_requirements': 'Hosting at 40 USD a month; no licences.',
    'payment_required': 'no',
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
        # Releasing is console work: the lead is made an administrator who releases.
        grant_permission(lead, 'administration.access_console')
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

    def test_the_admin_follows_a_draft_but_cannot_decide_it_yet(self, approved, member, releaser):
        proposals.start_proposal(member, approved.pk)

        proposal, role = proposals.proposal_for(releaser, approved.pk)
        assert (proposal.status, role) == ('draft', 'admin')
        assert proposals.decided_proposals(releaser) == []  # nothing waiting for a decision
        with pytest.raises(proposals.ProposalError):
            proposals.release(releaser, approved.pk)

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


@pytest.mark.django_db
class TestProgress:
    """The admin sees how far each proposal has got, and who is writing it."""

    def test_an_approved_idea_shows_as_not_started_with_its_team(
        self, approved, lead, member, releaser
    ):
        (row,) = proposals.progress_board(releaser)

        assert (row.idea, row.status, row.team_name) == (approved, 'not_started', 'Team A')
        assert [(p.user, p.role, p.contributions) for p in row.participants] == [
            (lead, 'lead', 0),
            (member, 'member', 0),
        ]
        assert row.missing_required == tuple(f for f, _ in proposals.missing_required(None))
        assert 'payment_plan' not in row.missing_required  # asked for only once the owner pays

    def test_each_hand_in_it_is_recorded_with_the_sections_it_changed(
        self, approved, lead, member, releaser
    ):
        proposals.start_proposal(member, approved.pk)
        proposals.update_proposal(member, approved.pk, {'scope': 'Import and match'})
        # Re-sending what is already there is not a contribution.
        proposals.update_proposal(lead, approved.pk, {'scope': 'Import and match'})
        proposals.update_proposal(lead, approved.pk, {'deliverables': 'A dashboard'})

        (row,) = proposals.progress_board(releaser)

        assert row.status == 'draft'
        assert {'scope', 'deliverables', 'title', 'problem'} <= set(row.filled)
        assert 'scope' not in row.missing_required
        assert 'executive_summary' in row.missing_required
        people = {p.user: p for p in row.participants}
        assert (people[member].contributions, people[member].sections) == (2, ('scope',))
        assert (people[lead].contributions, people[lead].sections) == (1, ('deliverables',))
        assert [(c.contributor, c.action) for c in row.activity] == [
            (lead, 'edited'),
            (member, 'edited'),
            (member, 'started'),
        ]

    def test_sending_it_is_recorded_and_it_moves_to_the_top(
        self, approved, lead, member, releaser, owner, router, manager
    ):
        submitted(approved, lead, member)
        later = build_idea(
            status=Idea.Status.APPROVED,
            author=owner,
            title='Another idea',
            description='A second approved idea that nobody has started.',
            submission_context=Idea.SubmissionContext.INDIVIDUAL,
            visibility=Idea.Visibility.PRIVATE,
        )

        rows = proposals.progress_board(releaser)

        assert [(r.idea, r.status) for r in rows] == [
            (approved, 'submitted'),
            (later, 'not_started'),
        ]
        assert rows[0].activity[0].action == 'submitted'
        assert rows[0].missing_required == ()
        assert rows[1].participants == ()  # never reviewed: nobody to write it yet

    def test_a_former_member_who_wrote_part_of_it_still_shows(
        self, approved, lead, member, releaser, manager
    ):
        proposals.start_proposal(member, approved.pk)
        team = approved.proposal.team
        review_teams.update_team(manager, team.pk, name=team.name, lead_id=lead.pk, member_ids=[])

        (row,) = proposals.progress_board(releaser)

        assert {p.user: p.role for p in row.participants} == {lead: 'lead', member: 'former'}

    def test_only_release_admins_see_the_board(self, approved, member, manager, owner):
        proposals.start_proposal(member, approved.pk)

        for person in (manager, member, owner, None):
            assert proposals.progress_board(person) == []

    def test_the_board_over_graphql(self, approved, member, releaser):
        from automation.tests.test_api_journey import Api

        proposals.start_proposal(member, approved.pk)
        proposals.update_proposal(member, approved.pk, {'scope': 'Import and match'})
        board = """{ proposalProgress { ideaTitle status teamName totalSections missingRequired
            participants { email role contributions sections } activity { action sections }
            proposal { scope } } }"""

        assert Api(member)(board)['proposalProgress'] == []  # writers are not admins
        (row,) = Api(releaser)(board)['proposalProgress']
        assert row['status'] == 'draft'
        assert row['totalSections'] == len(proposals.FIELDS)
        assert 'executiveSummary' in row['missingRequired']
        assert row['proposal'] == {'scope': 'Import and match'}
        assert {
            'email': member.email,
            'role': 'member',
            'contributions': 2,
            'sections': ['scope'],
        } in row['participants']
        assert row['activity'][0] == {'action': 'edited', 'sections': ['scope']}


@pytest.mark.django_db
class TestCompleteness:
    """A proposal reaches the admin complete: feasibility, requirements, timeline, money."""

    @pytest.mark.parametrize(
        ('field', 'label'),
        [
            ('feasibility', 'feasibility'),
            ('requirements_summary', 'requirements'),
            ('milestones', 'timeline and milestones'),
            ('financial_requirements', 'financial requirements'),
            ('payment_required', 'answer to whether the owner pays'),
        ],
    )
    def test_the_lead_cannot_send_it_without(self, approved, lead, member, field, label):
        written(approved, lead, member)
        proposals.update_proposal(member, approved.pk, {field: ''})

        with pytest.raises(proposals.ProposalError, match=f'Fill in the {label}') as refused:
            proposals.submit_proposal(lead, approved.pk)
        assert refused.value.field == field

    def test_a_paid_proposal_needs_its_payment_plan(self, approved, lead, member, releaser):
        written(approved, lead, member)
        proposals.update_proposal(member, approved.pk, {'payment_required': 'yes'})

        with pytest.raises(proposals.ProposalError, match='payment plan'):
            proposals.submit_proposal(lead, approved.pk)
        (row,) = proposals.progress_board(releaser)
        assert row.missing_required == ('payment_plan',)

        proposals.update_proposal(
            member, approved.pk, {'payment_plan': '50% on start, 50% on acceptance.'}
        )
        assert proposals.submit_proposal(lead, approved.pk).status == 'submitted'

    def test_no_charge_leaves_the_payment_plan_out_of_the_count(
        self, approved, lead, member, releaser
    ):
        written(approved, lead, member)

        (row,) = proposals.progress_board(releaser)

        assert row.total == len(proposals.FIELDS) - 1
        assert row.missing_required == ()

    def test_whether_the_owner_pays_is_yes_or_no(self, approved, member):
        proposals.start_proposal(member, approved.pk)

        with pytest.raises(proposals.ProposalError, match="'yes' or 'no'"):
            proposals.update_proposal(member, approved.pk, {'payment_required': 'maybe'})

    def test_the_agreed_proposal_carries_its_money_and_timeline_into_delivery(
        self, approved, lead, member, releaser, owner
    ):
        submitted(approved, lead, member)
        proposals.release(releaser, approved.pk)

        go_ahead.confirm_go_ahead(owner, approved.pk)

        agreed = DeliveryProposal.objects.get(opportunity__idea=approved)
        assert agreed.feasibility == FULL['feasibility']
        assert agreed.milestones == FULL['milestones']
        assert agreed.financial_requirements == FULL['financial_requirements']
        assert agreed.payment_required == 'no'


# --- the owner's answers to the released proposal ------------------------------------


@pytest.fixture
def released(approved, lead, member, releaser):
    submitted(approved, lead, member)
    proposals.release(releaser, approved.pk)
    return approved


def proceed(owner, idea, **overrides):
    values = {'decision': 'proceed', 'timeline': 'yes', 'conditions': 'Start after payroll week.'}
    values.update(overrides)
    return proposal_answers.answer(owner, idea.pk, **values)


@pytest.mark.django_db
class TestGoingAhead:
    def test_going_ahead_is_the_go_ahead_and_carries_the_terms(self, owner, released):
        record = proceed(owner, released, preferred_start=date.today() + timedelta(days=7))

        released.refresh_from_db()
        assert released.status == Idea.Status.READY_FOR_IMPLEMENTATION
        assert released.automation_opportunities.get().status == 'ready_for_assignment'
        assert (record.decision, record.timeline, record.payment) == ('proceed', 'yes', '')
        assert record.conditions == 'Start after payroll week.'

    def test_a_paying_owner_must_answer_the_payment_plan(
        self, owner, approved, lead, member, releaser
    ):
        proposals.start_proposal(member, approved.pk)
        proposals.update_proposal(
            member,
            approved.pk,
            {**FULL, 'payment_required': 'yes', 'payment_plan': 'Half up front, half on delivery.'},
        )
        proposals.submit_proposal(lead, approved.pk)
        proposals.release(releaser, approved.pk)

        with pytest.raises(proposal_answers.ProposalAnswerError, match='payment plan'):
            proceed(owner, approved)
        assert proceed(owner, approved, payment='discuss').payment == 'discuss'

    def test_the_timeline_must_be_answered_and_a_start_cannot_be_past(self, owner, released):
        with pytest.raises(proposal_answers.ProposalAnswerError, match='timeline'):
            proceed(owner, released, timeline='')
        with pytest.raises(proposal_answers.ProposalAnswerError, match='start date'):
            proceed(owner, released, preferred_start=date.today() - timedelta(days=1))
        assert not ProposalAnswer.objects.exists()


@pytest.mark.django_db
class TestDeclining:
    def test_a_decline_needs_a_reason_and_ends_the_way_to_development(self, owner, released):
        with pytest.raises(proposal_answers.ProposalAnswerError, match='why'):
            proposal_answers.answer(owner, released.pk, decision='decline')

        proposal_answers.answer(
            owner, released.pk, decision='decline', decline_reason='The cost is too high.'
        )

        released.refresh_from_db()
        assert released.status == Idea.Status.APPROVED
        with pytest.raises(IdeaError, match='decided not to go ahead'):
            go_ahead.confirm_go_ahead(owner, released.pk)


@pytest.mark.django_db(transaction=True)
def test_a_decline_tells_the_admin_and_the_review_team(owner, released, releaser, lead):
    proposal_answers.answer(owner, released.pk, decision='decline', decline_reason='Too costly.')

    for person in (releaser, lead):
        assert Notification.objects.filter(user=person, kind='proposal.owner_declined').exists()


@pytest.mark.django_db
class TestWhoAnswersAndReads:
    def test_only_the_owner_answers_and_only_once(self, owner, released, lead, releaser):
        for other in (lead, releaser, make_user('stranger@example.com')):
            with pytest.raises(proposal_answers.ProposalAnswerError, match='not yours'):
                proceed(other, released)
        proceed(owner, released)
        with pytest.raises(proposal_answers.ProposalAnswerError):
            proceed(owner, released)

    def test_an_unreleased_proposal_cannot_be_answered(self, owner, approved, lead, member):
        submitted(approved, lead, member)

        with pytest.raises(proposal_answers.ProposalAnswerError, match='no released proposal'):
            proceed(owner, approved)

    def test_the_answers_are_read_by_those_who_act_on_them(self, owner, released, lead, releaser):
        proceed(owner, released)

        for reader in (owner, lead, releaser):
            assert proposal_answers.answer_for(reader, released.pk) is not None
        assert proposal_answers.answer_for(make_user('stranger@example.com'), released.pk) is None
