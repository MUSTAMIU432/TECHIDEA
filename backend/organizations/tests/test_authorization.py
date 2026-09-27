import pytest

from identity.models import User
from organizations import authorization, services
from organizations.models import Membership, MembershipRole, Role


def _make_user(email='ada@example.com'):
    return User.objects.create_user(
        email=email,
        first_name='Ada',
        last_name='Lovelace',
        phone_number='+255712345678',
        password='a-strong-unique-pass-1',
    )


def _member_without_permission(organization, user, slug='member'):
    membership = Membership.objects.create(user=user, organization=organization)
    role = Role.objects.create(organization=organization, name=slug.title(), slug=slug)
    MembershipRole.objects.create(membership=membership, role=role)
    return membership, role


@pytest.mark.django_db
class TestAuthorizationPrimitives:
    def test_active_owner_with_permission_is_allowed(self):
        owner = _make_user()
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )

        assert authorization.is_member_of(owner, created.organization) is True
        assert authorization.has_permission(
            owner, created.organization, authorization.ORGANIZATION_VIEW
        )
        assert (
            authorization.require_permission(
                owner, created.organization, authorization.ORGANIZATION_MEMBERS_MANAGE
            )
            == created.membership
        )

    def test_member_without_permission_is_denied(self):
        owner = _make_user()
        member = _make_user('grace@example.com')
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        _member_without_permission(created.organization, member)

        assert (
            authorization.has_permission(
                member, created.organization, authorization.ORGANIZATION_MEMBERS_MANAGE
            )
            is False
        )
        with pytest.raises(authorization.AuthorizationError) as exc_info:
            authorization.require_permission(
                member, created.organization, authorization.ORGANIZATION_MEMBERS_MANAGE
            )
        assert exc_info.value.reason == 'forbidden'

    def test_inactive_user_and_membership_are_denied(self):
        owner = _make_user()
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        owner.is_active = False
        owner.save(update_fields=['is_active'])
        assert (
            authorization.has_permission(
                owner, created.organization, authorization.ORGANIZATION_VIEW
            )
            is False
        )

        owner.is_active = True
        owner.save(update_fields=['is_active'])
        created.membership.status = Membership.Status.INACTIVE
        created.membership.save(update_fields=['status'])
        assert (
            authorization.has_permission(
                owner, created.organization, authorization.ORGANIZATION_VIEW
            )
            is False
        )

    def test_non_member_and_unknown_organization_are_denied(self):
        user = _make_user()
        other_owner = _make_user('grace@example.com')
        other = services.create_organization_for_user(
            other_owner, services.CreateOrganizationInput(name='Other')
        )

        assert authorization.has_permission(user, other.organization, 'organization.view') is False
        with pytest.raises(authorization.AuthorizationError) as exc_info:
            authorization.require_permission(user, other.organization, 'organization.view')
        assert exc_info.value.reason == 'membership_required'
        assert authorization.has_permission(user, 999999, 'organization.view') is False

    def test_permission_from_another_organization_role_is_denied(self):
        owner = _make_user()
        other_owner = _make_user('grace@example.com')
        first = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='First')
        )
        second = services.create_organization_for_user(
            other_owner, services.CreateOrganizationInput(name='Second')
        )

        assert (
            authorization.has_permission(owner, second.organization, 'organization.view') is False
        )
        assert (
            authorization.has_permission(other_owner, first.organization, 'organization.view')
            is False
        )
        assert authorization.get_membership(owner, second.organization) is None
        assert authorization.get_membership(other_owner, first.organization) is None

    def test_unknown_role_and_permission_are_denied(self):
        owner = _make_user()
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )

        assert services.get_role_for_user(owner, 999999) is None
        assert services.list_permissions_for_role(owner, 999999) == []
        assert (
            authorization.has_permission(owner, created.organization, 'unknown.permission') is False
        )
        with pytest.raises(authorization.AuthorizationError) as exc_info:
            authorization.require_permission(None, created.organization, 'organization.view')
        assert exc_info.value.reason == 'unauthenticated'


@pytest.mark.django_db
class TestActiveOrganizationIds:
    """
    The set-valued sibling of `get_membership`, added in S2-003 for the
    questions that span many organizations at once - a tenant feed, or a
    visibility filter that has to answer "is this reader in the same
    organization as this idea?" in one query.

    Its guards are asserted directly rather than through a caller, because the
    one place that calls it (`ideas/selectors`) has already established that the
    user is authenticated, so the guards are only reachable by a direct caller -
    and an untested guard in a public helper is a guard nobody has checked.
    """

    def test_it_lists_the_organizations_an_active_member_belongs_to(self):
        owner = _make_user()
        first = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        second = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Other Co')
        )
        colleague = _make_user('colleague@example.com')
        Membership.objects.create(user=colleague, organization=first.organization)

        assert sorted(authorization.active_organization_ids(owner)) == sorted(
            [first.organization.pk, second.organization.pk]
        )
        assert authorization.active_organization_ids(colleague) == [first.organization.pk]

    def test_it_ignores_an_inactive_membership(self):
        owner = _make_user()
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        membership = Membership.objects.get(user=owner, organization=created.organization)
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])

        assert authorization.active_organization_ids(owner) == []

    def test_an_unauthenticated_caller_gets_nothing(self):
        assert authorization.active_organization_ids(None) == []

    def test_a_deactivated_caller_gets_nothing(self):
        user = _make_user()
        user.is_active = False
        user.save(update_fields=['is_active'])

        assert authorization.active_organization_ids(user) == []


@pytest.mark.django_db
class TestAuthorizationBoundaries:
    def test_member_cannot_self_escalate_to_owner(self):
        owner = _make_user()
        member = _make_user('grace@example.com')
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        membership, _ = _member_without_permission(created.organization, member)
        owner_role = Role.objects.get(organization=created.organization, slug='owner')

        with pytest.raises(services.OrganizationError) as exc_info:
            services.assign_role_to_membership(member, membership.pk, owner_role.pk)

        assert exc_info.value.reason == 'forbidden'
        assert not MembershipRole.objects.filter(
            membership=membership,
            role=owner_role,
        ).exists()

    def test_member_cannot_use_another_organizations_role_or_membership(self):
        owner = _make_user()
        other_owner = _make_user('grace@example.com')
        first = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='First')
        )
        second = services.create_organization_for_user(
            other_owner, services.CreateOrganizationInput(name='Second')
        )
        first_role = Role.objects.get(organization=first.organization, slug='owner')
        second_membership = second.membership

        with pytest.raises(services.OrganizationError, match='not found'):
            services.assign_role_to_membership(other_owner, second_membership.pk, first_role.pk)

    def test_member_cannot_view_members_without_permission(self):
        owner = _make_user()
        member = _make_user('grace@example.com')
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        _member_without_permission(created.organization, member)

        assert services.list_memberships_for_organization(member, created.organization.pk) == []
        assert services.list_memberships_for_organization(owner, created.organization.pk) != []

    def test_inactive_membership_cannot_authorize_role_mutation(self):
        owner = _make_user()
        member = _make_user('grace@example.com')
        created = services.create_organization_for_user(
            owner, services.CreateOrganizationInput(name='Acme Labs')
        )
        membership = Membership.objects.create(user=member, organization=created.organization)
        admin_role = Role.objects.create(
            organization=created.organization,
            name='Admin',
            slug='admin',
        )
        MembershipRole.objects.create(membership=membership, role=admin_role)
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])

        with pytest.raises(services.OrganizationError, match='active memberships'):
            services.assign_role_to_membership(owner, membership.pk, admin_role.pk)
