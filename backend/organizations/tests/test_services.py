from unittest.mock import patch

import pytest
from django.db import IntegrityError

from identity.models import User
from organizations import services
from organizations.models import (
    Membership,
    MembershipRole,
    Organization,
    Permission,
    Role,
    RolePermission,
)


def _make_user(email='ada@example.com', **overrides):
    fields = {
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
        'password': 'a-strong-unique-pass-1',
    }
    fields.update(overrides)
    return User.objects.create_user(email=email, **fields)


def _grant_role(user, organization, slug, *permission_codes):
    membership = Membership.objects.get(user=user, organization=organization)
    role = Role.objects.create(organization=organization, name=slug.title(), slug=slug)
    for code in permission_codes:
        RolePermission.objects.create(role=role, permission=Permission.objects.get(code=code))
    MembershipRole.objects.create(membership=membership, role=role)
    return role


@pytest.mark.django_db
class TestCreateOrganizationForUser:
    def test_creates_organization_and_active_creator_membership(self):
        user = _make_user()

        result = services.create_organization_for_user(
            user, services.CreateOrganizationInput(name='Acme Labs')
        )

        assert result.organization.name == 'Acme Labs'
        assert result.organization.slug == 'acme-labs'
        assert result.membership.user_id == user.pk
        assert result.membership.status == Membership.Status.ACTIVE
        assert Organization.objects.count() == 1
        assert Membership.objects.count() == 1

    def test_normalizes_custom_slug(self):
        user = _make_user()

        result = services.create_organization_for_user(
            user,
            services.CreateOrganizationInput(name='Acme Labs', slug='  ACME / Labs  '),
        )

        assert result.organization.slug == 'acme-labs'

    def test_blank_custom_slug_generates_from_name(self):
        user = _make_user()

        result = services.create_organization_for_user(
            user,
            services.CreateOrganizationInput(name='Acme Labs', slug='   '),
        )

        assert result.organization.slug == 'acme-labs'

    def test_rejects_duplicate_slug(self):
        user = _make_user()
        services.create_organization_for_user(
            user, services.CreateOrganizationInput(name='Acme Labs', slug='acme-labs')
        )

        with pytest.raises(services.OrganizationError) as exc_info:
            services.create_organization_for_user(
                _make_user('grace@example.com'),
                services.CreateOrganizationInput(name='Other Labs', slug='acme-labs'),
            )

        assert exc_info.value.field == 'slug'
        assert Organization.objects.count() == 1

    def test_rejects_missing_name(self):
        with pytest.raises(services.OrganizationError) as exc_info:
            services.create_organization_for_user(
                _make_user(), services.CreateOrganizationInput(name='  ')
            )

        assert exc_info.value.field == 'name'

    def test_rejects_overlong_name(self):
        with pytest.raises(services.OrganizationError) as exc_info:
            services.create_organization_for_user(
                _make_user(), services.CreateOrganizationInput(name='a' * 201)
            )

        assert exc_info.value.field == 'name'

    def test_rejects_overlong_slug(self):
        with pytest.raises(services.OrganizationError) as exc_info:
            services.create_organization_for_user(
                _make_user(),
                services.CreateOrganizationInput(name='Acme Labs', slug='a' * 101),
            )

        assert exc_info.value.field == 'slug'

    def test_rejects_inactive_user(self):
        user = _make_user()
        user.is_active = False
        user.save(update_fields=['is_active'])

        with pytest.raises(services.OrganizationError):
            services.create_organization_for_user(
                user, services.CreateOrganizationInput(name='Acme Labs')
            )

    def test_membership_failure_rolls_back_organization(self):
        user = _make_user()

        with (
            patch.object(
                services.Membership.objects,
                'create',
                side_effect=IntegrityError('membership failed'),
            ),
            pytest.raises(services.OrganizationError) as exc_info,
        ):
            services.create_organization_for_user(
                user, services.CreateOrganizationInput(name='Acme Labs')
            )

        assert exc_info.value.field is None
        assert 'membership' in exc_info.value.message.lower()
        assert Organization.objects.count() == 0
        assert Membership.objects.count() == 0

    def test_duplicate_slug_race_is_converted_to_domain_error(self):
        user = _make_user()

        with (
            patch.object(
                services.Organization.objects,
                'create',
                side_effect=IntegrityError('duplicate slug'),
            ),
            pytest.raises(services.OrganizationError) as exc_info,
        ):
            services.create_organization_for_user(
                user, services.CreateOrganizationInput(name='Acme Labs')
            )

        assert exc_info.value.field == 'slug'

    def test_creator_receives_owner_role_with_all_foundation_permissions(self):
        user = _make_user()

        result = services.create_organization_for_user(
            user, services.CreateOrganizationInput(name='Acme Labs')
        )

        owner_role = Role.objects.get(
            organization=result.organization,
            slug=services.DEFAULT_OWNER_ROLE_SLUG,
        )
        assert owner_role.is_system is True
        assert MembershipRole.objects.get(
            membership=result.membership,
            role=owner_role,
        )
        assert {
            role_permission.permission.code
            for role_permission in RolePermission.objects.filter(role=owner_role)
        } == {code for code, _, _ in services.PERMISSION_DEFINITIONS}
        assert Permission.objects.count() == len(services.PERMISSION_DEFINITIONS)

    def test_role_provisioning_failure_rolls_back_organization(self):
        user = _make_user()

        with (
            patch.object(
                services.Role.objects,
                'create',
                side_effect=IntegrityError('role failed'),
            ),
            pytest.raises(services.OrganizationError),
        ):
            services.create_organization_for_user(
                user, services.CreateOrganizationInput(name='Acme Labs')
            )

        assert Organization.objects.count() == 0
        assert Membership.objects.count() == 0
        assert Role.objects.count() == 0
        assert RolePermission.objects.count() == 0

    def test_membership_role_failure_rolls_back_owner_provisioning(self):
        user = _make_user()

        with (
            patch.object(
                services.MembershipRole.objects,
                'create',
                side_effect=IntegrityError('assignment failed'),
            ),
            pytest.raises(services.OrganizationError),
        ):
            services.create_organization_for_user(
                user, services.CreateOrganizationInput(name='Acme Labs')
            )

        assert Organization.objects.count() == 0
        assert Membership.objects.count() == 0
        assert Role.objects.count() == 0
        assert RolePermission.objects.count() == 0


@pytest.mark.django_db
class TestOrganizationAccessServices:
    def test_lists_only_active_memberships_for_user(self):
        user = _make_user()
        active = Organization.objects.create(name='Active', slug='active')
        inactive = Organization.objects.create(name='Inactive', slug='inactive')
        Membership.objects.create(user=user, organization=active)
        Membership.objects.create(
            user=user,
            organization=inactive,
            status=Membership.Status.INACTIVE,
        )

        result = services.list_organizations_for_user(user)

        assert [item.organization.pk for item in result] == [active.pk]

    def test_get_organization_requires_active_membership(self):
        user = _make_user()
        other_user = _make_user('grace@example.com')
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')
        Membership.objects.create(user=other_user, organization=organization)
        _grant_role(other_user, organization, 'viewer', 'organization.view')

        assert services.get_organization_for_user(user, organization.pk) is None
        assert services.get_organization_for_user(other_user, organization.pk) == organization

    def test_member_listing_hides_organizations_from_non_members(self):
        user = _make_user()
        other_user = _make_user('grace@example.com')
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')
        membership = Membership.objects.create(user=other_user, organization=organization)
        _grant_role(other_user, organization, 'member-viewer', 'organization.members.view')

        assert services.list_memberships_for_organization(user, organization.pk) == []
        assert services.list_memberships_for_organization(other_user, organization.pk) == [
            membership
        ]

    def test_member_listing_rejects_inactive_requester_membership(self):
        requester = _make_user()
        member = _make_user('grace@example.com')
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')
        Membership.objects.create(
            user=requester,
            organization=organization,
            status=Membership.Status.INACTIVE,
        )
        Membership.objects.create(user=member, organization=organization)

        assert services.list_memberships_for_organization(requester, organization.pk) == []

    def test_invalid_organization_id_is_not_accessible(self):
        user = _make_user()

        assert services.get_organization_for_user(user, 'not-an-id') is None
        assert services.list_memberships_for_organization(user, 'not-an-id') == []


@pytest.mark.django_db
class TestRoleServices:
    def test_lists_roles_only_for_active_memberships(self):
        user = _make_user()
        other_user = _make_user('grace@example.com')
        first = services.create_organization_for_user(
            user, services.CreateOrganizationInput(name='First')
        )
        second = services.create_organization_for_user(
            user, services.CreateOrganizationInput(name='Second')
        )
        services.create_organization_for_user(
            other_user, services.CreateOrganizationInput(name='Other')
        )

        roles = services.list_roles_for_user(user)

        assert {role.organization_id for role in roles} == {
            first.organization.pk,
            second.organization.pk,
        }
        second.membership.status = Membership.Status.INACTIVE
        second.membership.save(update_fields=['status'])
        assert services.list_roles_for_user(user) == [
            role for role in roles if role.organization_id == first.organization.pk
        ]

    def test_get_role_and_permissions_are_membership_scoped(self):
        owner = _make_user()
        other_user = _make_user('grace@example.com')
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        owner_role = Role.objects.get(organization=created.organization, slug='owner')

        assert services.get_role_for_user(owner, owner_role.pk) == owner_role
        assert services.get_role_for_user(other_user, owner_role.pk) is None
        assert services.list_permissions_for_role(other_user, owner_role.pk) == []
        assert {
            permission.code
            for permission in services.list_permissions_for_role(owner, owner_role.pk)
        } == {code for code, _, _ in services.PERMISSION_DEFINITIONS}

    def test_assigns_role_to_same_organization_membership(self):
        owner = _make_user()
        member = _make_user('grace@example.com')
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        membership = Membership.objects.create(user=member, organization=created.organization)
        role = Role.objects.create(
            organization=created.organization,
            name='Admin',
            slug='admin',
        )

        assignment = services.assign_role_to_membership(owner, membership.pk, role.pk)

        assert assignment.membership == membership
        assert assignment.role == role

    def test_rejects_cross_organization_role_assignment(self):
        owner = _make_user()
        other_owner = _make_user('grace@example.com')
        first = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='First')
        )
        second = services.create_organization_for_user(
            other_owner, services.CreateOrganizationInput(name='Second')
        )
        membership = second.membership
        role = Role.objects.get(organization=first.organization, slug='owner')

        with pytest.raises(services.OrganizationError, match='Membership or role not found'):
            services.assign_role_to_membership(other_owner, membership.pk, role.pk)

    def test_rejects_duplicate_role_assignment(self):
        owner = _make_user()
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        owner_role = Role.objects.get(organization=created.organization, slug='owner')

        with pytest.raises(services.OrganizationError, match='already has this role'):
            services.assign_role_to_membership(owner, created.membership.pk, owner_role.pk)

    def test_rejects_inactive_user_role_mutation(self):
        owner = _make_user()
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        owner.is_active = False
        owner.save(update_fields=['is_active'])
        owner_role = Role.objects.get(organization=created.organization, slug='owner')

        with pytest.raises(services.OrganizationError):
            services.assign_role_to_membership(owner, created.membership.pk, owner_role.pk)

    def test_removes_role_from_membership(self):
        owner = _make_user()
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        admin_role = Role.objects.create(
            organization=created.organization,
            name='Admin',
            slug='admin',
        )
        MembershipRole.objects.create(membership=created.membership, role=admin_role)

        services.remove_role_from_membership(owner, created.membership.pk, admin_role.pk)

        assert not MembershipRole.objects.filter(
            membership=created.membership,
            role=admin_role,
        ).exists()
        with pytest.raises(services.OrganizationError, match='does not have this role'):
            services.remove_role_from_membership(owner, created.membership.pk, admin_role.pk)

    def test_cannot_remove_system_role_from_last_active_holder(self):
        owner = _make_user()
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        owner_role = Role.objects.get(organization=created.organization, slug='owner')

        with pytest.raises(services.OrganizationError, match='last active holder'):
            services.remove_role_from_membership(owner, created.membership.pk, owner_role.pk)
