"""
The three journeys, end to end, from the author's side.

`test_lifecycle.py` pins the *matrix* and who may make each move. This file walks
the three journeys a person can actually be on, because a matrix can be
completely correct and still leave a question nobody asked: **what does the
submit button do for this person?**

The three, and why they differ:

    individual   draft -> submitted -> ... -> platform
    team         draft -> submitted -> ... -> platform
    organization draft -> submitted_to_organization -> (confirmed) -> submitted
                 -> ... -> platform

The first two are one move apart from nothing at all: no organization in the way
to confirm anything. The third has a stage that only an organization reviewer can
move, and it is two different people - the organization confirms, then the
*author* submits on. Asserted here as one chain per context, because that is the
shape a user experiences, and because a stage that only exists for one context is
exactly the kind of thing that silently stops being reachable.

Everything here goes through the operations the frontend calls - the services,
not the transition matrix - so a change that leaves the matrix intact but breaks a
journey fails in this file.
"""

import pytest
from django.core import mail

from ideas import lifecycle, services
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import organization_review, platform_review
from reviews import services as review_services
from reviews.tests.platform import (
    confirm_for_organization,
    grant_platform_reviewer,
    release_proposal,
    send_decision_letters,
)

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'
CRITERIA = (
    'PROBLEM_CLARITY',
    'AUTOMATION_SUITABILITY',
    'FEASIBILITY',
    'EXPECTED_BENEFIT',
    'EVIDENCE',
)


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password=VALID_PASSWORD,
    )


def make_organization(name='Acme Labs', owner=None):
    """The organization, plus the creator's membership (they hold the Owner role)."""
    return create_organization_for_user(
        owner or make_user(f'{name.split()[0].lower()}@example.com'),
        CreateOrganizationInput(name=name),
    ).organization


def make_team(name='Automation Squad', owner=None):
    from teams import services as team_services

    return team_services.create_team(
        owner or make_user('team-owner@example.com'), team_services.TeamInput(name=name)
    )


def add_member(organization, user, *, reviewer=False):
    from organizations.models import MembershipRole, Role

    membership = Membership.objects.create(user=user, organization=organization)
    if reviewer:
        MembershipRole.objects.create(
            membership=membership,
            role=Role.objects.get(organization=organization, slug=REVIEWER_ROLE_SLUG),
        )
    return membership


@pytest.fixture
def category():
    created, _ = Category.objects.get_or_create(name='Journeys')
    return created


@pytest.fixture
def author():
    return make_user('author@example.com')


@pytest.fixture
def platform_reviewer():
    """A reviewer who belongs to no organization at all - the normal case."""
    reviewer = make_user('platform@example.com')
    grant_platform_reviewer(reviewer)
    return reviewer


def file_idea(user, context, *, organization=None, team=None, title='An idea'):
    return services.create_idea_in_context(
        user,
        services.IdeaInput(
            title=title,
            description=DESCRIPTION,
            category_id=Category.objects.get_or_create(name='Journeys')[0].pk,
        ),
        submission_context=context,
        organization_id=organization.pk if organization else None,
        team_id=team.pk if team else None,
    )


def decide(reviewer, idea, decision, feedback='Worth automating.'):
    review = review_services.start_review(reviewer, idea.pk)
    return review_services.complete_review(
        reviewer,
        review_services.CompleteReviewInput(
            idea_id=idea.pk,
            review_id=review.pk,
            decision=decision,
            feedback=feedback,
            assessments=tuple(
                review_services.AssessmentInput(criterion, 'meets', f'on {criterion}')
                for criterion in CRITERIA
            ),
        ),
    )


# --- the individual journey ---------------------------------------------------------------


@pytest.mark.django_db
class TestTheIndividualJourney:
    def test_it_reaches_the_developer_track_in_eight_moves(self, author, platform_reviewer):
        """
        The whole journey, in order, with no organization anywhere.

        This is what "an organization is optional" means in practice: somebody who
        belongs to no tenant files an idea, submits it, and it is reviewed,
        approved, and handed to the developer track exactly like any other. No
        stage is skipped and no stage is invented.
        """
        idea = file_idea(author, Idea.SubmissionContext.INDIVIDUAL)
        assert (idea.organization_id, idea.team_id) == (None, None)

        assert services.submit_idea(author, idea.pk).status == Idea.Status.SUBMITTED

        decide(platform_reviewer, idea, 'changes_requested', 'Add the monthly volumes.')
        idea.refresh_from_db()
        assert idea.status == Idea.Status.CHANGES_REQUESTED

        assert services.submit_idea(author, idea.pk).status == Idea.Status.SUBMITTED

        decide(platform_reviewer, idea, 'approved')
        idea.refresh_from_db()
        assert idea.status == Idea.Status.APPROVED

        from ideas.go_ahead import confirm_go_ahead

        release_proposal(idea)
        confirm_go_ahead(author, idea.pk)
        assert (
            lifecycle.transition_idea(
                platform_reviewer, idea.pk, Idea.Status.AUTOMATION_PROPOSAL
            ).status
            == Idea.Status.AUTOMATION_PROPOSAL
        )

    def test_the_reports_follow_the_idea_wherever_it_came_from(
        self, author, platform_reviewer, category
    ):
        """
        A report copies the idea's context and its tenants, so a document that
        outlives the review still says who filed it and for whom.
        """
        idea = file_idea(author, Idea.SubmissionContext.INDIVIDUAL)
        services.submit_idea(author, idea.pk)
        decide(platform_reviewer, idea, 'approved')

        report = platform_review.report_for_idea(Idea.objects.get(pk=idea.pk))

        assert report.submission_context == Idea.SubmissionContext.INDIVIDUAL
        assert report.organization_id is None
        assert report.team_id is None
        assert report.round == 1
        assert {row['criterion'] for row in report.criteria} == {
            criterion.lower() for criterion in CRITERIA
        }


@pytest.mark.django_db
class TestTheTeamJourney:
    def test_it_is_checked_by_the_team_then_published_by_the_owner(self, author, platform_reviewer):
        """
        A team checks its own idea first, exactly as an organization does: the
        author submits, the team's reviewer verifies, and only then does the author
        publish it to the platform. A team still cannot *approve* anything.
        """
        from reviews.tests.platform import team_reviewer_for

        owner = make_user('team-owner@example.com')
        team = make_team(owner=owner)
        reviewer = team_reviewer_for(team)

        idea = file_idea(owner, Idea.SubmissionContext.TEAM, team=team)
        assert (idea.organization_id, idea.team_id) == (None, team.pk)

        assert services.submit_idea(owner, idea.pk).status == Idea.Status.SUBMITTED_TO_ORGANIZATION
        idea.refresh_from_db()
        review = organization_review.start_organization_review(reviewer, idea.pk)
        organization_review.complete_organization_review(
            reviewer,
            organization_review.CompleteOrganizationReviewInput(
                idea_id=idea.pk, review_id=review.pk, decision='confirmed', feedback='Good.'
            ),
        )
        assert services.submit_to_platform(owner, idea.pk).status == Idea.Status.SUBMITTED
        assert decide(platform_reviewer, idea, 'approved').decision == 'approved'

    def test_the_author_cannot_confirm_their_own_team_idea(self, author, platform_reviewer):
        """
        Confirmation belongs to the team's reviewers; the author is offered
        submission only, and holds no way round their own team's stage.
        """
        team = make_team(owner=author)
        idea = file_idea(author, Idea.SubmissionContext.TEAM, team=team)

        for target in (
            Idea.Status.ORGANIZATION_CONFIRMED,
            Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
        ):
            assert lifecycle.can_transition(author, idea, target) is False
            assert target not in lifecycle.available_transitions(author, idea)

        services.submit_idea(author, idea.pk)
        idea.refresh_from_db()
        assert lifecycle.is_organization_reviewer(author, idea) is False


@pytest.mark.django_db
class TestTheOrganizationJourney:
    """
    The longer one, and the one the product's "your organization validates first"
    rule is about. Three actor kinds: the author, the organization's reviewers,
    and the platform's.
    """

    @pytest.fixture
    def world(self, author):
        organization = make_organization(owner=author)
        reviewer = make_user('org-reviewer@example.com')
        add_member(organization, reviewer, reviewer=True)
        return {'organization': organization, 'reviewer': reviewer, 'author': author}

    def test_confirmation_and_submission_are_two_acts_by_two_people(self, world, platform_reviewer):
        """
        The organization confirms that this is what *it* wants to submit; the
        author then submits it. Asserted in both directions, because collapsing
        them is the mistake this design exists to prevent: if one act did both, a
        company could submit a member's idea to the platform without the author
        ever agreeing to it.
        """
        author = world['author']
        idea = file_idea(
            author, Idea.SubmissionContext.ORGANIZATION, organization=world['organization']
        )

        services.submit_idea(author, idea.pk)
        idea.refresh_from_db()
        assert idea.status == Idea.Status.SUBMITTED_TO_ORGANIZATION

        # The author cannot confirm their own idea, even holding the reviewer role.
        from reviews.organization_review import OrganizationReviewError

        with pytest.raises((OrganizationReviewError, services.IdeaError)):
            organization_review.start_organization_review(author, idea.pk)

        review = organization_review.start_organization_review(world['reviewer'], idea.pk)
        organization_review.complete_organization_review(
            world['reviewer'],
            organization_review.CompleteOrganizationReviewInput(
                idea_id=idea.pk,
                review_id=review.pk,
                decision='confirmed',
                feedback='This is what we want to submit.',
            ),
        )
        idea.refresh_from_db()
        assert idea.status == Idea.Status.ORGANIZATION_CONFIRMED

        # And now the author's turn.
        assert services.submit_to_platform(author, idea.pk).status == Idea.Status.SUBMITTED

        decide(platform_reviewer, idea, 'approved')
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.APPROVED

    def test_an_organization_can_send_it_back_instead(self, world, platform_reviewer):
        """
        The other outcome, and the one that makes the stage a review rather than a
        formality: the organization asks for changes, the author fixes the idea
        and resubmits **to the organization**, which is not yet the platform.
        """
        author = world['author']
        idea = file_idea(
            author, Idea.SubmissionContext.ORGANIZATION, organization=world['organization']
        )
        services.submit_idea(author, idea.pk)
        idea.refresh_from_db()

        review = organization_review.start_organization_review(world['reviewer'], idea.pk)
        organization_review.complete_organization_review(
            world['reviewer'],
            organization_review.CompleteOrganizationReviewInput(
                idea_id=idea.pk,
                review_id=review.pk,
                decision='changes_requested',
                feedback='Add the monthly volumes.',
            ),
        )
        idea.refresh_from_db()
        assert idea.status == Idea.Status.ORGANIZATION_CHANGES_REQUESTED

        # Back to the organization, not the platform.
        assert services.submit_idea(author, idea.pk).status == Idea.Status.SUBMITTED_TO_ORGANIZATION

    def test_the_report_names_the_organization_and_round_one(self, world, platform_reviewer):
        idea = file_idea(
            world['author'],
            Idea.SubmissionContext.ORGANIZATION,
            organization=world['organization'],
        )
        services.submit_idea(world['author'], idea.pk)
        confirm_for_organization(idea)
        idea.refresh_from_db()
        services.submit_to_platform(world['author'], idea.pk)
        decide(platform_reviewer, idea, 'approved')

        report = platform_review.report_for_idea(Idea.objects.get(pk=idea.pk))

        assert report.submission_context == Idea.SubmissionContext.ORGANIZATION
        assert report.organization_id == world['organization'].pk
        # Round one **of the platform track**: the organization's confirmation was
        # also round one, of its own scope.
        assert report.round == 1


# --- what the author is offered, per journey ----------------------------------------------


@pytest.mark.django_db
class TestWhatTheAuthorIsOffered:
    """
    One button, one label, and the stage behind it chosen by the idea rather than
    by the caller. The frontend reads `available_transitions` and
    `ideas.states`; this is the claim those two are built on.
    """

    def test_an_organization_draft_offers_only_the_organization(self, author, platform_reviewer):
        organization = make_organization(owner=author)
        idea = file_idea(author, Idea.SubmissionContext.ORGANIZATION, organization=organization)

        assert lifecycle.available_transitions(author, idea) == [
            Idea.Status.SUBMITTED_TO_ORGANIZATION
        ]
        # And the platform reviewer, who could do all sorts of things to an idea
        # they can read, is offered nothing at all on a draft.
        assert lifecycle.available_transitions(platform_reviewer, idea) == []

    def test_an_individual_draft_offers_only_the_platform(self, author, platform_reviewer):
        idea = file_idea(author, Idea.SubmissionContext.INDIVIDUAL)

        assert lifecycle.available_transitions(author, idea) == [Idea.Status.SUBMITTED]
        assert lifecycle.available_transitions(platform_reviewer, idea) == []

    def test_the_state_summary_reads_as_a_stage_and_not_a_code(self, author, platform_reviewer):
        """
        What the author actually reads. Asserted here rather than in
        `test_states.py`'s unit tests because the point is that a journey produces
        a *sensible sentence* at each step, which is only visible in sequence.
        """
        from ideas import states

        organization = make_organization(owner=author)
        idea = file_idea(author, Idea.SubmissionContext.ORGANIZATION, organization=organization)

        sentences = []
        services.submit_idea(author, idea.pk)
        idea.refresh_from_db()
        confirm_for_organization(idea)
        idea.refresh_from_db()
        services.submit_to_platform(author, idea.pk)
        decide(platform_reviewer, idea, 'changes_requested')
        idea.refresh_from_db()
        services.submit_idea(author, idea.pk)
        idea.refresh_from_db()
        decide(platform_reviewer, idea, 'approved')
        idea.refresh_from_db()
        for status in (
            Idea.Status.SUBMITTED_TO_ORGANIZATION,
            Idea.Status.ORGANIZATION_CONFIRMED,
            Idea.Status.SUBMITTED,
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.APPROVED,
        ):
            idea.status = status
            sentences.append(states.ACTION_LABELS[states.primary_action_for(author, idea)])

        assert all(sentences)
        # Five different stages, five different sentences. A table where two of
        # them read the same is a table somebody cannot use to tell "waiting for
        # my organization" from "waiting for the platform".
        assert len(set(sentences)) == len(sentences), sentences


# --- one business event, one notification -------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestWhatEachStageTellsPeople:
    """
    One event, one notification and one email, because a person who is told about
    a decision twice has to work out whether they heard it twice or decided twice.

    `transaction=True` because every one of these notifications is registered on
    `on_commit`: inside a rollback-at-the-end test transaction the callbacks never
    fire, and the tests would assert about a platform that never notifies anybody.
    """

    def test_the_organization_stage_tells_the_author(self, author, platform_reviewer):
        organization = make_organization(owner=author)
        idea = file_idea(author, Idea.SubmissionContext.ORGANIZATION, organization=organization)

        services.submit_idea(author, idea.pk)
        idea.refresh_from_db()
        confirm_for_organization(idea)
        idea.refresh_from_db()
        services.submit_to_platform(author, idea.pk)
        idea.refresh_from_db()
        decide(platform_reviewer, idea, 'approved')
        # The approval reaches the author in the letter an administrator sends.
        send_decision_letters(idea)

        # The notifications themselves, in order.
        from notifications.models import Notification

        titles = [
            (item.kind, item.title)
            for item in Notification.objects.filter(user=author).order_by('pk')
        ]
        # Three events the author is the recipient of, and no more: the queue
        # nudges went to the reviewers, not to them. One event, one notification -
        # a person told about a decision twice has to work out whether they heard
        # it twice or decided twice.
        assert [kind for kind, _ in titles] == [
            'idea.organization_confirmed',
            'idea.platform_approved',
        ]
        assert all(title for _, title in titles)
        # The queue nudges are not emailed: there may be many reviewers of each
        # kind, "somebody submitted something" is addressed to nobody in
        # particular, and mailing every reviewer of every submission is how a
        # notification stops being read.
        assert len(mail.outbox) == 2

    def test_a_queued_idea_tells_the_organization_reviewer_not_the_author(
        self, author, platform_reviewer
    ):
        from notifications.models import Notification

        organization = make_organization(owner=author)
        reviewer = make_user('org-reviewer@example.com')
        add_member(organization, reviewer, reviewer=True)
        idea = file_idea(author, Idea.SubmissionContext.ORGANIZATION, organization=organization)

        services.submit_idea(author, idea.pk)

        assert Notification.objects.filter(user=reviewer).count() == 1
        assert Notification.objects.filter(user=author).count() == 0
        queued = Notification.objects.get(user=reviewer)
        assert queued.idea_id == idea.pk
        assert queued.kind == 'review.organization_queue'
        # The author's own submission is not news to them.
        assert mail.outbox == []
