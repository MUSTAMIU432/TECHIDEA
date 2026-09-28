"""
The organization bootstrap authorization model (S1-009).

The one place in this project where the obvious authorization rule is the
wrong one. Every other organization-scoped operation is gated on a
permission held through a membership - but *creating* an organization cannot
be, and the reason is structural rather than a policy choice:

    authenticated user
      -> create organization
      -> creator membership (ACTIVE)
      -> Owner role for that organization
      -> Owner permissions (including organization.create)

At the moment `createOrganization` is called there is no organization, so
there is no membership, no role assignment, and no permission set capable of
authorizing it. Requiring `organization.create` would be circular: the grant
that would satisfy the check is created by the very operation being checked,
and the resulting "you are not allowed to create an organization" answer
would be unresolvable by any user, ever.

These tests pin that model in both directions, over real HTTP:

1. The bootstrap works - an authenticated user with no memberships anywhere
   can create an organization, and immediately holds every permission on
   their new organization including `organization.create`.
2. The bootstrap is the *only* thing that is not permission-checked, and it
   is not a loophole - it grants nothing beyond the one organization just
   created, and every other operation on that organization is still
   permission-checked and still tenant-isolated.
3. It is gated on being an active, authenticated user - the one requirement
   that is not circular.
"""

import json

import pytest
from django.test import Client

from identity.models import User
from organizations import authorization, services
from organizations.models import Membership, MembershipRole, Permission, Role

LOGIN_MUTATION = """
mutation Login($input: LoginInput!) {
  login(input: $input) { success accessToken }
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
      roles {
        id
        name
        slug
        isSystem
        permissions { code }
      }
    }
  }
}
"""

ORGANIZATIONS_QUERY = """
query MeOrganizations {
  meOrganizations { organization { id name } membership { id status } }
}
"""

ORGANIZATION_ROLES_QUERY = """
query OrganizationRoles($organizationId: ID!) {
  organizationRoles(organizationId: $organizationId) {
    id
    slug
    permissions { code }
  }
}
"""

ORGANIZATION_QUERY = """
query Organization($id: ID!) {
  organization(id: $id) { id name }
}
"""

PASSWORD = 'a-strong-unique-pass-1'
EMAIL = 'founder@example.com'


class Api:
    def __init__(self, client: Client, access_token: str | None = None):
        self.client = client
        self.access_token = access_token

    def data(self, query, variables=None):
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
        body = response.json()
        assert 'errors' not in body, body
        return body['data']


@pytest.fixture
def founder(client: Client) -> Api:
    """A signed-in user with no membership in any organization."""
    user = User.objects.create_user(
        email=EMAIL,
        first_name='Fay',
        last_name='Founder',
        phone_number='+255712345678',
        password=PASSWORD,
    )
    assert Membership.objects.filter(user=user).count() == 0

    api = Api(client)
    login = api.data(LOGIN_MUTATION, {'input': {'email': EMAIL, 'password': PASSWORD}})['login']
    assert login['success'] is True
    return Api(client, login['accessToken'])


@pytest.fixture
def bootstrapped(founder: Api) -> dict:
    created = founder.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'New Co'}})[
        'createOrganization'
    ]
    assert created['success'] is True, created
    return {
        'api': founder,
        'organization_id': created['organization']['id'],
        'membership_id': created['membership']['id'],
        'owner_role': created['membership']['roles'][0],
    }


# --- the bootstrap itself -----------------------------------------------------------


@pytest.mark.django_db
def test_a_user_with_no_membership_can_bootstrap_an_organization(founder: Api):
    """
    The case a permission gate would have made impossible. `founder` is
    asserted to hold no membership anywhere by the fixture, so nothing
    whatsoever authorizes this call except being an active authenticated
    user.
    """
    assert Membership.objects.filter(user__email=EMAIL).count() == 0

    created = founder.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'New Co'}})[
        'createOrganization'
    ]

    assert created['success'] is True
    assert created['message'] == 'Organization created successfully.'
    assert created['field'] is None
    assert created['organization']['slug'] == 'new-co'
    # The creator's membership exists, is active, and belongs to them.
    assert created['membership']['status'] == 'active'
    assert created['membership']['user']['email'] == EMAIL


@pytest.mark.django_db
def test_the_bootstrap_grants_the_owner_role_and_its_full_permission_set(
    bootstrapped: dict,
):
    """
    Step four of the bootstrap: the creator ends up holding Owner with every
    permission the platform defines, *including* `organization.create` - the
    very permission that could not have been required to get here.
    """
    owner_role = bootstrapped['owner_role']

    assert owner_role['slug'] == 'owner'
    assert owner_role['isSystem'] is True
    granted = {permission['code'] for permission in owner_role['permissions']}
    assert granted == {code for code, _, _ in services.PERMISSION_DEFINITIONS}
    assert authorization.ORGANIZATION_CREATE in granted


@pytest.mark.django_db
def test_the_creator_can_act_on_the_organization_they_just_created(bootstrapped: dict):
    """
    The bootstrap is not cosmetic: the permissions it grants are real, so
    every subsequent operation on the new organization works immediately.
    """
    api = bootstrapped['api']

    assert api.data(ORGANIZATIONS_QUERY)['meOrganizations'] == [
        {
            'organization': {
                'id': bootstrapped['organization_id'],
                'name': 'New Co',
            },
            'membership': {'id': bootstrapped['membership_id'], 'status': 'active'},
        }
    ]
    assert (
        api.data(ORGANIZATION_QUERY, {'id': bootstrapped['organization_id']})['organization']
        is not None
    )
    roles = api.data(ORGANIZATION_ROLES_QUERY, {'organizationId': bootstrapped['organization_id']})[
        'organizationRoles'
    ]
    # Bootstrap provisions the Owner role and (S3-002) the Reviewer role.
    assert [role['slug'] for role in roles] == ['owner', 'reviewer']


@pytest.mark.django_db
def test_the_owner_role_carries_organization_create_but_nothing_gates_on_it(
    bootstrapped: dict, founder: Api
):
    """
    The asymmetry, stated as a test so it cannot be "tidied up" by mistake:
    the permission exists and is granted to the Owner role (so the role
    describes the organization surface completely), and nothing anywhere
    consults it to decide whether a user may create an organization.
    """
    create_permission = Permission.objects.get(code=authorization.ORGANIZATION_CREATE)
    assert Role.objects.filter(
        role_permissions__permission=create_permission, slug='owner'
    ).exists()

    # The general gate, asked the question it would have to answer for this
    # operation, answers "no membership" - and would answer that for the
    # creator of a brand-new organization too, because the membership does
    # not exist yet. That is the circularity, demonstrated.
    founder = User.objects.get(email=EMAIL)
    with pytest.raises(authorization.AuthorizationError) as exc_info:
        authorization.require_permission(founder, '1', authorization.ORGANIZATION_CREATE)
    assert exc_info.value.reason in ('membership_required', 'forbidden')


# --- what the bootstrap is not ------------------------------------------------------


@pytest.mark.django_db
def test_an_unauthenticated_caller_cannot_bootstrap(client: Client):
    """
    The one requirement that is not circular, and it is genuinely enforced.
    """
    anonymous = Api(Client())

    created = anonymous.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Sneaky'}})[
        'createOrganization'
    ]

    assert created['success'] is False
    assert created['message'] == 'You must be signed in to create an organization.'
    assert created['organization'] is None
    assert created['membership'] is None


@pytest.mark.django_db
def test_a_deactivated_account_cannot_bootstrap(founder: Api):
    """
    `_require_active_user` is the whole gate, so it has to be the *real* one:
    an access token signed before deactivation must not be able to create an
    organization, because `info.context.user` is re-resolved from the token
    and the active flag on every request.
    """
    User.objects.filter(email=EMAIL).update(is_active=False)

    created = founder.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Ghost Co'}})[
        'createOrganization'
    ]

    assert created['success'] is False
    assert created['organization'] is None
    assert Membership.objects.filter(organization__name='Ghost Co').count() == 0


@pytest.mark.django_db
def test_bootstrapping_grants_nothing_beyond_the_organization_just_created(
    founder: Api, client: Client
):
    """
    "Authentication only" must not become "no authorization at all". Creating
    one organization grants the creator authority over that organization and
    nothing else - not another organization, not somebody else's, and no
    role anywhere.
    """
    first = founder.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Mine'}})[
        'createOrganization'
    ]
    second = founder.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Also Mine'}})[
        'createOrganization'
    ]
    assert first['success'] is True
    assert second['success'] is True

    # Two organizations, one membership and one Owner role in each, and no
    # role outside the two organizations just created.
    assert Membership.objects.filter(user__email=EMAIL).count() == 2
    assert Role.objects.filter(role_permissions__isnull=True).count() == 0
    assert set(
        MembershipRole.objects.filter(membership__user__email=EMAIL)
        .values_list('role__slug', flat=True)
        .distinct()
    ) == {'owner'}

    # And a different user gets nothing from someone else's bootstrap.
    other = User.objects.create_user(
        email='stranger@example.com',
        first_name='Strange',
        last_name='Rer',
        phone_number='+255700000999',
        password=PASSWORD,
    )
    assert Membership.objects.filter(user=other).count() == 0
    assert other.memberships.count() == 0


@pytest.mark.django_db
def test_a_second_bootstrap_does_not_inherit_the_first_organizations_authority(
    founder: Api,
):
    """
    Each organization gets its own Owner role and its own permission grants;
    authority over one is not authority over the other. (The roles are
    per-organization rows, so this holds by construction - pinned because a
    future "share the Owner role" optimisation would quietly break it.)
    """
    first = founder.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'One'}})[
        'createOrganization'
    ]
    second = founder.data(CREATE_ORGANIZATION_MUTATION, {'input': {'name': 'Two'}})[
        'createOrganization'
    ]

    first_role_id = first['membership']['roles'][0]['id']
    second_role_id = second['membership']['roles'][0]['id']

    assert first_role_id != second_role_id
    assert str(Role.objects.get(id=first_role_id).organization_id) == first['organization']['id']
    assert str(Role.objects.get(id=second_role_id).organization_id) == second['organization']['id']


@pytest.mark.django_db
def test_creating_an_organization_does_not_weaken_any_other_tenant(bootstrapped: dict):
    """
    A user who holds the Owner role in one organization still cannot reach
    another, so having bootstrapped (and therefore been granted
    `organization.create` and every other permission) grants no additional
    reach.
    """
    api = bootstrapped['api']
    outsider = User.objects.create_user(
        email='outsider@example.com',
        first_name='Out',
        last_name='Sider',
        phone_number='+255700000888',
        password=PASSWORD,
    )
    foreign = services.create_organization_for_user(
        outsider, services.CreateOrganizationInput(name='Theirs')
    )

    assert (
        api.data(ORGANIZATION_QUERY, {'id': str(foreign.organization.pk)})['organization'] is None
    )
    assert (
        api.data(ORGANIZATION_ROLES_QUERY, {'organizationId': str(foreign.organization.pk)})[
            'organizationRoles'
        ]
        == []
    )
