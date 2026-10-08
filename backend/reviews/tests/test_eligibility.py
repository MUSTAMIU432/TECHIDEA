"""
Reviewer eligibility: `reviews.eligibility`, and the two tracks it now answers
about separately.

Most of this file is the **organization** rule: "an active user, with an active
membership of the idea's own organization, holding `idea.review` there, who can
read the idea and did not write it". Each clause is broken once, on its own,
against a world in which every other clause holds, so a test failing names the
clause that broke. The positive cases use both ways of holding the permission
(the bootstrap Owner role and the Reviewer role) so a gate that only recognised
one of them would fail here.

The cross-organization cases are the ones that matter most. A reviewer in
Globex can *read* Acme's `PUBLIC` idea, which is exactly the situation in
which a gate built on "can read" plus "holds the permission somewhere" would
wave them through - and, after this phase, they would also be able to read the
submission itself if they held the platform permission.

`TestPlatformEligibility` at the end is the other half: platform review is
authorized by a platform-scoped permission and **nothing else**, so no
organization role - not even an organization Owner's - reaches it.
"""

import pytest

from ideas import lifecycle, services
from ideas.models import Category, Idea
from ideas.selectors import can_view_idea
from identity.models import User
from organizations import authorization
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews.eligibility import (
    can_organization_review,
    can_review,
    can_start_organization_review,
    can_start_review,
)
from reviews.tests.platform import (
    build_idea,
    grant_platform_reviewer,
    revoke_platform_reviewer,
)

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password=VALID_PASSWORD,
    )


def make_organization(name, owner):
    return create_organization_for_user(owner, CreateOrganizationInput(name=name)).organization


def add_member(organization, user, *, reviewer=False, status=Membership.Status.ACTIVE):
    membership = Membership.objects.create(user=user, organization=organization, status=status)
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


def make_idea(organization, author, *, status=Idea.Status.SUBMITTED, visibility=None):
    return build_idea(
        status=status,
        organization=organization,
        author=author,
        title='An idea',
        description=DESCRIPTION,
        category=Category.objects.create(name=f'Cat {Category.objects.count() + 1}'),
        visibility=visibility or Idea.Visibility.ORGANIZATION,
    )


@pytest.fixture
def world():
    acme_owner = make_user('owner@acme.example')
    acme = make_organization('Acme', acme_owner)
    author = make_user('author@acme.example')
    add_member(acme, author)
    reviewer = make_user('reviewer@acme.example')
    add_member(acme, reviewer, reviewer=True)
    member = make_user('member@acme.example')
    add_member(acme, member)

    globex_owner = make_user('owner@globex.example')
    globex = make_organization('Globex', globex_owner)
    globex_reviewer = make_user('reviewer@globex.example')
    add_member(globex, globex_reviewer, reviewer=True)

    return {
        'acme': acme,
        'acme_owner': acme_owner,
        'author': author,
        'reviewer': reviewer,
        'member': member,
        'globex': globex,
        'globex_owner': globex_owner,
        'globex_reviewer': globex_reviewer,
        'idea': make_idea(acme, author),
    }


@pytest.mark.django_db
class TestEligible:
    def test_a_member_holding_the_reviewer_role_is_eligible(self, world):
        assert can_organization_review(world['reviewer'], world['idea'])

    def test_the_owner_is_eligible_through_the_owner_role(self, world):
        assert can_organization_review(world['acme_owner'], world['idea'])

    def test_a_public_idea_is_reviewable_by_its_own_organizations_reviewer(self, world):
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        assert can_organization_review(world['reviewer'], idea)


@pytest.mark.django_db
class TestNotEligible:
    def test_no_user(self, world):
        assert not can_organization_review(None, world['idea'])

    def test_no_idea(self, world):
        assert not can_organization_review(world['reviewer'], None)

    def test_a_deactivated_account(self, world):
        User.objects.filter(pk=world['reviewer'].pk).update(is_active=False)
        world['reviewer'].refresh_from_db()

        assert not can_organization_review(world['reviewer'], world['idea'])

    def test_a_member_without_the_permission(self, world):
        assert not can_organization_review(world['member'], world['idea'])

    def test_a_member_whose_only_role_carries_no_permissions(self, world):
        contributor = Role.objects.create(
            organization=world['acme'], name='Contributor', slug='contributor'
        )
        MembershipRole.objects.create(
            membership=Membership.objects.get(user=world['member'], organization=world['acme']),
            role=contributor,
        )

        assert not can_organization_review(world['member'], world['idea'])

    def test_an_inactive_membership_that_holds_the_reviewer_role(self, world):
        inactive = make_user('inactive@acme.example')
        add_member(world['acme'], inactive, reviewer=True, status=Membership.Status.INACTIVE)

        assert not can_organization_review(inactive, world['idea'])

    def test_a_former_reviewer_whose_membership_was_deactivated(self, world):
        Membership.objects.filter(user=world['reviewer'], organization=world['acme']).update(
            status=Membership.Status.INACTIVE
        )

        assert not can_organization_review(world['reviewer'], world['idea'])

    def test_a_reviewer_whose_role_was_removed(self, world):
        MembershipRole.objects.filter(
            membership__user=world['reviewer'], role__slug=REVIEWER_ROLE_SLUG
        ).delete()

        assert not can_organization_review(world['reviewer'], world['idea'])

    def test_an_authenticated_user_outside_the_organization(self, world):
        assert not can_organization_review(make_user('outsider@example.com'), world['idea'])

    def test_the_author_even_when_holding_the_reviewer_role(self, world):
        MembershipRole.objects.create(
            membership=Membership.objects.get(user=world['author'], organization=world['acme']),
            role=Role.objects.get(organization=world['acme'], slug=REVIEWER_ROLE_SLUG),
        )

        assert not can_organization_review(world['author'], world['idea'])

    def test_the_owner_on_their_own_idea(self, world):
        own = make_idea(world['acme'], world['acme_owner'])

        assert not can_organization_review(world['acme_owner'], own)

    @pytest.mark.parametrize('visibility', [Idea.Visibility.PRIVATE, Idea.Visibility.DEPARTMENT])
    def test_an_idea_the_reviewer_cannot_read(self, world, visibility):
        """
        A `PRIVATE` or `DEPARTMENT` idea is author-only, so no reviewer - not even
        one holding `idea.review` in that very organization - may review it.

        `DEPARTMENT` is reserved vocabulary with no department tier behind it, so
        it fails closed to author-only rather than being treated as
        organization-wide. See `ideas.selectors`' module docstring.
        """
        # Built in `SUBMITTED_TO_ORGANIZATION` rather than submitted to it: a
        # private idea cannot be submitted, which is the same rule from the other
        # side. What matters here is only that the reviewer cannot *read* it, so
        # the row is written in a state where a reviewer could otherwise act.
        idea = make_idea(
            world['acme'],
            world['author'],
            status=Idea.Status.SUBMITTED_TO_ORGANIZATION,
            visibility=visibility,
        )

        assert not can_organization_review(world['reviewer'], idea)
        assert not can_organization_review(world['acme_owner'], idea)
        assert can_view_idea(world['author'], idea), 'the author can always read their own'


@pytest.mark.django_db
class TestTenantIsolation:
    def test_another_organizations_reviewer(self, world):
        assert not can_organization_review(world['globex_reviewer'], world['idea'])

    def test_another_organizations_owner(self, world):
        assert not can_organization_review(world['globex_owner'], world['idea'])

    def test_another_organizations_reviewer_on_a_public_idea_they_can_read(self, world):
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        assert not can_organization_review(world['globex_reviewer'], idea)
        assert not can_organization_review(world['globex_owner'], idea)

    def test_it_holds_in_both_directions(self, world):
        globex_idea = make_idea(
            world['globex'], world['globex_owner'], visibility=Idea.Visibility.PUBLIC
        )

        assert can_organization_review(world['globex_reviewer'], globex_idea)
        assert not can_organization_review(world['reviewer'], globex_idea)
        assert not can_organization_review(world['acme_owner'], globex_idea)

    def test_a_member_of_both_organizations_is_a_reviewer_only_where_they_hold_the_role(
        self, world
    ):
        add_member(world['globex'], world['reviewer'])
        globex_idea = make_idea(world['globex'], world['globex_owner'])

        assert can_organization_review(world['reviewer'], world['idea'])
        assert not can_organization_review(world['reviewer'], globex_idea)


@pytest.mark.django_db
class TestStatus:
    @pytest.mark.parametrize(
        'status',
        [
            Idea.Status.SUBMITTED,
            Idea.Status.UNDER_REVIEW,
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.APPROVED,
            Idea.Status.REJECTED,
        ],
    )
    def test_can_review_does_not_depend_on_status(self, world, status):
        idea = make_idea(world['acme'], world['author'], status=status)

        assert can_organization_review(world['reviewer'], idea)

    def test_can_start_organization_review_only_on_one_waiting_for_its_organization(self, world):
        """
        Exactly one state opens an organization review round.

        `SUBMITTED_TO_ORGANIZATION` is the queue an organization reviewer works;
        everything else is either somebody else's turn or already decided. The
        author re-submitting after a changes request is the *author's* move, not
        something a reviewer starts.
        """
        for status in Idea.Status.values:
            idea = make_idea(world['acme'], world['author'], status=status)

            assert can_start_organization_review(world['reviewer'], idea) is (
                status == Idea.Status.SUBMITTED_TO_ORGANIZATION
            ), status

    def test_can_start_review_requires_eligibility(self, world):
        assert not can_start_organization_review(world['member'], world['idea'])
        assert not can_start_organization_review(world['globex_reviewer'], world['idea'])
        assert not can_start_organization_review(world['author'], world['idea'])
        assert not can_start_organization_review(world['reviewer'], None)


@pytest.mark.django_db
class TestAgreesWithTheLifecycle:
    """
    Eligibility and the lifecycle's REVIEWER actor rule must never disagree:
    Reviews would otherwise offer a review the lifecycle refuses to record.
    These also prove the lifecycle gate itself moved to `idea.review` - the
    Reviewer role is not a system role, so under the old gate it could not
    have made these moves.
    """

    def test_a_reviewer_role_holder_can_start_a_review(self, world):
        # Through the review operation since S3-004; `transitionIdea` refuses it.
        from reviews.services import start_review

        start_review(world['reviewer'], world['idea'].pk)

        world['idea'].refresh_from_db()
        assert world['idea'].status == Idea.Status.UNDER_REVIEW

    @pytest.mark.parametrize('who', ['member', 'author', 'globex_reviewer', 'globex_owner'])
    def test_the_ineligible_are_refused_by_the_lifecycle_too(self, world, who):
        """
        Eligibility and the lifecycle must agree, or the UI could offer a review
        the server then refuses - or worse, the server could allow one the UI
        never showed.

        Checked on the **organization** decision, because that is the move an
        organization reviewer would make on this idea: `UNDER_REVIEW` belongs to
        the platform track, so asserting on it here would be asserting that an
        organization reviewer can claim a submission for the platform.
        """
        assert not can_organization_review(world[who], world['idea'])

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(
                world[who], world['idea'].pk, Idea.Status.ORGANIZATION_CONFIRMED
            )

    @pytest.mark.parametrize('who', ['reviewer', 'acme_owner', 'member', 'author'])
    def test_the_lifecycle_actor_rule_matches_eligibility(self, world, who):
        """
        `can_transition` is the actor rule alone; review-owned moves are not
        *offered* as transitions, but the rule must still agree with eligibility.

        Like for like: the organization track's decision against
        `can_start_organization_review`, on an idea the organization is holding.
        """
        organization_idea = make_idea(
            world['acme'], world['author'], status=Idea.Status.SUBMITTED_TO_ORGANIZATION
        )
        allowed = lifecycle.can_transition(
            world[who], organization_idea, Idea.Status.ORGANIZATION_CONFIRMED
        )

        assert allowed is can_start_organization_review(world[who], organization_idea)


# --- the platform track ----------------------------------------------------------------


@pytest.mark.django_db
class TestPlatformEligibility:
    """
    Platform review is authorized by a platform-scoped permission and nothing
    else.

    Every case here is the negative one, because the positive one is trivial: hold
    the permission and you are a reviewer. What matters is that **no organization
    or team arrangement produces that permission** - which is the whole of "an
    organization reviewer cannot platform-approve", stated as a property of the
    eligibility rule rather than as a check somebody wrote in a service.
    """

    def test_holding_the_permission_is_enough(self, world):
        reviewer = fresh(world['reviewer'])
        grant_platform_reviewer(reviewer)

        assert can_review(reviewer, world['idea'])
        assert can_start_review(reviewer, world['idea'])

    def test_an_organization_owner_is_not_a_platform_reviewer(self, world):
        # The bootstrap Owner holds every organization permission there is, and is
        # still not a platform reviewer.
        owner = fresh(world['acme_owner'])
        assert authorization.membership_has_permission(
            authorization.get_membership(owner, world['acme']),
            'idea.review',
        )

        assert not can_review(owner, world['idea'])
        assert not can_start_review(owner, world['idea'])

    def test_an_organization_reviewer_is_not_a_platform_reviewer(self, world):
        """
        The organization Reviewer role is not platform review.

        `add_member(..., reviewer=True)` grants both kinds of reviewer in this
        file's fixture, so the platform one is taken away again first - otherwise
        this test would be asserting nothing at all.
        """
        revoke_platform_reviewer(fresh(world['reviewer']))

        assert not can_review(fresh(world['reviewer']), world['idea'])

    def test_an_outsider_with_no_permissions_is_not_a_platform_reviewer(self, world):
        assert not can_review(make_user('outsider@example.com'), world['idea'])
        assert not can_review(None, world['idea'])

    def test_the_review_permission_needs_no_console(self, world):
        """
        Reviewing is the review permission alone; the console is for administrators.

        A platform reviewer works from the review workspace's platform queue, so
        `review_platform_submissions` is honoured by itself - and holding it opens
        no part of the administration console.
        """
        from administration.authorization import capabilities_for

        reviewer = fresh(world['reviewer'])
        revoke_platform_reviewer(reviewer)
        grant_platform_reviewer(fresh(reviewer), console=False)

        assert can_review(fresh(reviewer), world['idea'])
        assert capabilities_for(fresh(reviewer)).can_access_console is False

    def test_losing_the_permission_takes_the_ability_with_it(self, world):
        reviewer = fresh(world['reviewer'])
        grant_platform_reviewer(reviewer)
        assert can_review(reviewer, world['idea'])

        revoke_platform_reviewer(fresh(reviewer))

        assert not can_review(fresh(reviewer), world['idea'])

    def test_a_platform_reviewer_cannot_be_refused_for_tenant_membership(self, world):
        """
        Platform review is cross-tenant, and eligibility has to agree with that.

        Somebody who is not a member of the idea's organization, and holds nothing
        of theirs, is still eligible - because the platform reviews submissions
        from organizations its reviewers are not in. Without this the whole track
        would only work for reviewers who happened to be in the same tenant as the
        idea they had to decide.
        """
        reviewer = fresh(make_user('outsider@example.com'))
        grant_platform_reviewer(reviewer)

        assert not authorization.is_member_of(reviewer, world['acme'])
        assert can_review(reviewer, world['idea'])

    def test_the_author_never_reviews_their_own_idea_on_either_track(self, world):
        """
        Even the platform permission does not let an author review themselves.
        """
        author = fresh(world['author'])
        grant_platform_reviewer(author)

        assert not can_review(author, world['idea'])

        own = make_idea(world['acme'], author)
        assert not can_review(author, own)

    def test_can_start_review_only_on_a_submitted_idea(self, world):
        reviewer = fresh(world['reviewer'])
        grant_platform_reviewer(fresh(reviewer))

        for status in Idea.Status.values:
            idea = make_idea(world['acme'], world['author'], status=status)

            assert can_start_review(reviewer, idea) is (status == Idea.Status.SUBMITTED), status


def fresh(user):
    """
    `user` again from the database, so its permission cache is empty.

    Django caches a user's permissions on the instance for as long as that
    instance lives, so a test that grants or revokes a permission and then asks a
    question about the *same* Python object is asking about the answer it already
    computed. Every check in this class therefore asks about a freshly loaded
    user.
    """
    return User.objects.get(pk=user.pk)
