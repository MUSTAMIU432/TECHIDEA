"""
Changes requested and resubmission (S3-005).

The flow, end to end, through the operations that already exist:

    completeReview(CHANGES_REQUESTED)   review 1 completed, idea CHANGES_REQUESTED
    updateIdea                          the author edits the content
    submitIdea                          CHANGES_REQUESTED -> SUBMITTED
    startReview                         review 2, round 2, snapshot of the new content

No operation is new. What is new is that `update_idea` accepts a
changes-requested idea, and that is tested here as a rule of its own: who may
edit (the author, still a member), what may change (content, not
visibility), and what is never touched (the completed review, and the next
round, which only a reviewer opens).
"""

import json

import pytest
from django.utils import timezone

from ideas import lifecycle
from ideas import services as idea_services
from ideas.models import Category, Idea
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import services
from reviews.models import Review, ReviewCriterionAssessment

VALID_PASSWORD = 'a-strong-unique-pass-1'
ORIGINAL = 'We key every invoice in by hand, every month.'
REVISED = 'We key 1,200 invoices in by hand every month; it takes three days.'


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
    return membership


def edit(title='Automate the invoice run', description=REVISED, category=None, visibility=None):
    return idea_services.IdeaInput(
        title=title,
        description=description,
        category_id=category.pk if category else None,
        visibility=visibility,
    )


def send_back(reviewer, idea):
    """A real review round ending in CHANGES_REQUESTED, through S3-004's services."""
    review = services.start_review(reviewer, idea.pk)
    return services.complete_review(
        reviewer,
        services.CompleteReviewInput(
            idea_id=idea.pk,
            review_id=review.pk,
            decision='changes_requested',
            feedback='Add the monthly volume.',
            assessments=tuple(
                services.AssessmentInput(criterion, 'partially_meets', f'note on {criterion}')
                for criterion in ReviewCriterionAssessment.Criterion.values
            ),
        ),
    )


def frozen(review):
    """Every field of a completed review, and its assessments, as plain data."""
    review = Review.objects.get(pk=review.pk)
    return {
        'round': review.round,
        'reviewer_id': review.reviewer_id,
        'decision': review.decision,
        'feedback': review.feedback,
        'submission_snapshot': review.submission_snapshot,
        'created_at': review.created_at,
        'updated_at': review.updated_at,
        'completed_at': review.completed_at,
        'assessments': sorted(
            review.assessments.values_list('pk', 'criterion', 'rating', 'note', 'created_at')
        ),
    }


@pytest.fixture
def world(db):
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
    category = Category.objects.create(name='Finance')
    idea = Idea.objects.create(
        organization=acme,
        author=author,
        title='Automate the invoice run',
        description=ORIGINAL,
        category=category,
        visibility=Idea.Visibility.ORGANIZATION,
        status=Idea.Status.SUBMITTED,
        submitted_at=timezone.now(),
    )
    first = send_back(reviewer, idea)
    idea.refresh_from_db()
    return {
        'acme': acme,
        'owner': owner,
        'author': author,
        'reviewer': reviewer,
        'second': second,
        'member': member,
        'globex_owner': globex_owner,
        'category': category,
        'idea': idea,
        'first': first,
    }


# --- editing ----------------------------------------------------------------------------


class TestEditingWhileChangesAreRequested:
    def test_the_author_edits_the_content(self, world):
        assert world['idea'].status == Idea.Status.CHANGES_REQUESTED

        idea = idea_services.update_idea(
            world['author'], world['idea'].pk, edit(category=world['category'])
        )

        assert idea.description == REVISED
        assert idea.status == Idea.Status.CHANGES_REQUESTED

    def test_editing_keeps_the_original_submission_time(self, world):
        submitted_at = world['idea'].submitted_at

        idea = idea_services.update_idea(
            world['author'], world['idea'].pk, edit(category=world['category'])
        )

        assert idea.submitted_at == submitted_at

    def test_the_same_validation_as_a_draft(self, world):
        with pytest.raises(idea_services.IdeaError) as exc_info:
            idea_services.update_idea(world['author'], world['idea'].pk, edit(title='  '))
        assert exc_info.value.field == 'title'

        with pytest.raises(idea_services.IdeaError):
            idea_services.update_idea(
                world['author'],
                world['idea'].pk,
                idea_services.IdeaInput(title='T', description='', category_id=999_999),
            )

    def test_visibility_is_fixed_once_submitted(self, world):
        with pytest.raises(idea_services.IdeaError) as exc_info:
            idea_services.update_idea(
                world['author'],
                world['idea'].pk,
                edit(category=world['category'], visibility='private'),
            )

        assert exc_info.value.field == 'visibility'
        world['idea'].refresh_from_db()
        assert world['idea'].visibility == Idea.Visibility.ORGANIZATION
        assert world['idea'].description == ORIGINAL

    def test_resending_the_same_visibility_is_not_a_change(self, world):
        idea = idea_services.update_idea(
            world['author'],
            world['idea'].pk,
            edit(category=world['category'], visibility='organization'),
        )

        assert idea.description == REVISED

    def test_a_draft_may_still_change_visibility(self, world):
        draft = Idea.objects.create(
            organization=world['acme'],
            author=world['author'],
            title='Draft',
            visibility=Idea.Visibility.PRIVATE,
        )

        idea = idea_services.update_idea(world['author'], draft.pk, edit(visibility='organization'))

        assert idea.visibility == Idea.Visibility.ORGANIZATION

    @pytest.mark.parametrize(
        'status',
        [
            Idea.Status.SUBMITTED,
            Idea.Status.UNDER_REVIEW,
            Idea.Status.APPROVED,
            Idea.Status.REJECTED,
            Idea.Status.AUTOMATION_PROPOSAL,
        ],
    )
    def test_every_other_submitted_state_stays_read_only(self, world, status):
        Idea.objects.filter(pk=world['idea'].pk).update(status=status)

        with pytest.raises(idea_services.IdeaError) as exc_info:
            idea_services.update_idea(world['author'], world['idea'].pk, edit())

        assert exc_info.value.message == 'Only a draft can be edited.'

    @pytest.mark.parametrize('who', ['reviewer', 'member', 'owner'])
    def test_nobody_but_the_author_edits_it(self, world, who):
        with pytest.raises(idea_services.IdeaError) as exc_info:
            idea_services.update_idea(world[who], world['idea'].pk, edit())

        # The same answer as for an idea that does not exist.
        assert exc_info.value.message == 'Idea is unavailable.'
        world['idea'].refresh_from_db()
        assert world['idea'].description == ORIGINAL

    def test_another_organization_and_guessed_ids_answer_alike(self, world):
        for idea_id in (world['idea'].pk, 999_999, 'nope'):
            with pytest.raises(idea_services.IdeaError) as exc_info:
                idea_services.update_idea(world['globex_owner'], idea_id, edit())
            assert exc_info.value.message == 'Idea is unavailable.', idea_id

    def test_an_author_who_left_the_organization(self, world):
        Membership.objects.filter(user=world['author']).update(status=Membership.Status.INACTIVE)

        with pytest.raises(idea_services.IdeaError) as exc_info:
            idea_services.update_idea(world['author'], world['idea'].pk, edit())

        assert exc_info.value.reason == 'membership_required'

    def test_anonymous(self, world):
        with pytest.raises(idea_services.IdeaError) as exc_info:
            idea_services.update_idea(None, world['idea'].pk, edit())

        assert exc_info.value.reason == 'unauthenticated'


# --- resubmitting -----------------------------------------------------------------------


class TestResubmission:
    def test_the_author_resubmits_through_submit_idea(self, world):
        idea = idea_services.submit_idea(world['author'], world['idea'].pk)

        assert idea.status == Idea.Status.SUBMITTED

    def test_resubmission_creates_no_review(self, world):
        idea_services.submit_idea(world['author'], world['idea'].pk)

        assert list(Review.objects.filter(idea=world['idea'])) == [world['first']]

    def test_the_submission_rules_apply_again(self, world):
        Idea.objects.filter(pk=world['idea'].pk).update(description='Too short.')

        with pytest.raises(idea_services.IdeaError, match='at least'):
            idea_services.submit_idea(world['author'], world['idea'].pk)

        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.CHANGES_REQUESTED

    def test_a_missing_category_is_refused_again(self, world):
        Idea.objects.filter(pk=world['idea'].pk).update(category=None)

        with pytest.raises(idea_services.IdeaError, match='category'):
            idea_services.submit_idea(world['author'], world['idea'].pk)

    @pytest.mark.parametrize('who', ['reviewer', 'second', 'member', 'owner'])
    def test_only_the_author_resubmits(self, world, who):
        with pytest.raises(idea_services.IdeaError):
            idea_services.submit_idea(world[who], world['idea'].pk)
        with pytest.raises(idea_services.IdeaError):
            lifecycle.transition_idea(world[who], world['idea'].pk, Idea.Status.SUBMITTED)

        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.CHANGES_REQUESTED

    def test_another_organization_cannot_resubmit(self, world):
        with pytest.raises(idea_services.IdeaError) as exc_info:
            idea_services.submit_idea(world['globex_owner'], world['idea'].pk)

        assert exc_info.value.message == 'Idea is unavailable.'

    def test_transition_idea_is_the_same_author_only_move(self, world):
        idea = lifecycle.transition_idea(world['author'], world['idea'].pk, Idea.Status.SUBMITTED)

        assert idea.status == Idea.Status.SUBMITTED


# --- the review record ------------------------------------------------------------------


class TestReviewHistoryIsKept:
    def test_edit_and_resubmission_leave_review_one_exactly_as_it_was(self, world):
        before = frozen(world['first'])
        assert before['decision'] == Review.Decision.CHANGES_REQUESTED
        assert len(before['assessments']) == 5
        assert before['completed_at'] is not None

        idea_services.update_idea(
            world['author'], world['idea'].pk, edit(category=world['category'])
        )
        idea_services.submit_idea(world['author'], world['idea'].pk)

        assert frozen(world['first']) == before
        assert before['submission_snapshot']['description'] == ORIGINAL

    def test_the_next_round_is_opened_only_by_a_reviewer_and_snapshots_the_new_content(self, world):
        before = frozen(world['first'])
        idea_services.update_idea(
            world['author'], world['idea'].pk, edit(category=world['category'])
        )
        idea_services.submit_idea(world['author'], world['idea'].pk)
        assert Review.objects.filter(idea=world['idea']).count() == 1

        second = services.start_review(world['second'], world['idea'].pk)

        assert second.pk != world['first'].pk
        assert second.round == 2
        assert second.reviewer == world['second']
        assert second.completed_at is None
        assert second.submission_snapshot['description'] == REVISED
        assert frozen(world['first']) == before
        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.UNDER_REVIEW
        assert list(
            Review.objects.filter(idea=world['idea'])
            .order_by('round')
            .values_list('round', flat=True)
        ) == [1, 2]

    def test_the_first_reviewer_may_take_the_second_round(self, world):
        idea_services.submit_idea(world['author'], world['idea'].pk)

        assert services.start_review(world['reviewer'], world['idea'].pk).round == 2

    def test_the_author_still_cannot_review_their_resubmission(self, world):
        idea_services.submit_idea(world['author'], world['idea'].pk)

        with pytest.raises(services.ReviewError):
            services.start_review(world['author'], world['idea'].pk)


# --- GraphQL ----------------------------------------------------------------------------

UPDATE = """
mutation Update($input: UpdateIdeaInput!) {
  updateIdea(input: $input) { success message field idea { id description status } }
}
"""

SUBMIT = """
mutation Submit($id: ID!) {
  submitIdea(id: $id) { success message field idea { id status availableTransitions } }
}
"""


@pytest.fixture
def gql(client):
    def post(query, variables, user=None):
        headers = {}
        if user is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {issue_access_token(user.pk)[0]}'
        response = client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables}),
            content_type='application/json',
            **headers,
        )
        body = response.json()
        assert 'errors' not in body, body
        return body['data']

    return post


def update_variables(world, **overrides):
    fields = {
        'title': 'Automate the invoice run',
        'description': REVISED,
        'categoryId': str(world['category'].pk),
    }
    fields.update(overrides)
    return {'input': {'id': str(world['idea'].pk), 'idea': fields}}


class TestGraphQL:
    def test_the_author_edits_then_resubmits(self, gql, world):
        updated = gql(UPDATE, update_variables(world), world['author'])['updateIdea']
        assert updated['success'] is True
        assert updated['idea'] == {
            'id': str(world['idea'].pk),
            'description': REVISED,
            'status': 'CHANGES_REQUESTED',
        }

        submitted = gql(SUBMIT, {'id': str(world['idea'].pk)}, world['author'])['submitIdea']
        assert submitted['success'] is True
        assert submitted['idea']['status'] == 'SUBMITTED'
        assert submitted['idea']['availableTransitions'] == []

    def test_changes_requested_offers_the_author_resubmission(self, gql, world):
        body = gql(
            'query($id: ID!) { idea(id: $id) { availableTransitions } }',
            {'id': str(world['idea'].pk)},
            world['author'],
        )

        assert body['idea']['availableTransitions'] == ['SUBMITTED']

    @pytest.mark.parametrize('who', [None, 'reviewer', 'member', 'globex_owner'])
    def test_nobody_else_edits_or_resubmits(self, gql, world, who):
        user = world[who] if who else None

        updated = gql(UPDATE, update_variables(world), user)['updateIdea']
        submitted = gql(SUBMIT, {'id': str(world['idea'].pk)}, user)['submitIdea']

        assert updated['success'] is False
        assert updated['idea'] is None
        assert submitted['success'] is False
        assert submitted['idea'] is None
        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.CHANGES_REQUESTED

    def test_a_visibility_change_is_a_field_error(self, gql, world):
        updated = gql(UPDATE, update_variables(world, visibility='PRIVATE'), world['author'])[
            'updateIdea'
        ]

        assert updated['success'] is False
        assert updated['field'] == 'visibility'

    def test_an_incomplete_resubmission_is_refused(self, gql, world):
        Idea.objects.filter(pk=world['idea'].pk).update(description='Too short.')

        submitted = gql(SUBMIT, {'id': str(world['idea'].pk)}, world['author'])['submitIdea']

        assert submitted['success'] is False
        assert 'at least' in submitted['message']
