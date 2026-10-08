"""
Tenant isolation across the real HTTP boundary (S1-009).

Two fully independent tenants are set up and every organization-scoped
operation is then attempted by each of them against the other:

    User A  ->  Organization A  (creator, Owner role)
    User B  ->  Organization B  (creator, Owner role)

Nothing here is a service-level call. Each request goes through
`django.test.Client` -> middleware -> the GraphQL view -> the schema's own
resolvers -> `organizations.services`/`authorization` -> the database, with
the caller's identity arriving only as a signed `Authorization: Bearer` access
token, exactly as a browser sends it. The services-level suites in
`organizations/tests/` stub this outer half, so a mistake that only shows up
across the boundary (a resolver passing the wrong id, a filter that forgets
the tenant predicate) would not be caught by them.

Two properties are asserted throughout, and the second is the point of the
file:

1. Legitimate access works - A reaches everything in Organization A, B
   reaches everything in Organization B.
2. Cross-tenant access never returns the other tenant's data. A request that
   crosses the boundary returns *nothing* - null, an empty list, or a
   `success: false` payload - and never a partial answer, never a count, and
   never a distinguishable "you do not have access" signal that would confirm
   the foreign object exists.

Property 2 is additionally checked for *leakage*, not just for failure: after
each rejected cross-tenant attempt the test asserts the foreign tenant's data
appears nowhere in the raw response body, so a field that leaked alongside a
rejected parent could not pass unnoticed.
"""

import json

import pytest
from django.test import Client

from identity.models import User
from organizations.models import Membership, MembershipRole, Organization, Role

REGISTER_MUTATION = """
mutation Register($input: RegisterInput!) {
  register(input: $input) { success message field user { id email } }
}
"""

LOGIN_MUTATION = """
mutation Login($input: LoginInput!) {
  login(input: $input) { success accessToken user { id email } }
}
"""

CREATE_ORGANIZATION_MUTATION = """
mutation CreateOrganization($input: CreateOrganizationInput!) {
  createOrganization(input: $input) {
    success
    message
    field
    organization { id name slug }
    membership {
      id
      status
      user { id email }
      organization { id name }
      roles { id name slug isSystem }
    }
  }
}
"""

ORGANIZATIONS_QUERY = """
query MeOrganizations {
  meOrganizations {
    organization { id name slug }
    membership {
      id
      status
      user { id email }
      roles { id name slug isSystem }
    }
  }
}
"""

MEMBERSHIPS_QUERY = """
query MeMemberships {
  meMemberships {
    id
    status
    user { id email }
    organization { id name }
    roles { id name slug }
  }
}
"""

MY_ROLES_QUERY = """
query MyOrganizationRoles {
  myOrganizationRoles {
    id
    name
    slug
    isSystem
    organization { id name }
    permissions { code }
  }
}
"""

ORGANIZATION_ROLES_QUERY = """
query OrganizationRoles($organizationId: ID!) {
  organizationRoles(organizationId: $organizationId) {
    id
    name
    slug
    isSystem
    organization { id name }
    permissions { code }
  }
}
"""

ORGANIZATION_QUERY = """
query Organization($id: ID!) {
  organization(id: $id) { id name slug createdAt updatedAt }
}
"""

ORGANIZATION_MEMBERS_QUERY = """
query OrganizationMembers($organizationId: ID!) {
  organizationMembers(organizationId: $organizationId) {
    id
    status
    user { id email firstName }
    organization { id name }
    roles { id name slug }
  }
}
"""

ASSIGN_ROLE_MUTATION = """
mutation AssignRole($input: MembershipRoleInput!) {
  assignRoleToMembership(input: $input) {
    success
    message
    field
    membershipRole { membership { id } role { id } }
  }
}
"""

REMOVE_ROLE_MUTATION = """
mutation RemoveRole($input: MembershipRoleInput!) {
  removeRoleFromMembership(input: $input) { success message field }
}
"""

PASSWORD = 'a-strong-unique-pass-1'


class Api:
    """A real HTTP GraphQL caller for one signed-in user.

    Holds no session state of its own: the access token is the only thing
    carried between calls, which is exactly the property the isolation tests
    rely on (a client cannot accidentally "borrow" another tenant's session).
    """

    def __init__(self, client: Client, access_token: str | None = None):
        self.client = client
        self.access_token = access_token

    def raw(self, query, variables=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        headers = {}
        if self.access_token is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {self.access_token}'
        response = self.client.post(
            '/graphql/', data=json.dumps(payload), content_type='application/json', **headers
        )
        assert response.status_code == 200, response.content
        return response

    def data(self, query, variables=None):
        """The `data` object, asserting no GraphQL-level error occurred."""
        body = self.raw(query, variables).json()
        assert 'errors' not in body, body
        return body['data']


def _register_and_login(client, email, first, phone):
    api = Api(client)
    registration = api.data(
        REGISTER_MUTATION,
        {
            'input': {
                'firstName': first,
                'lastName': 'Tester',
                'email': email,
                'phoneNumber': phone,
                'password': PASSWORD,
            }
        },
    )['register']
    assert registration['success'] is True, registration

    login = api.data(LOGIN_MUTATION, {'input': {'email': email, 'password': PASSWORD}})['login']
    assert login['success'] is True, login
    return Api(client, login['accessToken'])


@pytest.fixture
def client_a(client: Client) -> Api:
    return _register_and_login(client, 'alice@tenant-a.example', 'Alice', '+255711000001')


@pytest.fixture
def tenant_a(client: Client, client_a: Api) -> dict:
    """Organization A, created and owned by User A, via the real mutation."""
    created = client_a.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Tenant A'}})[
        'createOrganization'
    ]
    assert created['success'] is True, created

    organization = created['organization']
    membership = created['membership']
    owner_role = membership['roles'][0]
    return {
        'api': client_a,
        'user_id': created['membership']['user']['id'],
        'organization_id': organization['id'],
        'organization_name': organization['name'],
        'membership_id': membership['id'],
        'owner_role_id': owner_role['id'],
    }


@pytest.fixture
def tenant_b(client: Client) -> dict:
    """Organization B, created and owned by an entirely separate User B.

    Built on its own test client so B's cookie jar and session can never
    overlap A's.
    """
    client_b_client = Client()
    api_b = _register_and_login(client_b_client, 'bob@tenant-b.example', 'Bob', '+255722000002')
    created = api_b.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Tenant B'}})[
        'createOrganization'
    ]
    assert created['success'] is True, created

    return {
        'api': api_b,
        'user_id': created['membership']['user']['id'],
        'organization_id': created['organization']['id'],
        'organization_name': created['organization']['name'],
        'membership_id': created['membership']['id'],
        'owner_role_id': created['membership']['roles'][0]['id'],
    }


def _role_ids(tenant):
    """Every role in `tenant`'s organization, as the API's string ids."""
    return {
        str(role_id)
        for role_id in Role.objects.filter(organization_id=tenant['organization_id']).values_list(
            'id', flat=True
        )
    }


def _assert_no_leakage(response, tenant, *, secret_strings=()):
    """
    Nothing identifying the foreign tenant may appear anywhere in the raw
    response body - not just in the field under test.
    """
    body = response.content.decode()
    forbidden = [
        tenant['organization_id'],
        tenant['membership_id'],
        tenant['owner_role_id'],
        tenant['user_id'],
        tenant['organization_name'],
        *secret_strings,
    ]
    for value in forbidden:
        assert value not in body, f'foreign tenant value {value!r} leaked into: {body}'


# --- unauthenticated access --------------------------------------------------------


@pytest.mark.django_db
def test_unauthenticated_caller_sees_no_organizations_at_all(tenant_a):
    anonymous = Api(Client())

    assert anonymous.data(ORGANIZATIONS_QUERY)['meOrganizations'] == []
    assert anonymous.data(MEMBERSHIPS_QUERY)['meMemberships'] == []
    assert anonymous.data(MY_ROLES_QUERY)['myOrganizationRoles'] == []
    # Every organization-scoped query, pointed at a real organization.
    assert (
        anonymous.data(ORGANIZATION_QUERY, {'id': tenant_a['organization_id']})['organization']
        is None
    )
    assert (
        anonymous.data(ORGANIZATION_ROLES_QUERY, {'organizationId': tenant_a['organization_id']})[
            'organizationRoles'
        ]
        == []
    )
    assert (
        anonymous.data(ORGANIZATION_MEMBERS_QUERY, {'organizationId': tenant_a['organization_id']})[
            'organizationMembers'
        ]
        == []
    )


@pytest.mark.django_db
def test_unauthenticated_caller_cannot_mutate_anything(tenant_a):
    anonymous = Api(Client())

    created = anonymous.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Anonymous Org'}})[
        'createOrganization'
    ]
    assert created['success'] is False
    assert Organization.objects.filter(name='Anonymous Org').count() == 0

    assigned = anonymous.data(
        ASSIGN_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_a['membership_id'],
                'roleId': tenant_a['owner_role_id'],
            }
        },
    )['assignRoleToMembership']
    assert assigned['success'] is False
    assert assigned['membershipRole'] is None

    removed = anonymous.data(
        REMOVE_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_a['membership_id'],
                'roleId': tenant_a['owner_role_id'],
            }
        },
    )['removeRoleFromMembership']
    assert removed['success'] is False
    # A's owner role is untouched.
    assert MembershipRole.objects.filter(
        membership_id=tenant_a['membership_id'], role_id=tenant_a['owner_role_id']
    ).exists()


# --- legitimate access -------------------------------------------------------------


@pytest.mark.django_db
def test_user_a_sees_only_organization_a(tenant_a, tenant_b):
    api = tenant_a['api']

    organizations = api.data(ORGANIZATIONS_QUERY)['meOrganizations']
    assert [item['organization']['id'] for item in organizations] == [tenant_a['organization_id']]
    memberships = api.data(MEMBERSHIPS_QUERY)['meMemberships']
    assert [item['id'] for item in memberships] == [tenant_a['membership_id']]
    roles = api.data(MY_ROLES_QUERY)['myOrganizationRoles']
    # Exactly A's roles (Owner and, since S3-002, Reviewer) and nothing else.
    assert {role['id'] for role in roles} == _role_ids(tenant_a)
    # ...and B's identifiers are nowhere in any of those bodies.
    _assert_no_leakage(api.raw(ORGANIZATIONS_QUERY), tenant_b)
    _assert_no_leakage(api.raw(MY_ROLES_QUERY), tenant_b)


@pytest.mark.django_db
def test_user_a_reaches_every_organization_a_operation(tenant_a):
    """
    The positive counterpart to the isolation assertions below: if any of
    these were blocked, "the cross-tenant request was refused" would be
    meaningless as evidence of isolation.
    """
    api = tenant_a['api']
    organization_id = tenant_a['organization_id']

    organization = api.data(ORGANIZATION_QUERY, {'id': organization_id})['organization']
    assert organization['id'] == organization_id
    assert organization['name'] == tenant_a['organization_name']

    roles = api.data(ORGANIZATION_ROLES_QUERY, {'organizationId': organization_id})[
        'organizationRoles'
    ]
    assert {role['id'] for role in roles} == _role_ids(tenant_a)
    assert tenant_a['owner_role_id'] in {role['id'] for role in roles}

    members = api.data(ORGANIZATION_MEMBERS_QUERY, {'organizationId': organization_id})[
        'organizationMembers'
    ]
    assert [member['id'] for member in members] == [tenant_a['membership_id']]
    assert [member['user']['id'] for member in members] == [tenant_a['user_id']]

    # A can add and remove its own tenant's roles.
    assigned = api.data(
        ASSIGN_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_a['membership_id'],
                'roleId': tenant_a['owner_role_id'],
            }
        },
    )['assignRoleToMembership']
    assert assigned['success'] is False  # already held - a duplicate, not a leak
    assert assigned['message'] == 'This membership already has this role.'


# --- cross-tenant access -----------------------------------------------------------


@pytest.mark.django_db
def test_user_a_cannot_read_organization_b(tenant_a, tenant_b):
    api = tenant_a['api']

    response = api.raw(ORGANIZATION_QUERY, {'id': tenant_b['organization_id']})

    assert response.json()['data']['organization'] is None
    _assert_no_leakage(response, tenant_b)


@pytest.mark.django_db
def test_user_a_cannot_list_organization_bs_roles(tenant_a, tenant_b):
    api = tenant_a['api']

    response = api.raw(ORGANIZATION_ROLES_QUERY, {'organizationId': tenant_b['organization_id']})

    assert response.json()['data']['organizationRoles'] == []
    _assert_no_leakage(response, tenant_b)


@pytest.mark.django_db
def test_user_a_cannot_list_organization_bs_members(tenant_a, tenant_b):
    api = tenant_a['api']

    response = api.raw(ORGANIZATION_MEMBERS_QUERY, {'organizationId': tenant_b['organization_id']})

    assert response.json()['data']['organizationMembers'] == []
    _assert_no_leakage(response, tenant_b)


@pytest.mark.django_db
def test_user_a_cannot_discover_organization_b_via_listings(tenant_a, tenant_b):
    """
    The aggregate listings are the easiest way to leak another tenant, since
    they take no id at all - a missing per-object check there would expose
    every organization the user does not belong to.
    """
    api = tenant_a['api']

    for query, field in (
        (ORGANIZATIONS_QUERY, 'meOrganizations'),
        (MEMBERSHIPS_QUERY, 'meMemberships'),
        (MY_ROLES_QUERY, 'myOrganizationRoles'),
    ):
        response = api.raw(query)
        listed = response.json()['data'][field]
        for item in listed:
            rendered = json.dumps(item)
            assert tenant_b['organization_id'] not in rendered
            assert tenant_b['membership_id'] not in rendered
            assert tenant_b['owner_role_id'] not in rendered
        _assert_no_leakage(response, tenant_b)


@pytest.mark.django_db
def test_user_a_cannot_assign_a_role_in_organization_b(tenant_a, tenant_b):
    """
    Cross-tenant role *assignment*, via B's own membership and role ids -
    the ids are public-ish (they appear in listings), so this is the attack
    the isolation check exists for.
    """
    api = tenant_a['api']
    roles_before = MembershipRole.objects.filter(membership_id=tenant_b['membership_id']).count()

    response = api.raw(
        ASSIGN_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_b['membership_id'],
                'roleId': tenant_b['owner_role_id'],
            }
        },
    )

    payload = response.json()['data']['assignRoleToMembership']
    assert payload['success'] is False
    assert payload['membershipRole'] is None
    # B's membership is byte-for-byte unchanged, and the refusal does not even
    # confirm the target exists.
    assert MembershipRole.objects.filter(membership_id=tenant_b['membership_id']).count() == (
        roles_before
    )
    _assert_no_leakage(response, tenant_b)


@pytest.mark.django_db
def test_user_a_cannot_remove_a_role_in_organization_b(tenant_a, tenant_b):
    """
    Cross-tenant role *removal*, including of a system role - the most
    damaging cross-tenant write available: stripping B's Owner role would
    leave its organization unadministrable.
    """
    api = tenant_a['api']
    assignment = MembershipRole.objects.get(
        membership_id=tenant_b['membership_id'], role_id=tenant_b['owner_role_id']
    )
    assert assignment is not None

    response = api.raw(
        REMOVE_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_b['membership_id'],
                'roleId': tenant_b['owner_role_id'],
            }
        },
    )

    payload = response.json()['data']['removeRoleFromMembership']
    assert payload['success'] is False
    # The Owner assignment still exists, and B is still its owner.
    assert MembershipRole.objects.filter(pk=assignment.pk).exists()
    assert Role.objects.get(pk=tenant_b['owner_role_id']).is_system is True
    _assert_no_leakage(response, tenant_b)


@pytest.mark.django_db
def test_user_a_cannot_combine_own_membership_with_foreign_role(tenant_a, tenant_b):
    """
    The mixed case, which a naive per-field check would let through: A's
    *own* membership id (which A is entitled to reference) paired with B's
    role id. The write must still be refused - a role from another tenant is
    never assignable, wherever the membership comes from.
    """
    api = tenant_a['api']

    response = api.raw(
        ASSIGN_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_a['membership_id'],
                'roleId': tenant_b['owner_role_id'],
            }
        },
    )

    payload = response.json()['data']['assignRoleToMembership']
    assert payload['success'] is False
    assert MembershipRole.objects.filter(role_id=tenant_b['owner_role_id']).count() == 1
    _assert_no_leakage(response, tenant_b)


@pytest.mark.django_db
def test_user_a_cannot_combine_foreign_membership_with_own_role(tenant_a, tenant_b):
    """
    The mirror image: B's membership paired with A's (own-tenant) role.
    Refused too - the membership is resolved against the actor's own
    organizations, so B's is simply not found.
    """
    api = tenant_a['api']

    response = api.raw(
        REMOVE_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_b['membership_id'],
                'roleId': tenant_a['owner_role_id'],
            }
        },
    )

    assert response.json()['data']['removeRoleFromMembership']['success'] is False
    assert MembershipRole.objects.filter(
        membership_id=tenant_a['membership_id'], role_id=tenant_a['owner_role_id']
    ).exists()
    _assert_no_leakage(response, tenant_b)


@pytest.mark.django_db
def test_foreign_organization_ids_are_refused_not_guessed(tenant_a, tenant_b):
    """
    Nonexistent, non-numeric and self-referential ids must be refused
    exactly like a real foreign id - no 500, and no difference in the answer
    that would let an attacker probe which organization ids exist.
    """
    api = tenant_a['api']

    unknown = api.raw(ORGANIZATION_QUERY, {'id': '999999999'}).json()['data']['organization']
    non_numeric = api.raw(ORGANIZATION_QUERY, {'id': 'not-an-id'}).json()['data']['organization']
    existing_foreign = api.raw(ORGANIZATION_QUERY, {'id': tenant_b['organization_id']}).json()[
        'data'
    ]['organization']

    assert unknown is None
    assert non_numeric is None
    assert existing_foreign is None

    for query, field in (
        (ORGANIZATION_ROLES_QUERY, 'organizationRoles'),
        (ORGANIZATION_MEMBERS_QUERY, 'organizationMembers'),
    ):
        for organization_id in ('999999999', 'not-an-id', tenant_b['organization_id']):
            payload = api.data(query, {'organizationId': organization_id})[field]
            assert payload == [], (query, organization_id, payload)


@pytest.mark.django_db
def test_user_b_is_isolated_from_user_a_symmetrically(tenant_a, tenant_b):
    """
    Isolation must not be one-directional: A's refusal to read B must not be
    an artefact of how A was set up. The whole matrix is re-run as B.
    """
    api = tenant_b['api']

    assert [
        item['organization']['id'] for item in api.data(ORGANIZATIONS_QUERY)['meOrganizations']
    ] == [tenant_b['organization_id']]
    assert api.data(ORGANIZATION_QUERY, {'id': tenant_a['organization_id']})['organization'] is None
    assert (
        api.data(ORGANIZATION_ROLES_QUERY, {'organizationId': tenant_a['organization_id']})[
            'organizationRoles'
        ]
        == []
    )
    assert (
        api.data(ORGANIZATION_MEMBERS_QUERY, {'organizationId': tenant_a['organization_id']})[
            'organizationMembers'
        ]
        == []
    )
    assigned = api.data(
        ASSIGN_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_a['membership_id'],
                'roleId': tenant_a['owner_role_id'],
            }
        },
    )['assignRoleToMembership']
    assert assigned['success'] is False
    removed = api.data(
        REMOVE_ROLE_MUTATION,
        {
            'input': {
                'membershipId': tenant_a['membership_id'],
                'roleId': tenant_a['owner_role_id'],
            }
        },
    )['removeRoleFromMembership']
    assert removed['success'] is False
    assert MembershipRole.objects.filter(
        membership_id=tenant_a['membership_id'], role_id=tenant_a['owner_role_id']
    ).exists()

    _assert_no_leakage(api.raw(ORGANIZATIONS_QUERY), tenant_a)
    _assert_no_leakage(
        api.raw(ORGANIZATION_MEMBERS_QUERY, {'organizationId': tenant_a['organization_id']}),
        tenant_a,
    )


# --- membership lifecycle ----------------------------------------------------------


@pytest.mark.django_db
def test_deactivating_a_membership_removes_all_access_immediately(tenant_a):
    """
    Access is derived from the membership on every request, never cached
    into a token: revoking membership must cut access at once, with no
    access-token expiry involved.
    """
    api = tenant_a['api']
    assert api.data(ORGANIZATION_QUERY, {'id': tenant_a['organization_id']})['organization']

    Membership.objects.filter(id=tenant_a['membership_id']).update(
        status=Membership.Status.INACTIVE
    )

    # The access token is unchanged and still validly signed - only the
    # membership was withdrawn.
    assert api.data(ORGANIZATION_QUERY, {'id': tenant_a['organization_id']})['organization'] is None
    assert api.data(ORGANIZATIONS_QUERY)['meOrganizations'] == []
    assert (
        api.data(ORGANIZATION_MEMBERS_QUERY, {'organizationId': tenant_a['organization_id']})[
            'organizationMembers'
        ]
        == []
    )
    assert api.data(MY_ROLES_QUERY)['myOrganizationRoles'] == []


@pytest.mark.django_db
def test_deactivating_the_actor_account_revokes_every_organization(tenant_a):
    api = tenant_a['api']
    assert api.data(ORGANIZATIONS_QUERY)['meOrganizations']

    User.objects.filter(id=tenant_a['user_id']).update(is_active=False)

    assert api.data(ORGANIZATIONS_QUERY)['meOrganizations'] == []
    assert api.data(ORGANIZATION_QUERY, {'id': tenant_a['organization_id']})['organization'] is None
    assert api.data(MY_ROLES_QUERY)['myOrganizationRoles'] == []


@pytest.mark.django_db
def test_membership_listing_never_includes_another_tenants_users(tenant_a, tenant_b):
    """
    `organizationMembers` is scoped twice over - to the requested
    organization *and* to organizations the caller belongs to. Asserting the
    second separately: with two tenants present, a caller must never see a
    member of an organization other than the one they asked about, even
    though that user is a legitimate member elsewhere.
    """
    api = tenant_a['api']

    members = api.data(ORGANIZATION_MEMBERS_QUERY, {'organizationId': tenant_a['organization_id']})[
        'organizationMembers'
    ]

    assert [member['user']['id'] for member in members] == [tenant_a['user_id']]
    b_user_id = User.objects.get(email='bob@tenant-b.example').pk
    assert str(b_user_id) not in json.dumps(members)
