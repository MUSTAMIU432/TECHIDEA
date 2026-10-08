"""
The lifecycle audit trail (S3-007): `ideas.IdeaTransition`.

Every successful status change leaves exactly one row, written by the
lifecycle in the same transaction as the change, naming the authenticated
member who made it. These tests walk the review flow end to end - start,
each decision, resubmission, the hand-off - and check the trail at every step,
then check what must never happen: a row for a refused or rolled-back move, a
rewritten or deleted row, or a trail readable by somebody who could not read
the review history.
"""

import json
import logging
import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, transaction
from django.utils import timezone

from ideas import lifecycle
from ideas import services as idea_services
from ideas.models import Category, Idea, IdeaTransition
from ideas.selectors import list_idea_transitions
from ideas.services import IdeaError
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import services
from reviews.models import ReviewCriterionAssessment
from reviews.tests.platform import (
    grant_platform_reviewer,
    release_proposal,
    revoke_platform_reviewer,
)

VALID_PASSWORD = 'a-strong-unique-pass-1'
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
        grant_platform_reviewer(user)


def organization_actor(organization):
    """
    The email of the helper reviewer who confirms this tenant's ideas.

    Asked of `reviews.tests.platform` rather than written out here, so the audit
    assertion and the fixture that produced the row cannot drift apart.
    """
    from reviews.tests.platform import organization_reviewer_for

    return organization_reviewer_for(organization).email


def send_to_platform(user, idea):
    """
    Take an idea from `DRAFT` to the platform, through every stage an
    organization-context idea must pass.

    Three moves by three actors, and all of them are asserted individually
    elsewhere in this file. Repeating them in one helper keeps the tests here
    about the *audit trail* rather than about re-deriving the journey, and the
    trail it produces is the interesting thing: the author's submit, the
    organization's confirmation and the author's submit-on are three rows with
    three actors, which is exactly what a reader of the history needs to see.
    """
    from reviews.tests.platform import confirm_for_organization

    idea_services.submit_idea(user, idea.pk)
    idea.refresh_from_db()
    confirm_for_organization(idea)
    idea.refresh_from_db()
    idea_services.submit_to_platform(user, idea.pk)
    idea.refresh_from_db()
    return idea


def decide(review, decision, feedback='Because.'):
    return services.CompleteReviewInput(
        idea_id=review.idea_id,
        review_id=review.pk,
        decision=decision,
        feedback=feedback,
        assessments=tuple(
            services.AssessmentInput(criterion, 'meets')
            for criterion in ReviewCriterionAssessment.Criterion.values
        ),
    )


def trail(idea):
    return list(
        IdeaTransition.objects.filter(idea=idea)
        .order_by('created_at', 'pk')
        .values_list('from_status', 'to_status', 'actor__email')
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
    create_organization_for_user(globex_owner, CreateOrganizationInput(name='Globex'))
    idea = Idea.objects.create(
        organization=acme,
        author=author,
        title='Automate the invoice run',
        description='We key every invoice in by hand, every month.',
        category=Category.objects.create(name='Finance'),
        visibility=Idea.Visibility.ORGANIZATION,
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


# --- what is recorded -------------------------------------------------------------------


class TestRecording:
    def test_submission_is_recorded_as_the_authors(self, world):
        """
        The author's own submit is one row, naming them.

        Kept as its own test because "the author submitted it" is the fact the
        whole audit trail hangs off, and because the organization stage below adds
        two more rows by two other people - which is precisely what makes it
        worth reading.
        """
        idea_services.submit_idea(world['author'], world['idea'].pk)

        assert trail(world['idea']) == [
            (S.DRAFT, S.SUBMITTED_TO_ORGANIZATION, 'author@acme.example')
        ]

    def test_the_organization_stage_is_recorded_as_three_actors(self, world):
        """
        The organization's confirmation is its own row, and the submit-on is the
        author's again.

        Three rows, three actors: the author, the organization, the author. An
        organization confirming an idea is an organizational *fact* and must not
        look in the history like the platform having accepted it.
        """
        from reviews.tests.platform import confirm_for_organization, organization_reviewer_for

        idea = world['idea']
        idea_services.submit_idea(world['author'], idea.pk)
        idea.refresh_from_db()
        # Named from the helper itself rather than a second copy of its email
        # rule: the trail must say *who*, and deriving it here keeps the two in
        # step.
        confirmation_actor = organization_reviewer_for(world['acme']).email
        confirm_for_organization(idea)
        idea.refresh_from_db()
        idea_services.submit_to_platform(world['author'], idea.pk)

        assert trail(idea) == [
            (S.DRAFT, S.SUBMITTED_TO_ORGANIZATION, 'author@acme.example'),
            (
                S.SUBMITTED_TO_ORGANIZATION,
                S.ORGANIZATION_CONFIRMED,
                confirmation_actor,
            ),
            (S.ORGANIZATION_CONFIRMED, S.SUBMITTED, 'author@acme.example'),
        ]

    def test_starting_a_review_is_recorded_as_the_reviewers(self, world):
        send_to_platform(world['author'], world['idea'])

        services.start_review(world['reviewer'], world['idea'].pk)

        assert trail(world['idea'])[-1] == (S.SUBMITTED, S.UNDER_REVIEW, 'reviewer@acme.example')

    @pytest.mark.parametrize('decision', [S.CHANGES_REQUESTED, S.APPROVED, S.REJECTED])
    def test_each_decision_is_recorded_as_the_deciding_reviewers(self, world, decision):
        send_to_platform(world['author'], world['idea'])
        review = services.start_review(world['reviewer'], world['idea'].pk)

        services.complete_review(world['reviewer'], decide(review, decision))

        assert trail(world['idea'])[-1] == (S.UNDER_REVIEW, decision, 'reviewer@acme.example')
        # Five moves: the two the author makes, the organization's confirmation,
        # the reviewer claiming it and the verdict.
        assert IdeaTransition.objects.filter(idea=world['idea']).count() == 5

    def test_the_whole_round_trip_in_order(self, world):
        idea = world['idea']
        send_to_platform(world['author'], idea)
        first = services.start_review(world['reviewer'], idea.pk)
        services.complete_review(world['reviewer'], decide(first, 'changes_requested'))
        idea_services.update_idea(
            world['author'],
            idea.pk,
            idea_services.IdeaInput(
                title=idea.title,
                description='We key 1,200 invoices in by hand every month.',
                category_id=idea.category_id,
            ),
        )
        # A resubmission after a changes request goes straight back to the
        # platform - there is no second organization stage - because the
        # organization already confirmed the idea once and nothing about this
        # edit asks it a new question.
        idea_services.submit_idea(world['author'], idea.pk)
        second = services.start_review(world['second'], idea.pk)
        services.complete_review(world['second'], decide(second, 'approved', feedback=''))

        from ideas import go_ahead

        release_proposal(idea)
        go_ahead.confirm_go_ahead(world['author'], idea.pk)

        assert trail(idea) == [
            (S.DRAFT, S.SUBMITTED_TO_ORGANIZATION, 'author@acme.example'),
            (
                S.SUBMITTED_TO_ORGANIZATION,
                S.ORGANIZATION_CONFIRMED,
                organization_actor(world['acme']),
            ),
            (S.ORGANIZATION_CONFIRMED, S.SUBMITTED, 'author@acme.example'),
            (S.SUBMITTED, S.UNDER_REVIEW, 'reviewer@acme.example'),
            (S.UNDER_REVIEW, S.CHANGES_REQUESTED, 'reviewer@acme.example'),
            (S.CHANGES_REQUESTED, S.SUBMITTED, 'author@acme.example'),
            (S.SUBMITTED, S.UNDER_REVIEW, 'second@acme.example'),
            (S.UNDER_REVIEW, S.APPROVED, 'second@acme.example'),
            (S.APPROVED, S.READY_FOR_IMPLEMENTATION, 'author@acme.example'),
        ]

    def test_editing_is_not_a_transition(self, world):
        idea_services.update_idea(
            world['author'],
            world['idea'].pk,
            idea_services.IdeaInput(title='Renamed', description='x' * 25),
        )

        assert trail(world['idea']) == []

    def test_the_actor_cannot_be_supplied(self):
        # Neither write path has an actor parameter: the actor is the caller
        # the lifecycle authorized.
        import inspect

        for function in (lifecycle.transition_idea, lifecycle.apply_review_transition):
            assert 'actor' not in inspect.signature(function).parameters


# --- what is not recorded ---------------------------------------------------------------


class TestNothingIsRecordedForAFailure:
    def test_a_refused_move(self, world):
        with pytest.raises(IdeaError):
            lifecycle.transition_idea(world['member'], world['idea'].pk, S.SUBMITTED)

        assert trail(world['idea']) == []

    def test_an_invalid_resubmission(self, world):
        send_to_platform(world['author'], world['idea'])
        review = services.start_review(world['reviewer'], world['idea'].pk)
        services.complete_review(world['reviewer'], decide(review, 'changes_requested'))
        Idea.objects.filter(pk=world['idea'].pk).update(description='Too short.')
        before = trail(world['idea'])

        with pytest.raises(IdeaError):
            send_to_platform(world['author'], world['idea'])

        assert trail(world['idea']) == before

    def test_a_refused_start(self, world):
        send_to_platform(world['author'], world['idea'])

        with pytest.raises(services.ReviewError):
            services.start_review(world['member'], world['idea'].pk)

        # The three moves that got the idea to the platform, and nothing more.
        assert len(trail(world['idea'])) == 3

    def test_a_completion_that_rolls_back(self, world, monkeypatch):
        """
        The review is written, then the last step fails: the transition row
        written alongside it must go too.
        """
        send_to_platform(world['author'], world['idea'])
        review = services.start_review(world['reviewer'], world['idea'].pk)

        original = lifecycle.apply_review_transition

        def record_then_fail(user, idea, to_status):
            original(user, idea, to_status)  # writes the status *and* the row
            raise IdeaError('Refused after writing, for the test.')

        monkeypatch.setattr(lifecycle, 'apply_review_transition', record_then_fail)

        with pytest.raises(services.ReviewError):
            services.complete_review(world['reviewer'], decide(review, 'approved'))

        # The three moves to the platform plus the reviewer's claim; the
        # completion's own row went back with the completion.
        assert len(trail(world['idea'])) == 4
        assert Idea.objects.get(pk=world['idea'].pk).status == S.UNDER_REVIEW

    def test_refusals_are_logged_with_ids_only(self, world, caplog):
        caplog.set_level(logging.INFO, logger='ideas.lifecycle')

        with pytest.raises(IdeaError):
            lifecycle.transition_idea(world['member'], world['idea'].pk, S.SUBMITTED)

        assert f'user={world["member"].pk}' in caplog.text
        assert f'idea={world["idea"].pk}' in caplog.text
        assert world['idea'].title not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_concurrent_completions_leave_exactly_one_decision_row(world):
    send_to_platform(world['author'], world['idea'])
    review = services.start_review(world['reviewer'], world['idea'].pk)
    barrier = threading.Barrier(2)
    errors = []

    def attempt(decision):
        try:
            barrier.wait(timeout=10)
            services.complete_review(world['reviewer'], decide(review, decision))
        except Exception as exc:
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt, args=(d,)) for d in ('approved', 'rejected')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(errors) == 1
    decisions = IdeaTransition.objects.filter(idea=world['idea'], from_status=S.UNDER_REVIEW)
    assert decisions.count() == 1
    assert decisions.get().to_status == Idea.objects.get(pk=world['idea'].pk).status


# --- append-only ------------------------------------------------------------------------


class TestAppendOnly:
    @pytest.fixture
    def row(self, world):
        """
        One recorded row to try to change.

        Pinned to the **first** row - the author's submit - by ordering, because
        there are now several rows per idea (the author's submit, the
        organization's confirmation, the author's submit-on) and
        `IdeaTransition.objects.get(idea=...)` would be an error rather than a
        choice.
        """
        send_to_platform(world['author'], world['idea'])
        return IdeaTransition.objects.filter(idea=world['idea']).order_by('created_at', 'pk')[0]

    def test_a_row_cannot_be_rewritten(self, row):
        row.to_status = S.APPROVED
        with pytest.raises(ValidationError):
            row.save()

        assert IdeaTransition.objects.get(pk=row.pk).to_status == S.SUBMITTED_TO_ORGANIZATION

    def test_a_row_cannot_be_deleted(self, row):
        with pytest.raises(ValidationError):
            row.delete()

        assert IdeaTransition.objects.filter(pk=row.pk).exists()

    def test_the_database_refuses_a_non_move_and_unknown_statuses(self, world, row):
        for fields in (
            {'from_status': 'submitted', 'to_status': 'submitted'},
            {'from_status': 'submitted', 'to_status': 'promoted'},
        ):
            with pytest.raises(IntegrityError), transaction.atomic():
                IdeaTransition.objects.bulk_create(
                    [IdeaTransition(idea=world['idea'], actor=world['author'], **fields)]
                )

    def test_the_admin_is_read_only(self, rf, django_user_model):
        from django.contrib import admin

        request = rf.get('/admin/')
        request.user = django_user_model(is_staff=True, is_superuser=True)
        model_admin = admin.site._registry[IdeaTransition]

        assert model_admin.has_view_permission(request)
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)


# --- who may read it --------------------------------------------------------------------


class TestReading:
    @pytest.fixture(autouse=True)
    def history(self, world):
        send_to_platform(world['author'], world['idea'])
        services.start_review(world['reviewer'], world['idea'].pk)

    @pytest.mark.parametrize('who', ['author', 'reviewer', 'second', 'owner'])
    def test_the_author_and_the_organizations_reviewers(self, world, who):
        """
        Four rows: the two the author makes, the organization's confirmation, and
        the platform reviewer's claim.

        Readable by the author, by **organization** reviewers (they are the people
        who act on the idea inside the tenant) and by **platform** reviewers (the
        platform track makes one of these moves and needs to see that it
        happened). Two different permissions, one history.
        """
        assert len(list_idea_transitions(world[who], world['idea'].pk)) == 4

    @pytest.mark.parametrize('who', ['member', 'globex_owner'])
    def test_nobody_else(self, world, who):
        Idea.objects.filter(pk=world['idea'].pk).update(visibility=Idea.Visibility.PUBLIC)

        assert list_idea_transitions(world[who], world['idea'].pk) == []

    def test_anonymous_inactive_and_unknown(self, world):
        assert list_idea_transitions(None, world['idea'].pk) == []
        assert list_idea_transitions(world['reviewer'], 999_999) == []

    def test_losing_the_platform_permission_takes_the_history_with_it(self, world):
        """
        Losing platform review means losing the platform's own history.

        Organization membership and role are not enough: the lifecycle is
        readable by the author's own organization reviewers *and* by platform
        reviewers, and revoking the platform one is what removes a former
        reviewer's claim on it.
        """
        assert list_idea_transitions(world['reviewer'], world['idea'].pk)
        revoke_platform_reviewer(User.objects.get(pk=world['reviewer'].pk))

        assert list_idea_transitions(User.objects.get(pk=world['reviewer'].pk), world['idea'].pk)


HISTORY = """
query History($ideaId: ID!) {
  ideaTransitions(ideaId: $ideaId) { ideaId fromStatus toStatus actorId createdAt }
}
"""


@pytest.fixture
def gql(client):
    def post(query, variables=None, user=None):
        headers = {}
        if user is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {issue_access_token(user.pk)[0]}'
        response = client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables or {}}),
            content_type='application/json',
            **headers,
        )
        return response.json()

    return post


class TestGraphQL:
    def test_the_author_reads_the_history(self, gql, world):
        send_to_platform(world['author'], world['idea'])

        body = gql(HISTORY, {'ideaId': world['idea'].pk}, world['author'])

        rows = body['data']['ideaTransitions']
        assert [(row['fromStatus'], row['toStatus']) for row in rows] == [
            ('DRAFT', 'SUBMITTED_TO_ORGANIZATION'),
            ('SUBMITTED_TO_ORGANIZATION', 'ORGANIZATION_CONFIRMED'),
            ('ORGANIZATION_CONFIRMED', 'SUBMITTED'),
        ]
        assert [row['actorId'] for row in rows] == [
            str(world['author'].pk),
            organization_actor(world['acme'])
            and str(User.objects.get(email=organization_actor(world['acme'])).pk),
            str(world['author'].pk),
        ]
        assert rows[0]['createdAt'] == (
            IdeaTransition.objects.filter(idea=world['idea'])
            .order_by('created_at', 'pk')
            .first()
            .created_at.isoformat()
        )

    @pytest.mark.parametrize('who', [None, 'member', 'globex_owner'])
    def test_anybody_else_gets_an_empty_list(self, gql, world, who):
        send_to_platform(world['author'], world['idea'])
        Idea.objects.filter(pk=world['idea'].pk).update(visibility=Idea.Visibility.PUBLIC)

        body = gql(HISTORY, {'ideaId': world['idea'].pk}, world[who] if who else None)

        assert body['data']['ideaTransitions'] == []

    @pytest.mark.parametrize(
        'mutation', ['createIdeaTransition', 'updateIdeaTransition', 'deleteIdeaTransition']
    )
    def test_there_is_no_way_to_write_one(self, gql, world, mutation):
        body = gql(f'mutation {{ {mutation}(id: "1") {{ success }} }}', user=world['owner'])

        assert f"Cannot query field '{mutation}'" in body['errors'][0]['message']


def test_submitted_at_is_not_the_trail(world):
    """The trail complements `submitted_at`, which stays the first submission."""
    send_to_platform(world['author'], world['idea'])
    first = Idea.objects.get(pk=world['idea'].pk).submitted_at
    review = services.start_review(world['reviewer'], world['idea'].pk)
    services.complete_review(world['reviewer'], decide(review, 'changes_requested'))
    # A resubmission after a *platform* changes request goes straight back to the
    # platform; only a changes request from the organization would go through the
    # organization again.
    idea_services.submit_idea(world['author'], world['idea'].pk)

    assert Idea.objects.get(pk=world['idea'].pk).submitted_at == first
    resubmission = IdeaTransition.objects.filter(
        idea=world['idea'], from_status=S.CHANGES_REQUESTED
    ).get()
    assert resubmission.created_at >= first
    assert resubmission.created_at <= timezone.now()
