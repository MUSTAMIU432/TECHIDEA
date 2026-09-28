"""
Review operations (S3-004): `reviews.services.start_review` and
`complete_review`.

What must hold, in the order a failure would hurt:

1. **Atomicity.** A review and the status move it causes commit together or
   not at all - a refused completion leaves no assessment, no decision and no
   moved idea.
2. **Concurrency.** Two reviewers starting the same idea at once: exactly one
   review, exactly one winner. Tested with two real connections, because a
   single-connection test cannot race itself.
3. **Authorization on every call**, never from the client's capability flags:
   eligibility is asked again after the lock, so a role removed, a
   membership deactivated or a visibility narrowed since the page loaded takes
   effect.
4. **Ownership and immutability.** Only the review's own reviewer completes
   it, once.
"""

import threading

import pytest
from django.db import connections
from django.utils import timezone

from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import services
from reviews.models import Review, ReviewCriterionAssessment

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'
CRITERIA = ReviewCriterionAssessment.Criterion.values


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


def make_idea(organization, author, *, status=Idea.Status.SUBMITTED, visibility=None):
    return Idea.objects.create(
        organization=organization,
        author=author,
        title='Automate the invoice run',
        description=DESCRIPTION,
        category=Category.objects.create(name=f'Cat {Category.objects.count() + 1}'),
        visibility=visibility or Idea.Visibility.ORGANIZATION,
        status=status,
        submitted_at=None if status == Idea.Status.DRAFT else timezone.now(),
    )


def assessments(rating='meets', **overrides):
    return tuple(
        services.AssessmentInput(criterion=c, rating=overrides.get(c, rating), note=f'on {c}')
        for c in CRITERIA
    )


def completion(review, decision='approved', feedback='Worth doing.', items=None):
    return services.CompleteReviewInput(
        idea_id=review.idea_id,
        review_id=review.pk,
        decision=decision,
        feedback=feedback,
        assessments=assessments() if items is None else items,
    )


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
    globex = create_organization_for_user(
        globex_owner, CreateOrganizationInput(name='Globex')
    ).organization
    globex_reviewer = make_user('reviewer@globex.example')
    add_member(globex, globex_reviewer, reviewer=True)
    return {
        'acme': acme,
        'owner': owner,
        'author': author,
        'reviewer': reviewer,
        'second': second,
        'member': member,
        'globex_reviewer': globex_reviewer,
        'idea': make_idea(acme, author),
    }


# --- start ------------------------------------------------------------------------------


class TestStartReview:
    def test_it_opens_round_one_with_a_snapshot_and_moves_the_idea(self, world):
        review = services.start_review(world['reviewer'], world['idea'].pk)

        assert review.round == 1
        assert review.reviewer == world['reviewer']
        assert review.completed_at is None
        assert review.submission_snapshot['title'] == 'Automate the invoice run'
        assert review.submission_snapshot['description'] == DESCRIPTION
        assert review.submission_snapshot['category']['name'].startswith('Cat')
        world['idea'].refresh_from_db()
        assert world['idea'].status == Idea.Status.UNDER_REVIEW

    def test_the_next_round_follows_the_last(self, world):
        idea = world['idea']
        Review.objects.create(
            idea=idea,
            reviewer=world['second'],
            round=1,
            submission_snapshot={'title': 'x'},
            decision='changes_requested',
            feedback='Numbers.',
            completed_at=timezone.now(),
        )

        assert services.start_review(world['reviewer'], idea.pk).round == 2

    @pytest.mark.parametrize(
        'status', [s for s in Idea.Status.values if s != Idea.Status.SUBMITTED]
    )
    def test_only_a_submitted_idea(self, world, status):
        idea = make_idea(world['acme'], world['author'], status=status)

        with pytest.raises(services.ReviewError, match='not waiting for review'):
            services.start_review(world['reviewer'], idea.pk)
        assert not Review.objects.filter(idea=idea).exists()

    def test_a_second_start_is_refused(self, world):
        services.start_review(world['reviewer'], world['idea'].pk)

        with pytest.raises(services.ReviewError, match='not waiting for review'):
            services.start_review(world['second'], world['idea'].pk)
        assert Review.objects.filter(idea=world['idea']).count() == 1

    @pytest.mark.parametrize('who', ['author', 'member'])
    def test_readers_who_are_not_its_reviewers(self, world, who):
        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.start_review(world[who], world['idea'].pk)
        world['idea'].refresh_from_db()
        assert world['idea'].status == Idea.Status.SUBMITTED

    def test_the_owner_on_their_own_idea(self, world):
        idea = make_idea(world['acme'], world['owner'])

        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.start_review(world['owner'], idea.pk)

    def test_another_organizations_reviewer_on_a_public_idea(self, world):
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.start_review(world['globex_reviewer'], idea.pk)

    def test_unreadable_ideas_answer_like_missing_ones(self, world):
        private = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PRIVATE)

        for who, idea_id in (
            ('globex_reviewer', world['idea'].pk),
            ('reviewer', private.pk),
            ('reviewer', 999_999),
            ('reviewer', 'nope'),
        ):
            with pytest.raises(services.ReviewError) as exc_info:
                services.start_review(world[who], idea_id)
            assert exc_info.value.message == 'Idea is unavailable.', (who, idea_id)

    def test_anonymous_and_deactivated(self, world):
        with pytest.raises(services.ReviewError) as exc_info:
            services.start_review(None, world['idea'].pk)
        assert exc_info.value.reason == 'unauthenticated'

        User.objects.filter(pk=world['reviewer'].pk).update(is_active=False)
        world['reviewer'].refresh_from_db()
        with pytest.raises(services.ReviewError):
            services.start_review(world['reviewer'], world['idea'].pk)

    def test_an_inactive_membership(self, world):
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)

        with pytest.raises(services.ReviewError):
            services.start_review(world['reviewer'], world['idea'].pk)

    def test_a_removed_role_takes_effect_whatever_the_client_was_shown(self, world):
        """The stale-capability case: eligible when the page loaded, not now."""
        MembershipRole.objects.filter(
            membership__user=world['reviewer'], role__slug=REVIEWER_ROLE_SLUG
        ).delete()

        with pytest.raises(services.ReviewError, match='not allowed to review'):
            services.start_review(world['reviewer'], world['idea'].pk)
        assert not Review.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_starts_produce_exactly_one_review(world):
    """
    Two reviewers press Start at once. The row lock makes the second wait,
    re-read `UNDER_REVIEW` and be refused: one review, one winner, one refusal.
    """
    idea = world['idea']
    errors: list[Exception] = []
    winners: list[int] = []
    barrier = threading.Barrier(2)

    def attempt(user):
        try:
            barrier.wait(timeout=10)
            winners.append(services.start_review(user, idea.pk).reviewer_id)
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [
        threading.Thread(target=attempt, args=(world[who],)) for who in ('reviewer', 'second')
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(winners) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], services.ReviewError)
    assert Review.objects.filter(idea=idea).count() == 1
    assert Review.objects.get(idea=idea).reviewer_id == winners[0]
    assert Idea.objects.get(pk=idea.pk).status == Idea.Status.UNDER_REVIEW


# --- complete ---------------------------------------------------------------------------


@pytest.fixture
def started(world):
    return services.start_review(world['reviewer'], world['idea'].pk)


class TestCompleteReview:
    @pytest.mark.parametrize('decision', ['changes_requested', 'approved', 'rejected'])
    def test_each_decision_records_everything_and_moves_the_idea(self, world, started, decision):
        review = services.complete_review(world['reviewer'], completion(started, decision))

        assert review.decision == decision
        assert review.feedback == 'Worth doing.'
        assert review.completed_at is not None
        assert {a.criterion: a.note for a in review.assessments.all()} == {
            c: f'on {c}' for c in CRITERIA
        }
        world['idea'].refresh_from_db()
        assert world['idea'].status == decision

    def test_approval_may_omit_feedback(self, world, started):
        review = services.complete_review(world['reviewer'], completion(started, feedback='  '))

        assert review.feedback == ''

    @pytest.mark.parametrize('decision', ['changes_requested', 'rejected'])
    def test_feedback_is_required_to_send_back_or_turn_down(self, world, started, decision):
        with pytest.raises(services.ReviewError) as exc_info:
            services.complete_review(world['reviewer'], completion(started, decision, feedback=''))

        assert exc_info.value.field == 'feedback'

    def test_not_applicable_is_a_rating(self, world, started):
        items = assessments(rating='not_applicable')

        review = services.complete_review(world['reviewer'], completion(started, items=items))

        assert set(review.assessments.values_list('rating', flat=True)) == {'not_applicable'}

    @pytest.mark.parametrize(
        ('items', 'field'),
        [
            (assessments()[:-1], 'assessments'),
            (
                (*assessments()[:-1], services.AssessmentInput('problem_clarity', 'meets')),
                'assessments',
            ),
            ((*assessments()[:-1], services.AssessmentInput('impact', 'meets')), 'assessments'),
            (
                (*assessments()[:-1], services.AssessmentInput('evidence', 'excellent')),
                'assessments',
            ),
            ((), 'assessments'),
        ],
        ids=['missing', 'duplicate', 'unknown-criterion', 'unknown-rating', 'none'],
    )
    def test_every_criterion_exactly_once_with_a_known_rating(self, world, started, items, field):
        with pytest.raises(services.ReviewError) as exc_info:
            services.complete_review(world['reviewer'], completion(started, items=items))

        assert exc_info.value.field == field
        self.assert_untouched(world, started)

    def test_an_unknown_decision(self, world, started):
        with pytest.raises(services.ReviewError) as exc_info:
            services.complete_review(world['reviewer'], completion(started, decision='promoted'))
        assert exc_info.value.field == 'decision'

    def test_overlong_text_is_refused(self, world, started):
        with pytest.raises(services.ReviewError):
            services.complete_review(world['reviewer'], completion(started, feedback='x' * 5001))
        long_note = tuple(
            services.AssessmentInput(a.criterion, a.rating, 'x' * 1001) for a in assessments()
        )
        with pytest.raises(services.ReviewError):
            services.complete_review(world['reviewer'], completion(started, items=long_note))

    def test_another_reviewer_cannot_complete_it(self, world, started):
        with pytest.raises(services.ReviewError, match='Review is unavailable'):
            services.complete_review(world['second'], completion(started))
        self.assert_untouched(world, started)

    @pytest.mark.parametrize('who', ['author', 'member', 'globex_reviewer'])
    def test_nobody_else_learns_anything(self, world, started, who):
        with pytest.raises(services.ReviewError) as exc_info:
            services.complete_review(world[who], completion(started))

        assert exc_info.value.message in {
            'Idea is unavailable.',
            'You are not allowed to review this idea.',
        }
        self.assert_untouched(world, started)

    def test_a_review_id_under_the_wrong_idea(self, world, started):
        other = make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)
        data = services.CompleteReviewInput(
            idea_id=other.pk,
            review_id=started.pk,
            decision='approved',
            feedback='',
            assessments=assessments(),
        )

        with pytest.raises(services.ReviewError, match='Review is unavailable'):
            services.complete_review(world['reviewer'], data)

    @pytest.mark.parametrize('review_id', [999_999, 'nope', None])
    def test_guessed_review_ids(self, world, started, review_id):
        data = services.CompleteReviewInput(
            idea_id=started.idea_id,
            review_id=review_id,
            decision='approved',
            feedback='',
            assessments=assessments(),
        )

        with pytest.raises(services.ReviewError, match='Review is unavailable'):
            services.complete_review(world['reviewer'], data)

    def test_double_completion_is_refused_and_changes_nothing(self, world, started):
        services.complete_review(world['reviewer'], completion(started, 'rejected', 'No.'))

        with pytest.raises(services.ReviewError, match='already been completed'):
            services.complete_review(world['reviewer'], completion(started, 'approved'))

        started.refresh_from_db()
        assert started.decision == 'rejected'
        assert started.assessments.count() == len(CRITERIA)

    def test_a_reviewer_who_lost_eligibility_mid_review(self, world, started):
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)

        with pytest.raises(services.ReviewError):
            services.complete_review(world['reviewer'], completion(started))
        self.assert_untouched(world, started)

    def test_anonymous(self, world, started):
        with pytest.raises(services.ReviewError) as exc_info:
            services.complete_review(None, completion(started))
        assert exc_info.value.reason == 'unauthenticated'

    def test_a_failure_after_the_review_is_written_rolls_everything_back(
        self, world, started, monkeypatch
    ):
        """
        Atomicity, proved by breaking the last step: the lifecycle refuses
        after the assessments and the decision were written, and none of it
        survives.
        """
        from ideas import lifecycle
        from ideas.services import IdeaError

        def refuse(*args, **kwargs):
            raise IdeaError('Refused for the test.')

        monkeypatch.setattr(lifecycle, 'apply_review_transition', refuse)

        with pytest.raises(services.ReviewError, match='Refused for the test'):
            services.complete_review(world['reviewer'], completion(started))
        self.assert_untouched(world, started)

    @staticmethod
    def assert_untouched(world, review):
        review.refresh_from_db()
        assert review.completed_at is None
        assert review.decision is None
        assert review.assessments.count() == 0
        assert Idea.objects.get(pk=world['idea'].pk).status == Idea.Status.UNDER_REVIEW
