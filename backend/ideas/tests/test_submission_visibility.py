"""
Only an idea its reviewers can read may be submitted (S3-008,
`docs/reviews-domain.md` D-6).

A submitted idea is waiting for a reviewer of its organization, and a
reviewer can only review an idea they can read. A `PRIVATE` idea is readable
by its author alone, so until S3-008 it could be submitted and then sit in
`SUBMITTED` for ever, out of every review queue. The move to `SUBMITTED` now
refuses it - through `submitIdea`, `transitionIdea` and the service alike,
for the first submission and a resubmission - and says what to do. The
meaning of `PRIVATE` is unchanged: a private draft stays private, and nothing
widens visibility on the author's behalf.
"""

import json

import pytest

from ideas import lifecycle, services
from ideas.models import Category, Idea, IdeaTransition
from ideas.services import SUBMISSION_VISIBILITY_MESSAGE, IdeaError
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import eligibility
from reviews.selectors import review_queue

V = Idea.Visibility


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password='a-strong-unique-pass-1',
    )


@pytest.fixture
def world(db):
    owner = make_user('owner@acme.example')
    acme = create_organization_for_user(owner, CreateOrganizationInput(name='Acme')).organization
    author = make_user('author@acme.example')
    Membership.objects.create(user=author, organization=acme)
    reviewer = make_user('reviewer@acme.example')
    MembershipRole.objects.create(
        membership=Membership.objects.create(user=reviewer, organization=acme),
        role=Role.objects.get(organization=acme, slug=REVIEWER_ROLE_SLUG),
    )
    category = Category.objects.create(name='Finance')

    def draft(visibility=None):
        return services.create_idea(
            author,
            acme.pk,
            services.IdeaInput(
                title='Automate the invoice run',
                description='We key every invoice in by hand, every month.',
                category_id=category.pk,
                visibility=visibility,
            ),
        )

    return {'acme': acme, 'author': author, 'reviewer': reviewer, 'draft': draft}


class TestSubmission:
    def test_a_new_idea_is_still_private_by_default(self, world):
        assert world['draft']().visibility == V.PRIVATE

    def test_a_private_draft_cannot_be_submitted(self, world):
        idea = world['draft']()

        with pytest.raises(IdeaError) as exc_info:
            services.submit_idea(world['author'], idea.pk)

        assert exc_info.value.message == SUBMISSION_VISIBILITY_MESSAGE
        idea.refresh_from_db()
        assert (idea.status, idea.visibility, idea.submitted_at) == (
            Idea.Status.DRAFT,
            V.PRIVATE,
            None,
        )
        assert not IdeaTransition.objects.filter(idea=idea).exists()

    def test_a_department_idea_cannot_be_submitted_either(self, world):
        # Not selectable through the API; stored directly to prove the rule does
        # not depend on that.
        idea = world['draft']()
        Idea.objects.filter(pk=idea.pk).update(visibility=V.DEPARTMENT)

        with pytest.raises(IdeaError):
            lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

    @pytest.mark.parametrize('visibility', [V.ORGANIZATION, V.PUBLIC])
    def test_a_reviewable_idea_is_submitted_and_queued(self, world, visibility):
        idea = world['draft'](visibility)

        services.submit_idea(world['author'], idea.pk)

        idea.refresh_from_db()
        assert idea.status == Idea.Status.SUBMITTED
        assert eligibility.can_start_review(world['reviewer'], idea)
        assert [i.pk for i in review_queue(world['reviewer'], world['acme'].pk).items] == [idea.pk]

    def test_widening_the_draft_then_submitting_is_the_way_forward(self, world):
        idea = world['draft']()
        services.update_idea(
            world['author'],
            idea.pk,
            services.IdeaInput(
                title=idea.title,
                description=idea.description,
                category_id=idea.category_id,
                visibility=V.ORGANIZATION,
            ),
        )

        assert services.submit_idea(world['author'], idea.pk).status == Idea.Status.SUBMITTED

    def test_content_problems_are_still_reported_first(self, world):
        idea = world['draft']()
        Idea.objects.filter(pk=idea.pk).update(description='Too short.')

        with pytest.raises(IdeaError) as exc_info:
            services.submit_idea(world['author'], idea.pk)

        assert 'at least' in exc_info.value.message

    def test_a_resubmission_is_held_to_the_same_rule(self, world):
        # Visibility is fixed after submission, so only an idea stored private
        # before S3-008 can be in this position.
        idea = world['draft'](V.ORGANIZATION)
        Idea.objects.filter(pk=idea.pk).update(
            status=Idea.Status.CHANGES_REQUESTED,
            visibility=V.PRIVATE,
            submitted_at='2026-01-01T00:00Z',
        )

        with pytest.raises(IdeaError) as exc_info:
            services.submit_idea(world['author'], idea.pk)

        assert exc_info.value.message == SUBMISSION_VISIBILITY_MESSAGE
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.CHANGES_REQUESTED


SUBMIT = """
mutation Submit($id: ID!) { submitIdea(id: $id) { success message field idea { status } } }
"""


def test_submit_idea_over_graphql_says_what_to_do(client, world):
    idea = world['draft']()
    token = issue_access_token(world['author'].pk)[0]

    body = client.post(
        '/graphql/',
        data=json.dumps({'query': SUBMIT, 'variables': {'id': idea.pk}}),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    ).json()

    assert body['data']['submitIdea'] == {
        'success': False,
        'message': SUBMISSION_VISIBILITY_MESSAGE,
        'field': None,
        'idea': None,
    }
