"""Giving accounts the platform's jobs from the console, and taking them away."""

import pytest

from administration import platform_roles, services
from administration.authorization import AdministrationError, capabilities_for
from administration.models import AdminAuditEntry
from automation import authorization as automation_authorization
from automation import delivery
from automation.tests.test_opportunity import make_user, opened
from identity.models import User
from reviews.tests.platform import grant_permission


def fresh(user):
    # Django caches permissions on the instance.
    return User.objects.get(pk=user.pk)


@pytest.fixture
def admin(db):
    user = make_user('admin@example.com')
    services.grant_platform_admin(user.email)
    return fresh(user)


@pytest.fixture
def person(db):
    return make_user('person@example.com')


@pytest.mark.django_db
class TestWhoMayHandOutRoles:
    def test_platform_administrators_hold_the_staffing_permissions(self, admin):
        capabilities = capabilities_for(admin)

        assert capabilities.can_manage_platform_roles is True
        assert capabilities.can_manage_reviewers is True
        # Staffing is not doing: the group still decides and routes nothing.
        assert capabilities.can_review_platform_submissions is False
        assert capabilities.can_assign_platform_reviewers is False
        assert capabilities.can_release_proposals is False

    def test_anyone_else_is_refused_and_sees_nothing(self, person):
        router = make_user('router@example.com')
        grant_permission(router, 'administration.access_console')
        grant_permission(router, 'administration.assign_platform_reviewers')

        for caller in (person, fresh(router), None):
            with pytest.raises(AdministrationError):
                platform_roles.grant_role(caller, 'developer', person.email)
            assert platform_roles.roles_overview(caller) == []


@pytest.mark.django_db
class TestGivingARole:
    def test_each_role_grants_exactly_its_power(self, admin, person):
        platform_roles.grant_role(admin, 'developer', person.email)
        assert automation_authorization.is_eligible_assignee(fresh(person)) is True
        assert automation_authorization.is_delivery_manager(fresh(person)) is False

        platform_roles.grant_role(admin, 'delivery_manager', person.email)
        assert automation_authorization.is_delivery_manager(fresh(person)) is True
        # Neither delivery role opens the console: it is the administrators' alone.
        assert capabilities_for(fresh(person)).can_access_console is False

        other = make_user('other-admin@example.com')
        services.grant_platform_admin(other.email)
        platform_roles.grant_role(admin, 'intake', other.email)
        assert capabilities_for(fresh(other)).can_assign_platform_reviewers is True
        assert capabilities_for(fresh(other)).can_release_proposals is False

        platform_roles.grant_role(admin, 'proposal_approver', other.email)
        assert capabilities_for(fresh(other)).can_release_proposals is True

    def test_console_roles_are_for_administrators_only(self, admin, person):
        for role in ('intake', 'proposal_approver'):
            with pytest.raises(AdministrationError, match='Only a platform administrator'):
                platform_roles.grant_role(admin, role, person.email)

        assert capabilities_for(fresh(person)).can_access_console is False

    def test_a_console_role_without_the_console_grants_nothing(self, admin):
        other = make_user('former-admin@example.com')
        services.grant_platform_admin(other.email)
        platform_roles.grant_role(admin, 'intake', other.email)
        services.revoke_platform_admin(other.email)  # no longer an administrator

        assert capabilities_for(fresh(other)).can_access_console is False
        assert capabilities_for(fresh(other)).can_assign_platform_reviewers is False

    def test_the_overview_lists_the_holders(self, admin, person):
        platform_roles.grant_role(admin, 'developer', person.email)

        holders = {entry.role.key: entry.holders for entry in platform_roles.roles_overview(admin)}

        assert holders['developer'] == [person]
        assert holders['delivery_manager'] == []

    def test_an_administrator_may_take_a_role_themselves(self, admin):
        platform_roles.grant_role(admin, 'proposal_approver', admin.email)

        assert capabilities_for(fresh(admin)).can_release_proposals is True

    def test_bad_requests_are_refused(self, admin, person):
        with pytest.raises(AdministrationError, match='valid role'):
            platform_roles.grant_role(admin, 'superuser', person.email)
        with pytest.raises(AdministrationError, match='No account'):
            platform_roles.grant_role(admin, 'developer', 'nobody@example.com')
        User.objects.filter(pk=person.pk).update(is_active=False)
        with pytest.raises(AdministrationError, match='deactivated'):
            platform_roles.grant_role(admin, 'developer', person.email)

    def test_giving_a_role_twice_is_refused(self, admin, person):
        platform_roles.grant_role(admin, 'developer', person.email)

        with pytest.raises(AdministrationError, match='already hold the Developer role'):
            platform_roles.grant_role(admin, 'developer', person.email)

    def test_every_change_is_audited(self, admin, person):
        platform_roles.grant_role(admin, 'developer', person.email)
        platform_roles.revoke_role(admin, 'developer', person.pk)

        entries = AdminAuditEntry.objects.filter(target_type='user', target_id=str(person.pk))
        assert [(e.action, e.actor_id, e.metadata) for e in entries.order_by('pk')] == [
            (AdminAuditEntry.Action.PLATFORM_ROLE_GRANTED, admin.pk, {'role': 'developer'}),
            (AdminAuditEntry.Action.PLATFORM_ROLE_REVOKED, admin.pk, {'role': 'developer'}),
        ]


@pytest.mark.django_db
class TestTakingARoleAway:
    def test_removing_a_role_removes_the_power(self, admin, person):
        platform_roles.grant_role(admin, 'developer', person.email)
        platform_roles.revoke_role(admin, 'developer', person.pk)

        assert automation_authorization.is_eligible_assignee(fresh(person)) is False

    def test_only_a_holder_can_lose_a_role(self, admin, person):
        with pytest.raises(AdministrationError, match='does not hold'):
            platform_roles.revoke_role(admin, 'developer', person.pk)

    def test_a_developer_still_delivering_keeps_the_role(self, admin, person):
        manager = make_user('manager@example.com')
        platform_roles.grant_role(admin, 'delivery_manager', manager.email)
        platform_roles.grant_role(admin, 'developer', person.email)
        opportunity = opened(make_user('author@example.com'))
        delivery.assign_opportunity(fresh(manager), opportunity.pk, assignee_user_id=person.pk)

        with pytest.raises(AdministrationError, match='still delivering'):
            platform_roles.revoke_role(admin, 'developer', person.pk)

    def test_the_last_delivery_manager_stays_while_work_is_open(self, admin, person):
        platform_roles.grant_role(admin, 'delivery_manager', person.email)
        opened(make_user('author@example.com'))

        with pytest.raises(AdministrationError, match='last delivery manager'):
            platform_roles.revoke_role(admin, 'delivery_manager', person.pk)

        platform_roles.grant_role(admin, 'delivery_manager', admin.email)
        platform_roles.revoke_role(admin, 'delivery_manager', person.pk)  # somebody else is left


@pytest.mark.django_db
class TestGraphQL:
    def test_grant_list_and_refusal_through_the_api(self, gql, admin, person):
        granted = gql(
            'mutation($e: String!){ grantPlatformRole(role:"developer", email:$e){ success } }',
            {'e': person.email},
            user=admin,
        )
        assert granted['grantPlatformRole']['success'] is True

        listed = gql('query{ platformRoles{ key holders{ email } } }', user=admin)
        developers = next(r for r in listed['platformRoles'] if r['key'] == 'developer')
        assert developers['holders'] == [{'email': person.email}]
        assert gql('query{ platformRoles{ key } }', user=person) == {'platformRoles': []}

        refused = gql(
            'mutation($u: ID!){ revokePlatformRole(role:"developer", userId:$u){ success } }',
            {'u': str(person.pk)},
            user=person,
        )
        assert refused['revokePlatformRole']['success'] is False
        capabilities = gql('query{ adminCapabilities{ canManagePlatformRoles } }', user=admin)
        assert capabilities['adminCapabilities']['canManagePlatformRoles'] is True
