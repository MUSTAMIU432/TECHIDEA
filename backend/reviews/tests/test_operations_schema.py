"""
`startReview` / `completeReview` at the GraphQL boundary (S3-004).

The service decides; these tests check that the boundary is thin and safe
when called directly, which is how an attacker would call it:

- a refusal is a payload (`success: false` plus a message), never a crash and
  never a message that distinguishes "exists but hidden" from "does not exist";
- enum-typed input means an unknown decision, criterion or rating never
  reaches the service - GraphQL validation refuses it;
- a success returns the review as its reviewer now sees it and the idea in its
  new state, so the client can reconcile without a second request.
"""

import json

import pytest
from django.utils import timezone

from ideas.models import Category, Idea
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews.models import Review

VALID_PASSWORD = 'a-strong-unique-pass-1'

START = """
mutation Start($ideaId: ID!) {
  startReview(ideaId: $ideaId) {
    success message field
    review { id round reviewerId decision completedAt submissionSnapshot }
    idea { id status viewerCanStartReview viewerActiveReviewId availableTransitions }
  }
}
"""

COMPLETE = """
mutation Complete($input: CompleteReviewInput!) {
  completeReview(input: $input) {
    success message field
    review { id decision feedback completedAt assessments { criterion rating note } }
    idea { id status viewerActiveReviewId }
  }
}
"""

TRANSITION = """
mutation Transition($id: ID!, $to: IdeaStatus!) {
  transitionIdea(id: $id, to: $to) { success message }
}
"""

CRITERIA = [
    'PROBLEM_CLARITY',
    'AUTOMATION_SUITABILITY',
    'FEASIBILITY',
    'EXPECTED_BENEFIT',
    'EVIDENCE',
]


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password=VALID_PASSWORD,
    )


def add_member(organization, user, *, reviewer=False):
    membership = Membership.objects.create(user=user, organization=organization)
    if reviewer:
        MembershipRole.objects.create(
            membership=membership,
            role=Role.objects.get(organization=organization, slug=REVIEWER_ROLE_SLUG),
        )


def make_idea(organization, author, *, status=Idea.Status.SUBMITTED, visibility=None):
    return Idea.objects.create(
        organization=organization,
        author=author,
        title='Automate the invoice run',
        description='A description long enough to be usable.',
        category=Category.objects.create(name=f'Cat {Category.objects.count() + 1}'),
        visibility=visibility or Idea.Visibility.ORGANIZATION,
        status=status,
        submitted_at=timezone.now(),
    )


@pytest.fixture
def world():
    owner = make_user('owner@acme.example')
    acme = create_organization_for_user(owner, CreateOrganizationInput(name='Acme')).organization
    author = make_user('author@acme.example')
    add_member(acme, author)
    reviewer = make_user('reviewer@acme.example')
    add_member(acme, reviewer, reviewer=True)
    second = make_user('second@acme.example')
    add_member(acme, second, reviewer=True)
    member = make_user('member@acme.example')
    add_member(acme, member)
    globex_owner = make_user('owner@globex.example')
    create_organization_for_user(globex_owner, CreateOrganizationInput(name='Globex'))
    return {
        'acme': acme,
        'author': author,
        'reviewer': reviewer,
        'second': second,
        'member': member,
        'globex_owner': globex_owner,
        'idea': make_idea(acme, author),
    }


@pytest.fixture
def gql(client):
    def post(query, variables=None, user=None):
        headers = {}
        if user is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {issue_access_token(user.pk)[0]}'
        response = client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables or {}}),
            content_type='application/json',
            **headers,
        )
        assert response.status_code == 200, response.content
        return response.json()

    return post


def ok(body, field):
    assert 'errors' not in body, body
    return body['data'][field]


def complete_input(idea_id, review_id, *, decision='APPROVED', feedback='Worth it.', **kwargs):
    ratings = kwargs.get('ratings', dict.fromkeys(CRITERIA, 'MEETS'))
    return {
        'input': {
            'ideaId': str(idea_id),
            'reviewId': str(review_id),
            'decision': decision,
            'feedback': feedback,
            'assessments': [
                {'criterion': criterion, 'rating': rating, 'note': f'on {criterion}'}
                for criterion, rating in ratings.items()
            ],
        }
    }


@pytest.fixture
def started(gql, world):
    payload = ok(gql(START, {'ideaId': world['idea'].pk}, world['reviewer']), 'startReview')
    assert payload['success'] is True, payload
    return payload


@pytest.mark.django_db
class TestStartReview:
    def test_a_reviewer_starts_and_the_client_can_reconcile(self, gql, world):
        payload = ok(gql(START, {'ideaId': world['idea'].pk}, world['reviewer']), 'startReview')

        assert payload['success'] is True
        assert payload['review']['round'] == 1
        assert payload['review']['reviewerId'] == str(world['reviewer'].pk)
        assert payload['review']['decision'] is None
        assert payload['review']['submissionSnapshot']['title'] == 'Automate the invoice run'
        assert payload['idea'] == {
            'id': str(world['idea'].pk),
            'status': 'UNDER_REVIEW',
            'viewerCanStartReview': False,
            'viewerActiveReviewId': payload['review']['id'],
            'availableTransitions': [],
        }

    def test_unauthenticated(self, gql, world):
        payload = ok(gql(START, {'ideaId': world['idea'].pk}), 'startReview')

        assert payload == {
            'success': False,
            'message': 'You must be signed in to review ideas.',
            'field': None,
            'review': None,
            'idea': None,
        }

    @pytest.mark.parametrize('who', ['member', 'author'])
    def test_readers_who_are_not_reviewers(self, gql, world, who):
        payload = ok(gql(START, {'ideaId': world['idea'].pk}, world[who]), 'startReview')

        assert payload['success'] is False
        assert payload['message'] == 'You are not allowed to review this idea.'

    def test_an_inactive_member(self, gql, world):
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)

        payload = ok(gql(START, {'ideaId': world['idea'].pk}, world['reviewer']), 'startReview')

        assert payload['success'] is False

    def test_wrong_organization_and_guessed_ids_answer_alike(self, gql, world):
        cross = ok(gql(START, {'ideaId': world['idea'].pk}, world['globex_owner']), 'startReview')
        missing = ok(gql(START, {'ideaId': 999_999}, world['globex_owner']), 'startReview')

        assert cross == missing
        assert cross['message'] == 'Idea is unavailable.'

    def test_a_public_idea_is_still_not_another_organizations_to_review(self, gql, world):
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        payload = ok(gql(START, {'ideaId': idea.pk}, world['globex_owner']), 'startReview')

        assert payload['message'] == 'You are not allowed to review this idea.'
        assert not Review.objects.filter(idea=idea).exists()

    def test_an_idea_already_under_review(self, gql, world, started):
        payload = ok(gql(START, {'ideaId': world['idea'].pk}, world['second']), 'startReview')

        assert payload['success'] is False
        assert payload['message'] == 'This idea is not waiting for review.'
        assert Review.objects.filter(idea=world['idea']).count() == 1

    def test_transition_idea_cannot_be_used_instead(self, gql, world):
        payload = ok(
            gql(TRANSITION, {'id': world['idea'].pk, 'to': 'UNDER_REVIEW'}, world['reviewer']),
            'transitionIdea',
        )

        assert payload['success'] is False
        assert not Review.objects.exists()


@pytest.mark.django_db
class TestCompleteReview:
    def test_a_reviewer_completes_their_review(self, gql, world, started):
        payload = ok(
            gql(
                COMPLETE,
                complete_input(world['idea'].pk, started['review']['id'], decision='REJECTED'),
                world['reviewer'],
            ),
            'completeReview',
        )

        assert payload['success'] is True
        assert payload['message'] == 'Review completed: Rejected.'
        assert payload['review']['decision'] == 'REJECTED'
        assert payload['review']['completedAt'] is not None
        assert len(payload['review']['assessments']) == 5
        assert payload['idea'] == {
            'id': str(world['idea'].pk),
            'status': 'REJECTED',
            'viewerActiveReviewId': None,
        }

    def test_unauthenticated(self, gql, world, started):
        payload = ok(
            gql(COMPLETE, complete_input(world['idea'].pk, started['review']['id'])),
            'completeReview',
        )

        assert payload['success'] is False
        assert payload['message'] == 'You must be signed in to review ideas.'

    def test_another_reviewer(self, gql, world, started):
        payload = ok(
            gql(
                COMPLETE,
                complete_input(world['idea'].pk, started['review']['id']),
                world['second'],
            ),
            'completeReview',
        )

        assert payload['success'] is False
        assert payload['message'] == 'Review is unavailable.'

    def test_cross_tenant(self, gql, world, started):
        payload = ok(
            gql(
                COMPLETE,
                complete_input(world['idea'].pk, started['review']['id']),
                world['globex_owner'],
            ),
            'completeReview',
        )

        assert payload['message'] == 'Idea is unavailable.'

    def test_an_already_completed_review(self, gql, world, started):
        variables = complete_input(world['idea'].pk, started['review']['id'])
        assert ok(gql(COMPLETE, variables, world['reviewer']), 'completeReview')['success']

        again = ok(gql(COMPLETE, variables, world['reviewer']), 'completeReview')

        assert again['success'] is False
        assert again['message'] == 'This review has already been completed.'

    @pytest.mark.parametrize('decision', ['CHANGES_REQUESTED', 'REJECTED'])
    def test_missing_required_feedback(self, gql, world, started, decision):
        payload = ok(
            gql(
                COMPLETE,
                complete_input(
                    world['idea'].pk, started['review']['id'], decision=decision, feedback=''
                ),
                world['reviewer'],
            ),
            'completeReview',
        )

        assert payload['success'] is False
        assert payload['field'] == 'feedback'

    def test_a_missing_criterion(self, gql, world, started):
        ratings = dict.fromkeys(CRITERIA[:-1], 'MEETS')

        payload = ok(
            gql(
                COMPLETE,
                complete_input(world['idea'].pk, started['review']['id'], ratings=ratings),
                world['reviewer'],
            ),
            'completeReview',
        )

        assert payload['success'] is False
        assert payload['field'] == 'assessments'

    @pytest.mark.parametrize(
        'variables',
        [
            {'decision': 'PROMOTED'},
            {'ratings': {**dict.fromkeys(CRITERIA, 'MEETS'), 'IMPACT': 'MEETS'}},
            {'ratings': {**dict.fromkeys(CRITERIA, 'MEETS'), 'EVIDENCE': 'FIVE_STARS'}},
        ],
        ids=['invalid-decision', 'invalid-criterion', 'invalid-rating'],
    )
    def test_invalid_enum_values_never_reach_the_service(self, gql, world, started, variables):
        body = gql(
            COMPLETE,
            complete_input(world['idea'].pk, started['review']['id'], **variables),
            world['reviewer'],
        )

        assert 'errors' in body
        assert Review.objects.get(pk=started['review']['id']).completed_at is None
