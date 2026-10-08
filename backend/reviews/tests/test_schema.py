"""
Reviews at the GraphQL boundary (S3-003).

The selectors decide; these tests check that the boundary neither widens nor
narrows them, and pin the properties that only exist at this layer:

- anonymous requests get the same empty answers as unauthorized ones, never a
  crash and never a distinguishing error;
- there is no way to address a review by its own id, and no review mutation
  exists yet;
- `submissionSnapshot` and in-progress rounds reach reviewers only;
- `viewerCanStartReview` / `viewerActiveReviewId` on `IdeaType` are per viewer.
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
from reviews.tests.platform import (
    build_idea,
    grant_platform_reviewer,
    revoke_platform_reviewer,
)

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'

QUEUE = """
query Queue($organizationId: ID!, $offset: Int, $limit: Int) {
  reviewQueue(organizationId: $organizationId, offset: $offset, limit: $limit) {
    items {
      id
      status
      authorId
      viewerCanStartReview
      viewerCanStartOrganizationReview
      viewerActiveReviewId
    }
    pageInfo { offset limit totalCount hasNextPage hasPreviousPage }
  }
}
"""

HISTORY = """
query History($ideaId: ID!) {
  ideaReviews(ideaId: $ideaId) {
    id ideaId round reviewerId decision feedback createdAt completedAt
    submissionSnapshot
    assessments { criterion rating note }
  }
}
"""

CAN_REVIEW = """
query CanReview($organizationId: ID!) {
  viewerCanReviewIn(organizationId: $organizationId)
}
"""

IDEA = """
query Idea($id: ID!) {
  idea(id: $id) {
    id
    viewerCanStartReview
    viewerCanStartOrganizationReview
    viewerActiveReviewId
  }
}
"""


def fresh(user):
    """`user` again from the database, so its permission cache is empty."""
    return User.objects.get(pk=user.pk)


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
        # Platform review is authorized by a platform-scoped permission and by
        # nothing else, so a reviewer built here is a *platform* reviewer too.
        grant_platform_reviewer(user)


def make_idea(
    organization, author, *, status=Idea.Status.SUBMITTED_TO_ORGANIZATION, visibility=None
):
    """
    An idea in `status`, defaulted to the one `reviewQueue` holds.

    `reviewQueue` is the **organization** queue, so `SUBMITTED_TO_ORGANIZATION` is
    what "waiting" means here. The platform queue is a separate field with its own
    tests.
    """
    return build_idea(
        status=status,
        organization=organization,
        author=author,
        title='An idea',
        description=DESCRIPTION,
        category=Category.objects.create(name=f'Cat {Category.objects.count() + 1}'),
        visibility=visibility or Idea.Visibility.ORGANIZATION,
    )


@pytest.fixture
def world():
    acme_owner = make_user('owner@acme.example')
    acme = create_organization_for_user(acme_owner, CreateOrganizationInput(name='Acme'))
    author = make_user('author@acme.example')
    add_member(acme.organization, author)
    reviewer = make_user('reviewer@acme.example')
    add_member(acme.organization, reviewer, reviewer=True)
    member = make_user('member@acme.example')
    add_member(acme.organization, member)
    globex_owner = make_user('owner@globex.example')
    globex = create_organization_for_user(globex_owner, CreateOrganizationInput(name='Globex'))
    globex_reviewer = make_user('reviewer@globex.example')
    add_member(globex.organization, globex_reviewer, reviewer=True)
    return {
        'acme': acme.organization,
        'globex': globex.organization,
        'acme_owner': acme_owner,
        'author': author,
        'reviewer': reviewer,
        'member': member,
        'globex_owner': globex_owner,
        'globex_reviewer': globex_reviewer,
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


def data(body):
    assert 'errors' not in body, body
    return body['data']


@pytest.mark.django_db
class TestReviewQueue:
    def test_a_reviewer_sees_the_queue_with_capabilities(self, gql, world):
        """
        The organization's queue, with the organization's capability flag.

        `viewerCanStartOrganizationReview` is the one that matters here;
        `viewerCanStartReview` is the **platform** flag and is false - holding
        `idea.review` in one organization says nothing about the platform track.
        """
        idea = make_idea(world['acme'], world['author'])

        queue = data(gql(QUEUE, {'organizationId': world['acme'].pk}, world['reviewer']))[
            'reviewQueue'
        ]

        assert queue['items'] == [
            {
                'id': str(idea.pk),
                'status': 'SUBMITTED_TO_ORGANIZATION',
                'authorId': str(world['author'].pk),
                'viewerCanStartOrganizationReview': True,
                'viewerCanStartReview': False,
                'viewerActiveReviewId': None,
            }
        ]
        assert queue['pageInfo']['totalCount'] == 1

    @pytest.mark.parametrize('who', [None, 'member', 'author', 'globex_reviewer', 'globex_owner'])
    def test_everybody_else_gets_an_empty_page_not_an_error(self, gql, world, who):
        make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        queue = data(gql(QUEUE, {'organizationId': world['acme'].pk}, world[who] if who else None))[
            'reviewQueue'
        ]

        assert queue['items'] == []
        assert queue['pageInfo']['totalCount'] == 0

    def test_pagination_arguments_are_applied_and_clamped(self, gql, world):
        for _ in range(3):
            make_idea(world['acme'], world['author'])

        page = data(
            gql(
                QUEUE,
                {'organizationId': world['acme'].pk, 'offset': 1, 'limit': 1},
                world['reviewer'],
            )
        )['reviewQueue']['pageInfo']
        assert page == {
            'offset': 1,
            'limit': 1,
            'totalCount': 3,
            'hasNextPage': True,
            'hasPreviousPage': True,
        }

        clamped = data(
            gql(QUEUE, {'organizationId': world['acme'].pk, 'limit': 9999}, world['reviewer'])
        )['reviewQueue']['pageInfo']
        assert clamped['limit'] == 50

    def test_a_garbage_organization_id_is_an_empty_page(self, gql, world):
        queue = data(gql(QUEUE, {'organizationId': 'nope'}, world['reviewer']))['reviewQueue']

        assert queue['items'] == []


@pytest.mark.django_db
class TestViewerCanReviewIn:
    def test_reviewers_and_owners(self, gql, world):
        for who in ('reviewer', 'acme_owner'):
            assert data(gql(CAN_REVIEW, {'organizationId': world['acme'].pk}, world[who]))[
                'viewerCanReviewIn'
            ]

    @pytest.mark.parametrize('who', [None, 'member', 'author', 'globex_reviewer'])
    def test_everybody_else(self, gql, world, who):
        assert (
            data(
                gql(CAN_REVIEW, {'organizationId': world['acme'].pk}, world[who] if who else None)
            )['viewerCanReviewIn']
            is False
        )


@pytest.mark.django_db
class TestIdeaReviews:
    @pytest.fixture
    def idea(self, world):
        idea = make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)
        # Assessed while open, then completed: the order S3-004 will write
        # them in, and the only one the model accepts.
        first = Review.objects.create(
            idea=idea,
            reviewer=world['acme_owner'],
            round=1,
            submission_snapshot={'title': 'Before'},
        )
        first.assessments.create(criterion='evidence', rating='does_not_meet', note='None yet.')
        first.decision = Review.Decision.CHANGES_REQUESTED
        first.feedback = 'Add numbers.'
        first.completed_at = timezone.now()
        first.save()
        Review.objects.create(
            idea=idea, reviewer=world['reviewer'], round=2, submission_snapshot={'title': 'After'}
        )
        return idea

    def test_a_reviewer_sees_every_round_with_snapshots(self, gql, world, idea):
        reviews = data(gql(HISTORY, {'ideaId': idea.pk}, world['reviewer']))['ideaReviews']

        assert [r['round'] for r in reviews] == [1, 2]
        assert reviews[0]['decision'] == 'CHANGES_REQUESTED'
        assert reviews[0]['assessments'] == [
            {'criterion': 'EVIDENCE', 'rating': 'DOES_NOT_MEET', 'note': 'None yet.'}
        ]
        assert reviews[0]['submissionSnapshot'] == {'title': 'Before'}
        assert reviews[1]['decision'] is None
        assert reviews[1]['completedAt'] is None

    def test_the_author_sees_completed_feedback_and_the_reviewer_id_but_no_snapshot(
        self, gql, world, idea
    ):
        reviews = data(gql(HISTORY, {'ideaId': idea.pk}, world['author']))['ideaReviews']

        assert len(reviews) == 1
        assert reviews[0]['feedback'] == 'Add numbers.'
        assert reviews[0]['reviewerId'] == str(world['acme_owner'].pk)
        assert reviews[0]['submissionSnapshot'] is None

    @pytest.mark.parametrize('who', [None, 'member', 'globex_reviewer', 'globex_owner'])
    def test_nobody_else_gets_any_review(self, gql, world, idea, who):
        """
        A `PUBLIC` idea is readable by everybody; its platform review history is
        not.

        Because reading it needs the *platform* permission, and none of these
        callers hold it. The organization Reviewer roles some of them do hold
        grant nothing here - which is the isolation this phase introduced.
        """
        revoke_platform_reviewer(fresh(world[who])) if who else None
        Idea.objects.filter(pk=idea.pk).update(visibility=Idea.Visibility.PUBLIC)

        body = gql(HISTORY, {'ideaId': idea.pk}, world[who] if who else None)

        assert data(body)['ideaReviews'] == []
        # And nothing about the reviewers leaks into the response at all.
        assert str(world['acme_owner'].email) not in json.dumps(body)

    def test_guessed_ids_answer_like_missing_ones(self, gql, world, idea):
        guessed = data(gql(HISTORY, {'ideaId': idea.pk}, world['globex_reviewer']))
        missing = data(gql(HISTORY, {'ideaId': 999_999}, world['globex_reviewer']))

        assert guessed == missing == {'ideaReviews': []}

    def test_there_is_no_way_to_address_a_review_by_its_own_id(self, gql, world, idea):
        review_id = Review.objects.filter(idea=idea).first().pk
        body = gql(f'query {{ review(id: "{review_id}") {{ id }} }}', user=world['globex_reviewer'])

        assert 'errors' in body
        assert "Cannot query field 'review'" in body['errors'][0]['message']

    # `startReview` and `completeReview` exist since S3-004 and are tested in
    # `test_operations_schema.py`; no other review mutation does.
    @pytest.mark.parametrize('mutation', ['claimReview', 'approveIdea', 'rejectIdea'])
    def test_no_other_review_mutation_exists(self, gql, world, mutation):
        body = gql(f'mutation {{ {mutation}(ideaId: "1") {{ success }} }}', user=world['reviewer'])

        assert 'errors' in body
        assert f"Cannot query field '{mutation}'" in body['errors'][0]['message']


@pytest.mark.django_db
class TestIdeaCapabilities:
    def capabilities(self, gql, idea, user):
        return data(gql(IDEA, {'id': idea.pk}, user))['idea']

    def test_an_organization_reviewer_on_an_idea_waiting_for_its_organization(self, gql, world):
        """
        The organization's flag is set for its own reviewer; the platform flag is
        not, and is not set by holding `idea.review`.
        """
        idea = make_idea(world['acme'], world['author'])

        assert self.capabilities(gql, idea, world['reviewer']) == {
            'id': str(idea.pk),
            'viewerCanStartReview': False,
            'viewerCanStartOrganizationReview': True,
            'viewerActiveReviewId': None,
        }

    @pytest.mark.parametrize('who', ['author', 'member'])
    def test_readers_who_are_not_reviewers(self, gql, world, who):
        idea = make_idea(world['acme'], world['author'])

        assert self.capabilities(gql, idea, world[who])['viewerCanStartReview'] is False

    def test_another_organizations_reviewer_on_a_public_idea(self, gql, world):
        """
        Neither flag is set for a reviewer of another organization.

        `viewerCanStartReview` is the platform one and they do not hold the
        platform permission; `viewerCanStartOrganizationReview` is their own
        organization's and this is not their organization. Worth pinning on a
        `PUBLIC` idea, which they *can* read - reading a submission is not
        deciding it.
        """
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        capabilities = self.capabilities(gql, idea, world['globex_reviewer'])
        assert capabilities['viewerCanStartReview'] is False
        assert capabilities['viewerActiveReviewId'] is None

    def test_an_anonymous_viewer_reads_no_idea_at_all(self, gql, world):
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        assert data(gql(IDEA, {'id': idea.pk}))['idea'] is None

    def test_an_inactive_reviewer(self, gql, world):
        """
        Losing the organization membership takes the idea with it.

        **But** a platform reviewer would still see it, and would still see the
        platform flags - platform review is cross-tenant and does not consult
        membership. Which is exactly why `idea` here is organization-scoped and
        not yet submitted: a submitted idea would be readable by anybody holding
        the platform permission, membership or not.
        """
        idea = make_idea(world['acme'], world['author'])
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)

        # They can no longer read an ORGANIZATION idea at all.
        assert data(gql(IDEA, {'id': idea.pk}, world['reviewer']))['idea'] is None

    def test_not_startable_outside_submitted(self, gql, world):
        idea = make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)

        assert self.capabilities(gql, idea, world['reviewer'])['viewerCanStartReview'] is False

    def test_the_active_review_is_the_viewers_own_only(self, gql, world):
        idea = make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)
        review = Review.objects.create(
            idea=idea, reviewer=world['reviewer'], round=1, submission_snapshot={'title': 'x'}
        )

        assert self.capabilities(gql, idea, world['reviewer'])['viewerActiveReviewId'] == str(
            review.pk
        )
        assert self.capabilities(gql, idea, world['acme_owner'])['viewerActiveReviewId'] is None
        assert self.capabilities(gql, idea, world['author'])['viewerActiveReviewId'] is None

    def test_resolving_capabilities_creates_nothing(self, gql, world):
        """
        Reading a capability flag writes nothing.

        Both flags are reported per idea on every render, so a resolver that
        created a review to answer them would turn a page into a workflow.
        """
        idea = make_idea(world['acme'], world['author'])

        self.capabilities(gql, idea, world['reviewer'])

        assert not Review.objects.exists()
        idea.refresh_from_db()
        assert idea.status == Idea.Status.SUBMITTED_TO_ORGANIZATION
