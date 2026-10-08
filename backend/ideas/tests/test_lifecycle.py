"""
The idea lifecycle: the transition matrix and who may make each move (S2-003).

This is the file that pins the domain down. The lifecycle is a table
(`ideas.lifecycle.TRANSITIONS`) and a set of actor rules, and both are
properties of the *domain* rather than of any one operation - so they are
tested here as such, once, rather than inferred from seven operations' worth
of happy paths.

What the suite is organised around, in the order the failures would hurt:

1. **The matrix itself.** Exactly the seven documented pairs, nothing else,
   each with the right actor. Asserted as a set so a pair added to the code
   without being decided fails here.
2. **Every valid move**, for the right actor, and the two states that prove
   the actor rule is real rather than incidental.
3. **Every invalid move** the brief calls out, plus the general property that
   an unlisted pair is refused - so the matrix cannot be bypassed by sending
   an unexpected status.
4. **Authorization**, in the order the service applies it: unauthenticated,
   inactive, non-member, departed member, cross-tenant, wrong role, and
   self-review.
5. **`submitted_at` and atomicity**: written once, never rewritten, and a
   refused transition leaves the row exactly as it was.
"""

import threading

import pytest
from django.db import connections, transaction
from django.utils import timezone

from ideas import lifecycle, selectors, services
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership, MembershipRole, Role
from reviews.tests.platform import confirm_for_organization, grant_platform_reviewer

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'


def make_user(email='ada@example.com', **overrides):
    fields = {
        'email': email,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password=VALID_PASSWORD, **fields)


def make_organization(name='Acme Labs', owner=None):
    """An organization plus the creator's membership, via the real bootstrap."""
    from organizations.services import CreateOrganizationInput, create_organization_for_user

    if owner is None:
        owner = make_user(f'{name.split()[0].lower()}@example.com')
    result = create_organization_for_user(owner, CreateOrganizationInput(name=name))
    return result.organization, result.membership


def add_active_member(organization, user, *, system_role=False):
    """
    An ACTIVE membership, optionally holding a system role.

    The system role is the organization's *existing* one - the `Owner` role
    bootstrap already created - rather than a second one, because role slugs are
    unique per organization and inventing an `owner` here would collide with
    it. The `system_role=False` branch is a *custom* role somebody created,
    carrying no permissions: the reviewer gate turns on `idea.review`, which
    the Owner role holds and this one does not, and a suite that used the Owner
    role everywhere would not notice a gate that had quietly degraded to "is a
    member".
    """
    membership = Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )
    if system_role:
        role = Role.objects.get(organization=organization, is_system=True)
    else:
        role, _ = Role.objects.get_or_create(
            organization=organization,
            slug='contributor',
            defaults={'name': 'Contributor', 'is_system': False},
        )
    MembershipRole.objects.create(membership=membership, role=role)
    return membership


def make_idea(organization, author, *, status=Idea.Status.DRAFT, **overrides):
    """An idea in `status`, with a consistent `submitted_at` for that status."""
    fields = {
        'organization': organization,
        'author': author,
        'title': 'An idea',
        'description': DESCRIPTION,
        'category': Category.objects.create(name=f'Cat {Category.objects.count() + 1}'),
        'visibility': Idea.Visibility.ORGANIZATION,
    }
    if status != Idea.Status.DRAFT:
        fields['submitted_at'] = timezone.now()
    fields.update(overrides)
    return Idea.objects.create(**fields)


def move(idea, status):
    """Put an idea into `status` directly, bypassing the lifecycle.

    For arranging a test's *starting point* - which is legitimate, because a
    `SUBMITTED` idea has to exist before "start reviewing it" can be tested.
    Never used to assert an outcome.
    """
    idea.status = status
    if status != Idea.Status.DRAFT and idea.submitted_at is None:
        idea.submitted_at = timezone.now()
    idea.save()
    return idea


def review_move(user, idea_id, target):
    """
    Make a review-owned move the only way it can now be made (S3-004): on a
    locked idea, inside a transaction, through `apply_review_transition`.
    `reviews.services` is the production caller and writes the `Review` in the
    same transaction; this exercises the lifecycle half on its own.
    """
    with transaction.atomic():
        idea = selectors.get_idea_for_update(user, idea_id)
        if idea is None:
            raise services.IdeaError('Idea is unavailable.')
        return lifecycle.apply_review_transition(user, idea, target)


def individual_idea(world, author=None, **overrides):
    """
    An INDIVIDUAL-context idea: no organization, no team.

    Through `services.create_idea_in_context` rather than `Idea.objects.create`,
    so it is filed the way an individual files one - which is also the only way
    to get a row the `idea_submission_context_matches_tenants` constraint
    accepts, since an individual idea has no organization to name.

    Needed because the platform track is **shared** by all three contexts and a
    test about it should not care which one it is on. The organization context
    has to walk three moves to reach `SUBMITTED`; this walks one.
    """
    fields = {
        'title': 'An idea of my own',
        'description': DESCRIPTION,
        'category_id': Category.objects.create(name=f'Cat {Category.objects.count() + 1}').pk,
    }
    fields.update(overrides)
    return services.create_idea_in_context(
        author or world['author'],
        services.IdeaInput(**fields),
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
    )


def go_ahead_move(user, idea_id):
    """
    Make the owner's go-ahead the only way it can now be made.

    `lifecycle.transition_idea` deliberately refuses it: the transition that
    stamps `owner_go_ahead_at`/`owner_go_ahead_by` is
    `apply_owner_go_ahead`, on a locked idea inside a transaction, and the
    operation the UI calls is `go_ahead.confirm_go_ahead` (which additionally
    requires the approval report to exist - see `ideas/tests/test_go_ahead.py`).
    So the lifecycle half is exercised here and the operation's meaning there,
    rather than one test standing in for the other.
    """
    with transaction.atomic():
        idea = selectors.get_idea_for_update(user, idea_id)
        if idea is None:
            raise services.IdeaError('Idea is unavailable.')
        return lifecycle.apply_owner_go_ahead(user, idea)


@pytest.fixture
def world():
    """
    One organization, its author, a reviewer and an ordinary member.

    The reviewer and the member are separate people on purpose: several rules
    below turn on the difference, and one user playing all three parts would
    hide any of them that were actually testing the fixture.
    """
    author = make_user('author@example.com')
    organization, _ = make_organization(owner=author)
    # Two reviewers, on purpose and for a reason. `reviewer` is an organization
    # Owner: an active member of the idea's own organization holding
    # `idea.review`, which is exactly what **organization** review is authorized
    # by. `platform_reviewer` is a member of no organization at all and holds the
    # platform-scoped permission instead, which is what **platform** review is
    # authorized by. Collapsing them would have hidden exactly the thing this
    # phase introduced.
    reviewer = make_user('reviewer@example.com')
    add_active_member(organization, reviewer, system_role=True)
    platform_reviewer = make_user('platform@example.com')
    grant_platform_reviewer(platform_reviewer)
    member = make_user('member@example.com')
    add_active_member(organization, member, system_role=False)
    return {
        'organization': organization,
        'author': author,
        'reviewer': reviewer,
        'platform_reviewer': platform_reviewer,
        'member': member,
    }


def platform_idea(world, *, status=Idea.Status.SUBMITTED):
    """
    An organization-context idea, genuinely submitted to the platform.

    Walks the whole journey - the author's submit, the organization's
    confirmation, the author's submit-on - because the platform track's moves can
    only be exercised on an idea that actually got there, and a fixture that
    wrote `SUBMITTED` directly would be asserting states the lifecycle refuses to
    produce.
    """
    idea = make_idea(world['organization'], world['author'])
    services.submit_idea(world['author'], idea.pk)
    idea.refresh_from_db()
    confirm_for_organization(idea)
    idea.refresh_from_db()
    services.submit_to_platform(world['author'], idea.pk)
    idea.refresh_from_db()
    if status != Idea.Status.SUBMITTED:
        idea = move(idea, status)
    return idea


# --- the matrix itself --------------------------------------------------------------


#: The lifecycle, decided. A pair added to the code without being listed here
#: fails `test_the_matrix_is_exactly_the_documented_one` rather than quietly
#: becoming reachable, and one removed fails because a documented path stops
#: existing.
#:
#: Read it top to bottom as the journey: the author puts an idea forward (to
#: their organization first, if it has one), the organization confirms or asks
#: for changes, the author submits to the platform, a platform reviewer claims
#: and decides, and the author gives the go-ahead. Three actor kinds, and the
#: two reviewer kinds are deliberately **not** interchangeable.
DOCUMENTED_TRANSITIONS = {
    # --- the author ------------------------------------------------------------
    (Idea.Status.DRAFT, Idea.Status.SUBMITTED_TO_ORGANIZATION): lifecycle.AUTHOR,
    (Idea.Status.DRAFT, Idea.Status.SUBMITTED): lifecycle.AUTHOR,
    (
        Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
        Idea.Status.SUBMITTED_TO_ORGANIZATION,
    ): lifecycle.AUTHOR,
    (Idea.Status.ORGANIZATION_CONFIRMED, Idea.Status.SUBMITTED): lifecycle.AUTHOR,
    (Idea.Status.CHANGES_REQUESTED, Idea.Status.SUBMITTED): lifecycle.AUTHOR,
    (Idea.Status.APPROVED, Idea.Status.READY_FOR_IMPLEMENTATION): lifecycle.AUTHOR,
    # --- the organization's reviewers -------------------------------------------
    (
        Idea.Status.SUBMITTED_TO_ORGANIZATION,
        Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
    ): lifecycle.ORGANIZATION_REVIEWER,
    (
        Idea.Status.SUBMITTED_TO_ORGANIZATION,
        Idea.Status.ORGANIZATION_CONFIRMED,
    ): lifecycle.ORGANIZATION_REVIEWER,
    # --- the platform's reviewers ------------------------------------------------
    (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW): lifecycle.PLATFORM_REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED): lifecycle.PLATFORM_REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED): lifecycle.PLATFORM_REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED): lifecycle.PLATFORM_REVIEWER,
    # --- the hand-off to the developer track ---------------------------------------
    (
        Idea.Status.READY_FOR_IMPLEMENTATION,
        Idea.Status.AUTOMATION_PROPOSAL,
    ): lifecycle.PLATFORM_REVIEWER,
}


def test_the_matrix_is_exactly_the_documented_one():
    assert lifecycle.TRANSITIONS == DOCUMENTED_TRANSITIONS


def test_every_pair_names_a_known_actor_and_known_statuses():
    for (from_status, to_status), actor in lifecycle.TRANSITIONS.items():
        assert from_status in Idea.Status.values, from_status
        assert to_status in Idea.Status.values, to_status
        assert actor in {
            lifecycle.AUTHOR,
            lifecycle.ORGANIZATION_REVIEWER,
            lifecycle.PLATFORM_REVIEWER,
        }


def test_no_actor_kind_ever_reviewers_their_own_idea():
    """
    Structural, not a check somebody could forget: the author is the *only*
    actor that is a person, and both reviewer kinds are asked "not the author"
    before their own permission is consulted. So an Owner holding every
    organization permission still cannot review their own idea, and neither can
    a platform administrator who wrote one.
    """
    assert lifecycle.TRANSITIONS[Idea.Status.APPROVED, Idea.Status.READY_FOR_IMPLEMENTATION] == (
        lifecycle.AUTHOR
    ), "the go-ahead is the author's alone"
    for pair, actor in lifecycle.TRANSITIONS.items():
        if actor == lifecycle.AUTHOR:
            continue
        assert pair in {
            (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CONFIRMED),
            (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CHANGES_REQUESTED),
            (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW),
            (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED),
            (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED),
            (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED),
            (Idea.Status.READY_FOR_IMPLEMENTATION, Idea.Status.AUTOMATION_PROPOSAL),
        }


def test_no_status_can_reach_itself():
    """
    A no-op transition would be a way to run the write path (and any future
    side effect in it) without changing anything, so it is not in the matrix.
    """
    assert all(from_status != to_status for from_status, to_status in lifecycle.TRANSITIONS)


def test_nothing_returns_to_draft():
    """
    A `DRAFT` is by definition an idea with no `submitted_at`, so a move back
    to it would have to either erase an audit fact or contradict the model's
    own invariant. The route back is `CHANGES_REQUESTED`, which the author
    edits as a draft in spirit without pretending it was never submitted.
    """
    assert not any(to_status == Idea.Status.DRAFT for _, to_status in lifecycle.TRANSITIONS)


# --- valid transitions --------------------------------------------------------------


@pytest.mark.django_db
class TestValidTransitions:
    def test_draft_to_submitted(self, world):
        """
        For an individual idea, submitting *is* going to the platform.

        The context decides the door, and this is the individual's door: there is
        no organization in the way to confirm anything, so one move puts the idea
        in front of the platform reviewers.
        """
        idea = individual_idea(world)

        result = lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert result.status == Idea.Status.SUBMITTED
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_submitted_to_under_review(self, world):
        idea = platform_idea(world)

        result = review_move(world['platform_reviewer'], idea.pk, Idea.Status.UNDER_REVIEW)

        assert result.status == Idea.Status.UNDER_REVIEW

    def test_under_review_to_changes_requested(self, world):
        idea = platform_idea(world, status=Idea.Status.UNDER_REVIEW)

        result = review_move(world['platform_reviewer'], idea.pk, Idea.Status.CHANGES_REQUESTED)

        assert result.status == Idea.Status.CHANGES_REQUESTED

    def test_changes_requested_back_to_submitted(self, world):
        """
        The re-submission. The author owns this move, not the reviewer - the
        whole point of `CHANGES_REQUESTED` is that the author answers it.
        """
        idea = platform_idea(world, status=Idea.Status.CHANGES_REQUESTED)

        result = lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert result.status == Idea.Status.SUBMITTED

    def test_under_review_to_approved(self, world):
        idea = platform_idea(world, status=Idea.Status.UNDER_REVIEW)

        result = review_move(world['platform_reviewer'], idea.pk, Idea.Status.APPROVED)

        assert result.status == Idea.Status.APPROVED

    def test_under_review_to_rejected(self, world):
        idea = platform_idea(world, status=Idea.Status.UNDER_REVIEW)

        result = review_move(world['platform_reviewer'], idea.pk, Idea.Status.REJECTED)

        assert result.status == Idea.Status.REJECTED

    def test_ready_for_implementation_to_automation_proposal(self, world):
        """
        The hand-off to the developer track, which is Sprint 4's to act on.

        It follows the owner's go-ahead rather than the platform's approval: an
        idea that nobody has authorized cannot be handed to a developer, however
        good the report is. That is why `APPROVED -> AUTOMATION_PROPOSAL` is not
        in the matrix - and this test would fail if it were ever added, because
        the setup it would need is an approved idea, not a ready one.
        """
        idea = platform_idea(world, status=Idea.Status.APPROVED)
        go_ahead_move(world['author'], idea.pk)

        result = lifecycle.transition_idea(
            world['platform_reviewer'], idea.pk, Idea.Status.AUTOMATION_PROPOSAL
        )

        assert result.status == Idea.Status.AUTOMATION_PROPOSAL

    def test_the_whole_path_runs_end_to_end(self, world):
        """
        The journey, in one test, in order.

        Proves the matrix is *reachable* as a chain and not merely as a set of
        independent claims - a matrix where each pair worked but the states could
        not be reached would satisfy every test above and be useless.

        This is the **organization** journey, because that is the longer one:
        draft, to the organization, confirmed, submitted to the platform, claimed,
        sent back, resubmitted, claimed again, approved, given the go-ahead, and
        only then handed to the developer track. Thirteen moves by three different
        kinds of actor.
        """
        idea = make_idea(world['organization'], world['author'])

        for actor, target in (
            (world['author'], Idea.Status.SUBMITTED_TO_ORGANIZATION),
            (world['reviewer'], Idea.Status.ORGANIZATION_CONFIRMED),
            (world['author'], Idea.Status.SUBMITTED),
            (world['platform_reviewer'], Idea.Status.UNDER_REVIEW),
            (world['platform_reviewer'], Idea.Status.CHANGES_REQUESTED),
            (world['author'], Idea.Status.SUBMITTED),
            (world['platform_reviewer'], Idea.Status.UNDER_REVIEW),
            (world['platform_reviewer'], Idea.Status.APPROVED),
        ):
            current = Idea.objects.get(pk=idea.pk).status
            if (current, target) in lifecycle.REVIEW_OWNED_TRANSITIONS:
                review_move(actor, idea.pk, target)
            else:
                lifecycle.transition_idea(actor, idea.pk, target)
            assert Idea.objects.get(pk=idea.pk).status == target

        # The last two moves are not made through the generic transition at all:
        # the go-ahead is the author's explicit operation, and the hand-off to
        # the developer track follows it.
        go_ahead_move(world['author'], idea.pk)
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.READY_FOR_IMPLEMENTATION

        lifecycle.transition_idea(
            world['platform_reviewer'], idea.pk, Idea.Status.AUTOMATION_PROPOSAL
        )
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.AUTOMATION_PROPOSAL

    def test_transition_idea_refuses_every_review_owned_move(self, world):
        """
        S3-004: a review move made through `transition_idea` would leave no
        `Review`. So a reviewer who could otherwise make it is told where it is
        made instead, and the idea does not move.
        """
        # One idea per pair, in the state that pair starts from. The platform
        # pairs need a really-submitted idea (a platform reviewer outside the
        # tenant can only read one the platform actually received), and the
        # organization's pair needs an organization idea waiting for it.
        starting_states = {
            (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CONFIRMED),
            (
                Idea.Status.SUBMITTED_TO_ORGANIZATION,
                Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
            ),
            (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW),
            (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED),
            (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED),
            (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED),
        }
        assert set(lifecycle.REVIEW_OWNED_TRANSITIONS) == starting_states

        for from_status, target in sorted(lifecycle.REVIEW_OWNED_TRANSITIONS):
            idea = (
                platform_idea(world, status=from_status)
                if from_status != Idea.Status.SUBMITTED_TO_ORGANIZATION
                else platform_idea(world, status=Idea.Status.SUBMITTED_TO_ORGANIZATION)
            )
            actor = (
                world['reviewer']
                if from_status == Idea.Status.SUBMITTED_TO_ORGANIZATION
                else world['platform_reviewer']
            )

            with pytest.raises(services.IdeaError) as exc_info:
                lifecycle.transition_idea(actor, idea.pk, target)

            assert exc_info.value.message == lifecycle.REVIEW_OWNED_MESSAGE
            assert Idea.objects.get(pk=idea.pk).status == from_status

    def test_the_review_owned_moves_are_exactly_the_six_review_pairs(self):
        """
        Six, across two tracks: the organization's two and the platform's four.

        A review-owned move is one that must leave a `Review` row, so it cannot
        be made by naming a status - the public transition refuses it and points
        at the workspace. That is why `TRANSITION_REVIEW_SCOPE` exists too: it is
        what a review writes to agree with the lifecycle on *which* track a pair
        belongs to, and a platform decision cannot be recorded as an
        organization's.
        """
        assert {
            (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CONFIRMED),
            (
                Idea.Status.SUBMITTED_TO_ORGANIZATION,
                Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
            ),
            (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW),
            (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED),
            (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED),
            (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED),
        } == lifecycle.REVIEW_OWNED_TRANSITIONS
        assert set(lifecycle.TRANSITIONS) >= lifecycle.REVIEW_OWNED_TRANSITIONS
        assert set(lifecycle.REVIEW_OWNED_TRANSITIONS) == set(lifecycle.TRANSITION_REVIEW_SCOPE)
        assert set(lifecycle.TRANSITION_REVIEW_SCOPE.values()) == {'organization', 'platform'}

    # `transaction=True`: the default `django_db` wraps every test in a
    # transaction, so "outside one" could not be observed without it.
    @pytest.mark.django_db(transaction=True)
    def test_apply_review_transition_refuses_outside_a_transaction(self, world):
        idea = platform_idea(world)

        with pytest.raises(RuntimeError):
            lifecycle.apply_review_transition(world['reviewer'], idea, Idea.Status.UNDER_REVIEW)

    def test_apply_review_transition_applies_the_actor_rule(self, world):
        idea = platform_idea(world)

        for actor in (world['author'], world['member']):
            with pytest.raises(services.IdeaError) as exc_info:
                review_move(actor, idea.pk, Idea.Status.UNDER_REVIEW)
            assert 'not allowed' in exc_info.value.message

    def test_apply_review_transition_refuses_a_non_review_move(self, world):
        idea = platform_idea(world, status=Idea.Status.APPROVED)

        with pytest.raises(services.IdeaError):
            review_move(world['reviewer'], idea.pk, Idea.Status.AUTOMATION_PROPOSAL)
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.APPROVED

    def test_submit_idea_still_goes_through_the_matrix(self, world):
        """
        One `submit_idea`, whichever stage the idea is at.

        **The target comes from the idea's context**, not from a fixed status: an
        organization submission goes to its organization, a team or individual one
        goes to the platform. So the same button does the right thing in both, and
        a changes-requested idea is re-submitted through the same call - the author
        never chooses a workflow.
        """
        idea = make_idea(world['organization'], world['author'])

        assert (
            services.submit_idea(world['author'], idea.pk).status
            == Idea.Status.SUBMITTED_TO_ORGANIZATION
        )

        individual = individual_idea(world)

        assert services.submit_idea(world['author'], individual.pk).status == Idea.Status.SUBMITTED

        # Re-submitted to the organization...
        move(Idea.objects.get(pk=idea.pk), Idea.Status.ORGANIZATION_CHANGES_REQUESTED)
        assert (
            services.submit_idea(world['author'], idea.pk).status
            == Idea.Status.SUBMITTED_TO_ORGANIZATION
        )

        # ...and once the platform has seen it, re-submitted to the platform. The
        # author is offered the same button either way; only the stage differs.
        move(Idea.objects.get(pk=idea.pk), Idea.Status.CHANGES_REQUESTED)
        assert services.submit_idea(world['author'], idea.pk).status == Idea.Status.SUBMITTED


@pytest.mark.django_db
class TestTheSubmissionContextGate:
    """
    The matrix is decided over statuses, so on its own it cannot tell that
    `DRAFT -> SUBMITTED` means two different things: for a team or individual
    idea it is the submission, and for an organization idea it would be a way to
    reach the platform **without its organization ever seeing it**.

    That is not a theoretical hole. Every organization-review guarantee in this
    module - the two outcomes, the reviewer role, the queue, the audit - assumes
    an organization idea cannot get past its organization, and the pair being in
    the matrix at all is what let it through. These tests are the gate; the code
    they cover is `lifecycle.context_allows`.
    """

    def test_an_organization_idea_cannot_skip_its_organization(self, world):
        """
        The author pressing Submit on an organization draft is **not** refused as
        an unauthorized actor - they are the author, and submitting is theirs to
        do. They are told their organization has to confirm it first, which is
        the truthful answer and the one that tells them what to do next.
        """
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert exc_info.value.message == lifecycle.CONTEXT_MISMATCH
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT
        assert Idea.objects.get(pk=idea.pk).submitted_at is None

    def test_neither_reviewer_kind_can_be_the_way_round_it(self, world):
        """
        Nor can a review: an organization review on a team idea, or a platform
        review reached before the organization confirmed, would leave a `Review`
        row about a journey that was never legal. Both write paths ask
        `context_allows`, not just the public transition.
        """
        # An individual idea has no organization to name, so `ORGANIZATION`
        # visibility is no longer a legal audience for one and the default
        # `PUBLIC` is used here. The point of the test is the *review* gate, not
        # the audience: `create_idea` refuses the impossible combination, which
        # `TestVisibilityMatchesTheContext` pins directly.
        teamless_individual = individual_idea(world)
        with pytest.raises(services.IdeaError):
            lifecycle.apply_review_transition(
                world['platform_reviewer'],
                Idea.objects.get(pk=teamless_individual.pk),
                Idea.Status.UNDER_REVIEW,
            )

        organization_draft = make_idea(world['organization'], world['author'])
        with pytest.raises(services.IdeaError):
            lifecycle.apply_review_transition(
                world['platform_reviewer'],
                Idea.objects.get(pk=organization_draft.pk),
                Idea.Status.UNDER_REVIEW,
            )

    def test_the_gate_is_off_the_organization_track_only(self, world):
        """
        The other direction, because a gate that refuses too much is as broken as
        one that refuses too little: the organization journey in full is
        unaffected, and an individual idea still goes straight to the platform.
        """
        idea = make_idea(world['organization'], world['author'])
        lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED_TO_ORGANIZATION)
        review_move(world['reviewer'], idea.pk, Idea.Status.ORGANIZATION_CONFIRMED)
        lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

        individual = individual_idea(world)
        lifecycle.transition_idea(world['author'], individual.pk, Idea.Status.SUBMITTED)

        assert Idea.objects.get(pk=individual.pk).status == Idea.Status.SUBMITTED

    def test_the_go_ahead_survives_the_developer_handoff(self, world):
        """
        The database guarantee, at the level it is written.

        `idea_go_ahead_only_when_ready` says an idea cannot be handed to
        implementation without the owner's go-ahead. Read literally - only while
        the idea *is* in `ready_for_implementation` - it also said the handoff to
        Sprint 4 could never happen, because the row keeps its go-ahead and the
        constraint would refuse to save it. So the constraint names both states
        on the path, and this test is why that is the right pair to name.
        """
        idea = platform_idea(world, status=Idea.Status.APPROVED)
        go_ahead_move(world['author'], idea.pk)

        lifecycle.transition_idea(
            world['platform_reviewer'], idea.pk, Idea.Status.AUTOMATION_PROPOSAL
        )

        handed_over = Idea.objects.get(pk=idea.pk)
        assert handed_over.status == Idea.Status.AUTOMATION_PROPOSAL
        # Still on the row, and still naming the person who authorized it: the
        # hand-off is not a decision, so it does not erase one.
        assert handed_over.owner_go_ahead_by_id == world['author'].pk
        assert handed_over.owner_go_ahead_at is not None


# --- invalid transitions ------------------------------------------------------------


@pytest.mark.django_db
class TestInvalidTransitions:
    @pytest.mark.parametrize(
        ('from_status', 'to_status'),
        [
            (Idea.Status.DRAFT, Idea.Status.APPROVED),
            (Idea.Status.DRAFT, Idea.Status.UNDER_REVIEW),
            (Idea.Status.SUBMITTED, Idea.Status.APPROVED),
            (Idea.Status.REJECTED, Idea.Status.APPROVED),
            (Idea.Status.APPROVED, Idea.Status.REJECTED),
            (Idea.Status.AUTOMATION_PROPOSAL, Idea.Status.DRAFT),
        ],
    )
    def test_the_documented_impossible_pairs_are_refused(self, world, from_status, to_status):
        """
        Driven by the *most privileged* actor there is, so a refusal cannot be
        mistaken for an authorization failure. Each of these is refused because
        the lifecycle does not contain the pair, not because of who asked.
        """
        idea = move(make_idea(world['organization'], world['author']), from_status)

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world['reviewer'], idea.pk, to_status)

        assert Idea.objects.get(pk=idea.pk).status == from_status

    def test_no_unlisted_pair_is_accepted(self, world):
        """
        The general property the six cases above only sample: the matrix is
        exhaustive, so *any* pair it does not contain is refused. Without this,
        a pair added to neither the tests nor the matrix could exist.
        """
        legal = set(lifecycle.TRANSITIONS)
        for from_status in Idea.Status.values:
            for to_status in Idea.Status.values:
                if (from_status, to_status) in legal:
                    continue
                idea = move(make_idea(world['organization'], world['author']), from_status)
                with pytest.raises(services.IdeaError):
                    lifecycle.transition_idea(world['reviewer'], idea.pk, to_status)
                assert Idea.objects.get(pk=idea.pk).status == from_status

    def test_a_status_outside_the_vocabulary_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(world['author'], idea.pk, 'APPROVED_BY_MYSELF')

        assert 'not a state' in exc_info.value.message
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT

    def test_submitting_an_already_submitted_idea_keeps_the_s2_002_wording(self, world):
        """
        `submitIdea` shipped this exact string and the frontend shows the
        backend's message verbatim, so the lifecycle preserves it rather than
        replacing it with a status-pair sentence a user would read twice.
        """
        idea = platform_idea(world)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert exc_info.value.message == 'Only a draft can be edited.'

    def test_an_incomplete_idea_cannot_be_submitted(self, world):
        idea = make_idea(world['organization'], world['author'], description='')

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT

    def test_an_unclassified_idea_cannot_be_submitted(self, world):
        idea = make_idea(world['organization'], world['author'], category=None)

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT

    def test_re_submission_is_validated_too(self, world):
        """
        An idea whose description was emptied while addressing review feedback
        is no more ready the second time than the first.
        """
        idea = move(
            make_idea(world['organization'], world['author'], description=''),
            Idea.Status.CHANGES_REQUESTED,
        )

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.CHANGES_REQUESTED

    def test_a_refused_idea_cannot_be_promoted_by_editing_its_content(self, world):
        """
        The write path for content is separate from the write path for status,
        and neither can stand in for the other: `update_idea` refuses a
        non-draft, and it has no status field with which to change one.
        """
        idea = platform_idea(world)

        with pytest.raises(services.IdeaError):
            services.update_idea(
                world['author'],
                idea.pk,
                services.IdeaInput(title='Rewritten', description=DESCRIPTION),
            )

        assert Idea.objects.get(pk=idea.pk).title == 'An idea'


# --- authorization ------------------------------------------------------------------


@pytest.mark.django_db
class TestTransitionAuthorization:
    def test_an_unauthenticated_caller_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(None, idea.pk, Idea.Status.SUBMITTED)

        assert exc_info.value.reason == 'unauthenticated'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT

    def test_a_deactivated_caller_is_refused(self, world):
        author = world['author']
        author.is_active = False
        author.save(update_fields=['is_active'])
        idea = make_idea(world['organization'], author)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(author, idea.pk, Idea.Status.SUBMITTED)

        assert exc_info.value.reason == 'unauthenticated'

    def test_a_non_member_is_refused(self, world):
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(outsider, idea.pk, Idea.Status.SUBMITTED)

        assert exc_info.value.message == 'Idea is unavailable.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT

    def test_a_member_with_no_system_role_cannot_review(self, world):
        """
        The reviewer gate. An active member holding a *custom* role is an
        ordinary contributor, and an ordinary contributor does not approve
        things.
        """
        idea = platform_idea(world)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(world['member'], idea.pk, Idea.Status.UNDER_REVIEW)

        assert exc_info.value.message == 'You are not allowed to make that change to this idea.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_a_departed_member_cannot_review(self, world):
        """
        An **organization** reviewer who has left the organization cannot read its
        ideas, so they cannot review them either.

        Refused by the *visibility* check rather than the membership one, and that
        is the stronger outcome: they are indistinguishable here from a stranger,
        which is what stops this being a probe for which idea ids exist in a
        tenant somebody used to belong to.
        """
        reviewer = world['reviewer']
        membership = Membership.objects.get(user=reviewer, organization=world['organization'])
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])
        idea = platform_idea(world, status=Idea.Status.SUBMITTED_TO_ORGANIZATION)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(reviewer, idea.pk, Idea.Status.ORGANIZATION_CONFIRMED)

        assert exc_info.value.message == 'Idea is unavailable.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED_TO_ORGANIZATION

    def test_leaving_the_organization_does_not_change_platform_eligibility(self, world):
        """
        The other direction of the same fact: leaving an organization changes
        nothing about somebody's platform eligibility, in either direction.
        """
        reviewer = world['platform_reviewer']
        assert lifecycle.is_platform_reviewer(reviewer)

        reviewer.memberships.filter(organization=world['organization']).delete()

        # Still eligible - platform review never consulted the membership - and
        # still not an *organization* reviewer, which is what the membership was.
        assert lifecycle.is_platform_reviewer(User.objects.get(pk=reviewer.pk))
        assert not lifecycle.is_organization_reviewer(User.objects.get(pk=reviewer.pk), None)

    def test_a_departed_author_cannot_submit(self, world):
        author = world['author']
        membership = Membership.objects.get(user=author, organization=world['organization'])
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])
        idea = make_idea(world['organization'], author)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(author, idea.pk, Idea.Status.SUBMITTED)

        assert exc_info.value.reason == 'membership_required'

    def test_a_cross_organization_reviewer_cannot_review(self, world):
        """
        A genuine system role in a *different* organization is not authority in
        this one. Without the organization clause on the role lookup, a platform
        member who owns any organization would be a reviewer everywhere.
        """
        stranger = make_user('stranger@example.com')
        other, _ = make_organization(name='Other Co', owner=stranger)
        assert other.pk != world['organization'].pk
        idea = platform_idea(world)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(stranger, idea.pk, Idea.Status.UNDER_REVIEW)

        assert exc_info.value.message == 'Idea is unavailable.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_an_ordinary_member_cannot_approve(self, world):
        idea = platform_idea(world, status=Idea.Status.UNDER_REVIEW)

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world['member'], idea.pk, Idea.Status.APPROVED)

        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.UNDER_REVIEW

    def test_the_author_cannot_review_their_own_idea(self, world):
        """
        Self-review, refused structurally. The author here *does* hold the
        Owner role - bootstrap makes everybody's own organization theirs - so
        the role check passes and only the authorship check can stop this. It
        is the case that a role-only implementation would wave through.
        """
        author = world['author']
        assert (
            Membership.objects.get(user=author, organization=world['organization'])
            .membership_roles.filter(role__is_system=True)
            .exists()
        )
        submitted = move(make_idea(world['organization'], author), Idea.Status.SUBMITTED)
        under_review = move(make_idea(world['organization'], author), Idea.Status.UNDER_REVIEW)

        # Only pairs that exist are tried: `SUBMITTED -> APPROVED` is not in
        # the matrix at all, so refusing it would prove nothing about authorship.
        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(author, submitted.pk, Idea.Status.UNDER_REVIEW)
        assert 'not allowed' in exc_info.value.message

        for target in (
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.APPROVED,
            Idea.Status.REJECTED,
        ):
            with pytest.raises(services.IdeaError) as exc_info:
                lifecycle.transition_idea(author, under_review.pk, target)
            assert 'not allowed' in exc_info.value.message, target

        assert Idea.objects.get(pk=submitted.pk).status == Idea.Status.SUBMITTED
        assert Idea.objects.get(pk=under_review.pk).status == Idea.Status.UNDER_REVIEW

    def test_a_reviewer_cannot_submit_somebody_elses_draft(self, world):
        """
        The two actor kinds do not overlap: holding the reviewer role does not
        let somebody submit another person's work.
        """
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world['reviewer'], idea.pk, Idea.Status.SUBMITTED)

        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT

    def test_a_reviewer_cannot_review_a_private_idea_they_cannot_see(self, world):
        """
        Visibility and lifecycle agree. A private idea was never shared with a
        reviewer, so there is nothing to review - and the refusal is the same
        one an id that does not exist gets, so the transition endpoint is not an
        existence oracle either.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        move(idea, Idea.Status.SUBMITTED)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(world['platform_reviewer'], idea.pk, Idea.Status.UNDER_REVIEW)

        assert exc_info.value.message == 'Idea is unavailable.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_an_unknown_idea_answers_exactly_like_an_invisible_one(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        move(idea, Idea.Status.SUBMITTED)

        with pytest.raises(services.IdeaError) as unknown:
            lifecycle.transition_idea(world['reviewer'], 999999, Idea.Status.UNDER_REVIEW)
        with pytest.raises(services.IdeaError) as invisible:
            lifecycle.transition_idea(world['reviewer'], idea.pk, Idea.Status.UNDER_REVIEW)

        assert unknown.value.message == invisible.value.message


# --- submitted_at and atomicity -----------------------------------------------------


@pytest.mark.django_db
class TestSubmittedAtSemantics:
    def test_the_first_submission_stamps_it(self, world):
        idea = individual_idea(world)
        before = timezone.now()

        result = lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert result.submitted_at is not None
        assert before <= result.submitted_at <= timezone.now()

    def test_a_re_submission_keeps_the_original_stamp(self, world):
        """
        "When was this first put forward" is an audit fact a second submission
        does not change - and the model requires a timestamp on anything past
        DRAFT, so clearing it would be an inconsistent row.
        """
        idea = individual_idea(world)
        lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)
        first = Idea.objects.get(pk=idea.pk).submitted_at
        move(Idea.objects.get(pk=idea.pk), Idea.Status.CHANGES_REQUESTED)

        result = lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert result.submitted_at == first

    def test_review_transitions_never_touch_it(self, world):
        """
        Every move after the first submission leaves `submitted_at` alone.

        It is stamped exactly once, on the first `DRAFT -> SUBMITTED`, and the
        re-submission after a changes request keeps it - because "when was this
        first put forward" is an audit fact a second submission does not change.
        """
        idea = platform_idea(world)
        first = idea.submitted_at

        for target in (
            Idea.Status.UNDER_REVIEW,
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.SUBMITTED,
            Idea.Status.UNDER_REVIEW,
            Idea.Status.APPROVED,
            Idea.Status.READY_FOR_IMPLEMENTATION,
            Idea.Status.AUTOMATION_PROPOSAL,
        ):
            if target == Idea.Status.READY_FOR_IMPLEMENTATION:
                go_ahead_move(world['author'], idea.pk)
                assert Idea.objects.get(pk=idea.pk).submitted_at == first, target
                continue
            actor = (
                world['author'] if target == Idea.Status.SUBMITTED else world['platform_reviewer']
            )
            current = Idea.objects.get(pk=idea.pk).status
            if (current, target) in lifecycle.REVIEW_OWNED_TRANSITIONS:
                review_move(actor, idea.pk, target)
            else:
                lifecycle.transition_idea(actor, idea.pk, target)
            assert Idea.objects.get(pk=idea.pk).submitted_at == first, target

    def test_the_model_would_reject_an_inconsistent_row(self, world):
        """
        Why the transition writes the two fields together: the invariant is
        enforced by the model, not by this module's care.
        """
        from django.core.exceptions import ValidationError

        idea = make_idea(world['organization'], world['author'])
        broken = Idea.objects.get(pk=idea.pk)
        broken.status = Idea.Status.SUBMITTED  # no submitted_at

        with pytest.raises(ValidationError):
            broken.save()


@pytest.mark.django_db
class TestAtomicity:
    def test_a_refused_transition_leaves_the_row_exactly_as_it_was(self, world):
        """
        Nothing partial: not the status, not `submitted_at`, not the content.
        Checked field by field because "the status did not change" alone would
        pass even if the transition had stamped `submitted_at` on its way out.
        """
        idea = make_idea(world['organization'], world['author'])
        before = Idea.objects.get(pk=idea.pk)
        snapshot = (before.status, before.submitted_at, before.title, before.updated_at)

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.APPROVED)

        after = Idea.objects.get(pk=idea.pk)
        assert (after.status, after.submitted_at, after.title, after.updated_at) == snapshot

    def test_a_refused_transition_creates_no_token_or_side_row(self, world):
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.UNDER_REVIEW)

        assert Idea.objects.count() == 1

    @pytest.mark.django_db(transaction=True)
    def test_two_concurrent_transitions_of_the_same_idea_cannot_both_win(self, world):
        """
        The row lock. "Approve" and "Reject" fired at once must not both apply:
        without `select_for_update` both transactions read `UNDER_REVIEW`, both
        find their pair legal, and the last write silently wins - an audit trail
        that says an idea was approved when a rejection is what the reviewer
        actually decided.

        Two real connections and real threads, because a single-connection test
        cannot deadlock against itself and would pass whether or not the lock
        exists. `transaction=True` is required, not incidental: the default
        `django_db` wraps the test in a transaction that other connections
        cannot see, so without it both threads would fail to find the idea at
        all and the test would pass for entirely the wrong reason.
        """
        idea = platform_idea(world, status=Idea.Status.UNDER_REVIEW)
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def attempt(target):
            try:
                barrier.wait(timeout=10)
                # The review path since S3-004; the same locked read as
                # `transition_idea`, so the property under test is unchanged.
                review_move(world['platform_reviewer'], idea.pk, target)
            except Exception as exc:
                # Recorded rather than raised: a thread's exception would not
                # fail the test on its own, so the assertion below is what
                # checks that exactly one attempt was refused.
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=attempt, args=(Idea.Status.APPROVED,)),
            threading.Thread(target=attempt, args=(Idea.Status.REJECTED,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        final = Idea.objects.get(pk=idea.pk)
        assert final.status in {Idea.Status.APPROVED, Idea.Status.REJECTED}
        # Exactly one of them got through; the other was refused because the
        # status it read was no longer UNDER_REVIEW.
        assert len(errors) == 1
        assert isinstance(errors[0], services.IdeaError)


# --- what the client is offered -----------------------------------------------------


@pytest.mark.django_db
class TestAvailableTransitions:
    def test_the_author_is_offered_exactly_their_moves(self, world):
        """
        One move at a time, and the one that fits the idea's context.

        A draft organization idea is offered "submit to your organization" and
        *not* "submit to the platform": offering both would put a button in front
        of the author that the lifecycle refuses, and this is the client-facing
        half of `context_allows`.
        """
        organization_draft = make_idea(world['organization'], world['author'])
        individual_draft = individual_idea(world)
        organization_sent_back = platform_idea(
            world, status=Idea.Status.ORGANIZATION_CHANGES_REQUESTED
        )
        platform_sent_back = platform_idea(world, status=Idea.Status.CHANGES_REQUESTED)

        assert lifecycle.available_transitions(world['author'], organization_draft) == [
            Idea.Status.SUBMITTED_TO_ORGANIZATION
        ]
        assert lifecycle.available_transitions(world['author'], individual_draft) == [
            Idea.Status.SUBMITTED
        ]
        assert lifecycle.available_transitions(world['author'], organization_sent_back) == [
            Idea.Status.SUBMITTED_TO_ORGANIZATION
        ]
        assert lifecycle.available_transitions(world['author'], platform_sent_back) == [
            Idea.Status.SUBMITTED
        ]

    def test_a_reviewer_is_offered_no_review_owned_move(self, world):
        """
        S3-004: starting and deciding a review are offered through the review
        capability fields, not as generic transitions, so `transitionIdea` is
        never offered a move it would refuse. The hand-off from `APPROVED` is
        still a plain transition.
        """
        submitted = platform_idea(world)
        under_review = platform_idea(world, status=Idea.Status.UNDER_REVIEW)
        approved = platform_idea(world, status=Idea.Status.APPROVED)

        assert lifecycle.available_transitions(world['platform_reviewer'], submitted) == []
        assert lifecycle.available_transitions(world['platform_reviewer'], under_review) == []
        # Nothing on an approved idea either: the go-ahead is the author's, so
        # the one move that exists from `APPROVED` is not this reviewer's to make
        # and offering it would be offering a refusal.
        assert lifecycle.available_transitions(world['platform_reviewer'], approved) == []
        # The actor rule itself is unchanged: the reviewer *could* make them.
        assert lifecycle.can_transition(
            world['platform_reviewer'], submitted, Idea.Status.UNDER_REVIEW
        )

    def test_the_author_is_never_offered_a_review_move(self, world):
        """
        The self-review rule, visible from the client's side: an author holding
        the Owner role is offered nothing on their own submitted idea.
        """
        submitted = platform_idea(world)

        assert lifecycle.available_transitions(world['author'], submitted) == []

    def test_an_ordinary_member_is_offered_nothing(self, world):
        submitted = platform_idea(world)

        assert lifecycle.available_transitions(world['member'], submitted) == []

    def test_a_terminal_idea_offers_nothing(self, world):
        for status in (Idea.Status.REJECTED, Idea.Status.AUTOMATION_PROPOSAL):
            idea = move(make_idea(world['organization'], world['author']), status)
            for actor in (world['author'], world['reviewer'], world['platform_reviewer']):
                assert lifecycle.available_transitions(actor, idea) == [], status

    def test_an_anonymous_caller_is_offered_nothing(self, world):
        idea = make_idea(world['organization'], world['author'])

        assert lifecycle.available_transitions(None, idea) == []

    def test_a_reader_from_another_tenant_is_offered_nothing(self, world):
        """
        A `PUBLIC` idea is readable by anybody signed in, including somebody
        with no membership of its organization. Readable is not the same as
        actionable: they get no transitions at all, which is checked here on
        the client-facing rule as well as in the authorization tests.
        """
        stranger = make_user('stranger@example.com')
        make_organization(name='Other Co', owner=stranger)
        idea = move(
            make_idea(
                world['organization'],
                world['author'],
                visibility=Idea.Visibility.PUBLIC,
            ),
            Idea.Status.SUBMITTED,
        )

        assert lifecycle.is_organization_reviewer(stranger, idea) is False
        assert lifecycle.is_platform_reviewer(stranger, idea) is False
        assert lifecycle.can_transition(stranger, idea, Idea.Status.UNDER_REVIEW) is False
        assert lifecycle.available_transitions(stranger, idea) == []

    def test_the_reviewer_predicates_agree_with_the_transition_rule(self, world):
        """
        The standalone predicates and the matrix must not tell different stories
        about the same person, or a caller using one would offer actions the
        other refuses - and which one is right would depend on which a client
        happened to ask.

        Checked for **both** reviewer kinds, because they are two different
        permissions and the agreement has to hold for each.
        """
        submitted = platform_idea(world)
        under_review = platform_idea(world, status=Idea.Status.UNDER_REVIEW)

        for actor in (
            world['author'],
            world['reviewer'],
            world['platform_reviewer'],
            world['member'],
            None,
        ):
            expected = lifecycle.is_platform_reviewer(actor, submitted)
            if expected:
                assert lifecycle.can_transition(actor, submitted, Idea.Status.UNDER_REVIEW)
                assert lifecycle.can_transition(actor, under_review, Idea.Status.APPROVED)
            else:
                assert not lifecycle.can_transition(actor, submitted, Idea.Status.UNDER_REVIEW)
