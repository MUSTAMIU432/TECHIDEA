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

from ideas.models import Idea, IdeaTransition
from identity.models import User
from identity.tokens import issue_access_token
from notifications.models import Notification
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import eligibility, selectors, services
from reviews.models import Review, ReviewCriterionAssessment
from reviews.tests.platform import (
    grant_platform_reviewer,
    make_submitted,
    revoke_platform_reviewer,
)

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
        # Platform review is authorized by a platform-scoped permission and by
        # nothing else, so a reviewer built here is a *platform* reviewer too.
        # The organization Reviewer role above still governs the organization
        # review queue, which is a separate track with separate rules.
        grant_platform_reviewer(user)
    return membership


def completion(review, decision='approved', feedback='Worth doing.'):
    return services.CompleteReviewInput(
        idea_id=review.idea_id,
        review_id=review.pk,
        decision=decision,
        feedback=feedback,
        assessments=tuple(services.AssessmentInput(c, 'meets') for c in CRITERIA),
    )


# Every way the holder of a platform review can stop being a platform reviewer.
#
# Each of these removes something that **actually** decides platform review. The
# organization Reviewer role is deliberately not one of them: platform review is
# authorized by a platform permission and never consults an organization role, so
# dropping that role would leave the reviewer perfectly eligible and these tests
# would pass for the wrong reason. `test_losing_the_organization_role_is_not_a_loss`
# pins that separately.
def _lose_permission(world):
    revoke_platform_reviewer(world['reviewer'])


def _deactivate(world):
    User.objects.filter(pk=world['reviewer'].pk).update(is_active=False)


def _lose_permission_and_leave(world):
    revoke_platform_reviewer(world['reviewer'])
    Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)


def _leave(world):
    """
    Just the membership, with the platform permission left in place.

    Kept as its own loss because it is the one that *should not* count: platform
    review does not consult organization membership at all, so a reviewer who
    leaves the organization is still a platform reviewer. `test_leaving_the
    organization_is_not_a_loss` pins that.
    """
    Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)


LOSSES = {
    'platform permission removed': _lose_permission,
    'account deactivated': _deactivate,
    'permission removed and membership deactivated': _lose_permission_and_leave,
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
    # Globex's reviewer is left as an **organization** reviewer and nothing more:
    # the platform permission is taken away again straight after `add_member`
    # granted it. Holding `idea.review` somewhere - even as a reviewer, even on a
    # PUBLIC idea - must grant nothing on the platform track, and the refusals
    # below are what prove it.
    revoke_platform_reviewer(globex_reviewer)
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
    """
    A review whose reviewer is no longer eligible to decide it.

    The loss is the **platform permission**, not the organization membership -
    see the `LOSSES` table. Leaving the organization is deliberately *not* a
    platform loss, and `test_organization_loss_is_not_a_platform_loss` asserts
    that, so a stalled review here has to be stalled for the real reason.
    """
    _lose_permission(world)
    return started


def reviews_of(idea, scope=Review.Scope.PLATFORM):
    """
    One track's rounds as `(round, reviewer, decision)`.

    **Scoped, and defaulted to the platform track**, because an idea in this
    fixture has already been confirmed by its organization before it reaches the
    platform - so an unscoped list would start with the organization's
    confirmation and every assertion about "round 1" would be about the wrong
    review. Pass `scope=ORGANIZATION` to see the other half.
    """
    return list(
        Review.objects.filter(idea=idea, scope=scope)
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

    @pytest.mark.parametrize(
        'loss',
        [
            # The organization Reviewer role does not decide platform review.
            lambda world: MembershipRole.objects.filter(
                membership__user=world['reviewer']
            ).delete(),
            # Nor does organization membership.
            lambda world: Membership.objects.filter(user=world['reviewer']).update(
                status=Membership.Status.INACTIVE
            ),
        ],
        ids=('reviewer role removed', 'membership deactivated'),
    )
    def test_organization_loss_is_not_a_platform_loss(self, world, started, loss):
        """
        Losing something on the *organization* side does not stall a review.

        The converse of the `LOSSES` table, and the assertion that keeps it
        honest: if dropping the role silently *did* count, the take-over tests
        would still pass while proving the wrong thing. It is also the practical
        reason the tracks are separate - an organization may reorganise its
        reviewers without disrupting the platform's work.
        """
        loss(world)

        with pytest.raises(services.ReviewError, match='not waiting for review'):
            services.start_review(world['second'], world['idea'].pk)

        started.refresh_from_db()
        assert started.completed_at is None, 'the round was withdrawn'
        assert reviews_of(world['idea']) == [(1, 'reviewer@acme.example', None)]

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
        before = list(
            IdeaTransition.objects.filter(idea=world['idea']).values_list('pk', flat=True)
        )

        services.start_review(world['second'], world['idea'].pk)

        # Nothing new. A take-over completes one round and opens the next
        # *without moving the idea*, so there is no status change to record - the
        # two review rows are the whole record.
        assert (
            list(IdeaTransition.objects.filter(idea=world['idea']).values_list('pk', flat=True))
            == before
        )

    def test_nobody_is_emailed(self, world, stalled, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            services.start_review(world['second'], world['idea'].pk)

        assert mail.outbox == []

    def test_a_take_over_decides_nothing_so_it_notifies_nobody(
        self, world, stalled, django_capture_on_commit_callbacks
    ):
        """
        A take-over is not a decision.

        Defence in depth: withdrawing round 1 and opening round 2 decides nothing
        about the idea, so it produces no notification and no email. The
        notification service is only ever reached from a decision, so there is no
        sender here to refuse a withdrawn round - the rule is enforced by the fact
        that nothing calls it.
        """
        with django_capture_on_commit_callbacks(execute=True):
            services.start_review(world['second'], world['idea'].pk)

        assert mail.outbox == []
        assert not Notification.objects.filter(idea=world['idea']).exists()

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
        # The second reviewer's *platform* permission is what makes them stallable.
        revoke_platform_reviewer(fresh(world['second']))

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
        # One **platform** review: the organization's confirmation that got the
        # idea onto the platform is a separate round in the other track.
        assert reviews_of(world['idea']) == [(1, 'reviewer@acme.example', None)]
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
        """
        Holding `idea.review` in another organization is not platform review.

        Worth pinning on a PUBLIC idea, because a public idea is readable by
        every signed-in user: being able to *read* the submission must not imply
        being able to *decide* it.
        """
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
        # `second` is a platform reviewer *and* an organization member; losing the
        # organization membership is not enough to stop them, so the platform
        # permission has to go as well.
        Membership.objects.filter(user=world['second']).update(status=Membership.Status.INACTIVE)
        revoke_platform_reviewer(fresh(world['second']))

        with pytest.raises(services.ReviewError):
            services.start_review(world['second'], world['idea'].pk)
        self.assert_untouched(world, stalled)

    @pytest.mark.parametrize('decision', ['approved', 'rejected', 'changes_requested'])
    def test_a_completed_review_cannot_be_taken_over(self, world, started, decision):
        services.complete_review(world['reviewer'], completion(started, decision))
        _lose_permission(world)

        with pytest.raises(services.ReviewError):
            services.start_review(world['second'], world['idea'].pk)

        started.refresh_from_db()
        assert started.decision == decision
        assert len(reviews_of(world['idea'])) == 1

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

        # Only losing the platform permission stalls it.
        _lose_permission(world)

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

        assert [r.decision for r in author_view.reviews] == [
            Review.Decision.CONFIRMED,
            Review.Decision.WITHDRAWN,
        ]
        # `third` is a platform reviewer, so they see the organization's
        # confirmation too - both tracks, each numbering its own rounds.
        assert [r.round for r in reviewer_view.reviews] == [1, 1, 2]
        assert selectors.list_idea_reviews(world['member'], world['idea'].pk).reviews == []


# --- two reviewers at once --------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_take_overs_produce_exactly_one(world):
    services.start_review(world['reviewer'], world['idea'].pk)
    _lose_permission(world)
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
    assert (
        Review.objects.get(idea=world['idea'], scope=Review.Scope.PLATFORM, round=2).reviewer_id
        == winners[0]
    )
    assert Idea.objects.get(pk=world['idea'].pk).status == S.UNDER_REVIEW
    # Four moves and no more: the author's submit, the organization's
    # confirmation, the author's submit to the platform, and the reviewer's
    # claim. The take-over itself moves nothing, which is the point.
    assert IdeaTransition.objects.filter(idea=world['idea']).count() == 4


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
query History($ideaId: ID!) { ideaReviews(ideaId: $ideaId) { scope round decision reviewerId } }
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

        all_rows = gql(HISTORY, {'ideaId': idea_id}, world['second'])['ideaReviews']
        # Both tracks, each numbering its own rounds: the organization's
        # confirmation is its round 1, the platform's withdrawn round and its
        # replacement are rounds 1 and 2.
        assert [(r['scope'], r['round'], r['decision']) for r in all_rows] == [
            ('ORGANIZATION', 1, 'CONFIRMED'),
            ('PLATFORM', 1, 'WITHDRAWN'),
            ('PLATFORM', 2, None),
        ]

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
