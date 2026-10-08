"""
Review models and their database constraints (S3-002).

Each constraint is tested twice where it can be: through `save()` (which runs
`full_clean()` and is what every domain write will use) and underneath it,
with `bulk_create` or a queryset `update`, which skip model validation and
therefore reach the database's own constraint. The second half is the point:
it proves the rule holds for a write path that forgot to validate.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership
from organizations.services import CreateOrganizationInput, create_organization_for_user
from reviews.models import SCOPE_DECISIONS, Review, ReviewCriterionAssessment

VALID_PASSWORD = 'a-strong-unique-pass-1'
SNAPSHOT = {'title': 'An idea', 'description': 'A description long enough.', 'category': 'Ops'}


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password=VALID_PASSWORD,
    )


@pytest.fixture
def world():
    owner = make_user('owner@acme.example')
    organization = create_organization_for_user(
        owner, CreateOrganizationInput(name='Acme')
    ).organization
    author = make_user('author@acme.example')
    Membership.objects.create(user=author, organization=organization)
    idea = Idea.objects.create(
        organization=organization,
        author=author,
        title='An idea',
        description='A description long enough.',
        category=Category.objects.create(name='Ops'),
        visibility=Idea.Visibility.ORGANIZATION,
        status=Idea.Status.UNDER_REVIEW,
        submitted_at=timezone.now(),
    )
    return {'owner': owner, 'author': author, 'idea': idea, 'reviewer': owner}


def open_review(world, **overrides):
    fields = {
        'idea': world['idea'],
        'reviewer': world['reviewer'],
        'round': 1,
        'submission_snapshot': SNAPSHOT,
    }
    fields.update(overrides)
    return Review.objects.create(**fields)


def complete(review, decision=Review.Decision.APPROVED):
    review.decision = decision
    review.feedback = 'Looks worth doing.'
    review.completed_at = timezone.now()
    review.save()
    return review


def db_reject(model, **fields):
    """Insert beneath `full_clean()` and expect the database to refuse."""
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.bulk_create([model(**fields)])


@pytest.mark.django_db
class TestReview:
    def test_an_open_review_has_no_decision_and_no_completion(self, world):
        review = open_review(world)
        review.refresh_from_db()

        assert review.decision is None
        assert review.completed_at is None
        assert review.feedback == ''
        assert review.is_completed is False
        assert review.created_at is not None

    def test_the_snapshot_is_stored_as_given(self, world):
        review = open_review(world)
        review.refresh_from_db()

        assert review.submission_snapshot == SNAPSHOT

    def test_a_snapshot_must_be_an_object(self, world):
        with pytest.raises(ValidationError):
            open_review(world, submission_snapshot=['not', 'an', 'object'])

    def test_completing_a_review(self, world):
        review = complete(open_review(world), Review.Decision.CHANGES_REQUESTED)
        review.refresh_from_db()

        assert review.decision == Review.Decision.CHANGES_REQUESTED
        assert review.is_completed

    def test_the_decisions_are_the_idea_statuses_they_lead_to(self):
        """
        One vocabulary, two scopes.

        The platform's three verdicts are idea statuses, and `CONFIRMED` - the
        organization track's one decision - leads to `ORGANIZATION_CONFIRMED`.
        `WITHDRAWN` is the odd one out: a take-over leads to no status at all,
        which is what makes it a release rather than a verdict.
        """
        assert set(Review.Decision.values) == {
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.APPROVED,
            Idea.Status.REJECTED,
            Review.Decision.CONFIRMED,
            Review.Decision.WITHDRAWN,
        }
        assert Review.Decision.WITHDRAWN not in Idea.Status.values

    def test_each_scope_may_record_only_its_own_decisions(self, world):
        """
        "An organization cannot approve" and "the platform cannot confirm",
        as a property of the rows rather than of a service.

        Both directions are refused by the model, so no future code path - a
        resolver, a management command, a bulk write - can produce an
        organization review that says `APPROVED`.
        """
        for scope, allowed in (
            (
                Review.Scope.ORGANIZATION,
                {Review.Decision.CONFIRMED, Review.Decision.CHANGES_REQUESTED},
            ),
            (
                Review.Scope.PLATFORM,
                {
                    Review.Decision.APPROVED,
                    Review.Decision.REJECTED,
                    Review.Decision.CHANGES_REQUESTED,
                },
            ),
        ):
            assert set(SCOPE_DECISIONS[scope]) == allowed, scope
            for decision in Review.Decision.values:
                if decision == Review.Decision.WITHDRAWN or decision in allowed:
                    continue
                with pytest.raises(ValidationError):
                    Review.objects.create(
                        idea=world['idea'],
                        reviewer=world['reviewer'],
                        scope=scope,
                        round=world['idea'].reviews.count() + 1,
                        decision=decision,
                        submission_snapshot=SNAPSHOT,
                    )

    def test_the_author_cannot_be_the_reviewer(self, world):
        with pytest.raises(ValidationError):
            open_review(world, reviewer=world['author'])

    def test_rounds_order_an_ideas_history(self, world):
        first = complete(open_review(world), Review.Decision.CHANGES_REQUESTED)
        second = open_review(world, round=2)

        assert list(world['idea'].reviews.all()) == [first, second]


@pytest.mark.django_db
class TestRound:
    def test_round_starts_at_one(self, world):
        with pytest.raises(ValidationError):
            open_review(world, round=0)

    def test_round_zero_is_refused_by_the_database(self, world):
        db_reject(
            Review,
            idea=world['idea'],
            reviewer=world['reviewer'],
            round=0,
            submission_snapshot=SNAPSHOT,
        )

    def test_a_round_is_unique_per_idea(self, world):
        complete(open_review(world))

        with pytest.raises(ValidationError):
            open_review(world)

    def test_a_duplicate_round_is_refused_by_the_database(self, world):
        complete(open_review(world))

        db_reject(
            Review,
            idea=world['idea'],
            reviewer=world['reviewer'],
            round=1,
            decision=Review.Decision.APPROVED,
            completed_at=timezone.now(),
            submission_snapshot=SNAPSHOT,
        )


@pytest.mark.django_db
class TestOneOpenReviewPerIdea:
    def test_a_second_open_review_is_refused(self, world):
        open_review(world)

        with pytest.raises(ValidationError):
            open_review(world, round=2)

    def test_a_second_open_review_is_refused_by_the_database(self, world):
        open_review(world)

        db_reject(
            Review,
            idea=world['idea'],
            reviewer=world['reviewer'],
            round=2,
            submission_snapshot=SNAPSHOT,
        )

    def test_a_new_round_may_open_once_the_previous_one_is_completed(self, world):
        complete(open_review(world), Review.Decision.CHANGES_REQUESTED)

        assert open_review(world, round=2).completed_at is None

    def test_other_ideas_are_unaffected(self, world):
        open_review(world)
        other = Idea.objects.create(
            organization=world['idea'].organization,
            author=world['author'],
            title='Another',
            status=Idea.Status.UNDER_REVIEW,
            submitted_at=timezone.now(),
        )

        assert open_review(world, idea=other).completed_at is None


@pytest.mark.django_db
class TestDecisionConsistency:
    def test_a_decision_without_completion_is_refused(self, world):
        with pytest.raises(ValidationError):
            open_review(world, decision=Review.Decision.APPROVED)

    def test_completion_without_a_decision_is_refused(self, world):
        with pytest.raises(ValidationError):
            open_review(world, completed_at=timezone.now())

    @pytest.mark.parametrize(
        'fields',
        [
            {'decision': 'approved'},
            {'completed_at': timezone.now()},
        ],
    )
    def test_the_pairing_is_enforced_by_the_database(self, world, fields):
        db_reject(
            Review,
            idea=world['idea'],
            reviewer=world['reviewer'],
            round=1,
            submission_snapshot=SNAPSHOT,
            **fields,
        )

    def test_an_unknown_decision_is_refused(self, world):
        with pytest.raises(ValidationError):
            open_review(world, decision='promoted', completed_at=timezone.now())

    def test_an_unknown_decision_is_refused_by_the_database(self, world):
        db_reject(
            Review,
            idea=world['idea'],
            reviewer=world['reviewer'],
            round=1,
            decision='promoted',
            completed_at=timezone.now(),
            submission_snapshot=SNAPSHOT,
        )


@pytest.mark.django_db
class TestImmutability:
    def test_a_completed_review_cannot_be_saved_again(self, world):
        review = complete(open_review(world), Review.Decision.REJECTED)

        review.decision = Review.Decision.APPROVED
        with pytest.raises(ValidationError):
            review.save()

        review.refresh_from_db()
        assert review.decision == Review.Decision.REJECTED

    def test_a_completed_review_cannot_be_reopened(self, world):
        review = complete(open_review(world))

        review.decision = None
        review.completed_at = None
        with pytest.raises(ValidationError):
            review.save()

    def test_an_open_review_can_still_be_changed(self, world):
        review = open_review(world)

        review.feedback = 'Draft note.'
        review.save()

        review.refresh_from_db()
        assert review.feedback == 'Draft note.'


@pytest.mark.django_db
class TestReviewerProtect:
    def test_a_reviewer_with_reviews_cannot_be_deleted(self, world):
        open_review(world)

        with pytest.raises(ProtectedError):
            world['reviewer'].delete()

    def test_deleting_the_idea_removes_its_reviews(self, world):
        review = open_review(world)

        world['idea'].delete()

        assert not Review.objects.filter(pk=review.pk).exists()


@pytest.mark.django_db
class TestCriterionAssessment:
    def assess(self, review, criterion, rating=ReviewCriterionAssessment.Rating.MEETS):
        return ReviewCriterionAssessment.objects.create(
            review=review, criterion=criterion, rating=rating
        )

    def test_the_fixed_criteria(self):
        assert ReviewCriterionAssessment.Criterion.names == [
            'PROBLEM_CLARITY',
            'AUTOMATION_SUITABILITY',
            'FEASIBILITY',
            'EXPECTED_BENEFIT',
            'EVIDENCE',
        ]

    def test_the_ratings_are_categorical(self):
        assert ReviewCriterionAssessment.Rating.names == [
            'MEETS',
            'PARTIALLY_MEETS',
            'DOES_NOT_MEET',
            'NOT_APPLICABLE',
        ]

    def test_every_criterion_can_be_assessed(self, world):
        review = open_review(world)

        for criterion in ReviewCriterionAssessment.Criterion.values:
            self.assess(review, criterion, ReviewCriterionAssessment.Rating.NOT_APPLICABLE)

        assert review.assessments.count() == len(ReviewCriterionAssessment.Criterion.values)

    def test_a_criterion_is_assessed_once_per_review(self, world):
        review = open_review(world)
        self.assess(review, ReviewCriterionAssessment.Criterion.FEASIBILITY)

        with pytest.raises(ValidationError):
            self.assess(review, ReviewCriterionAssessment.Criterion.FEASIBILITY)

    def test_a_duplicate_criterion_is_refused_by_the_database(self, world):
        review = open_review(world)
        self.assess(review, ReviewCriterionAssessment.Criterion.FEASIBILITY)

        db_reject(
            ReviewCriterionAssessment,
            review=review,
            criterion='feasibility',
            rating='meets',
        )

    def test_the_same_criterion_may_appear_in_different_reviews(self, world):
        first = open_review(world)
        self.assess(first, ReviewCriterionAssessment.Criterion.EVIDENCE)
        complete(first, Review.Decision.CHANGES_REQUESTED)
        second = open_review(world, round=2)

        self.assess(second, ReviewCriterionAssessment.Criterion.EVIDENCE)

    @pytest.mark.parametrize(
        ('criterion', 'rating'),
        [('impact', 'meets'), ('feasibility', 'excellent'), ('feasibility', '5')],
    )
    def test_unknown_criteria_and_ratings_are_refused(self, world, criterion, rating):
        review = open_review(world)

        with pytest.raises(ValidationError):
            self.assess(review, criterion, rating)
        db_reject(ReviewCriterionAssessment, review=review, criterion=criterion, rating=rating)

    def test_a_completed_review_cannot_gain_assessments(self, world):
        review = complete(open_review(world))

        with pytest.raises(ValidationError):
            self.assess(review, ReviewCriterionAssessment.Criterion.EVIDENCE)
