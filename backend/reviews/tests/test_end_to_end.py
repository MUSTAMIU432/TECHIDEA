"""
The Sprint 3 review workflow end to end, over real HTTP (S3-008).

Every step goes through the endpoints a client uses - `register` and `login`
for accounts, the Django admin for memberships, the GraphQL API for everything
else - and nothing is written to the database by the test, so a wiring
mistake between the domains (a membership the lifecycle does not see, a role
the queue ignores, an audit row a mutation forgets) fails here even when every
unit suite passes.

How a second person joins an organization (`docs/reviews-domain.md` D-1):
there is deliberately no self-service invitation. Staff add the membership in
the Django admin, and the organization's owner then grants the Reviewer role
with `assignRoleToMembership`. That is the path exercised below.

The four flows: a new idea to approval (with the decision email and the
lifecycle history), changes requested to a second round, rejection, and every
operation refused to everybody who is not the reviewer or the author.
"""

import json
import re

import pytest
from django.core import mail
from django.test import Client

from ideas.models import Category, Idea, IdeaTransition
from identity.models import User
from organizations.models import Membership
from reviews.models import Review, ReviewCriterionAssessment
from reviews.notifications import REVIEW_DECISION_SUBJECT
from reviews.tests.invariants import assert_review_records_consistent

PASSWORD = 'a-strong-unique-pass-1'
CRITERIA = [c.upper() for c in ReviewCriterionAssessment.Criterion.values]

REGISTER = """
mutation Register($input: RegisterInput!) { register(input: $input) { success message } }
"""
LOGIN = """
mutation Login($input: LoginInput!) { login(input: $input) { success accessToken user { id } } }
"""
CREATE_ORGANIZATION = """
mutation Create($input: CreateOrganizationInput!) {
  createOrganization(input: $input) { success organization { id } }
}
"""
MEMBERS = """
query Members($organizationId: ID!) {
  organizationMembers(organizationId: $organizationId) { id user { id } }
}
"""
ROLES = """
query Roles($organizationId: ID!) { organizationRoles(organizationId: $organizationId) { id slug } }
"""
ASSIGN_ROLE = """
mutation Assign($input: MembershipRoleInput!) {
  assignRoleToMembership(input: $input) { success message }
}
"""
REMOVE_ROLE = """
mutation Remove($input: MembershipRoleInput!) {
  removeRoleFromMembership(input: $input) { success message }
}
"""
CATEGORIES = 'query { categories { id } }'
CREATE_IDEA = """
mutation Create($input: CreateIdeaInput!) {
  createIdea(input: $input) { success message idea { id status visibility } }
}
"""
UPDATE_IDEA = """
mutation Update($input: UpdateIdeaInput!) {
  updateIdea(input: $input) { success message field idea { status visibility description } }
}
"""
SUBMIT_IDEA = """
mutation Submit($id: ID!) { submitIdea(id: $id) { success message idea { status } } }
"""
TRANSITION_IDEA = """
mutation Transition($id: ID!, $to: IdeaStatus!) {
  transitionIdea(id: $id, to: $to) { success message }
}
"""
IDEA = """
query Idea($id: ID!) {
  idea(id: $id) {
    status availableTransitions discussionOpen viewerCanStartReview viewerActiveReviewId
  }
}
"""
QUEUE = """
query Queue($organizationId: ID!) {
  reviewQueue(organizationId: $organizationId) { items { id } pageInfo { totalCount } }
}
"""
CAN_REVIEW = """
query CanReview($organizationId: ID!) { viewerCanReviewIn(organizationId: $organizationId) }
"""
REVIEWS = """
query Reviews($ideaId: ID!) {
  ideaReviews(ideaId: $ideaId) {
    id round reviewerId decision feedback completedAt submissionSnapshot
    assessments { criterion rating note }
  }
}
"""
TRANSITIONS = """
query History($ideaId: ID!) {
  ideaTransitions(ideaId: $ideaId) { fromStatus toStatus actorId }
}
"""
START = """
mutation Start($ideaId: ID!) {
  startReview(ideaId: $ideaId) { success message review { id round } idea { status } }
}
"""
COMPLETE = """
mutation Complete($input: CompleteReviewInput!) {
  completeReview(input: $input) { success message field review { decision } idea { status } }
}
"""


class Api:
    """A GraphQL caller over the real endpoint, one bearer token per person."""

    def __init__(self, client: Client):
        self.client = client
        self.tokens: dict[str, str] = {}
        self.ids: dict[str, str] = {}

    def run(self, query, variables=None, as_=None):
        headers = {}
        if as_ is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {self.tokens[as_]}'
        response = self.client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables or {}}),
            content_type='application/json',
            **headers,
        )
        assert response.status_code == 200, response.content
        body = response.json()
        # Refusals are payloads in this project, never an `errors` array.
        assert 'errors' not in body, body
        return body['data']

    def sign_up(self, name):
        email = f'{name}@example.com'
        registered = self.run(
            REGISTER,
            {
                'input': {
                    'firstName': name.title(),
                    'lastName': 'Tester',
                    'email': email,
                    'phoneNumber': '+255712345678',
                    'password': PASSWORD,
                }
            },
        )['register']
        assert registered['success'], registered
        login = self.run(LOGIN, {'input': {'email': email, 'password': PASSWORD}})['login']
        assert login['success'], login
        self.tokens[name] = login['accessToken']
        self.ids[name] = login['user']['id']


def staff_adds_member(staff: Client, user_id, organization_id):
    """The D-1 path: a membership added through the Django admin's own form."""
    response = staff.post(
        '/admin/organizations/membership/add/',
        {'user': user_id, 'organization': organization_id, 'status': 'active', '_save': 'Save'},
    )
    assert response.status_code == 302, response.content.decode()[:2000]


def staff_deactivates_member(staff: Client, user_id, organization_id):
    membership = Membership.objects.get(user_id=user_id, organization_id=organization_id)
    response = staff.post(
        f'/admin/organizations/membership/{membership.pk}/change/',
        {'user': user_id, 'organization': organization_id, 'status': 'inactive', '_save': 'Save'},
    )
    assert response.status_code == 302, response.content.decode()[:2000]


@pytest.fixture
def staff(db):
    User.objects.create_superuser(
        email='staff@example.com',
        first_name='Staff',
        last_name='User',
        phone_number='+255712345678',
        password=PASSWORD,
    )
    staff_client = Client()
    staff_client.force_login(User.objects.get(email='staff@example.com'))
    return staff_client


@pytest.fixture
def platform(db, client, staff, category):
    """
    Two organizations built only through the product's own entry points:
    Acme (owner, author, reviewer, second reviewer, plain member) and Globex
    (its owner, who reviews there).
    """
    api = Api(client)
    for name in ('owner', 'author', 'reviewer', 'second', 'member', 'globex'):
        api.sign_up(name)

    acme = api.run(CREATE_ORGANIZATION, {'input': {'name': 'Acme'}}, 'owner')
    acme_id = acme['createOrganization']['organization']['id']
    globex = api.run(CREATE_ORGANIZATION, {'input': {'name': 'Globex'}}, 'globex')
    globex_id = globex['createOrganization']['organization']['id']

    for name in ('author', 'reviewer', 'second', 'member'):
        staff_adds_member(staff, api.ids[name], acme_id)

    roles = api.run(ROLES, {'organizationId': acme_id}, 'owner')['organizationRoles']
    reviewer_role = next(role['id'] for role in roles if role['slug'] == 'reviewer')
    members = api.run(MEMBERS, {'organizationId': acme_id}, 'owner')['organizationMembers']
    membership = {m['user']['id']: m['id'] for m in members}
    for name in ('reviewer', 'second'):
        assigned = api.run(
            ASSIGN_ROLE,
            {'input': {'membershipId': membership[api.ids[name]], 'roleId': reviewer_role}},
            'owner',
        )['assignRoleToMembership']
        assert assigned['success'], assigned

    category_id = api.run(CATEGORIES)['categories'][0]['id']
    api.acme_id, api.globex_id = acme_id, globex_id
    api.reviewer_role = reviewer_role
    api.membership = membership
    api.category_id = category_id
    api.staff = staff
    return api


@pytest.fixture
def category(db):
    # Categories are platform reference data, managed by staff; there is no
    # API that creates one.
    return Category.objects.create(name='Finance')


def draft_idea(
    api, *, visibility=None, description='We key every invoice in by hand, every month.'
):
    idea_input = {
        'title': 'Automate the invoice run',
        'description': description,
        'categoryId': api.category_id,
    }
    if visibility:
        idea_input['visibility'] = visibility
    created = api.run(
        CREATE_IDEA, {'input': {'organizationId': api.acme_id, 'idea': idea_input}}, 'author'
    )['createIdea']
    assert created['success'], created
    return created['idea']['id']


def submitted_idea(api):
    idea_id = draft_idea(api, visibility='ORGANIZATION')
    assert api.run(SUBMIT_IDEA, {'id': idea_id}, 'author')['submitIdea']['success']
    return idea_id


def complete_input(idea_id, review_id, decision, feedback='', rating='MEETS'):
    return {
        'input': {
            'ideaId': idea_id,
            'reviewId': review_id,
            'decision': decision,
            'feedback': feedback,
            'assessments': [
                {'criterion': c, 'rating': rating, 'note': f'On {c.lower()}.'} for c in CRITERIA
            ],
        }
    }


def start_and_decide(api, idea_id, decision, feedback='', as_='reviewer'):
    started = api.run(START, {'ideaId': idea_id}, as_)['startReview']
    assert started['success'], started
    completed = api.run(
        COMPLETE, complete_input(idea_id, started['review']['id'], decision, feedback), as_
    )['completeReview']
    assert completed['success'], completed
    return started['review']


def decision_emails():
    # Registration sends activation emails too; only the review's are counted.
    return [m for m in mail.outbox if m.subject == REVIEW_DECISION_SUBJECT]


def history(api, idea_id, as_='author'):
    return [
        (row['fromStatus'], row['toStatus'], row['actorId'])
        for row in api.run(TRANSITIONS, {'ideaId': idea_id}, as_)['ideaTransitions']
    ]


# --- D-1: joining an organization and becoming a reviewer ------------------------------


class TestMembership:
    def test_staff_added_members_become_reviewers_through_the_owners_role_grant(self, platform):
        api = platform
        for name, expected in (
            ('owner', True),
            ('reviewer', True),
            ('second', True),
            ('author', False),
            ('member', False),
            ('globex', False),
        ):
            answer = api.run(CAN_REVIEW, {'organizationId': api.acme_id}, name)
            assert answer['viewerCanReviewIn'] is expected, name

    def test_only_the_owner_grants_the_role(self, platform):
        api = platform
        for name in ('reviewer', 'member', 'globex'):
            refused = api.run(
                ASSIGN_ROLE,
                {
                    'input': {
                        'membershipId': api.membership[api.ids['member']],
                        'roleId': api.reviewer_role,
                    }
                },
                name,
            )['assignRoleToMembership']
            assert refused['success'] is False, name
        assert api.run(CAN_REVIEW, {'organizationId': api.acme_id}, 'member') == {
            'viewerCanReviewIn': False
        }


# --- flow 1: new idea to approval ----------------------------------------------------


def test_flow_new_idea_to_approval(platform, django_capture_on_commit_callbacks):
    api = platform

    # Draft, saved with the default visibility: private, and not yet submittable.
    idea_id = draft_idea(api)
    refused = api.run(SUBMIT_IDEA, {'id': idea_id}, 'author')['submitIdea']
    assert refused['success'] is False
    assert 'private idea cannot be reviewed' in refused['message']

    # Save the draft with a reviewable visibility, then submit.
    saved = api.run(
        UPDATE_IDEA,
        {
            'input': {
                'id': idea_id,
                'idea': {
                    'title': 'Automate the invoice run',
                    'description': 'We key every invoice in by hand, every month.',
                    'categoryId': api.category_id,
                    'visibility': 'ORGANIZATION',
                },
            }
        },
        'author',
    )['updateIdea']
    assert saved['idea'] == {
        'status': 'DRAFT',
        'visibility': 'ORGANIZATION',
        'description': 'We key every invoice in by hand, every month.',
    }
    assert api.run(SUBMIT_IDEA, {'id': idea_id}, 'author')['submitIdea']['idea'] == {
        'status': 'SUBMITTED'
    }

    # The reviewer finds it in the queue and is offered the start.
    queue = api.run(QUEUE, {'organizationId': api.acme_id}, 'reviewer')['reviewQueue']
    assert [item['id'] for item in queue['items']] == [idea_id]
    assert api.run(IDEA, {'id': idea_id}, 'reviewer')['idea']['viewerCanStartReview'] is True

    started = api.run(START, {'ideaId': idea_id}, 'reviewer')['startReview']
    assert started['idea'] == {'status': 'UNDER_REVIEW'}
    assert api.run(QUEUE, {'organizationId': api.acme_id}, 'reviewer')['reviewQueue']['items'] == []
    # The author sees nothing of a review still being written.
    assert api.run(REVIEWS, {'ideaId': idea_id}, 'author')['ideaReviews'] == []

    with django_capture_on_commit_callbacks(execute=True):
        completed = api.run(
            COMPLETE,
            complete_input(idea_id, started['review']['id'], 'APPROVED', 'Clear win.'),
            'reviewer',
        )['completeReview']
    assert completed['review'] == {'decision': 'APPROVED'}
    assert completed['idea'] == {'status': 'APPROVED'}

    # The author is told, once, without the feedback in the email.
    (email,) = decision_emails()
    assert email.to == ['author@example.com']
    assert 'Approved' in email.body
    assert 'Clear win.' not in email.body

    # The author reads the decision, the feedback and all five criteria.
    (review,) = api.run(REVIEWS, {'ideaId': idea_id}, 'author')['ideaReviews']
    assert (review['round'], review['decision'], review['feedback']) == (
        1,
        'APPROVED',
        'Clear win.',
    )
    assert review['reviewerId'] == api.ids['reviewer']
    assert review['submissionSnapshot'] is None
    assert sorted(a['criterion'] for a in review['assessments']) == sorted(CRITERIA)

    assert history(api, idea_id) == [
        ('DRAFT', 'SUBMITTED', api.ids['author']),
        ('SUBMITTED', 'UNDER_REVIEW', api.ids['reviewer']),
        ('UNDER_REVIEW', 'APPROVED', api.ids['reviewer']),
    ]
    assert history(api, idea_id, 'second') == history(api, idea_id)
    assert_review_records_consistent(Idea.objects.get(pk=idea_id))


# --- flow 2: changes requested, revision, a second round ------------------------------


def test_flow_changes_requested_to_a_second_round(platform, django_capture_on_commit_callbacks):
    api = platform
    idea_id = submitted_idea(api)
    with django_capture_on_commit_callbacks(execute=True):
        first = start_and_decide(
            api, idea_id, 'CHANGES_REQUESTED', 'Say how many invoices a month.'
        )

    # The author sees the feedback and is offered the resubmission.
    (round_one,) = api.run(REVIEWS, {'ideaId': idea_id}, 'author')['ideaReviews']
    assert (round_one['decision'], round_one['feedback']) == (
        'CHANGES_REQUESTED',
        'Say how many invoices a month.',
    )
    assert api.run(IDEA, {'id': idea_id}, 'author')['idea']['availableTransitions'] == ['SUBMITTED']

    # Edit and save, then resubmit explicitly: saving alone does not resubmit.
    revised = 'We key 1,200 invoices in by hand every month, which takes four days.'
    saved = api.run(
        UPDATE_IDEA,
        {
            'input': {
                'id': idea_id,
                'idea': {
                    'title': 'Automate the invoice run',
                    'description': revised,
                    'categoryId': api.category_id,
                },
            }
        },
        'author',
    )['updateIdea']
    assert saved['idea']['status'] == 'CHANGES_REQUESTED'
    reviews_before_resubmission = Review.objects.filter(idea_id=idea_id).count()

    assert api.run(SUBMIT_IDEA, {'id': idea_id}, 'author')['submitIdea']['idea'] == {
        'status': 'SUBMITTED'
    }
    # Resubmitting creates no review: only a reviewer's start does.
    assert Review.objects.filter(idea_id=idea_id).count() == reviews_before_resubmission == 1

    with django_capture_on_commit_callbacks(execute=True):
        second = start_and_decide(api, idea_id, 'APPROVED', as_='second')

    rounds = api.run(REVIEWS, {'ideaId': idea_id}, 'second')['ideaReviews']
    assert [(r['round'], r['decision']) for r in rounds] == [
        (1, 'CHANGES_REQUESTED'),
        (2, 'APPROVED'),
    ]
    assert second['id'] != first['id']
    # Round one is exactly as it was, and each round reviewed its own content.
    assert {k: rounds[0][k] for k in round_one if k != 'submissionSnapshot'} == {
        k: round_one[k] for k in round_one if k != 'submissionSnapshot'
    }
    assert rounds[0]['submissionSnapshot']['description'] != revised
    assert rounds[1]['submissionSnapshot']['description'] == revised

    assert history(api, idea_id) == [
        ('DRAFT', 'SUBMITTED', api.ids['author']),
        ('SUBMITTED', 'UNDER_REVIEW', api.ids['reviewer']),
        ('UNDER_REVIEW', 'CHANGES_REQUESTED', api.ids['reviewer']),
        ('CHANGES_REQUESTED', 'SUBMITTED', api.ids['author']),
        ('SUBMITTED', 'UNDER_REVIEW', api.ids['second']),
        ('UNDER_REVIEW', 'APPROVED', api.ids['second']),
    ]
    # One per decision: the request for changes, then the approval.
    assert [m.to for m in decision_emails()] == [['author@example.com']] * 2
    assert_review_records_consistent(Idea.objects.get(pk=idea_id))


def test_there_is_no_client_side_way_to_create_or_rewrite_a_review(platform):
    api = platform
    for mutation in ('createReview', 'updateReview', 'deleteReview', 'createIdeaTransition'):
        response = api.client.post(
            '/graphql/',
            data=json.dumps({'query': f'mutation {{ {mutation}(id: "1") {{ success }} }}'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {api.tokens["owner"]}',
        )
        assert re.search(
            f"Cannot query field '{mutation}'", response.json()['errors'][0]['message']
        )


# --- flow 3: rejection ---------------------------------------------------------------


def test_flow_rejection_is_terminal(platform):
    api = platform
    idea_id = submitted_idea(api)
    start_and_decide(api, idea_id, 'REJECTED', 'Already covered by the ERP project.')

    for name in ('author', 'reviewer', 'second', 'owner'):
        idea = api.run(IDEA, {'id': idea_id}, name)['idea']
        assert idea['status'] == 'REJECTED'
        assert idea['availableTransitions'] == [], name
        assert idea['discussionOpen'] is False
        assert idea['viewerCanStartReview'] is False

    assert api.run(SUBMIT_IDEA, {'id': idea_id}, 'author')['submitIdea']['success'] is False
    assert api.run(START, {'ideaId': idea_id}, 'second')['startReview']['success'] is False
    for target in ('SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'AUTOMATION_PROPOSAL'):
        for name in ('author', 'reviewer'):
            moved = api.run(TRANSITION_IDEA, {'id': idea_id, 'to': target}, name)
            assert moved['transitionIdea']['success'] is False

    assert history(api, idea_id)[-1] == ('UNDER_REVIEW', 'REJECTED', api.ids['reviewer'])
    assert len(history(api, idea_id)) == 3
    assert_review_records_consistent(Idea.objects.get(pk=idea_id))


# --- flow 4: everybody else is refused ------------------------------------------------


def _refused_everywhere(api, name, idea_id, review_id):
    """Every review operation, as `name` (or anonymously when None)."""
    assert api.run(QUEUE, {'organizationId': api.acme_id}, name)['reviewQueue']['items'] == []
    assert api.run(CAN_REVIEW, {'organizationId': api.acme_id}, name) == {
        'viewerCanReviewIn': False
    }
    assert api.run(REVIEWS, {'ideaId': idea_id}, name)['ideaReviews'] == []
    assert api.run(TRANSITIONS, {'ideaId': idea_id}, name)['ideaTransitions'] == []
    assert api.run(START, {'ideaId': idea_id}, name)['startReview']['success'] is False
    completed = api.run(COMPLETE, complete_input(idea_id, review_id, 'APPROVED'), name)
    assert completed['completeReview']['success'] is False
    for target in ('UNDER_REVIEW', 'APPROVED', 'REJECTED', 'CHANGES_REQUESTED'):
        moved = api.run(TRANSITION_IDEA, {'id': idea_id, 'to': target}, name)
        assert moved['transitionIdea']['success'] is False
    idea = api.run(IDEA, {'id': idea_id}, name)['idea']
    if idea is not None:
        assert (idea['viewerCanStartReview'], idea['viewerActiveReviewId']) == (False, None)


@pytest.mark.parametrize('visibility', ['ORGANIZATION', 'PUBLIC'])
def test_flow_authorization_failures(platform, visibility):
    """
    With a review open on an idea, nobody but its reviewer can act on it, and
    nobody but the author and the organization's reviewers can read its
    history - whatever the idea's visibility.
    """
    api = platform
    idea_id = draft_idea(api, visibility=visibility)
    api.run(SUBMIT_IDEA, {'id': idea_id}, 'author')
    started = api.run(START, {'ideaId': idea_id}, 'reviewer')['startReview']
    review_id = started['review']['id']
    snapshot = (
        list(IdeaTransition.objects.filter(idea_id=idea_id).values_list('pk', flat=True)),
        list(Review.objects.filter(idea_id=idea_id).values('pk', 'completed_at', 'reviewer_id')),
    )

    # Another organization, and an ordinary member of this one.
    for name in ('globex', 'member'):
        _refused_everywhere(api, name, idea_id, review_id)

    # Anonymous: no token at all.
    anonymous = api.run(START, {'ideaId': idea_id})['startReview']
    assert anonymous['success'] is False
    assert api.run(REVIEWS, {'ideaId': idea_id})['ideaReviews'] == []
    assert api.run(TRANSITIONS, {'ideaId': idea_id})['ideaTransitions'] == []
    assert (
        api.run(COMPLETE, complete_input(idea_id, review_id, 'APPROVED'))['completeReview'][
            'success'
        ]
        is False
    )

    # The author: reads their history, but can never review their own idea.
    assert api.run(START, {'ideaId': idea_id}, 'author')['startReview']['success'] is False
    assert (
        api.run(COMPLETE, complete_input(idea_id, review_id, 'APPROVED'), 'author')[
            'completeReview'
        ]['success']
        is False
    )

    # Another eligible reviewer cannot complete or take over an active review.
    assert api.run(START, {'ideaId': idea_id}, 'second')['startReview']['success'] is False
    assert (
        api.run(COMPLETE, complete_input(idea_id, review_id, 'APPROVED'), 'second')[
            'completeReview'
        ]['success']
        is False
    )

    # A reviewer whose role the owner removed.
    removed = api.run(
        REMOVE_ROLE,
        {
            'input': {
                'membershipId': api.membership[api.ids['second']],
                'roleId': api.reviewer_role,
            }
        },
        'owner',
    )['removeRoleFromMembership']
    assert removed['success'], removed
    _refused_everywhere(api, 'second', idea_id, review_id)

    assert snapshot == (
        list(IdeaTransition.objects.filter(idea_id=idea_id).values_list('pk', flat=True)),
        list(Review.objects.filter(idea_id=idea_id).values('pk', 'completed_at', 'reviewer_id')),
    )

    # Finally the reviewer's own membership is deactivated by staff: they can no
    # longer complete, and their review becomes one another reviewer may take.
    staff_deactivates_member(api.staff, api.ids['reviewer'], api.acme_id)
    _refused_everywhere(api, 'reviewer', idea_id, review_id)
    assert snapshot[1] == list(
        Review.objects.filter(idea_id=idea_id).values('pk', 'completed_at', 'reviewer_id')
    )
    assert api.run(IDEA, {'id': idea_id}, 'owner')['idea']['viewerCanStartReview'] is True
    taken = api.run(START, {'ideaId': idea_id}, 'owner')['startReview']
    assert taken['success'] is True
    assert taken['review']['round'] == 2
    assert_review_records_consistent(Idea.objects.get(pk=idea_id))
