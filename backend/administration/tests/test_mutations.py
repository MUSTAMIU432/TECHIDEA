"""
Administrative operations: authorization, the owning domains' rules, and the
audit trail.

Every mutation is refused - with nothing changed and nothing audited - for a
caller without its specific permission, including an administrator who only
has console access. Allowed operations still obey the rules of the domain they
touch (the last Owner, a duplicate category name), and every success, and every
rule-refusal of an account or role change, leaves an `AdminAuditEntry`.
"""

import pytest
from django.utils import timezone

from administration.models import AdminAuditEntry
from ideas.models import Category
from identity.authentication import login
from identity.models import RefreshSession
from organizations.models import MembershipRole, Role

pytestmark = pytest.mark.django_db

SET_ACTIVE = """
mutation($input: AdminSetUserActiveInput!) {
  adminSetUserActive(input: $input) { success message field user { id isActive } }
}
"""

ASSIGN = """
mutation($input: AdminMembershipRoleInput!) {
  adminAssignMembershipRole(input: $input) {
    success message field member { id roles { slug } }
  }
}
"""

REMOVE = """
mutation($input: AdminMembershipRoleInput!) {
  adminRemoveMembershipRole(input: $input) {
    success message field member { id roles { slug } }
  }
}
"""

CREATE_CATEGORY = """
mutation($input: AdminCreateCategoryInput!) {
  adminCreateCategory(input: $input) {
    success message field category { id name slug description isActive ideaCount }
  }
}
"""

UPDATE_CATEGORY = """
mutation($input: AdminUpdateCategoryInput!) {
  adminUpdateCategory(input: $input) {
    success message field category { id name slug description }
  }
}
"""

SET_CATEGORY_ACTIVE = """
mutation($input: AdminSetCategoryActiveInput!) {
  adminSetCategoryActive(input: $input) { success message category { isActive ideaCount } }
}
"""


def _entries(**filters):
    return list(AdminAuditEntry.objects.filter(**filters).order_by('pk'))


def _mutations(world):
    """Every admin mutation, with valid input, keyed by its root field."""
    membership = world['member'].memberships.get()
    reviewer_role = Role.objects.get(organization=world['acme'], slug='reviewer')
    return {
        'adminSetUserActive': (
            SET_ACTIVE,
            {'input': {'userId': world['member'].pk, 'isActive': False}},
        ),
        'adminAssignMembershipRole': (
            ASSIGN,
            {'input': {'membershipId': membership.pk, 'roleId': reviewer_role.pk}},
        ),
        'adminRemoveMembershipRole': (
            REMOVE,
            {'input': {'membershipId': membership.pk, 'roleId': reviewer_role.pk}},
        ),
        'adminCreateCategory': (CREATE_CATEGORY, {'input': {'name': 'Logistics'}}),
        'adminUpdateCategory': (
            UPDATE_CATEGORY,
            {'input': {'id': world['finance'].pk, 'name': 'Money'}},
        ),
        'adminSetCategoryActive': (
            SET_CATEGORY_ACTIVE,
            {'input': {'id': world['finance'].pk, 'isActive': False}},
        ),
    }


class TestAuthorization:
    @pytest.mark.parametrize(
        'who', [None, 'member', 'acme_owner', 'reviewer', 'globex_owner', 'limited_admin']
    )
    def test_every_mutation_is_refused_without_its_permission(self, gql, world, who):
        user = world[who] if who else None
        audit_before = AdminAuditEntry.objects.count()

        for name, (query, variables) in _mutations(world).items():
            payload = gql(query, variables, user=user)[name]
            assert payload['success'] is False, name

        world['member'].refresh_from_db()
        world['finance'].refresh_from_db()
        assert world['member'].is_active is True
        assert world['finance'].name == 'Finance'
        assert world['finance'].is_active is True
        assert not Category.objects.filter(name='Logistics').exists()
        assert not MembershipRole.objects.filter(
            membership__user=world['member'], role__slug='reviewer'
        ).exists()
        assert AdminAuditEntry.objects.count() == audit_before

    def test_the_organization_owner_cannot_use_the_admin_role_mutation_on_their_own_org(
        self, gql, world
    ):
        query, variables = _mutations(world)['adminAssignMembershipRole']
        payload = gql(query, variables, user=world['acme_owner'])['adminAssignMembershipRole']
        assert payload == {
            'success': False,
            'message': 'You do not have permission to perform this action.',
            'field': None,
            'member': None,
        }


class TestUserAccounts:
    def test_deactivating_an_account_revokes_its_sessions_and_is_audited(self, gql, world):
        member = world['member']
        login(member.email, 'a-strong-unique-pass-1')
        assert RefreshSession.objects.filter(user=member, revoked_at__isnull=True).count() == 1

        payload = gql(
            SET_ACTIVE,
            {'input': {'userId': member.pk, 'isActive': False, 'reason': 'Left the company'}},
            user=world['admin'],
        )['adminSetUserActive']

        assert payload['success'] is True
        assert payload['user']['isActive'] is False
        assert not RefreshSession.objects.filter(user=member, revoked_at__isnull=True).exists()
        (entry,) = _entries(action='user.deactivated')
        assert entry.actor == world['admin']
        assert entry.result == 'succeeded'
        assert entry.target_type == 'user'
        assert entry.target_id == str(member.pk)
        assert entry.target_label == member.email
        assert entry.metadata == {'reason': 'Left the company', 'revoked_sessions': 1}
        assert entry.created_at <= timezone.now()

    def test_a_deactivated_account_can_no_longer_use_its_token(self, gql, world):
        gql(
            SET_ACTIVE,
            {'input': {'userId': world['member'].pk, 'isActive': False}},
            user=world['admin'],
        )
        assert gql('query { me { id } }', user=world['member'])['me'] is None

    def test_reactivating(self, gql, world):
        world['member'].is_active = False
        world['member'].save()
        payload = gql(
            SET_ACTIVE,
            {'input': {'userId': world['member'].pk, 'isActive': True}},
            user=world['admin'],
        )['adminSetUserActive']
        assert payload['success'] is True
        assert _entries(action='user.activated')[0].result == 'succeeded'

    @pytest.mark.parametrize(
        ('target', 'is_active', 'message'),
        [
            ('admin', False, 'You cannot change the status of your own account.'),
            ('superuser', False, "Only a superuser can change a superuser's account."),
            ('member', True, 'This account is already active.'),
        ],
    )
    def test_rule_refusals_are_audited(self, gql, world, target, is_active, message):
        payload = gql(
            SET_ACTIVE,
            {'input': {'userId': world[target].pk, 'isActive': is_active}},
            user=world['admin'],
        )['adminSetUserActive']

        assert payload['success'] is False
        assert payload['message'] == message
        world[target].refresh_from_db()
        assert world[target].is_active is True
        (entry,) = _entries(result='refused')
        assert entry.actor == world['admin']
        assert entry.message == message

    def test_a_superuser_can_change_a_superuser(self, gql, world, django_user_model):
        other = django_user_model.objects.create_superuser(
            email='root2@platform.example',
            password='a-strong-unique-pass-1',
            first_name='R',
            last_name='Two',
            phone_number='+255712345678',
        )
        payload = gql(
            SET_ACTIVE, {'input': {'userId': other.pk, 'isActive': False}}, user=world['superuser']
        )['adminSetUserActive']
        assert payload['success'] is True

    def test_an_unknown_account_is_not_found_and_not_audited(self, gql, world):
        payload = gql(
            SET_ACTIVE, {'input': {'userId': 999999, 'isActive': False}}, user=world['admin']
        )['adminSetUserActive']
        assert payload['message'] == 'User not found.'
        assert not _entries(action='user.deactivated')

    def test_an_overlong_reason_is_refused(self, gql, world):
        payload = gql(
            SET_ACTIVE,
            {'input': {'userId': world['member'].pk, 'isActive': False, 'reason': 'x' * 501}},
            user=world['admin'],
        )['adminSetUserActive']
        assert payload['field'] == 'reason'

    def test_the_audit_trail_is_on_the_user_detail(self, gql, world):
        gql(
            SET_ACTIVE,
            {'input': {'userId': world['member'].pk, 'isActive': False}},
            user=world['admin'],
        )
        detail = gql(
            'query($id: ID!) { adminUser(id: $id) { auditEntries { action actor { email } } } }',
            {'id': world['member'].pk},
            user=world['admin'],
        )['adminUser']
        assert detail['auditEntries'] == [
            {'action': 'USER_DEACTIVATED', 'actor': {'email': 'admin@platform.example'}}
        ]


class TestOrganizationRoles:
    def test_assign_and_remove_a_role(self, gql, world):
        query, variables = _mutations(world)['adminAssignMembershipRole']
        payload = gql(query, variables, user=world['admin'])['adminAssignMembershipRole']
        assert payload['success'] is True
        assert [role['slug'] for role in payload['member']['roles']] == ['reviewer']

        query, variables = _mutations(world)['adminRemoveMembershipRole']
        payload = gql(query, variables, user=world['admin'])['adminRemoveMembershipRole']
        assert payload['success'] is True
        assert payload['member']['roles'] == []

        entries = _entries(target_type='membership')
        assert [entry.action for entry in entries] == [
            'membership_role.assigned',
            'membership_role.removed',
        ]
        assert entries[0].organization_id == world['acme'].pk
        assert entries[0].metadata['role_slug'] == 'reviewer'

    def test_the_last_owner_keeps_the_owner_role(self, gql, world):
        membership = world['acme_owner'].memberships.get()
        owner_role = Role.objects.get(organization=world['acme'], slug='owner')

        payload = gql(
            REMOVE,
            {'input': {'membershipId': membership.pk, 'roleId': owner_role.pk}},
            user=world['admin'],
        )['adminRemoveMembershipRole']

        assert payload['success'] is False
        assert payload['message'] == 'The last active holder of a system role cannot be removed.'
        assert MembershipRole.objects.filter(membership=membership, role=owner_role).exists()
        (entry,) = _entries(result='refused')
        assert entry.action == 'membership_role.removed'

    def test_a_role_from_another_organization_is_not_found(self, gql, world):
        globex_role = Role.objects.get(organization=world['globex'], slug='reviewer')
        payload = gql(
            ASSIGN,
            {
                'input': {
                    'membershipId': world['member'].memberships.get().pk,
                    'roleId': globex_role.pk,
                }
            },
            user=world['admin'],
        )['adminAssignMembershipRole']
        assert payload['message'] == 'Membership or role not found.'

    def test_an_inactive_membership_cannot_gain_a_role(self, gql, world):
        membership = world['member'].memberships.get()
        membership.status = 'inactive'
        membership.save()
        query, variables = _mutations(world)['adminAssignMembershipRole']
        payload = gql(query, variables, user=world['admin'])['adminAssignMembershipRole']
        assert payload['message'] == 'Roles can only be assigned to active memberships.'

    def test_a_duplicate_role_is_refused(self, gql, world):
        query, variables = _mutations(world)['adminAssignMembershipRole']
        gql(query, variables, user=world['admin'])
        payload = gql(query, variables, user=world['admin'])['adminAssignMembershipRole']
        assert payload['message'] == 'This membership already has this role.'


class TestCategories:
    def test_create_edit_retire_and_restore(self, gql, world):
        created = gql(
            CREATE_CATEGORY,
            {'input': {'name': ' Logistics ', 'description': 'Moving things.'}},
            user=world['admin'],
        )['adminCreateCategory']
        assert created['success'] is True
        assert created['category']['slug'] == 'logistics'
        assert created['category']['ideaCount'] == 0
        category_id = created['category']['id']

        updated = gql(
            UPDATE_CATEGORY,
            {'input': {'id': category_id, 'name': 'Supply chain', 'description': 'Moving.'}},
            user=world['admin'],
        )['adminUpdateCategory']
        assert updated['category']['name'] == 'Supply chain'
        assert updated['category']['slug'] == 'logistics'

        retired = gql(
            SET_CATEGORY_ACTIVE,
            {'input': {'id': world['finance'].pk, 'isActive': False}},
            user=world['admin'],
        )['adminSetCategoryActive']
        assert retired['success'] is True
        assert retired['category'] == {'isActive': False, 'ideaCount': 3}
        # Existing ideas keep their category; new ideas can no longer pick it.
        world['org_idea'].refresh_from_db()
        assert world['org_idea'].category_id == world['finance'].pk
        names = gql('query { categories { name } }', user=world['author'])['categories']
        assert {'name': 'Finance'} not in names

        restored = gql(
            SET_CATEGORY_ACTIVE,
            {'input': {'id': world['finance'].pk, 'isActive': True}},
            user=world['admin'],
        )['adminSetCategoryActive']
        assert restored['category']['isActive'] is True

        assert [entry.action for entry in _entries(target_type='category')] == [
            'category.created',
            'category.updated',
            'category.deactivated',
            'category.activated',
        ]
        assert _entries(action='category.updated')[0].metadata == {
            'changes': {
                'name': ['Logistics', 'Supply chain'],
                'description': ['Moving things.', 'Moving.'],
            }
        }

    @pytest.mark.parametrize('name', ['Finance', 'finance', ''])
    def test_a_duplicate_or_blank_name_is_refused(self, gql, world, name):
        payload = gql(CREATE_CATEGORY, {'input': {'name': name}}, user=world['admin'])[
            'adminCreateCategory'
        ]
        assert payload['success'] is False
        assert payload['field'] == 'name'

    def test_renaming_onto_an_existing_name_is_refused(self, gql, world):
        other = Category.objects.create(name='Operations')
        payload = gql(
            UPDATE_CATEGORY, {'input': {'id': other.pk, 'name': 'Finance'}}, user=world['admin']
        )['adminUpdateCategory']
        assert payload['success'] is False
        assert payload['field'] == 'name'

    def test_a_no_op_is_refused(self, gql, world):
        payload = gql(
            UPDATE_CATEGORY,
            {'input': {'id': world['finance'].pk, 'name': 'Finance'}},
            user=world['admin'],
        )['adminUpdateCategory']
        assert payload['message'] == 'Nothing to change.'
        payload = gql(
            SET_CATEGORY_ACTIVE,
            {'input': {'id': world['finance'].pk, 'isActive': True}},
            user=world['admin'],
        )['adminSetCategoryActive']
        assert payload['message'] == 'This category is already active.'

    def test_the_admin_list_includes_retired_categories(self, gql, world):
        Category.objects.create(name='Retired', is_active=False)
        categories = gql(
            'query { adminCategories { name isActive ideaCount } }', user=world['admin']
        )['adminCategories']
        assert {'name': 'Retired', 'isActive': False, 'ideaCount': 0} in categories
        assert {'name': 'Finance', 'isActive': True, 'ideaCount': 3} in categories
