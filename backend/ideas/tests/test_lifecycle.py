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
from django.db import connections
from django.utils import timezone

from ideas import lifecycle, services
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership, MembershipRole, Role

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
    deliberately not flagged `is_system`: that flag is the distinction the
    reviewer gate turns on, and a suite that used the Owner role everywhere
    would not notice a gate that had quietly degraded to "is a member".
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
    reviewer = make_user('reviewer@example.com')
    add_active_member(organization, reviewer, system_role=True)
    member = make_user('member@example.com')
    add_active_member(organization, member, system_role=False)
    return {
        'organization': organization,
        'author': author,
        'reviewer': reviewer,
        'member': member,
    }


# --- the matrix itself --------------------------------------------------------------


def test_the_matrix_is_exactly_the_documented_one():
    """
    The lifecycle, as a set. A pair added to the code without being decided
    fails here rather than quietly becoming reachable, and one removed fails
    because a documented path stops existing.
    """
    assert lifecycle.TRANSITIONS == {
        (Idea.Status.DRAFT, Idea.Status.SUBMITTED): lifecycle.AUTHOR,
        (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW): lifecycle.REVIEWER,
        (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED): lifecycle.REVIEWER,
        (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED): lifecycle.REVIEWER,
        (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED): lifecycle.REVIEWER,
        (Idea.Status.CHANGES_REQUESTED, Idea.Status.SUBMITTED): lifecycle.AUTHOR,
        (Idea.Status.APPROVED, Idea.Status.AUTOMATION_PROPOSAL): lifecycle.REVIEWER,
    }


def test_every_pair_names_a_known_actor_and_known_statuses():
    for (from_status, to_status), actor in lifecycle.TRANSITIONS.items():
        assert from_status in Idea.Status.values, from_status
        assert to_status in Idea.Status.values, to_status
        assert actor in {lifecycle.AUTHOR, lifecycle.REVIEWER}


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
        idea = make_idea(world['organization'], world['author'])

        result = lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert result.status == Idea.Status.SUBMITTED
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_submitted_to_under_review(self, world):
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)

        result = lifecycle.transition_idea(world['reviewer'], idea.pk, Idea.Status.UNDER_REVIEW)

        assert result.status == Idea.Status.UNDER_REVIEW

    def test_under_review_to_changes_requested(self, world):
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.UNDER_REVIEW)

        result = lifecycle.transition_idea(
            world['reviewer'], idea.pk, Idea.Status.CHANGES_REQUESTED
        )

        assert result.status == Idea.Status.CHANGES_REQUESTED

    def test_changes_requested_back_to_submitted(self, world):
        """
        The re-submission. The author owns this move, not the reviewer - the
        whole point of `CHANGES_REQUESTED` is that the author answers it.
        """
        idea = move(
            make_idea(world['organization'], world['author']), Idea.Status.CHANGES_REQUESTED
        )

        result = lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert result.status == Idea.Status.SUBMITTED

    def test_under_review_to_approved(self, world):
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.UNDER_REVIEW)

        result = lifecycle.transition_idea(world['reviewer'], idea.pk, Idea.Status.APPROVED)

        assert result.status == Idea.Status.APPROVED

    def test_under_review_to_rejected(self, world):
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.UNDER_REVIEW)

        result = lifecycle.transition_idea(world['reviewer'], idea.pk, Idea.Status.REJECTED)

        assert result.status == Idea.Status.REJECTED

    def test_approved_to_automation_proposal(self, world):
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.APPROVED)

        result = lifecycle.transition_idea(
            world['reviewer'], idea.pk, Idea.Status.AUTOMATION_PROPOSAL
        )

        assert result.status == Idea.Status.AUTOMATION_PROPOSAL

    def test_the_whole_path_runs_end_to_end(self, world):
        """
        Every pair in one test, in order. Proves the matrix is *reachable* as a
        chain and not merely as seven independent claims - a matrix where each
        pair worked but the states could not be reached would satisfy the tests
        above and be useless.
        """
        idea = make_idea(world['organization'], world['author'])

        for actor, target in (
            (world['author'], Idea.Status.SUBMITTED),
            (world['reviewer'], Idea.Status.UNDER_REVIEW),
            (world['reviewer'], Idea.Status.CHANGES_REQUESTED),
            (world['author'], Idea.Status.SUBMITTED),
            (world['reviewer'], Idea.Status.UNDER_REVIEW),
            (world['reviewer'], Idea.Status.APPROVED),
            (world['reviewer'], Idea.Status.AUTOMATION_PROPOSAL),
        ):
            lifecycle.transition_idea(actor, idea.pk, target)
            assert Idea.objects.get(pk=idea.pk).status == target

    def test_submit_idea_still_goes_through_the_matrix(self, world):
        """
        S2-002's operation is a delegation, not a second implementation, so it
        obeys the lifecycle: the author may submit, and a changes-requested
        idea may be re-submitted through the same call.
        """
        idea = make_idea(world['organization'], world['author'])

        assert services.submit_idea(world['author'], idea.pk).status == Idea.Status.SUBMITTED

        move(Idea.objects.get(pk=idea.pk), Idea.Status.CHANGES_REQUESTED)
        assert services.submit_idea(world['author'], idea.pk).status == Idea.Status.SUBMITTED


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
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)

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
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)

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
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(world['member'], idea.pk, Idea.Status.UNDER_REVIEW)

        assert exc_info.value.message == 'You are not allowed to make that change to this idea.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_a_departed_member_cannot_review(self, world):
        """
        Refused by the *visibility* check rather than the membership one, and
        that is the stronger outcome: a reviewer who has left the organization
        cannot read the tenant's ideas either, so they are indistinguishable
        here from a stranger - which is what stops this endpoint being a probe
        for which idea ids exist in a tenant somebody used to belong to.
        """
        reviewer = world['reviewer']
        membership = Membership.objects.get(user=reviewer, organization=world['organization'])
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(reviewer, idea.pk, Idea.Status.UNDER_REVIEW)

        assert exc_info.value.message == 'Idea is unavailable.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

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
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)

        with pytest.raises(services.IdeaError) as exc_info:
            lifecycle.transition_idea(stranger, idea.pk, Idea.Status.UNDER_REVIEW)

        assert exc_info.value.message == 'Idea is unavailable.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_an_ordinary_member_cannot_approve(self, world):
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.UNDER_REVIEW)

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
            lifecycle.transition_idea(world['reviewer'], idea.pk, Idea.Status.UNDER_REVIEW)

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
        idea = make_idea(world['organization'], world['author'])
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
        idea = make_idea(world['organization'], world['author'])
        lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)
        first = Idea.objects.get(pk=idea.pk).submitted_at
        move(Idea.objects.get(pk=idea.pk), Idea.Status.CHANGES_REQUESTED)

        result = lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)

        assert result.submitted_at == first

    def test_review_transitions_never_touch_it(self, world):
        idea = make_idea(world['organization'], world['author'])
        lifecycle.transition_idea(world['author'], idea.pk, Idea.Status.SUBMITTED)
        first = Idea.objects.get(pk=idea.pk).submitted_at

        for target in (
            Idea.Status.UNDER_REVIEW,
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.SUBMITTED,
            Idea.Status.UNDER_REVIEW,
            Idea.Status.APPROVED,
            Idea.Status.AUTOMATION_PROPOSAL,
        ):
            actor = world['author'] if target == Idea.Status.SUBMITTED else world['reviewer']
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
        idea = move(make_idea(world['organization'], world['author']), Idea.Status.UNDER_REVIEW)
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def attempt(target):
            try:
                barrier.wait(timeout=10)
                lifecycle.transition_idea(world['reviewer'], idea.pk, target)
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
        draft = make_idea(world['organization'], world['author'])
        changes_requested = move(
            make_idea(world['organization'], world['author']), Idea.Status.CHANGES_REQUESTED
        )

        assert lifecycle.available_transitions(world['author'], draft) == [Idea.Status.SUBMITTED]
        assert lifecycle.available_transitions(world['author'], changes_requested) == [
            Idea.Status.SUBMITTED
        ]

    def test_a_reviewer_is_offered_the_review_moves_and_nothing_else(self, world):
        submitted = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)
        under_review = move(
            make_idea(world['organization'], world['author']), Idea.Status.UNDER_REVIEW
        )

        assert lifecycle.available_transitions(world['reviewer'], submitted) == [
            Idea.Status.UNDER_REVIEW
        ]
        # In the model's own enum order, which is what `TARGET_STATUSES` walks -
        # deterministic, and the same order the GraphQL enum is declared in.
        assert lifecycle.available_transitions(world['reviewer'], under_review) == [
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.REJECTED,
            Idea.Status.APPROVED,
        ]

    def test_the_author_is_never_offered_a_review_move(self, world):
        """
        The self-review rule, visible from the client's side: an author holding
        the Owner role is offered nothing on their own submitted idea.
        """
        submitted = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)

        assert lifecycle.available_transitions(world['author'], submitted) == []

    def test_an_ordinary_member_is_offered_nothing(self, world):
        submitted = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)

        assert lifecycle.available_transitions(world['member'], submitted) == []

    def test_a_terminal_idea_offers_nothing(self, world):
        for status in (Idea.Status.REJECTED, Idea.Status.AUTOMATION_PROPOSAL):
            idea = move(make_idea(world['organization'], world['author']), status)
            for actor in (world['author'], world['reviewer']):
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

        assert lifecycle.is_reviewer(stranger, idea) is False
        assert lifecycle.can_transition(stranger, idea, Idea.Status.UNDER_REVIEW) is False
        assert lifecycle.available_transitions(stranger, idea) == []

    def test_is_reviewer_agrees_with_the_transition_rule(self, world):
        """
        The standalone predicate and the matrix must not tell different stories
        about the same person, or a caller using one would offer actions the
        other refuses.
        """
        submitted = move(make_idea(world['organization'], world['author']), Idea.Status.SUBMITTED)
        under_review = move(
            make_idea(world['organization'], world['author']), Idea.Status.UNDER_REVIEW
        )

        for actor in (world['author'], world['reviewer'], world['member'], None):
            expected = lifecycle.is_reviewer(actor, submitted)
            if expected:
                assert lifecycle.can_transition(actor, submitted, Idea.Status.UNDER_REVIEW)
                assert lifecycle.can_transition(actor, under_review, Idea.Status.APPROVED)
            else:
                assert not lifecycle.can_transition(actor, submitted, Idea.Status.UNDER_REVIEW)
