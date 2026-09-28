import json

import pytest
from django.test import Client

from graphql_api.schema import schema as root_schema
from identity.models import User
from organizations.models import Membership, MembershipRole, Organization, Role

REGISTER_MUTATION = """
mutation Register($input: RegisterInput!) {
  register(input: $input) { success }
}
"""

LOGIN_MUTATION = """
mutation Login($input: LoginInput!) {
  login(input: $input) { accessToken user { id } }
}
"""

CREATE_ORGANIZATION_MUTATION = """
mutation CreateOrganization($input: CreateOrganizationInput!) {
  createOrganization(input: $input) {
    success
    message
    field
    organization { id name slug }
    membership { id status user { id email } }
  }
}
"""

ME_ORGANIZATIONS_QUERY = """
query MeOrganizations {
  meOrganizations {
    organization { id name slug }
    membership {
      id status user { id email }
      roles { id name slug permissions { code } }
    }
  }
}
"""

ME_MEMBERSHIPS_QUERY = """
query MeMemberships {
  meMemberships { id status organization { id name slug } user { id email } }
}
"""

ORGANIZATION_QUERY = """
query Organization($id: ID!) {
  organization(id: $id) { id name slug }
}
"""

ORGANIZATION_MEMBERS_QUERY = """
query OrganizationMembers($organizationId: ID!) {
  organizationMembers(organizationId: $organizationId) {
    id status user { id email } organization { id slug }
  }
}
"""

MY_ORGANIZATION_ROLES_QUERY = """
query MyOrganizationRoles {
  myOrganizationRoles {
    id name slug isSystem
    organization { id name slug }
    permissions { code name }
  }
}
"""

ORGANIZATION_ROLES_QUERY = """
query OrganizationRoles($organizationId: ID!) {
  organizationRoles(organizationId: $organizationId) {
    id name slug isSystem
    organization { id name slug }
    permissions { code name }
  }
}
"""

ASSIGN_ROLE_MUTATION = """
mutation AssignRole($input: MembershipRoleInput!) {
  assignRoleToMembership(input: $input) {
    success message field
    membershipRole { membership { id } role { id slug } }
  }
}
"""

REMOVE_ROLE_MUTATION = """
mutation RemoveRole($input: MembershipRoleInput!) {
  removeRoleFromMembership(input: $input) {
    success message field
  }
}
"""


@pytest.fixture
def gql(client: Client):
    def post(query, variables=None, access_token=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        headers = {}
        if access_token is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {access_token}'
        return client.post(
            '/graphql/',
            data=json.dumps(payload),
            content_type='application/json',
            **headers,
        )

    return post


def _register_and_login(gql, email):
    registration = gql(
        REGISTER_MUTATION,
        {
            'input': {
                'firstName': 'Ada',
                'lastName': 'Lovelace',
                'email': email,
                'phoneNumber': '+255712345678',
                'password': 'a-strong-unique-pass-1',
            }
        },
    )
    assert registration.json()['data']['register']['success'] is True

    login = gql(
        LOGIN_MUTATION,
        {'input': {'email': email, 'password': 'a-strong-unique-pass-1'}},
    )
    return login.json()['data']['login']['accessToken']


@pytest.mark.django_db
def test_create_organization_persists_creator_membership(gql):
    access_token = _register_and_login(gql, 'ada@example.com')

    response = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=access_token,
    )

    body = response.json()
    assert 'errors' not in body
    payload = body['data']['createOrganization']
    assert payload['success'] is True
    assert payload['organization']['name'] == 'Acme Labs'
    assert payload['organization']['slug'] == 'acme-labs'
    assert payload['membership']['status'] == Membership.Status.ACTIVE
    user = User.objects.get(email='ada@example.com')
    assert Membership.objects.filter(
        user=user, organization_id=payload['organization']['id']
    ).exists()


@pytest.mark.django_db
def test_create_organization_rejects_unauthenticated_request(gql):
    response = gql(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Acme Labs'}})

    payload = response.json()['data']['createOrganization']
    assert payload['success'] is False
    assert payload['organization'] is None
    assert Organization.objects.count() == 0


@pytest.mark.django_db
def test_me_organizations_returns_membership_information(gql):
    access_token = _register_and_login(gql, 'ada@example.com')
    gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=access_token,
    )

    response = gql(ME_ORGANIZATIONS_QUERY, access_token=access_token)

    body = response.json()
    assert 'errors' not in body
    item = body['data']['meOrganizations'][0]
    assert item['organization']['name'] == 'Acme Labs'
    assert item['membership']['status'] == Membership.Status.ACTIVE
    assert item['membership']['user']['email'] == 'ada@example.com'
    assert item['membership']['roles'][0]['slug'] == 'owner'
    assert 'organization.view' in {
        permission['code'] for permission in item['membership']['roles'][0]['permissions']
    }


@pytest.mark.django_db
def test_me_memberships_returns_current_users_memberships(gql):
    access_token = _register_and_login(gql, 'ada@example.com')
    gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=access_token,
    )

    response = gql(ME_MEMBERSHIPS_QUERY, access_token=access_token)

    assert response.json()['data']['meMemberships'][0]['organization']['slug'] == 'acme-labs'


@pytest.mark.django_db
def test_organization_query_is_scoped_to_membership(gql):
    ada_token = _register_and_login(gql, 'ada@example.com')
    grace_token = _register_and_login(gql, 'grace@example.com')
    created = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=ada_token,
    )
    organization_id = created.json()['data']['createOrganization']['organization']['id']

    allowed = gql(ORGANIZATION_QUERY, {'id': organization_id}, access_token=ada_token)
    denied = gql(ORGANIZATION_QUERY, {'id': organization_id}, access_token=grace_token)

    assert allowed.json()['data']['organization']['id'] == organization_id
    assert denied.json()['data']['organization'] is None


@pytest.mark.django_db
def test_organization_members_cannot_be_read_across_membership_boundary(gql):
    ada_token = _register_and_login(gql, 'ada@example.com')
    grace_token = _register_and_login(gql, 'grace@example.com')
    created = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=ada_token,
    )
    organization_id = created.json()['data']['createOrganization']['organization']['id']

    allowed = gql(
        ORGANIZATION_MEMBERS_QUERY,
        {'organizationId': organization_id},
        access_token=ada_token,
    )
    denied = gql(
        ORGANIZATION_MEMBERS_QUERY,
        {'organizationId': organization_id},
        access_token=grace_token,
    )

    assert len(allowed.json()['data']['organizationMembers']) == 1
    assert denied.json()['data']['organizationMembers'] == []


@pytest.mark.django_db
def test_unauthenticated_queries_return_empty_membership_data(gql):
    organizations = gql(ME_ORGANIZATIONS_QUERY)
    members = gql(ORGANIZATION_MEMBERS_QUERY, {'organizationId': '1'})
    roles = gql(MY_ORGANIZATION_ROLES_QUERY)
    organization_roles = gql(ORGANIZATION_ROLES_QUERY, {'organizationId': '1'})

    assert organizations.json()['data']['meOrganizations'] == []
    assert members.json()['data']['organizationMembers'] == []
    assert roles.json()['data']['myOrganizationRoles'] == []
    assert organization_roles.json()['data']['organizationRoles'] == []


@pytest.mark.django_db
def test_inactive_user_cannot_create_organization_even_with_existing_token(gql):
    access_token = _register_and_login(gql, 'ada@example.com')
    User.objects.filter(email='ada@example.com').update(is_active=False)

    response = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=access_token,
    )

    assert response.json()['data']['createOrganization']['success'] is False
    assert Organization.objects.count() == 0


@pytest.mark.django_db
def test_duplicate_slug_returns_field_error_without_duplicate_membership(gql):
    access_token = _register_and_login(gql, 'ada@example.com')
    first = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=access_token,
    )
    second = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Another name', 'slug': 'acme-labs'}},
        access_token=access_token,
    )

    assert first.json()['data']['createOrganization']['success'] is True
    assert second.json()['data']['createOrganization']['field'] == 'slug'
    assert Organization.objects.count() == 1
    assert Membership.objects.count() == 1


@pytest.mark.django_db
def test_my_organization_roles_returns_only_current_users_roles(gql):
    access_token = _register_and_login(gql, 'ada@example.com')
    gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=access_token,
    )

    response = gql(MY_ORGANIZATION_ROLES_QUERY, access_token=access_token)

    assert 'errors' not in response.json()
    roles = {role['slug']: role for role in response.json()['data']['myOrganizationRoles']}
    # Bootstrap provisions the Owner role and (S3-002) the Reviewer role.
    assert set(roles) == {'owner', 'reviewer'}
    assert roles['owner']['isSystem'] is True
    assert {permission['code'] for permission in roles['owner']['permissions']} >= {
        'organization.view',
        'organization.members.manage',
        'idea.review',
    }
    assert roles['reviewer']['isSystem'] is False
    assert {permission['code'] for permission in roles['reviewer']['permissions']} == {
        'organization.view',
        'idea.review',
    }


@pytest.mark.django_db
def test_organization_roles_are_membership_scoped(gql):
    ada_token = _register_and_login(gql, 'ada@example.com')
    grace_token = _register_and_login(gql, 'grace@example.com')
    created = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=ada_token,
    )
    organization_id = created.json()['data']['createOrganization']['organization']['id']

    allowed = gql(
        ORGANIZATION_ROLES_QUERY,
        {'organizationId': organization_id},
        access_token=ada_token,
    )
    denied = gql(
        ORGANIZATION_ROLES_QUERY,
        {'organizationId': organization_id},
        access_token=grace_token,
    )

    assert {role['slug'] for role in allowed.json()['data']['organizationRoles']} == {
        'owner',
        'reviewer',
    }
    assert denied.json()['data']['organizationRoles'] == []


@pytest.mark.django_db
def test_assign_role_rejects_cross_organization_membership(gql):
    ada_token = _register_and_login(gql, 'ada@example.com')
    grace_token = _register_and_login(gql, 'grace@example.com')
    first = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'First'}},
        access_token=ada_token,
    )
    second = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Second'}},
        access_token=grace_token,
    )
    first_organization_id = first.json()['data']['createOrganization']['organization']['id']
    second_payload = second.json()['data']['createOrganization']
    second_membership_id = second_payload['membership']['id']
    role = Role.objects.get(organization_id=first_organization_id, slug='owner')

    response = gql(
        ASSIGN_ROLE_MUTATION,
        {'input': {'membershipId': second_membership_id, 'roleId': str(role.pk)}},
        access_token=grace_token,
    )

    assert response.json()['data']['assignRoleToMembership']['success'] is False
    assert 'not found' in response.json()['data']['assignRoleToMembership']['message']


@pytest.mark.django_db
def test_assign_and_remove_role_for_same_organization_membership(gql):
    access_token = _register_and_login(gql, 'ada@example.com')
    created = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=access_token,
    )
    payload = created.json()['data']['createOrganization']
    membership_id = payload['membership']['id']
    role = Role.objects.create(
        organization_id=payload['organization']['id'],
        name='Admin',
        slug='admin',
    )
    variables = {'input': {'membershipId': membership_id, 'roleId': str(role.pk)}}

    assigned = gql(ASSIGN_ROLE_MUTATION, variables, access_token=access_token)
    duplicate = gql(ASSIGN_ROLE_MUTATION, variables, access_token=access_token)
    removed = gql(REMOVE_ROLE_MUTATION, variables, access_token=access_token)

    assert assigned.json()['data']['assignRoleToMembership']['success'] is True
    assert duplicate.json()['data']['assignRoleToMembership']['success'] is False
    assert removed.json()['data']['removeRoleFromMembership']['success'] is True


@pytest.mark.django_db
def test_member_without_permission_cannot_view_members_or_self_escalate(gql):
    ada_token = _register_and_login(gql, 'ada@example.com')
    grace_token = _register_and_login(gql, 'grace@example.com')
    created = gql(
        CREATE_ORGANIZATION_MUTATION,
        {'input': {'name': 'Acme Labs'}},
        access_token=ada_token,
    )
    payload = created.json()['data']['createOrganization']
    organization_id = payload['organization']['id']
    grace = User.objects.get(email='grace@example.com')
    grace_membership = Membership.objects.create(
        user=grace,
        organization_id=organization_id,
    )
    member_role = Role.objects.create(
        organization_id=organization_id,
        name='Member',
        slug='member',
    )
    owner_role = Role.objects.get(organization_id=organization_id, slug='owner')
    MembershipRole.objects.create(membership=grace_membership, role=member_role)

    members = gql(
        ORGANIZATION_MEMBERS_QUERY,
        {'organizationId': organization_id},
        access_token=grace_token,
    )
    organization = gql(ORGANIZATION_QUERY, {'id': organization_id}, access_token=grace_token)
    escalation = gql(
        ASSIGN_ROLE_MUTATION,
        {
            'input': {
                'membershipId': str(grace_membership.pk),
                'roleId': str(owner_role.pk),
            }
        },
        access_token=grace_token,
    )

    assert members.json()['data']['organizationMembers'] == []
    assert organization.json()['data']['organization'] is None
    assert escalation.json()['data']['assignRoleToMembership']['success'] is False


def test_role_schema_omits_sensitive_fields_and_client_identity(gql):
    schema_text = str(root_schema)
    assert 'myOrganizationRoles' in schema_text
    assert 'organizationRoles' in schema_text
    assert 'assignRoleToMembership' in schema_text

    role_type = root_schema.get_type_by_name('RoleType')
    role_fields = {field.name for field in role_type.fields}
    assert 'user' not in role_fields
    assert 'membership' not in role_fields
    assert 'password' not in role_fields
    assert 'token' not in role_fields

    input_type = root_schema.get_type_by_name('MembershipRoleInput')
    input_fields = {field.name for field in input_type.fields}
    assert input_fields == {'membership_id', 'role_id'}
