"""
Taking over a stalled review (S3-008, `docs/reviews-domain.md` §5.3, D-2).

A review is stalled when the reviewer holding it stops being eligible: they
leave the organization, lose `idea.review`, are deactivated, or can no longer
read the idea. Nobody can decide it - `complete_review` refuses its own
reviewer too - so the idea would sit in `UNDER_REVIEW` for ever. Another
eligible reviewer takes it over with `startReview`: the open round is
completed as `WITHDRAWN`, round n+1 opens for them, and the idea stays
`UNDER_REVIEW`.

What must never happen: a take-over of an eligible reviewer's review, a
take-over by anybody who could not start a review, two successful take-overs
of one review, a second open review, a lost or rewritten round, a status
move, an audit row for a move that did not happen, or an email about a
decision nobody made.
"""

import json
import logging
import threading

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connections
from django.utils import timezone

from ideas.models import Category, Idea, IdeaTransition
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import eligibility, selectors, services
from reviews.models import Review, ReviewCriterionAssessment

VALID_PASSWORD = 'a-strong-unique-pass-1'
CRITERIA = ReviewCriterionAssessment.Criterion.values
S = Idea.Status


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


def completion(review, decision='approved', feedback='Worth doing.'):
    return services.CompleteReviewInput(
        idea_id=review.idea_id,
        review_id=review.pk,
        decision=decision,
        feedback=feedback,
        assessments=tuple(services.AssessmentInput(c, 'meets') for c in CRITERIA),
    )


# Every way the holder of a review can stop being its reviewer.
def _leave(world):
    Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)


def _lose_role(world):
    MembershipRole.objects.filter(membership__user=world['reviewer']).delete()


def _deactivate(world):
    User.objects.filter(pk=world['reviewer'].pk).update(is_active=False)


def _removed(world):
    MembershipRole.objects.filter(membership__user=world['reviewer']).delete()
    Membership.objects.filter(user=world['reviewer']).delete()


LOSSES = {
    'membership deactivated': _leave,
    'reviewer role removed': _lose_role,
    'account deactivated': _deactivate,
    'membership removed': _removed,
}


@pytest.fixture
def world(db):
    owner = make_user('owner@acme.example')
    acme = create_organization_for_user(owner, CreateOrganizationInput(name='Acme')).organization
    author = make_user('author@acme.example')
    add_member(acme, author, reviewer=True)  # holds the role, still never reviews own idea
    reviewer = make_user('reviewer@acme.example')
    add_member(acme, reviewer, reviewer=True)
    second = make_user('second@acme.example')
    add_member(acme, second, reviewer=True)
    third = make_user('third@acme.example')
    add_member(acme, third, reviewer=True)
    member = make_user('member@acme.example')
    add_member(acme, member)
    globex_owner = make_user('owner@globex.example')
    globex = create_organization_for_user(
        globex_owner, CreateOrganizationInput(name='Globex')
    ).organization
    globex_reviewer = make_user('reviewer@globex.example')
    add_member(globex, globex_reviewer, reviewer=True)
    idea = Idea.objects.create(
        organization=acme,
        author=author,
        title='Automate the invoice run',
        description='We key every invoice in by hand, every month.',
        category=Category.objects.create(name='Finance'),
        visibility=Idea.Visibility.ORGANIZATION,
        status=S.SUBMITTED,
        submitted_at=timezone.now(),
    )
    return {
        'acme': acme,
        'owner': owner,
        'author': author,
        'reviewer': reviewer,
        'second': second,
        'third': third,
        'member': member,
        'globex_owner': globex_owner,
        'globex_reviewer': globex_reviewer,
        'idea': idea,
    }


@pytest.fixture
def started(world):
    return services.start_review(world['reviewer'], world['idea'].pk)


@pytest.fixture
def stalled(world, started):
    _leave(world)
    return started


def reviews_of(idea):
    return list(
        Review.objects.filter(idea=idea)
        .order_by('round')
        .values_list('round', 'reviewer__email', 'decision')
    )


def fresh(user):
    return User.objects.get(pk=user.pk)


# --- the take-over --------------------------------------------------------------------


class TestTakeOver:
    @pytest.mark.parametrize('loss', LOSSES.values(), ids=LOSSES.keys())
    def test_another_reviewer_takes_over_whatever_the_loss(self, world, started, loss):
        loss(world)

        review = services.start_review(world['second'], world['idea'].pk)

        assert (review.round, review.reviewer_id, review.completed_at) == (
            2,
            world['second'].pk,
            None,
        )
        assert reviews_of(world['idea']) == [
            (1, 'reviewer@acme.example', Review.Decision.WITHDRAWN),
            (2, 'second@acme.example', None),
        ]
        assert Idea.objects.get(pk=world['idea'].pk).status == S.UNDER_REVIEW

    def test_the_withdrawn_round_records_who_held_it_and_when_it_was_released(self, world, stalled):
        before = timezone.now()
        new = services.start_review(world['second'], world['idea'].pk)

        stalled.refresh_from_db()
        assert stalled.reviewer_id == world['reviewer'].pk
        assert stalled.decision == Review.Decision.WITHDRAWN
        assert before <= stalled.completed_at <= new.created_at
        assert stalled.feedback == ''
        assert stalled.assessments.count() == 0
        # Nobody reviewed anything new: the next round is of the same content.
        assert new.submission_snapshot == stalled.submission_snapshot

    def test_no_status_moves_so_no_transition_is_recorded(self, world, stalled):
        before = IdeaTransition.objects.filter(idea=world['idea']).count()

        services.start_review(world['second'], world['idea'].pk)

        # Only the original start: the fixture's idea was stored as submitted.
        assert IdeaTransition.objects.filter(idea=world['idea']).count() == before == 1

    def test_nobody_is_emailed(self, world, stalled, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            services.start_review(world['second'], world['idea'].pk)

        assert mail.outbox == []

    def test_the_decision_email_never_reports_a_withdrawn_round(self, world, stalled):
        # Defence in depth: the take-over schedules no email, and the sender
        # itself refuses a withdrawn review if one ever reached it.
        from reviews.notifications import send_review_decision_email

        services.start_review(world['second'], world['idea'].pk)
        send_review_decision_email(stalled.pk)

        assert mail.outbox == []

    def test_the_take_over_is_logged_with_ids_only(self, world, stalled, caplog):
        caplog.set_level(logging.INFO, logger='reviews.services')

        new = services.start_review(world['second'], world['idea'].pk)

        assert f'withdrawn_review={stalled.pk}' in caplog.text
        assert f'previous_reviewer={world["reviewer"].pk}' in caplog.text
        assert f'review={new.pk}, reviewer={world["second"].pk}' in caplog.text
        assert world['idea'].title not in caplog.text

    def test_the_new_reviewer_decides_and_the_author_is_told_once(
        self, world, stalled, django_capture_on_commit_callbacks
    ):
        new = services.start_review(world['second'], world['idea'].pk)

        with django_capture_on_commit_callbacks(execute=True):
            services.complete_review(world['second'], completion(new, 'approved', ''))

        assert reviews_of(world['idea']) == [
            (1, 'reviewer@acme.example', Review.Decision.WITHDRAWN),
            (2, 'second@acme.example', Review.Decision.APPROVED),
        ]
        assert Idea.objects.get(pk=world['idea'].pk).status == S.APPROVED
        last = IdeaTransition.objects.filter(idea=world['idea']).last()
        assert (last.from_status, last.to_status, last.actor_id) == (
            S.UNDER_REVIEW,
            S.APPROVED,
            world['second'].pk,
        )
        assert [m.to for m in mail.outbox] == [['author@acme.example']]

    def test_the_former_reviewer_cannot_complete_the_withdrawn_round_even_if_reinstated(
        self, world, stalled
    ):
        services.start_review(world['second'], world['idea'].pk)
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.ACTIVE)

        with pytest.raises(services.ReviewError):
            services.complete_review(world['reviewer'], completion(stalled))

        stalled.refresh_from_db()
        assert stalled.decision == Review.Decision.WITHDRAWN

    def test_a_withdrawn_round_is_as_immutable_as_any_completed_one(self, world, stalled):
        services.start_review(world['second'], world['idea'].pk)
        stalled.refresh_from_db()

        stalled.feedback = 'Rewritten.'
        with pytest.raises(ValidationError):
            stalled.save()
        with pytest.raises(ValidationError):
            stalled.delete()
        with pytest.raises(ValidationError):
            ReviewCriterionAssessment.objects.create(
                review=stalled, criterion=CRITERIA[0], rating='meets'
            )

    def test_the_new_reviewer_can_be_taken_over_from_in_turn(self, world, stalled):
        services.start_review(world['second'], world['idea'].pk)
        MembershipRole.objects.filter(membership__user=world['second']).delete()

        services.start_review(world['third'], world['idea'].pk)

        assert [row[2] for row in reviews_of(world['idea'])] == [
            Review.Decision.WITHDRAWN,
            Review.Decision.WITHDRAWN,
            None,
        ]


# --- who may not take over, and when --------------------------------------------------


class TestRefusals:
    def assert_untouched(self, world, review):
        review.refresh_from_db()
        assert review.completed_at is None
        assert Review.objects.filter(idea=world['idea']).count() == 1
        assert Idea.objects.get(pk=world['idea'].pk).status == S.UNDER_REVIEW

    def test_an_eligible_reviewers_review_is_never_taken(self, world, started):
        with pytest.raises(services.ReviewError) as exc_info:
            services.start_review(world['second'], world['idea'].pk)

        assert exc_info.value.message == 'This idea is not waiting for review.'
        self.assert_untouched(world, started)

    def test_the_holder_cannot_take_over_their_own_review(self, world, started):
        with pytest.raises(services.ReviewError):
            services.start_review(world['reviewer'], world['idea'].pk)
        self.assert_untouched(world, started)

    def test_the_author_cannot_take_over_even_holding_the_role(self, world, stalled):
        with pytest.raises(services.ReviewError) as exc_info:
            services.start_review(world['author'], world['idea'].pk)

        assert exc_info.value.message == 'You are not allowed to review this idea.'
        self.assert_untouched(world, stalled)

    def test_a_member_without_the_permission(self, world, stalled):
        with pytest.raises(services.ReviewError):
            services.start_review(world['member'], world['idea'].pk)
        self.assert_untouched(world, stalled)

    @pytest.mark.parametrize('who', ['globex_reviewer', 'globex_owner'])
    def test_another_organization_even_on_a_public_idea(self, world, stalled, who):
        Idea.objects.filter(pk=world['idea'].pk).update(visibility=Idea.Visibility.PUBLIC)

        with pytest.raises(services.ReviewError) as exc_info:
            services.start_review(world[who], world['idea'].pk)

        assert exc_info.value.message == 'You are not allowed to review this idea.'
        self.assert_untouched(world, stalled)

    def test_anonymous_and_deactivated(self, world, stalled):
        with pytest.raises(services.ReviewError):
            services.start_review(None, world['idea'].pk)
        User.objects.filter(pk=world['second'].pk).update(is_active=False)
        with pytest.raises(services.ReviewError):
            services.start_review(fresh(world['second']), world['idea'].pk)
        self.assert_untouched(world, stalled)

    def test_an_ineligible_would_be_taker(self, world, stalled):
        Membership.objects.filter(user=world['second']).update(status=Membership.Status.INACTIVE)

        with pytest.raises(services.ReviewError):
            services.start_review(world['second'], world['idea'].pk)
        self.assert_untouched(world, stalled)

    @pytest.mark.parametrize('decision', ['approved', 'rejected', 'changes_requested'])
    def test_a_completed_review_cannot_be_taken_over(self, world, started, decision):
        services.complete_review(world['reviewer'], completion(started, decision))
        _leave(world)

        with pytest.raises(services.ReviewError):
            services.start_review(world['second'], world['idea'].pk)

        started.refresh_from_db()
        assert started.decision == decision
        assert Review.objects.filter(idea=world['idea']).count() == 1

    def test_withdrawn_is_not_a_decision_a_reviewer_can_record(self, world, started):
        with pytest.raises(services.ReviewError) as exc_info:
            services.complete_review(world['reviewer'], completion(started, 'withdrawn'))

        assert exc_info.value.field == 'decision'
        self.assert_untouched(world, started)

    def test_transition_idea_cannot_be_used_instead(self, world, stalled):
        from ideas import lifecycle
        from ideas.services import IdeaError

        for target in (S.SUBMITTED, S.APPROVED):
            with pytest.raises(IdeaError):
                lifecycle.transition_idea(world['second'], world['idea'].pk, target)
        self.assert_untouched(world, stalled)


# --- what the capability flags say ------------------------------------------------------


class TestCapabilities:
    def test_offered_only_to_eligible_reviewers_while_stalled(self, world, started):
        idea = world['idea']
        idea.refresh_from_db()
        assert not eligibility.can_start_review(world['second'], idea)

        _leave(world)

        assert eligibility.can_start_review(world['second'], idea)
        for who in ('reviewer', 'author', 'member', 'globex_reviewer'):
            assert not eligibility.can_start_review(fresh(world[who]), idea), who
        assert not eligibility.can_start_review(None, idea)

    def test_after_the_take_over_the_new_reviewer_has_the_active_review(self, world, stalled):
        new = services.start_review(world['second'], world['idea'].pk)
        idea = Idea.objects.get(pk=world['idea'].pk)

        assert selectors.active_review_id_for(world['second'], idea) == new.pk
        assert not eligibility.can_start_review(world['third'], idea)

    def test_history_shows_the_withdrawn_round_to_the_author_and_reviewers(self, world, stalled):
        services.start_review(world['second'], world['idea'].pk)

        author_view = selectors.list_idea_reviews(world['author'], world['idea'].pk)
        reviewer_view = selectors.list_idea_reviews(world['third'], world['idea'].pk)

        assert [r.decision for r in author_view.reviews] == [Review.Decision.WITHDRAWN]
        assert [r.round for r in reviewer_view.reviews] == [1, 2]
        assert selectors.list_idea_reviews(world['member'], world['idea'].pk).reviews == []


# --- two reviewers at once --------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_take_overs_produce_exactly_one(world):
    services.start_review(world['reviewer'], world['idea'].pk)
    _leave(world)
    barrier = threading.Barrier(2)
    winners, errors = [], []

    def attempt(user):
        try:
            barrier.wait(timeout=10)
            winners.append(services.start_review(user, world['idea'].pk).reviewer_id)
        except services.ReviewError as exc:
            errors.append(exc.message)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt, args=(world[w],)) for w in ('second', 'third')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(winners) == 1
    assert errors == ['This idea is not waiting for review.']
    rows = reviews_of(world['idea'])
    assert [(r, d) for r, _e, d in rows] == [(1, Review.Decision.WITHDRAWN), (2, None)]
    assert Review.objects.get(round=2).reviewer_id == winners[0]
    assert Idea.objects.get(pk=world['idea'].pk).status == S.UNDER_REVIEW
    assert IdeaTransition.objects.filter(idea=world['idea']).count() == 1  # the start only


# --- GraphQL ----------------------------------------------------------------------------

START = """
mutation Start($ideaId: ID!) {
  startReview(ideaId: $ideaId) {
    success message review { round reviewerId decision } idea { status }
  }
}
"""

CAPABILITIES = """
query Idea($id: ID!) { idea(id: $id) { status viewerCanStartReview viewerActiveReviewId } }
"""

HISTORY = """
query History($ideaId: ID!) { ideaReviews(ideaId: $ideaId) { round decision reviewerId } }
"""

COMPLETE = """
mutation Complete($input: CompleteReviewInput!) {
  completeReview(input: $input) { success message field }
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


class TestGraphQL:
    def test_a_reviewer_is_offered_and_takes_over_a_stalled_review(self, gql, world, stalled):
        idea_id = world['idea'].pk

        offered = gql(CAPABILITIES, {'id': idea_id}, world['second'])['idea']
        assert offered == {
            'status': 'UNDER_REVIEW',
            'viewerCanStartReview': True,
            'viewerActiveReviewId': None,
        }

        result = gql(START, {'ideaId': idea_id}, world['second'])['startReview']
        assert result['success'] is True
        assert result['review'] == {
            'round': 2,
            'reviewerId': str(world['second'].pk),
            'decision': None,
        }
        assert result['idea']['status'] == 'UNDER_REVIEW'

        history = gql(HISTORY, {'ideaId': idea_id}, world['second'])['ideaReviews']
        assert [(r['round'], r['decision']) for r in history] == [(1, 'WITHDRAWN'), (2, None)]

    def test_nobody_is_offered_an_eligible_reviewers_review(self, gql, world, started):
        for who in ('second', 'member', 'author'):
            idea = gql(CAPABILITIES, {'id': world['idea'].pk}, world[who])['idea']
            assert idea['viewerCanStartReview'] is False, who

        refused = gql(START, {'ideaId': world['idea'].pk}, world['second'])['startReview']
        assert refused == {
            'success': False,
            'message': 'This idea is not waiting for review.',
            'review': None,
            'idea': None,
        }

    def test_withdrawn_cannot_be_sent_as_a_decision(self, gql, world, started):
        result = gql(
            COMPLETE,
            {
                'input': {
                    'ideaId': world['idea'].pk,
                    'reviewId': started.pk,
                    'decision': 'WITHDRAWN',
                    'assessments': [{'criterion': c.upper(), 'rating': 'MEETS'} for c in CRITERIA],
                }
            },
            world['reviewer'],
        )['completeReview']

        assert result == {'success': False, 'message': 'Choose a decision.', 'field': 'decision'}
        started.refresh_from_db()
        assert started.completed_at is None
