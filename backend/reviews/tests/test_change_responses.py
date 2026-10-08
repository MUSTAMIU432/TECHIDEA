"""The owner's response to a request for changes, and the notification it answers."""

import pytest

from ideas import services as idea_services
from ideas.models import Category, Idea
from identity.models import User
from notifications.models import Notification
from reviews import change_responses, services
from reviews.models import ChangeResponse, Review, ReviewCriterionAssessment
from reviews.tests.platform import grant_platform_reviewer, submit

PASSWORD = 'a-strong-unique-pass-1'
CRITERIA = [c.value for c in ReviewCriterionAssessment.Criterion]


def make_user(email):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
    )


@pytest.fixture
def owner(db):
    return make_user('owner@example.com')


@pytest.fixture
def reviewer(db):
    user = make_user('reviewer@example.com')
    grant_platform_reviewer(user, console=False)
    return User.objects.get(pk=user.pk)


@pytest.fixture
def sent_back(owner, reviewer):
    """An individual idea the platform asked to change, as the owner finds it."""
    draft = Idea.objects.create(
        author=owner,
        title='Tax collection',
        description='Taxes are collected on paper and reconciled by hand.',
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        visibility=Idea.Visibility.PRIVATE,
        category=Category.objects.get_or_create(name='Responses Fixture')[0],
    )
    idea = submit(draft)
    review = services.start_review(reviewer, idea.pk)
    services.complete_review(
        reviewer,
        services.CompleteReviewInput(
            idea_id=idea.pk,
            review_id=review.pk,
            decision='changes_requested',
            feedback='Please attach the collection forms.',
            assessments=tuple(services.AssessmentInput(c, 'meets', '') for c in CRITERIA),
        ),
    )
    idea.refresh_from_db()
    assert idea.status == Idea.Status.CHANGES_REQUESTED
    return idea


def unread_changes_requests(owner, idea):
    return Notification.objects.filter(
        user=owner, idea=idea, kind='idea.platform_changes_requested', is_read_at__isnull=True
    ).count()


@pytest.mark.django_db(transaction=True)
class TestResponding:
    def test_a_response_is_kept_and_the_idea_goes_back_to_the_reviewers(
        self, owner, reviewer, sent_back
    ):
        assert unread_changes_requests(owner, sent_back) == 1

        response, idea = change_responses.respond(
            owner, sent_back.pk, 'Attached the forms for March and April.'
        )

        assert idea.status == Idea.Status.SUBMITTED
        assert response.review == Review.objects.get(idea=sent_back, decision='changes_requested')
        assert response.message == 'Attached the forms for March and April.'
        # The request was answered, so it stops waiting in the owner's bell.
        assert unread_changes_requests(owner, sent_back) == 0

    def test_resubmitting_from_the_form_also_clears_the_notification(self, owner, sent_back):
        idea_services.submit_idea(owner, sent_back.pk)

        assert unread_changes_requests(owner, sent_back) == 0
        assert not ChangeResponse.objects.exists()


@pytest.mark.django_db
class TestRefusals:
    def test_an_empty_response_is_refused_and_nothing_moves(self, owner, sent_back):
        with pytest.raises(change_responses.ChangeResponseError, match='what you changed'):
            change_responses.respond(owner, sent_back.pk, '   ')

        sent_back.refresh_from_db()
        assert sent_back.status == Idea.Status.CHANGES_REQUESTED

    def test_only_the_author_can_answer(self, reviewer, sent_back):
        with pytest.raises(change_responses.ChangeResponseError):
            change_responses.respond(make_user('stranger@example.com'), sent_back.pk, 'Done.')
        with pytest.raises(change_responses.ChangeResponseError):
            change_responses.respond(reviewer, sent_back.pk, 'Done.')
        assert not ChangeResponse.objects.exists()

    def test_there_must_be_a_request_to_answer(self, owner, sent_back):
        change_responses.respond(owner, sent_back.pk, 'Attached the forms.')

        with pytest.raises(change_responses.ChangeResponseError, match='Nobody is waiting'):
            change_responses.respond(owner, sent_back.pk, 'Again.')


@pytest.mark.django_db
class TestReading:
    def test_the_author_and_the_reviewers_read_it_and_nobody_else(self, owner, reviewer, sent_back):
        change_responses.respond(owner, sent_back.pk, 'Attached the forms.')

        for reader in (owner, reviewer):
            (view,) = change_responses.responses_for(reader, sent_back.pk)
            assert view.response.message == 'Attached the forms.'
            assert view.review.round == 1
        assert change_responses.responses_for(make_user('stranger@example.com'), sent_back.pk) == []
