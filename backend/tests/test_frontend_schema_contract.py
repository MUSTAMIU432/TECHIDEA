"""
The GraphQL schema contract the frontend's documents depend on (S1-009).

`frontend/src/features/organizations/api/organizationApi.ts` selects a
specific set of field names, and there is no way for the Python suite to run
those documents - the two sides are separate processes in separate languages,
and the schema is generated at runtime from the Strawberry types rather than
read from a shared SDL file.

What *is* practical is to pin both ends of the contract from each side:

- `frontend/src/features/organizations/api/organizationApi.test.ts` asserts
  the request documents select exactly `FRONTEND_SELECTED_FIELDS` below.
- This file asserts the schema declares every one of those names, and that it
  declares exactly `SCHEMA_DECLARED_FIELDS` - which is a superset, because a
  client is free to fetch fewer fields than a type has.

A rename on either side therefore fails one of the two suites rather than
surfacing at runtime as a GraphQL `errors` array and an empty screen.

Both lists are deliberately duplicated rather than shared. A single source of
truth would still have to be kept in step with the generated schema, and a
test that reads its expectations from the thing it is testing checks nothing.

Everything here is read from the schema's SDL - the same text a client
receives - rather than from the Strawberry types' Python attributes, because
the names a client asks for are the SDL names. Strawberry applies its own
camelCase conversion to the `snake_case` Python fields on the way out, so
`created_at` is `createdAt` in the schema and asking for the snake_case name
is a query error.
"""

import pytest
from graphql import (
    FieldDefinitionNode,
    InputObjectTypeDefinitionNode,
    InputValueDefinitionNode,
    InterfaceTypeDefinitionNode,
    ListTypeNode,
    NonNullTypeNode,
    ObjectTypeDefinitionNode,
    parse,
)

from graphql_api.schema import schema as root_schema

# Type name -> the field names the frontend's documents actually select. Keep
# in step with CONTRACT in
# frontend/src/features/organizations/api/organizationApi.test.ts.
FRONTEND_SELECTED_FIELDS = {
    'OrganizationType': {
        'id',
        'name',
        'slug',
        'createdAt',
        'updatedAt',
    },
    'MembershipType': {
        'id',
        'status',
        'createdAt',
        'updatedAt',
        'organization',
        'roles',
        'user',
    },
    'UserType': {
        'id',
        'email',
        'firstName',
        'lastName',
    },
    'RoleType': {
        'id',
        'name',
        'slug',
        'description',
        'isSystem',
        'createdAt',
        'updatedAt',
        'organization',
        'permissions',
    },
    'PermissionType': {
        'code',
        'createdAt',
        'description',
        'id',
        'name',
        'updatedAt',
    },
    'OrganizationMembershipType': {
        'organization',
        'membership',
    },
    'CreateOrganizationPayload': {
        'success',
        'message',
        'field',
        'organization',
        'membership',
    },
}

# Type name -> every field the schema declares. The frontend selects a subset
# (see above); this is the whole surface, so a field added to a type has to be
# a deliberate act rather than something that appears and quietly changes what
# is reachable.
SCHEMA_DECLARED_FIELDS = {
    'OrganizationType': {
        'id',
        'name',
        'slug',
        'createdAt',
        'updatedAt',
    },
    'MembershipType': {
        'id',
        'status',
        'createdAt',
        'updatedAt',
        'organization',
        'roles',
        'user',
    },
    'UserType': {
        'id',
        'email',
        'firstName',
        'lastName',
        'phoneNumber',
        'isActive',
        'isVerified',
        'avatarUrl',
    },
    'RoleType': {
        'id',
        'name',
        'slug',
        'description',
        'isSystem',
        'createdAt',
        'updatedAt',
        'organization',
        'permissions',
    },
    'PermissionType': {
        'id',
        'code',
        'name',
        'description',
        'createdAt',
        'updatedAt',
    },
    'OrganizationMembershipType': {
        'organization',
        'membership',
    },
    'CreateOrganizationPayload': {
        'success',
        'message',
        'field',
        'organization',
        'membership',
    },
}

# Types that describe a person or an organization, as opposed to the result
# of authenticating as them. A credential has no business in any of these -
# and the frontend reaches them through a membership listing, so anything
# here is readable by one member about another.
DATA_TYPES = (
    'OrganizationType',
    'MembershipType',
    'UserType',
    'RoleType',
    'PermissionType',
    'OrganizationMembershipType',
    'CreateOrganizationPayload',
)

SCHEMA_SDL = str(root_schema)
_SDL_DEFINITIONS = parse(SCHEMA_SDL).definitions


@pytest.fixture(scope='module')
def types_by_name() -> dict:
    return {
        definition.name.value: definition
        for definition in _SDL_DEFINITIONS
        if isinstance(definition, (ObjectTypeDefinitionNode, InterfaceTypeDefinitionNode))
    }


def _field_names(definition) -> set[str]:
    return {
        selection.name.value
        for selection in definition.fields
        if isinstance(selection, FieldDefinitionNode)
    }


def _input_fields(type_name: str) -> dict[str, InputValueDefinitionNode]:
    definition = next(
        definition
        for definition in _SDL_DEFINITIONS
        if isinstance(definition, InputObjectTypeDefinitionNode)
        and definition.name.value == type_name
    )
    return {
        selection.name.value: selection
        for selection in definition.fields
        if isinstance(selection, InputValueDefinitionNode)
    }


@pytest.mark.parametrize('type_name', sorted(FRONTEND_SELECTED_FIELDS))
def test_the_schema_declares_every_field_the_frontend_selects(type_name, types_by_name):
    """
    The half that catches a backend-side rename: a field the frontend asks
    for that the schema no longer declares is a runtime query error, and the
    symptom is a silently empty screen.
    """
    assert type_name in types_by_name, f'{type_name} is missing from the schema'
    declared = _field_names(types_by_name[type_name])

    assert FRONTEND_SELECTED_FIELDS[type_name] <= declared


@pytest.mark.parametrize('type_name', sorted(SCHEMA_DECLARED_FIELDS))
def test_the_schema_declares_exactly_the_documented_fields(type_name, types_by_name):
    """
    The half that stops the contract rotting. The list is complete, not a
    sample: a field added to one of these types must be a deliberate act -
    added to the documents, or consciously left unfetched - rather than
    appearing in the schema and quietly changing what is reachable.
    """
    assert _field_names(types_by_name[type_name]) == SCHEMA_DECLARED_FIELDS[type_name]


def test_the_user_type_exposes_only_public_fields(types_by_name):
    """
    `UserType` is the one type in this contract that is also a security
    boundary: the frontend reaches it through a membership listing, so
    anything here is readable by one member about another. Asserted about
    the whole type's shape rather than about one query, because a field
    nobody currently selects is still one a future query could.
    """
    declared = _field_names(types_by_name['UserType'])

    assert declared == {
        'id',
        'email',
        'firstName',
        'lastName',
        'phoneNumber',
        'isActive',
        'isVerified',
        'avatarUrl',
    }
    forbidden = ('password', 'secret', 'token', 'credential', 'hash')
    for name in declared:
        assert not any(fragment in name.lower() for fragment in forbidden), name


@pytest.mark.parametrize('type_name', DATA_TYPES)
def test_no_data_type_declares_a_sensitive_field(type_name, types_by_name):
    """
    Schema-wide over the data types, rather than per type: whatever a resolver
    is asked for, no type describing a person or an organization may expose a
    credential, a hash, or an external identity's subject.
    """
    forbidden = ('password', 'secret', 'token', 'credential', 'hash', 'providersubject')

    for name in _field_names(types_by_name[type_name]):
        lowered = name.lower()
        assert not any(fragment in lowered for fragment in forbidden), f'{type_name}.{name}'


def test_the_auth_payload_carries_an_access_token_and_never_a_refresh_credential(
    types_by_name,
):
    """
    The one type where a token *is* the answer, so the rule above has to be
    stated rather than assumed: the access token is delivered in the response
    body (short-lived, held in memory only), and the refresh credential never
    is - it travels solely as the HttpOnly cookie
    (see identity/schema.py). A `refreshToken`/`refresh_token` field here
    would move that credential into a place JavaScript can read.
    """
    declared = _field_names(types_by_name['AuthPayload'])

    assert 'accessToken' in declared
    assert not any('refresh' in name.lower() for name in declared), declared


def test_the_auth_payload_exposes_only_what_the_client_needs(types_by_name):
    """
    The whole type, so a field added to the sign-in response cannot slip past
    the check above by being named something less obviously dangerous.
    """
    assert _field_names(types_by_name['AuthPayload']) == {
        'success',
        'message',
        'accessToken',
        'accessTokenExpiresAt',
        'user',
    }


@pytest.mark.parametrize(
    ('root_type', 'field_name'),
    [
        ('Query', 'meOrganizations'),
        ('Mutation', 'createOrganization'),
    ],
)
def test_the_root_fields_the_frontend_calls_exist(root_type, field_name, types_by_name):
    assert field_name in _field_names(types_by_name[root_type])


def test_the_organization_query_takes_the_organization_id_the_frontend_sends(
    types_by_name,
):
    """
    The frontend calls `organization(id: ID!)`. The argument name is part of
    the contract, and so is the fact that it is an *argument*: the id is what
    the server authorizes against, so a document that could pass an
    organization without naming it would be a different, weaker API.
    """
    field = next(
        selection
        for selection in types_by_name['Query'].fields
        if selection.name.value == 'organization'
    )

    assert [argument.name.value for argument in field.arguments] == ['id']
    assert isinstance(field.arguments[0].type, NonNullTypeNode)


def test_the_organization_input_takes_exactly_the_fields_the_frontend_sends():
    """
    `CreateOrganizationInput` is the one input the frontend supplies a
    variable object for. Its shape is the contract: `name` required, `slug`
    optional, and nothing else - an extra required field would make every
    request from the current frontend fail validation.
    """
    fields = _input_fields('CreateOrganizationInput')

    assert set(fields) == {'name', 'slug'}
    assert isinstance(fields['name'].type, NonNullTypeNode)
    assert not isinstance(fields['slug'].type, NonNullTypeNode)


def test_the_register_input_matches_what_the_sign_up_form_sends():
    """
    The other input the frontend supplies: `SignUpForm` builds a
    `RegisterInput` from its own fields. The full set is pinned because
    registration is the one unauthenticated write, and a new required field
    would break every sign-up rather than being caught by a partial fetch.
    """
    fields = _input_fields('RegisterInput')

    assert set(fields) == {'firstName', 'lastName', 'email', 'phoneNumber', 'password'}
    for name, selection in fields.items():
        assert isinstance(selection.type, NonNullTypeNode), name
        assert not isinstance(selection.type.type, ListTypeNode), name


def test_no_response_type_uses_a_nullable_list_of_nullable_elements():
    """
    A contract detail rather than a security one, but it bites: `[String]`
    rather than `[String!]` lets the backend put `null` in a list the
    frontend's TypeScript types promise contains no nulls, and the failure
    surfaces as a `Cannot read properties of null` far from its cause. Every
    list in the schema is declared non-null at both levels.
    """
    for definition in _SDL_DEFINITIONS:
        if not isinstance(definition, (ObjectTypeDefinitionNode, InterfaceTypeDefinitionNode)):
            continue
        for selection in definition.fields:
            if not isinstance(selection, FieldDefinitionNode):
                continue
            type_ = selection.type
            if isinstance(type_, NonNullTypeNode) and isinstance(type_.type, ListTypeNode):
                assert isinstance(type_.type.type, NonNullTypeNode), (
                    f'{definition.name.value}.{selection.name.value} is a list of nullable elements'
                )
