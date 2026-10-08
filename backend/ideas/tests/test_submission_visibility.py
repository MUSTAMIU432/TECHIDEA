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
from reviews.selectors import platform_queue, review_queue
from reviews.tests.platform import grant_platform_reviewer

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
    # A second reviewer who belongs to no organization at all: platform review is
    # authorized by a platform-scoped permission, so somebody outside every
    # tenant is the normal case for it rather than the exception.
    platform_reviewer = make_user('platform@example.com')
    grant_platform_reviewer(platform_reviewer)
    category = Category.objects.create(name='Finance')

    def draft(visibility=None):
        return services.create_idea(
            author,
            acme.pk,
            services.IdeaInput(
                title='Automate the invoice run',
                description='We key every invoice in by hand, every month.',
                category_id=category.pk,
            ),
        )

    def individual_draft(visibility=None):
        return services.create_idea_in_context(
            author,
            services.IdeaInput(
                title='Automate the invoice run',
                description='We key every invoice in by hand, every month.',
                category_id=category.pk,
            ),
            submission_context=Idea.SubmissionContext.INDIVIDUAL,
        )

    return {
        'acme': acme,
        'author': author,
        'reviewer': reviewer,
        'platform_reviewer': platform_reviewer,
        'draft': draft,
        'individual_draft': individual_draft,
    }


class TestSubmission:
    def test_a_new_idea_gets_its_levels_audience(self, world):
        assert world['draft']().visibility == V.ORGANIZATION

    def test_a_private_draft_cannot_be_submitted(self, world):
        # Not creatable any more (the audience follows the level), so this is a row
        # from before that change: the rule still has to hold for it.
        idea = world['draft']()
        Idea.objects.filter(pk=idea.pk).update(visibility=V.PRIVATE)

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
        """
        Submitted, and queued for the reviewers who can actually read it.

        An **organization** idea, so its reviewers are its own organization's -
        which is why the queue is the organization's and the eligibility is the
        organization one. `PUBLIC` is listed here too and is genuinely reviewable:
        the rule is not "organization visibility or nothing", it is "a visibility
        somebody waiting for it can read".
        """
        idea = world['draft'](visibility)

        services.submit_idea(world['author'], idea.pk)

        idea.refresh_from_db()
        assert idea.status == Idea.Status.SUBMITTED_TO_ORGANIZATION
        assert eligibility.can_start_organization_review(world['reviewer'], idea)
        assert [i.pk for i in review_queue(world['reviewer'], world['acme'].pk).items] == [idea.pk]

    def test_an_individual_idea_is_queued_for_the_platform(self, world):
        """
        The other half of the same rule, and the reason it is context-dependent.

        An individual or team idea's reviewers are the platform's, who are outside
        the filer's tenant - so the visibility that makes it reviewable is
        `PUBLIC`, and it lands in the platform queue rather than any tenant's.
        """
        idea = world['individual_draft'](V.PUBLIC)

        services.submit_idea(world['author'], idea.pk)

        idea.refresh_from_db()
        assert idea.status == Idea.Status.SUBMITTED
        assert eligibility.can_start_review(world['platform_reviewer'], idea)
        assert idea.pk in {i.pk for i in platform_queue(world['platform_reviewer']).items}
        # And not in any organization's queue: the two tracks are separate
        # workspaces, not two views of one list.
        assert review_queue(world['reviewer'], world['acme'].pk).items == []

    def test_widening_the_draft_then_submitting_is_the_way_forward(self, world):
        idea = world['draft']()
        services.update_idea(
            world['author'],
            idea.pk,
            services.IdeaInput(
                title=idea.title,
                description=idea.description,
                category_id=idea.category_id,
            ),
        )

        assert (
            services.submit_idea(world['author'], idea.pk).status
            == Idea.Status.SUBMITTED_TO_ORGANIZATION
        )

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
    Idea.objects.filter(pk=idea.pk).update(visibility=V.PRIVATE)  # a row from before the change
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
