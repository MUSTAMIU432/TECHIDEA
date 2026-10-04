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

from ideas import go_ahead, lifecycle
from ideas.models import Idea
from ideas.services import IdeaError
from identity.models import User
from identity.tokens import issue_access_token
from notifications.models import Notification
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import services
from reviews.models import Review, ReviewCriterionAssessment
from reviews.tests.platform import (
    grant_platform_reviewer,
    make_submitted,
    revoke_platform_reviewer,
)

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
        # Platform review is authorized by a platform-scoped permission and by
        # nothing else, so a reviewer built here is a *platform* reviewer too.
        # The organization Reviewer role above still governs the organization
        # review queue, which is a separate track with separate rules.
        grant_platform_reviewer(user)
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
    idea = make_submitted(
        acme,
        author,
        title='Automate the invoice run',
        description='We key every invoice in by hand, every month.',
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
        # Still refused, but no longer as "unavailable": a platform reviewer may
        # read a submission they were sent, and `globex_owner` is the owner of a
        # *different* organization without the platform review permission. The
        # refusal is therefore about what they may do, not about whether they
        # can see the idea - which is the honest answer now that platform review
        # is independent of any organization.
        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.complete_review(world['globex_owner'], approve(open_review))
        assert_still_open(world, open_review)

    def test_a_reviewer_whose_role_was_removed(self, world, open_review):
        # Losing the platform permission - which is what platform review is
        # authorized by - takes the ability to review with you. The organization
        # Reviewer role is deliberately *not* what this test removes: platform
        # review does not consult it, so removing it would change nothing and the
        # test would pass for the wrong reason.
        revoke_platform_reviewer(world['reviewer'])

        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.complete_review(world['reviewer'], approve(open_review))
        assert_still_open(world, open_review)

    def test_losing_the_organization_role_does_not_take_platform_review_away(
        self, world, open_review
    ):
        # The converse, and the reason the two tracks are separate: the
        # organization Reviewer role governs the *organization* review only.
        # Removing it leaves platform review intact, which is what lets an
        # organization reviewer and a platform reviewer be different people.
        MembershipRole.objects.filter(
            membership__user=world['reviewer'], role__slug=REVIEWER_ROLE_SLUG
        ).delete()

        services.complete_review(world['reviewer'], approve(open_review))
        assert Review.objects.get(pk=open_review.pk).decision == Review.Decision.APPROVED

    def test_a_reviewer_who_left_the_organization_can_still_decide(self, world, open_review):
        """
        Leaving the organization does **not** take a platform review away.

        Platform review is authorized by a platform permission, and membership of
        the idea's organization is not consulted at any point. This is the same
        independence as `test_approval_is_not_the_hand_off`, seen from the
        authorization side: the platform's work cannot be reorganised away from
        underneath it.
        """
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)

        services.complete_review(world['reviewer'], approve(open_review))
        assert Review.objects.get(pk=open_review.pk).decision == Review.Decision.APPROVED

    def test_a_reviewer_who_lost_the_platform_permission_cannot(self, world, open_review):
        """The permission is what is lost, and what stops them."""
        revoke_platform_reviewer(User.objects.get(pk=world['reviewer'].pk))

        with pytest.raises(services.ReviewError):
            services.complete_review(
                User.objects.get(pk=world['reviewer'].pk), approve(open_review)
            )
        assert_still_open(world, open_review)

    def test_an_author_can_never_approve_their_own_idea(self, world):
        own = make_submitted(
            world['acme'],
            world['reviewer'],
            title='Mine',
            description='A description long enough to be usable.',
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

    def test_approval_is_not_the_hand_off(self, world, open_review):
        """
        Platform approval does not put an idea in front of developers.

        The one move out of `APPROVED` is the **owner's** go-ahead, and it belongs
        to the author alone - not the reviewer who approved it, not an
        organization Owner. That is the whole of "platform approval is not owner
        go-ahead", and it is asserted here as a refusal rather than as a missing
        feature, because a reviewer who could hand their own approval straight on
        would be approving twice.
        """
        services.complete_review(world['reviewer'], approve(open_review))

        approved = Idea.objects.get(pk=world['idea'].pk)
        assert approved.status == Idea.Status.APPROVED

        # The reviewer is offered nothing at all.
        assert lifecycle.available_transitions(world['reviewer'], approved) == []
        with pytest.raises(IdeaError, match='not allowed'):
            lifecycle.transition_idea(
                world['reviewer'], approved.pk, Idea.Status.READY_FOR_IMPLEMENTATION
            )

        # The author is offered exactly the go-ahead.
        assert lifecycle.available_transitions(world['author'], approved) == [
            Idea.Status.READY_FOR_IMPLEMENTATION
        ]

    def test_the_hand_off_reaches_automation_only_after_the_go_ahead(self, world, open_review):
        """The Sprint 4 boundary: go-ahead, then the automation-opportunity move."""
        services.complete_review(world['reviewer'], approve(open_review))
        ready = go_ahead.confirm_go_ahead(world['author'], world['idea'].pk)

        assert ready.status == Idea.Status.READY_FOR_IMPLEMENTATION
        assert ready.owner_go_ahead_at is not None

        # Only after `READY_FOR_IMPLEMENTATION` does the automation-opportunity
        # move exist at all. Nothing in this repository takes it - Sprint 4 will.
        assert (
            lifecycle.TRANSITIONS[
                (Idea.Status.READY_FOR_IMPLEMENTATION, Idea.Status.AUTOMATION_PROPOSAL)
            ]
            == lifecycle.PLATFORM_REVIEWER
        )


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
        """
        Who may read a review history, and what is in it.

        Two things are new since this test was written, and both are the point of
        splitting the tracks: the history now holds an **organization** review as
        well as the platform one (the fixture confirms the idea before submitting
        it), and a platform reviewer sees the history as well as the organization
        reviewer does.
        """
        from reviews.selectors import list_idea_reviews

        history = list_idea_reviews(world['author'], world['idea'].pk).reviews
        assert history[-1] == approved
        assert [review.scope for review in history] == ['organization', 'platform']

        assert list_idea_reviews(world['second'], world['idea'].pk).reviews == history
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

        # One decision, one notification, two channels: the in-app notification
        # and the email that goes with it.
        notification = Notification.objects.get(user=world['author'])
        assert notification.kind == 'idea.platform_approved'
        assert notification.report_id is not None
        assert notification.is_read is False

        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ['author@acme.example']
        assert message.subject == notification.title
        assert 'Automate the invoice run' in message.subject
        assert 'https://app.example/app/notifications' in message.body
        # The report's own contents stay behind authentication: the email says a
        # review is complete and where to read it, and nothing more.
        assert 'Worth automating.' not in message.body
        assert 'on evidence' not in message.body
        assert 'Meets' not in message.body

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

        # A changes request is the same event with a different wording, and it
        # produces the same two channels.
        notification = Notification.objects.get(user=world['author'])
        assert notification.kind == 'idea.platform_changes_requested'
        assert 'asked for changes' in notification.body
        assert 'Add numbers.' not in mail.outbox[0].body
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
        # A delivery failure is logged and the decision stands - which is the
        # whole of "if email fails, platform approval remains valid".
        assert 'Could not send the notification email' in caplog.text

    def test_nothing_is_written_or_sent_for_a_review_with_no_decision(self, world, open_review):
        """
        No decision, no notification.

        Worth pinning because the notification service is the only delivery code
        now: "the platform was notified" and "a review exists" are different
        facts, and only the first should ever produce a message. An open review -
        including one a take-over has just closed as `WITHDRAWN` - is not a
        verdict and says nothing to the author.
        """
        services._deliver(open_review.pk, None)

        assert mail.outbox == []
        assert not Notification.objects.filter(user=world['author']).exists()

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
        # Approval leaves the reviewer with nothing to do and the author with the
        # go-ahead. The old assertion expected the reviewer to be offered the
        # automation hand-off, which would have meant an approval being able to
        # carry straight on to implementation without the owner ever deciding.
        assert payload['idea'] == {
            'status': 'APPROVED',
            'availableTransitions': [],
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
