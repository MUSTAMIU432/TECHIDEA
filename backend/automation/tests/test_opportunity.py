"""
The automation opportunity: opened by the owner's go-ahead on a released proposal, ready for a
developer, carrying the idea's ownership context; duplicates, authority, isolation, audit.
"""

import json

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from automation import services
from automation.authorization import AutomationError
from automation.models import AutomationOpportunity, OpportunityEvent
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership
from reviews.tests.platform import grant_permission, release_proposal
from teams import services as team_services

PASSWORD = 'a-strong-unique-pass-1'
MANAGE = 'automation.manage_delivery'


def make_user(email):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
    )


def make_org(owner, name='Acme Labs'):
    from organizations.services import CreateOrganizationInput, create_organization_for_user

    return create_organization_for_user(owner, CreateOrganizationInput(name=name)).organization


def ready_idea(author, **overrides):
    fields = {
        'author': author,
        'title': 'Automate hostel payments',
        'description': 'Payments are tracked by hand and it takes the finance team days.',
        'visibility': Idea.Visibility.PRIVATE,
        'submission_context': Idea.SubmissionContext.INDIVIDUAL,
        'status': Idea.Status.READY_FOR_IMPLEMENTATION,
        'submitted_at': timezone.now(),
        'owner_go_ahead_at': timezone.now(),
        'category': Category.objects.get_or_create(name='Automation Fixture')[0],
    }
    fields.update(overrides)
    if fields['status'] != Idea.Status.READY_FOR_IMPLEMENTATION:
        # The go-ahead exists only on a ready idea; a draft has not been submitted.
        fields['owner_go_ahead_at'] = None
    if fields['status'] == Idea.Status.DRAFT:
        fields['submitted_at'] = None
    return Idea.objects.create(**fields)


@pytest.fixture
def author(db):
    return make_user('author@example.com')


@pytest.fixture
def stranger(db):
    return make_user('stranger@example.com')


@pytest.fixture
def manager(db):
    user = make_user('manager@example.com')
    grant_permission(user, MANAGE)
    return user


def opened(author, **overrides):
    """A ready idea with a released proposal, opened the way the go-ahead opens it."""
    idea = ready_idea(author, **overrides)
    release_proposal(idea)
    return services.open_from_go_ahead(idea)


@pytest.mark.django_db
class TestHowItOpens:
    def test_the_go_ahead_opens_it_ready_for_a_developer_with_the_accepted_proposal(self, author):
        opportunity = opened(author)

        assert opportunity.status == 'ready_for_assignment'
        assert opportunity.ready_at is not None
        assert opportunity.owner_id == author.pk
        assert opportunity.submission_context == 'individual'
        assert opportunity.organization_id is None
        assert opportunity.team_id is None
        assert opportunity.approved_at == opportunity.idea.owner_go_ahead_at
        assert opportunity.proposal.status == 'accepted'
        assert opportunity.proposal.title == opportunity.idea.title

    def test_it_needs_a_released_proposal(self, author):
        idea = ready_idea(author)

        with pytest.raises(AutomationError, match='no released proposal'):
            services.open_from_go_ahead(idea)
        assert not AutomationOpportunity.objects.exists()

    def test_an_organization_idea_keeps_its_organization(self, author):
        organization = make_org(author)
        opportunity = opened(
            author,
            organization=organization,
            submission_context=Idea.SubmissionContext.ORGANIZATION,
            visibility=Idea.Visibility.ORGANIZATION,
        )

        assert opportunity.submission_context == 'organization'
        assert opportunity.organization_id == organization.pk

    def test_a_team_idea_keeps_its_team(self, author):
        team = team_services.create_team(author, team_services.TeamInput(name='Automation Team'))
        opportunity = opened(
            author,
            team=team,
            submission_context=Idea.SubmissionContext.TEAM,
            visibility=Idea.Visibility.TEAM,
        )

        assert opportunity.team_id == team.pk
        assert opportunity.submission_context == 'team'

    def test_the_delivery_managers_are_told(
        self, author, manager, django_capture_on_commit_callbacks
    ):
        from notifications.models import Notification

        with django_capture_on_commit_callbacks(execute=True):
            opened(author)

        assert Notification.objects.filter(user=manager, kind='delivery.go_ahead_received').exists()


@pytest.mark.django_db
class TestOpeningByHand:
    """The delivery manager's recovery path, held to the same rules as the go-ahead."""

    def test_only_a_delivery_manager_can(self, author, stranger, manager):
        idea = ready_idea(author)
        release_proposal(idea)

        for person in (author, stranger):
            with pytest.raises(AutomationError, match='not available'):
                services.create_opportunity(person, idea.pk)
        assert services.create_opportunity(manager, idea.pk).status == 'ready_for_assignment'

    @pytest.mark.parametrize(
        'status',
        [
            Idea.Status.DRAFT,
            Idea.Status.SUBMITTED,
            Idea.Status.UNDER_REVIEW,
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.REJECTED,
            Idea.Status.APPROVED,
        ],
    )
    def test_nothing_short_of_the_go_ahead_can_enter(self, author, manager, status):
        idea = ready_idea(author, status=status)

        with pytest.raises(AutomationError, match='ready for implementation'):
            services.create_opportunity(manager, idea.pk)

    def test_it_still_needs_a_released_proposal(self, author, manager):
        idea = ready_idea(author)

        with pytest.raises(AutomationError, match='no released proposal'):
            services.create_opportunity(manager, idea.pk)

    def test_signed_out_callers_are_refused(self, author):
        with pytest.raises(AutomationError, match='Sign in'):
            services.create_opportunity(None, ready_idea(author).pk)


@pytest.mark.django_db
class TestDuplicates:
    def test_a_second_opportunity_for_the_same_idea_is_refused(self, author, manager):
        opportunity = opened(author)

        with pytest.raises(AutomationError, match='already has'):
            services.create_opportunity(manager, opportunity.idea_id)
        assert AutomationOpportunity.objects.count() == 1

    def test_the_database_refuses_it_even_without_the_service(self, author):
        first = opened(author)

        with pytest.raises(IntegrityError), transaction.atomic():
            AutomationOpportunity.objects.create(
                idea=first.idea,
                title='Again',
                submission_context=first.submission_context,
                owner=author,
                created_by=author,
            )

    def test_a_cancelled_opportunity_frees_the_idea(self, author, manager):
        first = opened(author)
        services.cancel_opportunity(manager, first.pk)

        second = services.create_opportunity(manager, first.idea_id)

        assert second.pk != first.pk


@pytest.mark.django_db
class TestCancelling:
    def test_only_the_delivery_team_cancels(self, author, manager):
        opportunity = opened(author)

        with pytest.raises(AutomationError, match='cannot cancel'):
            services.cancel_opportunity(author, opportunity.pk)
        assert services.cancel_opportunity(manager, opportunity.pk).status == 'cancelled'

    def test_a_cancelled_opportunity_is_closed(self, author, manager):
        opportunity = opened(author)
        services.cancel_opportunity(manager, opportunity.pk)

        with pytest.raises(AutomationError, match='closed'):
            services.create_requirement(
                manager, opportunity.pk, services.RequirementInput(title='Too late')
            )
        with pytest.raises(AutomationError, match='closed'):
            services.cancel_opportunity(manager, opportunity.pk)


@pytest.mark.django_db
class TestRequirementsAndSolution:
    """The delivery team's working material while the opportunity is ready, assigned or started."""

    def test_the_owner_adds_requirements(self, author):
        opportunity = opened(author)

        requirement = services.create_requirement(
            author,
            opportunity.pk,
            services.RequirementInput(
                title='Receipts are matched', type='functional', priority='high'
            ),
        )

        assert requirement.status == 'open'
        assert requirement.priority == 'high'

    def test_a_requirement_needs_a_title_and_known_choices(self, author):
        opportunity = opened(author)

        with pytest.raises(AutomationError, match='Title is required'):
            services.create_requirement(
                author, opportunity.pk, services.RequirementInput(title=' ')
            )
        with pytest.raises(AutomationError, match='valid priority'):
            services.create_requirement(
                author, opportunity.pk, services.RequirementInput(title='X', priority='urgent')
            )

    def test_status_and_priority_can_be_updated(self, author):
        opportunity = opened(author)
        requirement = services.create_requirement(
            author, opportunity.pk, services.RequirementInput(title='X')
        )

        updated = services.update_requirement(
            author, requirement.pk, services.RequirementInput(status='satisfied', priority='low')
        )

        assert (updated.status, updated.priority) == ('satisfied', 'low')

    def test_a_stranger_cannot_touch_requirements(self, author, stranger):
        opportunity = opened(author)
        requirement = services.create_requirement(
            author, opportunity.pk, services.RequirementInput(title='X')
        )

        with pytest.raises(AutomationError):
            services.update_requirement(
                stranger, requirement.pk, services.RequirementInput(status='rejected')
            )
        requirement.refresh_from_db()
        assert requirement.status == 'open'

    def test_the_solution_is_the_delivery_teams_not_the_owners(self, author, manager):
        opportunity = opened(author)

        with pytest.raises(AutomationError, match='cannot edit the solution'):
            services.update_solution(author, opportunity.pk, {'summary': 'Mine'})

        solution = services.update_solution(manager, opportunity.pk, {'summary': 'Theirs'})
        assert solution.summary == 'Theirs'

    def test_an_unknown_solution_field_is_refused(self, author, manager):
        opportunity = opened(author)

        with pytest.raises(AutomationError, match='Unknown solution field'):
            services.update_solution(manager, opportunity.pk, {'owner_id': '1'})


@pytest.mark.django_db
class TestReadingAndIsolation:
    def test_an_opportunity_is_as_private_as_its_idea(self, author, stranger):
        opportunity = opened(author)

        assert services.get_opportunity(author, opportunity.pk) is not None
        assert services.get_opportunity(stranger, opportunity.pk) is None
        assert list(services.list_opportunities(stranger)) == []

    def test_another_organizations_member_cannot_read_an_org_opportunity(self, author):
        organization = make_org(author)
        opportunity = opened(
            author,
            organization=organization,
            submission_context=Idea.SubmissionContext.ORGANIZATION,
            visibility=Idea.Visibility.ORGANIZATION,
        )
        outsider = make_user('outsider@example.com')
        make_org(outsider, name='Other Co')
        colleague = make_user('colleague@example.com')
        Membership.objects.create(
            user=colleague, organization=organization, status=Membership.Status.ACTIVE
        )

        assert services.get_opportunity(colleague, opportunity.pk) is not None
        assert services.get_opportunity(outsider, opportunity.pk) is None

    def test_the_way_back_from_an_idea(self, author):
        opportunity = opened(author)

        assert services.opportunity_for_idea(author, opportunity.idea_id).pk == opportunity.pk


@pytest.mark.django_db
class TestAudit:
    def test_every_change_leaves_an_event_and_events_cannot_change(self, author, manager):
        opportunity = opened(author)
        services.cancel_opportunity(manager, opportunity.pk)

        actions = list(opportunity.events.values_list('action', flat=True))
        assert actions == ['opportunity_created', 'opportunity_status_changed']
        event = opportunity.events.last()
        assert (event.from_status, event.to_status) == ('ready_for_assignment', 'cancelled')
        assert event.actor_id == manager.pk

        from django.core.exceptions import ValidationError

        event.metadata = {'tampered': True}
        with pytest.raises(ValidationError):
            event.save()
        with pytest.raises(ValidationError):
            event.delete()
        assert OpportunityEvent.objects.count() == 2


CREATE = """
mutation($input: CreateOpportunityInput!) {
  createAutomationOpportunity(input: $input) {
    success message field opportunity { id status ideaId capabilities { canTransition } }
  }
}
"""
QUERY = 'query($id: ID!) { automationOpportunity(id: $id) { id title status } }'
LOGIN = 'mutation($i: LoginInput!){ login(input: $i){ success accessToken } }'


def gql(client, query, variables, token):
    response = client.post(
        '/graphql/',
        data=json.dumps({'query': query, 'variables': variables}),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )
    body = response.json()
    assert 'errors' not in body, body
    return body['data']


def token_for(client, user):
    data = client.post(
        '/graphql/',
        data=json.dumps(
            {'query': LOGIN, 'variables': {'i': {'email': user.email, 'password': PASSWORD}}}
        ),
        content_type='application/json',
    ).json()['data']
    return data['login']['accessToken']


@pytest.mark.django_db
class TestGraphQL:
    def test_opening_by_hand_is_the_managers_and_refusals_are_payloads(
        self, author, stranger, manager
    ):
        client = Client()
        idea = ready_idea(author)
        release_proposal(idea)

        refused = gql(
            client, CREATE, {'input': {'ideaId': str(idea.pk)}}, token_for(client, author)
        )['createAutomationOpportunity']
        assert refused['success'] is False

        created = gql(
            client, CREATE, {'input': {'ideaId': str(idea.pk)}}, token_for(Client(), manager)
        )['createAutomationOpportunity']
        assert created['success'] is True
        assert created['opportunity']['status'] == 'ready_for_assignment'

        again = gql(
            client, CREATE, {'input': {'ideaId': str(idea.pk)}}, token_for(Client(), manager)
        )['createAutomationOpportunity']
        assert again['success'] is False
        assert again['field'] == 'idea'

    def test_a_private_opportunity_reads_as_null_to_a_stranger(self, author, stranger):
        client = Client()
        opportunity = opened(author)

        mine = gql(client, QUERY, {'id': str(opportunity.pk)}, token_for(client, author))
        theirs = gql(client, QUERY, {'id': str(opportunity.pk)}, token_for(Client(), stranger))

        assert mine['automationOpportunity']['title'] == opportunity.title
        assert theirs['automationOpportunity'] is None
