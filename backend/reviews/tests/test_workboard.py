"""A reviewer's work list, and notifications that lead to the page where the work is done."""

import pytest

from administration import services as admin_services
from ideas.models import Category, Idea
from identity.models import User
from notifications.models import Notification
from reviews import decision_letters, proposals, services, workboard
from reviews.models import DecisionLetter, Review, ReviewCriterionAssessment
from reviews.tests.platform import grant_permission, grant_platform_reviewer, submit

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
def reviewer(db):
    user = make_user('reviewer@example.com')
    grant_platform_reviewer(user, console=False)
    return User.objects.get(pk=user.pk)


@pytest.fixture
def admin(db):
    user = make_user('admin@example.com')
    admin_services.grant_platform_admin(user.email)
    grant_permission(user, 'administration.release_proposals')
    return User.objects.get(pk=user.pk)


@pytest.fixture
def idea(db):
    draft = Idea.objects.create(
        author=make_user('owner@example.com'),
        title='Tax collection',
        description='Taxes are collected on paper and reconciled by hand.',
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        visibility=Idea.Visibility.PRIVATE,
        category=Category.objects.get_or_create(name='Workboard Fixture')[0],
    )
    return submit(draft)


def stage(reviewer):
    (item,) = workboard.workboard_for(reviewer)
    return item.stage, item.step, item.action_label, item.action_path


@pytest.mark.django_db
class TestTheWorkList:
    def test_it_follows_the_idea_from_the_review_to_the_proposal(self, reviewer, admin, idea):
        review = services.start_review(reviewer, idea.pk)
        assert stage(reviewer) == (
            'reviewing',
            0,
            'Continue the review',
            f'/app/reviews?idea={idea.pk}',
        )

        services.complete_review(
            reviewer,
            services.CompleteReviewInput(
                idea_id=idea.pk,
                review_id=review.pk,
                decision='approved',
                feedback='Worth doing.',
                assessments=tuple(services.AssessmentInput(c, 'meets', '') for c in CRITERIA),
            ),
        )
        # Decided, and nothing for the reviewer to do until the admin tells the owner.
        assert stage(reviewer)[:3] == ('awaiting_admin', 1, None)

        decision_letters.send(admin, DecisionLetter.objects.get(idea=idea).pk)
        assert stage(reviewer) == (
            'write_proposal',
            2,
            'Start the proposal',
            f'/app/reviews/proposals/{idea.pk}',
        )

        proposals.start_proposal(reviewer, idea.pk)
        assert stage(reviewer)[:3] == ('writing', 2, 'Continue the proposal')

    def test_items_waiting_on_the_reviewer_come_first(self, reviewer, idea):
        services.start_review(reviewer, idea.pk)
        other = submit(
            Idea.objects.create(
                author=idea.author,
                title='Hostel payments',
                description='Payments are tracked by hand.',
                submission_context=Idea.SubmissionContext.INDIVIDUAL,
                visibility=Idea.Visibility.PRIVATE,
                category=idea.category,
            )
        )
        review = services.start_review(reviewer, other.pk)
        services.complete_review(
            reviewer,
            services.CompleteReviewInput(
                idea_id=other.pk,
                review_id=review.pk,
                decision='changes_requested',
                feedback='Add the forms.',
                assessments=tuple(services.AssessmentInput(c, 'meets', '') for c in CRITERIA),
            ),
        )

        items = workboard.workboard_for(reviewer)
        assert [(i.idea.title, i.needs_me) for i in items] == [
            ('Tax collection', True),
            ('Hostel payments', False),
        ]

    def test_nothing_for_anybody_but_a_platform_reviewer(self, idea):
        assert workboard.workboard_for(idea.author) == []
        assert workboard.workboard_for(None) == []
        assert not Review.objects.exists()


@pytest.mark.django_db
class TestNotificationsLeadToTheWork:
    def test_each_kind_opens_the_page_where_it_is_acted_on(self, reviewer, idea):
        def path(kind, user=reviewer):
            return Notification(user=user, kind=kind, title='t', body='b', idea=idea).action_path

        assert path('review.platform_queue') == f'/app/reviews?idea={idea.pk}'
        assert path('proposal.writing_opened') == f'/app/reviews/proposals/{idea.pk}'
        assert path('proposal.changes_requested') == f'/app/reviews/proposals/{idea.pk}'
        assert path('proposal.released', idea.author) == f'/app/ideas/{idea.pk}/proposal'
        assert path('review.decision_awaiting_release') == '/app/admin/decisions'
        assert path('proposal.submitted') == '/app/admin/proposals'


@pytest.mark.django_db
class TestOwnerNotificationsLeadToTheirSection:
    def test_the_owner_lands_on_the_section_the_news_is_about(self, idea):
        def path(kind):
            return Notification(
                user=idea.author, kind=kind, title='t', body='b', idea=idea
            ).action_path

        assert path('idea.platform_changes_requested') == f'/app/ideas/{idea.pk}#respond'
        assert path('idea.platform_approved') == f'/app/ideas/{idea.pk}#decision-letter'
        assert path('idea.platform_rejected') == f'/app/ideas/{idea.pk}#decision-letter'
        assert path('proposal.declined') == f'/app/ideas/{idea.pk}#proposal'

    def test_delivery_news_opens_the_project_on_its_tab(self):
        from automation import delivery
        from automation.tests.test_opportunity import make_user as make_member
        from automation.tests.test_opportunity import opened

        author = make_member('author@example.com')
        manager = make_member('manager@example.com')
        grant_permission(manager, 'automation.manage_delivery')
        developer = make_member('dev@example.com')
        grant_permission(developer, 'automation.be_assignable')
        opportunity = opened(author)

        def path(kind):
            return Notification(
                user=author, kind=kind, title='t', body='b', idea=opportunity.idea
            ).action_path

        # Before a project exists, the opportunity.
        assert path('automation.opportunity_assigned') == (
            f'/app/automation/opportunities/{opportunity.pk}?tab=assignment'
        )
        delivery.assign_opportunity(
            User.objects.get(pk=manager.pk), opportunity.pk, assignee_user_id=developer.pk
        )
        project = delivery.create_project(User.objects.get(pk=developer.pk), opportunity.pk)

        assert path('automation.ready_for_uat') == (
            f'/app/automation/projects/{project.pk}?tab=uat'
        )
        assert path('automation.deployment_completed') == (
            f'/app/automation/projects/{project.pk}?tab=deployment'
        )
