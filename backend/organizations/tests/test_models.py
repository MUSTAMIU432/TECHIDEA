import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from identity.models import User
from organizations.models import (
    Membership,
    MembershipRole,
    Organization,
    Permission,
    Role,
    RolePermission,
)


def _make_user(email='ada@example.com'):
    return User.objects.create_user(
        email=email,
        first_name='Ada',
        last_name='Lovelace',
        phone_number='+255712345678',
        password='a-strong-unique-pass-1',
    )


@pytest.mark.django_db
class TestOrganizationModel:
    def test_organization_can_be_created_with_stable_fields(self):
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')

        assert organization.pk is not None
        assert organization.name == 'Acme Labs'
        assert organization.slug == 'acme-labs'
        assert organization.created_at is not None
        assert organization.updated_at is not None

    def test_slug_is_unique(self):
        Organization.objects.create(name='Acme Labs', slug='acme-labs')

        with pytest.raises(IntegrityError):
            Organization.objects.create(name='Another Lab', slug='acme-labs')

    def test_str_returns_name(self):
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')

        assert str(organization) == 'Acme Labs'


@pytest.mark.django_db
class TestMembershipModel:
    def test_membership_links_user_and_organization(self):
        user = _make_user()
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')

        membership = Membership.objects.create(user=user, organization=organization)

        assert membership.user_id == user.pk
        assert membership.organization_id == organization.pk
        assert membership.status == Membership.Status.ACTIVE
        assert list(user.memberships.all()) == [membership]
        assert list(organization.memberships.all()) == [membership]

    def test_user_and_organization_pair_is_unique_regardless_of_status(self):
        user = _make_user()
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')
        Membership.objects.create(user=user, organization=organization)

        with pytest.raises(IntegrityError):
            Membership.objects.create(
                user=user,
                organization=organization,
                status=Membership.Status.INACTIVE,
            )

    def test_deleting_organization_deletes_memberships(self):
        user = _make_user()
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')
        membership = Membership.objects.create(user=user, organization=organization)

        organization.delete()

        assert not Membership.objects.filter(pk=membership.pk).exists()

    def test_deleting_user_deletes_memberships(self):
        user = _make_user()
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')
        membership = Membership.objects.create(user=user, organization=organization)

        user.delete()

        assert not Membership.objects.filter(pk=membership.pk).exists()


@pytest.mark.django_db
class TestAuthorizationModel:
    def test_permission_code_is_normalized_and_validated(self):
        permission = Permission.objects.create(
            code=' Test.Normalize ',
            name='Test normalize',
            description='Test normalization.',
        )

        assert permission.code == 'test.normalize'

        with pytest.raises(ValidationError):
            Permission(code='not a code', name='Invalid').full_clean()

    def test_permission_code_is_globally_unique(self):
        Permission.objects.create(code='test.view', name='Test view')

        with pytest.raises((IntegrityError, ValidationError)):
            Permission.objects.create(code='test.view', name='Another view')

    def test_role_slug_is_unique_within_organization_but_reusable_across_organizations(self):
        first = Organization.objects.create(name='First', slug='first')
        second = Organization.objects.create(name='Second', slug='second')
        Role.objects.create(organization=first, name='Admin', slug='admin')
        Role.objects.create(organization=second, name='Admin', slug='admin')

        with pytest.raises(IntegrityError):
            Role.objects.create(organization=first, name='Another admin', slug='admin')

    def test_role_permission_pair_is_unique(self):
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')
        role = Role.objects.create(organization=organization, name='Admin', slug='admin')
        permission = Permission.objects.create(code='test.role.view', name='Test role view')
        RolePermission.objects.create(role=role, permission=permission)

        with pytest.raises(IntegrityError):
            RolePermission.objects.create(role=role, permission=permission)

    def test_membership_role_pair_is_unique(self):
        user = _make_user()
        organization = Organization.objects.create(name='Acme Labs', slug='acme-labs')
        membership = Membership.objects.create(user=user, organization=organization)
        role = Role.objects.create(organization=organization, name='Admin', slug='admin')
        MembershipRole.objects.create(membership=membership, role=role)

        with pytest.raises((IntegrityError, ValidationError)):
            MembershipRole.objects.create(membership=membership, role=role)

    def test_membership_role_rejects_cross_organization_assignment(self):
        user = _make_user()
        first = Organization.objects.create(name='First', slug='first')
        second = Organization.objects.create(name='Second', slug='second')
        membership = Membership.objects.create(user=user, organization=first)
        role = Role.objects.create(organization=second, name='Admin', slug='admin')

        with pytest.raises(ValidationError):
            MembershipRole(membership=membership, role=role).save()
