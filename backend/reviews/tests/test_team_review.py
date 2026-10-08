"""
The team level: a team's own reviewers check a team idea before the platform does.

The same loop an organization has - submit, reviewers verify or send it back asking
for changes or more documents, the owner resubmits, then publishes upward - with a
different set of reviewers, and with no audience beyond the team.
"""

import pytest
from django.utils import timezone

from ideas import selectors, services
from ideas.models import Category, Idea
from identity.models import User
from notifications.models import Notification
from reviews import organization_review
from reviews.organization_review import CompleteOrganizationReviewInput
from reviews.tests.platform import grant_platform_reviewer, team_reviewer_for
from teams import services as team_services

PASSWORD = 'a-strong-unique-pass-1'


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
def team(owner):
    return team_services.create_team(owner, team_services.TeamInput(name='Automation Team'))


@pytest.fixture
def member(team, owner):
    person = make_user('member@example.com')
    team_services.add_existing_member(owner, team, person)
    return person


@pytest.fixture
def author(team, owner):
    person = make_user('author@example.com')
    team_services.add_existing_member(owner, team, person)
    return person


def draft(author, team, **overrides):
    fields = {
        'author': author,
        'team': team,
        'submission_context': Idea.SubmissionContext.TEAM,
        'visibility': Idea.Visibility.TEAM,
        'title': 'Automate the invoice run',
        'description': 'Payments are tracked by hand and it takes the finance team days.',
        'category': Category.objects.get_or_create(name='Team Review Fixture')[0],
    }
    fields.update(overrides)
    return Idea.objects.create(**fields)


def decide(reviewer, idea, decision, feedback=''):
    review = organization_review.start_organization_review(reviewer, idea.pk)
    return organization_review.complete_organization_review(
        reviewer,
        CompleteOrganizationReviewInput(
            idea_id=idea.pk, review_id=review.pk, decision=decision, feedback=feedback
        ),
    )


@pytest.mark.django_db
class TestTheTeamStage:
    def test_a_team_idea_goes_to_its_team_first_not_the_platform(self, author, team):
        idea = draft(author, team)

        moved = services.submit_idea(author, idea.pk)

        assert moved.status == Idea.Status.SUBMITTED_TO_ORGANIZATION

    def test_an_individual_idea_still_goes_straight_to_the_platform(self, author):
        idea = Idea.objects.create(
            author=author,
            title='Mine',
            description='Payments are tracked by hand and it takes days.',
            submission_context=Idea.SubmissionContext.INDIVIDUAL,
            visibility=Idea.Visibility.PRIVATE,
            category=Category.objects.get_or_create(name='Team Review Fixture')[0],
        )

        assert services.submit_idea(author, idea.pk).status == Idea.Status.SUBMITTED

    def test_the_owner_cannot_skip_the_team_stage(self, author, team):
        from ideas import lifecycle

        idea = draft(author, team)

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(author, idea.pk, Idea.Status.SUBMITTED)

    def test_the_whole_loop_ends_with_the_owner_publishing_to_the_platform(self, author, team):
        reviewer = team_reviewer_for(team)
        platform = make_user('platform@example.com')
        grant_platform_reviewer(platform)
        idea = draft(author, team)

        services.submit_idea(author, idea.pk)
        decide(reviewer, idea, 'changes_requested', "Please attach last month's invoices.")
        idea.refresh_from_db()
        assert idea.status == Idea.Status.ORGANIZATION_CHANGES_REQUESTED

        # The owner fixes it and sends it back to the team, not to the platform.
        moved = services.submit_idea(author, idea.pk)
        assert moved.status == Idea.Status.SUBMITTED_TO_ORGANIZATION

        decide(reviewer, idea, 'confirmed', 'Looks right.')
        idea.refresh_from_db()
        assert idea.status == Idea.Status.ORGANIZATION_CONFIRMED
        # It has left the reviewers' queue ...
        assert organization_review.team_queue_for(reviewer, team.pk) == []
        # ... and only the owner publishes it upward.
        services.submit_to_platform(author, idea.pk)
        idea.refresh_from_db()
        assert idea.status == Idea.Status.SUBMITTED
        assert selectors.can_view_idea(platform, idea) is True


@pytest.mark.django_db
class TestWhoReviews:
    def test_a_plain_member_sees_the_idea_but_cannot_review_it(self, author, team, member):
        idea = draft(author, team)
        services.submit_idea(author, idea.pk)
        idea.refresh_from_db()

        assert selectors.can_view_idea(member, idea) is True
        with pytest.raises(organization_review.OrganizationReviewError):
            organization_review.start_organization_review(member, idea.pk)
        assert organization_review.team_queue_for(member, team.pk) == []

    def test_a_reviewer_member_can_and_the_queue_lists_the_idea(self, author, team):
        reviewer = team_reviewer_for(team)
        idea = draft(author, team)
        services.submit_idea(author, idea.pk)

        assert [i.pk for i in organization_review.team_queue_for(reviewer, team.pk)] == [idea.pk]

    def test_nobody_reviews_their_own_idea_even_as_a_reviewer(self, owner, team):
        idea = draft(owner, team)  # the owner holds the review permission
        services.submit_idea(owner, idea.pk)

        with pytest.raises(organization_review.OrganizationReviewError):
            organization_review.start_organization_review(owner, idea.pk)
        assert organization_review.team_queue_for(owner, team.pk) == []

    def test_a_reviewer_of_another_team_cannot_touch_it(self, author, team):
        other_owner = make_user('other@example.com')
        other = team_services.create_team(other_owner, team_services.TeamInput(name='Other Team'))
        stranger_reviewer = team_reviewer_for(other)
        idea = draft(author, team)
        services.submit_idea(author, idea.pk)

        with pytest.raises(organization_review.OrganizationReviewError):
            organization_review.start_organization_review(stranger_reviewer, idea.pk)
        assert organization_review.team_queue_for(stranger_reviewer, team.pk) == []

    def test_changes_need_a_reason_and_the_author_is_told_in_team_words(
        self, author, team, django_capture_on_commit_callbacks
    ):
        reviewer = team_reviewer_for(team)
        idea = draft(author, team)
        services.submit_idea(author, idea.pk)
        review = organization_review.start_organization_review(reviewer, idea.pk)

        with pytest.raises(organization_review.OrganizationReviewError):
            organization_review.complete_organization_review(
                reviewer,
                CompleteOrganizationReviewInput(
                    idea_id=idea.pk, review_id=review.pk, decision='changes_requested'
                ),
            )
        with django_capture_on_commit_callbacks(execute=True):
            organization_review.complete_organization_review(
                reviewer,
                CompleteOrganizationReviewInput(
                    idea_id=idea.pk,
                    review_id=review.pk,
                    decision='changes_requested',
                    feedback='Attach the March invoices.',
                ),
            )

        note = Notification.objects.get(user=author, kind='idea.organization_changes_requested')
        assert 'team' in note.body
        assert 'organization' not in note.body


@pytest.mark.django_db
class TestMakingReviewers:
    def test_the_owner_appoints_and_removes_a_reviewer(self, owner, team, member):
        from teams import authorization as team_authorization

        team_services.set_member_reviewer(owner, team, member.pk, True)
        assert team_authorization.can_review_for(member, team.pk) is True

        team_services.set_member_reviewer(owner, team, member.pk, False)
        assert team_authorization.can_review_for(member, team.pk) is False

    def test_a_member_cannot_appoint_themselves(self, team, member):
        with pytest.raises(team_services.TeamError):
            team_services.set_member_reviewer(member, team, member.pk, True)

    def test_only_active_members_can_be_made_reviewers(self, owner, team):
        outsider = make_user('outsider@example.com')

        with pytest.raises(team_services.TeamError, match='not on this team'):
            team_services.set_member_reviewer(owner, team, outsider.pk, True)

    def test_an_ordinary_member_still_cannot_approve_anything(self, team):
        from teams import authorization as team_authorization

        assert 'team.ideas.approve' not in team_authorization.ALL_TEAM_PERMISSIONS


@pytest.mark.django_db
class TestAudienceFollowsTheLevel:
    def _input(self, category, **extra):
        return services.IdeaInput(
            title='Automate the invoice run',
            description='Payments are tracked by hand and it takes the finance team days.',
            category_id=category.pk,
            **extra,
        )

    def test_each_level_gets_its_own_audience_with_no_choice(self, owner, team):
        category = Category.objects.get_or_create(name='Team Review Fixture')[0]

        team_idea = services.create_idea_in_context(
            owner,
            self._input(category),
            submission_context=Idea.SubmissionContext.TEAM,
            team_id=team.pk,
        )
        solo = services.create_idea_in_context(
            owner,
            self._input(category),
            submission_context=Idea.SubmissionContext.INDIVIDUAL,
        )

        assert team_idea.visibility == Idea.Visibility.TEAM
        assert solo.visibility == Idea.Visibility.PRIVATE

    @pytest.mark.parametrize('named', ['PUBLIC', 'public', 'organization'])
    def test_asking_for_another_audience_is_refused_not_ignored(self, owner, team, named):
        category = Category.objects.get_or_create(name='Team Review Fixture')[0]

        with pytest.raises(services.IdeaError) as refused:
            services.create_idea_in_context(
                owner,
                self._input(category, visibility=named),
                submission_context=Idea.SubmissionContext.TEAM,
                team_id=team.pk,
            )
        assert refused.value.field == 'visibility'

    def test_a_team_idea_is_invisible_outside_the_team(self, author, team):
        outsider = make_user('outsider@example.com')
        idea = draft(author, team)
        services.submit_idea(author, idea.pk)
        idea.refresh_from_db()

        assert selectors.can_view_idea(outsider, idea) is False

    def test_a_draft_is_the_authors_alone_even_inside_the_team(self, author, team, member):
        idea = draft(author, team)

        assert selectors.can_view_idea(author, idea) is True
        assert selectors.can_view_idea(member, idea) is False


@pytest.mark.django_db
def test_existing_public_ideas_are_converted_to_their_owners_level():
    from importlib import import_module

    from django.apps import apps

    author = make_user('legacy@example.com')
    legacy = Idea.objects.create(
        author=author,
        title='Legacy',
        description='A public idea from before the change.',
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        visibility=Idea.Visibility.PUBLIC,
        status=Idea.Status.SUBMITTED,
        submitted_at=timezone.now(),
    )

    import_module('ideas.migrations.0010_audience_follows_level').convert(apps, None)

    legacy.refresh_from_db()
    assert legacy.visibility == Idea.Visibility.PRIVATE
