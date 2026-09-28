"""
Reviewer approval (S3-006).

Approval is not a separate operation: it is `completeReview` with
`decision = APPROVED`, the same locked, validated, all-or-nothing completion
S3-004 built for every decision. This file pins that path end to end - what
approval writes, what it refuses, what can never change afterwards, and what
it deliberately does *not* do (create an opportunity or a proposal) - plus the
author's decision email, which is sent only once the decision is committed.
"""

import json
import threading

import pytest
from django.apps import apps
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connections
from django.utils import timezone

from ideas import lifecycle
from ideas.models import Category, Idea
from ideas.services import IdeaError
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import notifications, services
from reviews.models import Review, ReviewCriterionAssessment

VALID_PASSWORD = 'a-strong-unique-pass-1'
CRITERIA = ReviewCriterionAssessment.Criterion.values


def make_user(email, first='Test'):
    return User.objects.create_user(
        email=email,
        first_name=first,
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


def approve(review, *, items=None, feedback='Worth automating.'):
    return services.CompleteReviewInput(
        idea_id=review.idea_id,
        review_id=review.pk,
        decision='approved',
        feedback=feedback,
        assessments=tuple(
            services.AssessmentInput(criterion, 'meets', f'on {criterion}')
            for criterion in CRITERIA
        )
        if items is None
        else items,
    )


@pytest.fixture
def world(db):
    owner = make_user('owner@acme.example')
    acme = create_organization_for_user(owner, CreateOrganizationInput(name='Acme')).organization
    author = make_user('author@acme.example', first='Ada')
    add_member(acme, author)
    reviewer = make_user('reviewer@acme.example')
    add_member(acme, reviewer, reviewer=True)
    second = make_user('second@acme.example')
    add_member(acme, second, reviewer=True)
    member = make_user('member@acme.example')
    add_member(acme, member)
    globex_owner = make_user('owner@globex.example')
    create_organization_for_user(globex_owner, CreateOrganizationInput(name='Globex'))
    idea = Idea.objects.create(
        organization=acme,
        author=author,
        title='Automate the invoice run',
        description='We key every invoice in by hand, every month.',
        category=Category.objects.create(name='Finance'),
        visibility=Idea.Visibility.ORGANIZATION,
        status=Idea.Status.SUBMITTED,
        submitted_at=timezone.now(),
    )
    return {
        'acme': acme,
        'owner': owner,
        'author': author,
        'reviewer': reviewer,
        'second': second,
        'member': member,
        'globex_owner': globex_owner,
        'idea': idea,
    }


@pytest.fixture
def open_review(world):
    return services.start_review(world['reviewer'], world['idea'].pk)


def assert_still_open(world, review):
    review.refresh_from_db()
    assert review.completed_at is None
    assert review.decision is None
    assert review.assessments.count() == 0
    assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.UNDER_REVIEW


# --- the approval -----------------------------------------------------------------------


class TestApproval:
    def test_approving_completes_the_review_and_approves_the_idea(self, world, open_review):
        review = services.complete_review(world['reviewer'], approve(open_review))

        assert review.decision == Review.Decision.APPROVED
        assert review.completed_at is not None
        assert review.feedback == 'Worth automating.'
        assert sorted(review.assessments.values_list('criterion', flat=True)) == sorted(CRITERIA)
        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.APPROVED

    def test_approval_keeps_submitted_at(self, world, open_review):
        submitted_at = world['idea'].submitted_at

        services.complete_review(world['reviewer'], approve(open_review))

        assert Idea.objects.get(pk=world['idea'].pk).submitted_at == submitted_at

    def test_approval_needs_no_feedback(self, world, open_review):
        review = services.complete_review(world['reviewer'], approve(open_review, feedback=''))

        assert review.decision == Review.Decision.APPROVED

    def test_every_criterion_is_still_required(self, world, open_review):
        partial = tuple(services.AssessmentInput(criterion, 'meets') for criterion in CRITERIA[:-1])

        with pytest.raises(services.ReviewError) as exc_info:
            services.complete_review(world['reviewer'], approve(open_review, items=partial))

        assert exc_info.value.field == 'assessments'
        assert_still_open(world, open_review)

    @pytest.mark.parametrize(
        ('criterion', 'rating'), [('impact', 'meets'), ('evidence', 'excellent')]
    )
    def test_criteria_and_ratings_must_be_known(self, world, open_review, criterion, rating):
        items = (
            *(services.AssessmentInput(c, 'meets') for c in CRITERIA if c != 'evidence'),
            services.AssessmentInput(criterion, rating),
        )

        with pytest.raises(services.ReviewError):
            services.complete_review(world['reviewer'], approve(open_review, items=items))
        assert_still_open(world, open_review)

    def test_no_opportunity_or_proposal_is_created(self, world, open_review):
        before = {model._meta.label for model in apps.get_models()}

        services.complete_review(world['reviewer'], approve(open_review))

        assert {model._meta.label for model in apps.get_models()} == before
        assert not any('opportunit' in label.lower() for label in before)
        assert not any('proposal' in label.lower() for label in before)
        # Approval stops at APPROVED; nothing moves the idea further.
        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.APPROVED


# --- who may approve --------------------------------------------------------------------


class TestWhoMayApprove:
    def test_another_eligible_reviewer_cannot_approve_someone_elses_review(
        self, world, open_review
    ):
        with pytest.raises(services.ReviewError, match='Review is unavailable'):
            services.complete_review(world['second'], approve(open_review))
        assert_still_open(world, open_review)

    @pytest.mark.parametrize('who', ['author', 'member'])
    def test_readers_who_are_not_reviewers(self, world, open_review, who):
        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.complete_review(world[who], approve(open_review))
        assert_still_open(world, open_review)

    def test_another_organization_cannot_approve(self, world, open_review):
        with pytest.raises(services.ReviewError, match='Idea is unavailable'):
            services.complete_review(world['globex_owner'], approve(open_review))
        assert_still_open(world, open_review)

    def test_a_reviewer_whose_role_was_removed(self, world, open_review):
        MembershipRole.objects.filter(
            membership__user=world['reviewer'], role__slug=REVIEWER_ROLE_SLUG
        ).delete()

        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.complete_review(world['reviewer'], approve(open_review))
        assert_still_open(world, open_review)

    def test_a_reviewer_who_left_the_organization(self, world, open_review):
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)

        with pytest.raises(services.ReviewError):
            services.complete_review(world['reviewer'], approve(open_review))
        assert_still_open(world, open_review)

    def test_an_author_can_never_approve_their_own_idea(self, world):
        own = Idea.objects.create(
            organization=world['acme'],
            author=world['reviewer'],
            title='Mine',
            description='A description long enough to be usable.',
            category=Category.objects.create(name='Ops'),
            visibility=Idea.Visibility.ORGANIZATION,
            status=Idea.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )

        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.start_review(world['reviewer'], own.pk)

    def test_anonymous(self, world, open_review):
        with pytest.raises(services.ReviewError) as exc_info:
            services.complete_review(None, approve(open_review))
        assert exc_info.value.reason == 'unauthenticated'


# --- state ------------------------------------------------------------------------------


class TestState:
    def test_the_idea_must_still_be_under_review(self, world, open_review):
        # An inconsistent row a buggy write path could leave: the lock and the
        # status check refuse it rather than approving from the wrong state.
        Idea.objects.filter(pk=world['idea'].pk).update(status=Idea.Status.SUBMITTED)

        with pytest.raises(services.ReviewError, match='not under review'):
            services.complete_review(world['reviewer'], approve(open_review))
        open_review.refresh_from_db()
        assert open_review.completed_at is None

    def test_a_decided_review_cannot_be_approved(self, world, open_review):
        rejection = services.CompleteReviewInput(
            idea_id=open_review.idea_id,
            review_id=open_review.pk,
            decision='rejected',
            feedback='Not now.',
            assessments=approve(open_review).assessments,
        )
        services.complete_review(world['reviewer'], rejection)

        with pytest.raises(services.ReviewError, match='already been completed'):
            services.complete_review(world['reviewer'], approve(open_review))
        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.REJECTED

    def test_transition_idea_still_refuses_to_approve(self, world, open_review):
        with pytest.raises(IdeaError) as exc_info:
            lifecycle.transition_idea(world['reviewer'], world['idea'].pk, Idea.Status.APPROVED)

        assert exc_info.value.message == lifecycle.REVIEW_OWNED_MESSAGE
        assert_still_open(world, open_review)

    def test_the_hand_off_from_approved_is_still_available(self, world, open_review):
        services.complete_review(world['reviewer'], approve(open_review))

        assert lifecycle.available_transitions(
            world['reviewer'], Idea.objects.get(pk=world['idea'].pk)
        ) == [Idea.Status.AUTOMATION_PROPOSAL]
        moved = lifecycle.transition_idea(
            world['reviewer'], world['idea'].pk, Idea.Status.AUTOMATION_PROPOSAL
        )
        assert moved.status == Idea.Status.AUTOMATION_PROPOSAL


# --- immutability -----------------------------------------------------------------------


class TestImmutabilityAfterApproval:
    @pytest.fixture
    def approved(self, world, open_review):
        return services.complete_review(world['reviewer'], approve(open_review))

    def test_the_review_cannot_be_edited(self, approved):
        approved.decision = Review.Decision.REJECTED
        with pytest.raises(ValidationError):
            approved.save()

        assert Review.objects.get(pk=approved.pk).decision == Review.Decision.APPROVED

    def test_no_assessment_can_be_added(self, approved):
        approved.assessments.all().delete()  # a queryset delete, below the model...
        with pytest.raises(ValidationError):  # ...but nothing can be written back
            ReviewCriterionAssessment.objects.create(
                review=approved, criterion='evidence', rating='does_not_meet'
            )

    def test_the_review_cannot_be_deleted(self, approved):
        with pytest.raises(ValidationError):
            approved.delete()

        assert Review.objects.filter(pk=approved.pk).exists()

    def test_an_open_review_is_still_deletable_by_the_model(self, world, open_review):
        # The guard is about history; an unfinished row carries no decision.
        open_review.delete()

        assert not Review.objects.filter(pk=open_review.pk).exists()

    def test_history_stays_readable_as_before(self, world, approved):
        from reviews.selectors import list_idea_reviews

        assert list_idea_reviews(world['author'], world['idea'].pk).reviews == [approved]
        assert list_idea_reviews(world['second'], world['idea'].pk).reviews == [approved]
        assert list_idea_reviews(world['member'], world['idea'].pk).reviews == []
        assert list_idea_reviews(world['globex_owner'], world['idea'].pk).reviews == []


# --- concurrency ------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_completions_produce_one_decision(world):
    """
    The same reviewer sends Approve and Reject at once (a double click in two
    tabs). The row locks make the second wait, re-read a completed review and
    be refused: one decision, one status, one set of assessments.
    """
    review = services.start_review(world['reviewer'], world['idea'].pk)
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def attempt(decision):
        data = services.CompleteReviewInput(
            idea_id=review.idea_id,
            review_id=review.pk,
            decision=decision,
            feedback='Decided.',
            assessments=approve(review).assessments,
        )
        try:
            barrier.wait(timeout=10)
            services.complete_review(world['reviewer'], data)
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=attempt, args=(decision,)) for decision in ('approved', 'rejected')
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(errors) == 1
    assert isinstance(errors[0], services.ReviewError)
    review.refresh_from_db()
    assert review.decision in {'approved', 'rejected'}
    assert review.assessments.count() == len(CRITERIA)
    assert Idea.objects.get(pk=world['idea'].pk).status == review.decision


# --- the author's email -----------------------------------------------------------------


class TestDecisionEmail:
    def test_the_author_is_emailed_once_the_approval_commits(
        self, world, open_review, django_capture_on_commit_callbacks, settings
    ):
        settings.FRONTEND_URL = 'https://app.example'

        with django_capture_on_commit_callbacks(execute=True):
            services.complete_review(world['reviewer'], approve(open_review))

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ['author@acme.example']
        assert message.subject == notifications.REVIEW_DECISION_SUBJECT
        assert 'Decision: Approved' in message.body
        assert 'Automate the invoice run' in message.body
        assert 'https://app.example/app/ideas' in message.body
        # The feedback and the criteria stay behind authentication.
        assert 'Worth automating.' not in message.body
        assert 'on evidence' not in message.body

    def test_a_changes_requested_decision_says_what_to_do_next(
        self, world, open_review, django_capture_on_commit_callbacks
    ):
        data = services.CompleteReviewInput(
            idea_id=open_review.idea_id,
            review_id=open_review.pk,
            decision='changes_requested',
            feedback='Add numbers.',
            assessments=approve(open_review).assessments,
        )

        with django_capture_on_commit_callbacks(execute=True):
            services.complete_review(world['reviewer'], data)

        assert 'Decision: Changes requested' in mail.outbox[0].body
        assert 'submit it again' in mail.outbox[0].body

    def test_nothing_is_sent_when_the_completion_is_refused(
        self, world, open_review, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True), pytest.raises(services.ReviewError):
            services.complete_review(world['second'], approve(open_review))

        assert mail.outbox == []

    def test_nothing_is_sent_when_the_completion_rolls_back(
        self, world, open_review, django_capture_on_commit_callbacks, monkeypatch
    ):
        def refuse(*args, **kwargs):
            raise IdeaError('Refused for the test.')

        monkeypatch.setattr(lifecycle, 'apply_review_transition', refuse)

        with (
            django_capture_on_commit_callbacks(execute=True) as callbacks,
            pytest.raises(services.ReviewError),
        ):
            services.complete_review(world['reviewer'], approve(open_review))

        assert callbacks == []
        assert mail.outbox == []

    def test_a_delivery_failure_is_logged_and_the_decision_stands(
        self, world, open_review, django_capture_on_commit_callbacks, monkeypatch, caplog
    ):
        def broken_send(self, fail_silently=False):
            raise ConnectionRefusedError('smtp down')

        monkeypatch.setattr('django.core.mail.EmailMessage.send', broken_send)

        with django_capture_on_commit_callbacks(execute=True):
            services.complete_review(world['reviewer'], approve(open_review))

        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.APPROVED
        assert 'Could not send the review decision email' in caplog.text

    def test_no_email_for_an_unknown_or_open_review(self, world, open_review):
        notifications.send_review_decision_email(open_review.pk)
        notifications.send_review_decision_email(999_999)

        assert mail.outbox == []

    def test_starting_a_review_sends_nothing(self, world, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            services.start_review(world['second'], world['idea'].pk)

        assert mail.outbox == []


# --- GraphQL ----------------------------------------------------------------------------

COMPLETE = """
mutation Complete($input: CompleteReviewInput!) {
  completeReview(input: $input) {
    success message
    review { decision completedAt }
    idea { status availableTransitions viewerActiveReviewId }
  }
}
"""

TRANSITION = """
mutation Transition($id: ID!, $to: IdeaStatus!) {
  transitionIdea(id: $id, to: $to) { success message }
}
"""


@pytest.fixture
def gql(client):
    def post(query, variables, user):
        response = client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {issue_access_token(user.pk)[0]}',
        )
        body = response.json()
        assert 'errors' not in body, body
        return body['data']

    return post


def graphql_input(review):
    return {
        'input': {
            'ideaId': str(review.idea_id),
            'reviewId': str(review.pk),
            'decision': 'APPROVED',
            'assessments': [
                {'criterion': criterion.upper(), 'rating': 'MEETS'} for criterion in CRITERIA
            ],
        }
    }


class TestGraphQL:
    def test_approval_is_the_existing_complete_review(self, gql, world, open_review):
        payload = gql(COMPLETE, graphql_input(open_review), world['reviewer'])['completeReview']

        assert payload['success'] is True
        assert payload['message'] == 'Review completed: Approved.'
        assert payload['review']['decision'] == 'APPROVED'
        assert payload['idea'] == {
            'status': 'APPROVED',
            'availableTransitions': ['AUTOMATION_PROPOSAL'],
            'viewerActiveReviewId': None,
        }

    @pytest.mark.parametrize('who', ['second', 'author', 'member', 'globex_owner'])
    def test_nobody_else_can_approve_through_graphql(self, gql, world, open_review, who):
        payload = gql(COMPLETE, graphql_input(open_review), world[who])['completeReview']

        assert payload['success'] is False
        assert_still_open(world, open_review)

    def test_transition_idea_cannot_approve_through_graphql(self, gql, world, open_review):
        payload = gql(
            TRANSITION, {'id': str(world['idea'].pk), 'to': 'APPROVED'}, world['reviewer']
        )['transitionIdea']

        assert payload['success'] is False
        assert_still_open(world, open_review)
