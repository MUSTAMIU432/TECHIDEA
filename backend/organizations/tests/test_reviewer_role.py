"""
The `idea.review` permission and the Reviewer role (S3-002).

Two questions, answered for new organizations (bootstrap) and for
organizations that existed before S3-002 (migration 0003):

1. Does the Owner hold `idea.review`, so nobody who could review before the
   reviewer gate switched from "holds a system role" to the permission lost
   the ability?
2. Is there a grantable, non-system Reviewer role carrying exactly
   `organization.view` and `idea.review`, and is it an ordinary role, assigned
   and removed through the existing services and scoped to one organization?
"""

from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection

from identity.models import User
from organizations import authorization, services
from organizations.authorization import IDEA_REVIEW, ORGANIZATION_VIEW
from organizations.models import Membership, MembershipRole, Organization, Permission, Role

VALID_PASSWORD = 'a-strong-unique-pass-1'

migration_0003 = import_module(
    'organizations.migrations.0003_idea_review_permission_and_reviewer_role'
)


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password=VALID_PASSWORD,
    )


def make_organization(name, owner):
    result = services.create_organization_for_user(
        owner, services.CreateOrganizationInput(name=name)
    )
    return result.organization, result.membership


def add_member(organization, user, status=Membership.Status.ACTIVE):
    return Membership.objects.create(user=user, organization=organization, status=status)


def role_codes(role):
    return set(role.role_permissions.values_list('permission__code', flat=True))


@pytest.fixture
def acme():
    owner = make_user('owner@acme.example')
    organization, owner_membership = make_organization('Acme', owner)
    return {'organization': organization, 'owner': owner, 'owner_membership': owner_membership}


@pytest.mark.django_db
class TestPermission:
    def test_idea_review_is_a_defined_permission(self):
        assert IDEA_REVIEW == 'idea.review'
        assert IDEA_REVIEW in {code for code, _, _ in services.PERMISSION_DEFINITIONS}

    def test_bootstrap_provisions_the_permission_row(self, acme):
        assert Permission.objects.filter(code=IDEA_REVIEW).exists()


@pytest.mark.django_db
class TestBootstrap:
    def test_the_owner_role_holds_idea_review(self, acme):
        owner_role = Role.objects.get(organization=acme['organization'], slug='owner')

        assert IDEA_REVIEW in role_codes(owner_role)
        assert authorization.has_permission(acme['owner'], acme['organization'], IDEA_REVIEW)

    def test_a_non_system_reviewer_role_is_provisioned_with_exactly_its_permissions(self, acme):
        reviewer_role = Role.objects.get(
            organization=acme['organization'], slug=services.REVIEWER_ROLE_SLUG
        )

        assert reviewer_role.is_system is False
        assert role_codes(reviewer_role) == {ORGANIZATION_VIEW, IDEA_REVIEW}

    def test_the_reviewer_role_is_not_assigned_to_anyone_by_bootstrap(self, acme):
        reviewer_role = Role.objects.get(
            organization=acme['organization'], slug=services.REVIEWER_ROLE_SLUG
        )

        assert not MembershipRole.objects.filter(role=reviewer_role).exists()

    def test_the_owner_is_still_the_only_system_role(self, acme):
        assert list(
            Role.objects.filter(organization=acme['organization'], is_system=True).values_list(
                'slug', flat=True
            )
        ) == ['owner']


@pytest.mark.django_db
class TestGrantingReviewing:
    def test_a_plain_member_does_not_hold_idea_review(self, acme):
        member = make_user('member@acme.example')
        add_member(acme['organization'], member)

        assert not authorization.has_permission(member, acme['organization'], IDEA_REVIEW)

    def test_an_authenticated_non_member_does_not_hold_idea_review(self, acme):
        outsider = make_user('outsider@example.com')

        assert not authorization.has_permission(outsider, acme['organization'], IDEA_REVIEW)

    def test_the_owner_grants_and_removes_the_reviewer_role_through_the_existing_services(
        self, acme
    ):
        member = make_user('member@acme.example')
        membership = add_member(acme['organization'], member)
        reviewer_role = Role.objects.get(
            organization=acme['organization'], slug=services.REVIEWER_ROLE_SLUG
        )

        services.assign_role_to_membership(acme['owner'], membership.pk, reviewer_role.pk)
        assert authorization.has_permission(member, acme['organization'], IDEA_REVIEW)

        # Not a system role, so the last-holder rule does not apply to it.
        services.remove_role_from_membership(acme['owner'], membership.pk, reviewer_role.pk)
        assert not authorization.has_permission(member, acme['organization'], IDEA_REVIEW)

    def test_a_member_cannot_grant_themselves_the_reviewer_role(self, acme):
        member = make_user('member@acme.example')
        membership = add_member(acme['organization'], member)
        reviewer_role = Role.objects.get(
            organization=acme['organization'], slug=services.REVIEWER_ROLE_SLUG
        )

        with pytest.raises(services.OrganizationError):
            services.assign_role_to_membership(member, membership.pk, reviewer_role.pk)
        assert not authorization.has_permission(member, acme['organization'], IDEA_REVIEW)

    def test_the_reviewer_role_is_listed_among_the_organization_roles(self, acme):
        slugs = {
            role.slug
            for role in services.list_roles_for_user(acme['owner'], acme['organization'].pk)
        }

        assert services.REVIEWER_ROLE_SLUG in slugs

    def test_an_inactive_membership_holding_the_role_does_not_hold_the_permission(self, acme):
        member = make_user('member@acme.example')
        membership = add_member(acme['organization'], member)
        reviewer_role = Role.objects.get(
            organization=acme['organization'], slug=services.REVIEWER_ROLE_SLUG
        )
        MembershipRole.objects.create(membership=membership, role=reviewer_role)
        Membership.objects.filter(pk=membership.pk).update(status=Membership.Status.INACTIVE)

        assert not authorization.has_permission(member, acme['organization'], IDEA_REVIEW)


@pytest.mark.django_db
class TestTenantScoping:
    def test_a_reviewer_in_one_organization_holds_nothing_in_another(self, acme):
        globex_owner = make_user('owner@globex.example')
        globex, _ = make_organization('Globex', globex_owner)
        reviewer = make_user('reviewer@acme.example')
        membership = add_member(acme['organization'], reviewer)
        MembershipRole.objects.create(
            membership=membership,
            role=Role.objects.get(
                organization=acme['organization'], slug=services.REVIEWER_ROLE_SLUG
            ),
        )

        assert authorization.has_permission(reviewer, acme['organization'], IDEA_REVIEW)
        assert not authorization.has_permission(reviewer, globex, IDEA_REVIEW)
        # And the other way: Globex's owner is a reviewer in Globex only.
        assert not authorization.has_permission(globex_owner, acme['organization'], IDEA_REVIEW)

    def test_another_organizations_reviewer_role_cannot_be_assigned_here(self, acme):
        globex_owner = make_user('owner@globex.example')
        globex, _ = make_organization('Globex', globex_owner)
        member = make_user('member@acme.example')
        membership = add_member(acme['organization'], member)
        foreign_role = Role.objects.get(organization=globex, slug=services.REVIEWER_ROLE_SLUG)

        with pytest.raises(services.OrganizationError):
            services.assign_role_to_membership(acme['owner'], membership.pk, foreign_role.pk)


def _run_forward():
    with connection.schema_editor() as schema_editor:
        migration_0003.grant_idea_review(apps, schema_editor)


def _run_reverse():
    with connection.schema_editor() as schema_editor:
        migration_0003.revoke_idea_review(apps, schema_editor)


def _pre_s3_organization(slug):
    """
    An organization as it looked before S3-002: an Owner role holding the five
    organization permissions but not `idea.review`, and no Reviewer role.
    """
    owner = make_user(f'owner@{slug}.example')
    organization = Organization.objects.create(name=slug.title(), slug=slug)
    membership = add_member(organization, owner)
    owner_role = Role.objects.create(
        organization=organization, name='Owner', slug='owner', is_system=True
    )
    for permission in Permission.objects.exclude(code=IDEA_REVIEW):
        owner_role.role_permissions.create(permission=permission)
    MembershipRole.objects.create(membership=membership, role=owner_role)
    return organization, owner, owner_role


@pytest.mark.django_db
class TestMigrationBackfill:
    def test_existing_owners_gain_idea_review_and_organizations_gain_a_reviewer_role(self):
        # The test database is fully migrated, so the permission row exists;
        # the backfill must also work when it has to create it.
        Permission.objects.filter(code=IDEA_REVIEW).delete()
        organization, owner, owner_role = _pre_s3_organization('legacy')
        assert not authorization.has_permission(owner, organization, IDEA_REVIEW)

        _run_forward()

        assert IDEA_REVIEW in role_codes(owner_role)
        assert authorization.has_permission(owner, organization, IDEA_REVIEW)
        reviewer_role = Role.objects.get(organization=organization, slug='reviewer')
        assert reviewer_role.is_system is False
        assert role_codes(reviewer_role) == {ORGANIZATION_VIEW, IDEA_REVIEW}

    def test_the_backfill_is_idempotent(self):
        organization, _, owner_role = _pre_s3_organization('legacy')

        _run_forward()
        _run_forward()

        assert Role.objects.filter(organization=organization, slug='reviewer').count() == 1
        assert owner_role.role_permissions.filter(permission__code=IDEA_REVIEW).count() == 1

    def test_a_pre_existing_reviewer_slug_role_is_left_exactly_as_it_was(self):
        organization, _, _ = _pre_s3_organization('legacy')
        custom = Role.objects.create(
            organization=organization, name='Proof-reader', slug='reviewer', is_system=False
        )

        _run_forward()

        assert role_codes(custom) == set()
        assert Role.objects.filter(organization=organization, slug='reviewer').count() == 1

    def test_only_owner_roles_gain_the_permission(self):
        organization, _, _ = _pre_s3_organization('legacy')
        contributor = Role.objects.create(
            organization=organization, name='Contributor', slug='contributor', is_system=False
        )

        _run_forward()

        assert IDEA_REVIEW not in role_codes(contributor)

    def test_the_reverse_removes_what_the_forward_added_and_nothing_else(self):
        organization, owner, owner_role = _pre_s3_organization('legacy')
        other, _, _ = _pre_s3_organization('custom')
        custom = Role.objects.create(
            organization=other, name='Proof-reader', slug='reviewer', is_system=False
        )
        _run_forward()

        _run_reverse()

        assert not Permission.objects.filter(code=IDEA_REVIEW).exists()
        assert not Role.objects.filter(organization=organization, slug='reviewer').exists()
        assert Role.objects.filter(pk=custom.pk).exists()
        assert Role.objects.filter(pk=owner_role.pk).exists()
        assert not authorization.has_permission(owner, organization, IDEA_REVIEW)
