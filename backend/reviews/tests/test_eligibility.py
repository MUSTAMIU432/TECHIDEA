"""
Reviewer eligibility (S3-002): `reviews.eligibility`.

The rule under test is "an active user, with an active membership of the
idea's own organization, holding `idea.review` there, who can read the idea
and did not write it". Each clause is broken once, on its own, against a world
in which every other clause holds, so a test failing names the clause that
broke. The positive cases use both ways of holding the permission (the
bootstrap Owner role and the Reviewer role) so a gate that only recognised
one of them would fail here.

The cross-organization cases are the ones that matter most. A reviewer in
Globex can *read* Acme's `PUBLIC` idea, which is exactly the situation in
which a gate built on "can read" plus "holds the permission somewhere" would
wave them through.
"""

import pytest
from django.utils import timezone

from ideas import lifecycle, services
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews.eligibility import can_review, can_start_review

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
    return membership


def make_idea(organization, author, *, status=Idea.Status.SUBMITTED, visibility=None):
    return Idea.objects.create(
        organization=organization,
        author=author,
        title='An idea',
        description=DESCRIPTION,
        category=Category.objects.create(name=f'Cat {Category.objects.count() + 1}'),
        visibility=visibility or Idea.Visibility.ORGANIZATION,
        status=status,
        submitted_at=None if status == Idea.Status.DRAFT else timezone.now(),
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
        assert can_review(world['reviewer'], world['idea'])

    def test_the_owner_is_eligible_through_the_owner_role(self, world):
        assert can_review(world['acme_owner'], world['idea'])

    def test_a_public_idea_is_reviewable_by_its_own_organizations_reviewer(self, world):
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        assert can_review(world['reviewer'], idea)


@pytest.mark.django_db
class TestNotEligible:
    def test_no_user(self, world):
        assert not can_review(None, world['idea'])

    def test_no_idea(self, world):
        assert not can_review(world['reviewer'], None)

    def test_a_deactivated_account(self, world):
        User.objects.filter(pk=world['reviewer'].pk).update(is_active=False)
        world['reviewer'].refresh_from_db()

        assert not can_review(world['reviewer'], world['idea'])

    def test_a_member_without_the_permission(self, world):
        assert not can_review(world['member'], world['idea'])

    def test_a_member_whose_only_role_carries_no_permissions(self, world):
        contributor = Role.objects.create(
            organization=world['acme'], name='Contributor', slug='contributor'
        )
        MembershipRole.objects.create(
            membership=Membership.objects.get(user=world['member'], organization=world['acme']),
            role=contributor,
        )

        assert not can_review(world['member'], world['idea'])

    def test_an_inactive_membership_that_holds_the_reviewer_role(self, world):
        inactive = make_user('inactive@acme.example')
        add_member(world['acme'], inactive, reviewer=True, status=Membership.Status.INACTIVE)

        assert not can_review(inactive, world['idea'])

    def test_a_former_reviewer_whose_membership_was_deactivated(self, world):
        Membership.objects.filter(user=world['reviewer'], organization=world['acme']).update(
            status=Membership.Status.INACTIVE
        )

        assert not can_review(world['reviewer'], world['idea'])

    def test_a_reviewer_whose_role_was_removed(self, world):
        MembershipRole.objects.filter(
            membership__user=world['reviewer'], role__slug=REVIEWER_ROLE_SLUG
        ).delete()

        assert not can_review(world['reviewer'], world['idea'])

    def test_an_authenticated_user_outside_the_organization(self, world):
        assert not can_review(make_user('outsider@example.com'), world['idea'])

    def test_the_author_even_when_holding_the_reviewer_role(self, world):
        MembershipRole.objects.create(
            membership=Membership.objects.get(user=world['author'], organization=world['acme']),
            role=Role.objects.get(organization=world['acme'], slug=REVIEWER_ROLE_SLUG),
        )

        assert not can_review(world['author'], world['idea'])

    def test_the_owner_on_their_own_idea(self, world):
        own = make_idea(world['acme'], world['acme_owner'])

        assert not can_review(world['acme_owner'], own)

    @pytest.mark.parametrize('visibility', [Idea.Visibility.PRIVATE, Idea.Visibility.DEPARTMENT])
    def test_an_idea_the_reviewer_cannot_read(self, world, visibility):
        idea = make_idea(world['acme'], world['author'], visibility=visibility)

        assert not can_review(world['reviewer'], idea)
        assert not can_review(world['acme_owner'], idea)


@pytest.mark.django_db
class TestTenantIsolation:
    def test_another_organizations_reviewer(self, world):
        assert not can_review(world['globex_reviewer'], world['idea'])

    def test_another_organizations_owner(self, world):
        assert not can_review(world['globex_owner'], world['idea'])

    def test_another_organizations_reviewer_on_a_public_idea_they_can_read(self, world):
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        assert not can_review(world['globex_reviewer'], idea)
        assert not can_review(world['globex_owner'], idea)

    def test_it_holds_in_both_directions(self, world):
        globex_idea = make_idea(
            world['globex'], world['globex_owner'], visibility=Idea.Visibility.PUBLIC
        )

        assert can_review(world['globex_reviewer'], globex_idea)
        assert not can_review(world['reviewer'], globex_idea)
        assert not can_review(world['acme_owner'], globex_idea)

    def test_a_member_of_both_organizations_is_a_reviewer_only_where_they_hold_the_role(
        self, world
    ):
        add_member(world['globex'], world['reviewer'])
        globex_idea = make_idea(world['globex'], world['globex_owner'])

        assert can_review(world['reviewer'], world['idea'])
        assert not can_review(world['reviewer'], globex_idea)


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

        assert can_review(world['reviewer'], idea)

    def test_can_start_review_only_on_a_submitted_idea(self, world):
        for status in Idea.Status.values:
            idea = make_idea(world['acme'], world['author'], status=status)

            assert can_start_review(world['reviewer'], idea) is (status == Idea.Status.SUBMITTED), (
                status
            )

    def test_can_start_review_requires_eligibility(self, world):
        assert not can_start_review(world['member'], world['idea'])
        assert not can_start_review(world['globex_reviewer'], world['idea'])
        assert not can_start_review(world['author'], world['idea'])
        assert not can_start_review(world['reviewer'], None)


@pytest.mark.django_db
class TestAgreesWithTheLifecycle:
    """
    Eligibility and the lifecycle's REVIEWER actor rule must never disagree:
    Reviews would otherwise offer a review the lifecycle refuses to record.
    These also prove the lifecycle gate itself moved to `idea.review` - the
    Reviewer role is not a system role, so under the old gate it could not
    have made these moves.
    """

    def test_a_reviewer_role_holder_can_make_the_reviewer_moves(self, world):
        idea = lifecycle.transition_idea(
            world['reviewer'], world['idea'].pk, Idea.Status.UNDER_REVIEW
        )
        assert idea.status == Idea.Status.UNDER_REVIEW

        idea = lifecycle.transition_idea(world['reviewer'], idea.pk, Idea.Status.APPROVED)
        assert idea.status == Idea.Status.APPROVED

    @pytest.mark.parametrize('who', ['member', 'author', 'globex_reviewer', 'globex_owner'])
    def test_the_ineligible_are_refused_by_the_lifecycle_too(self, world, who):
        assert not can_review(world[who], world['idea'])

        with pytest.raises(services.IdeaError):
            lifecycle.transition_idea(world[who], world['idea'].pk, Idea.Status.UNDER_REVIEW)

    @pytest.mark.parametrize('who', ['reviewer', 'acme_owner', 'member', 'author'])
    def test_available_transitions_match_eligibility(self, world, who):
        offered = Idea.Status.UNDER_REVIEW in lifecycle.available_transitions(
            world[who], world['idea']
        )

        assert offered is can_start_review(world[who], world['idea'])
