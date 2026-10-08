import json

import pytest
from django.test import Client

from identity.models import User

REGISTER_MUTATION = """
mutation Register($input: RegisterInput!) {
  register(input: $input) {
    success
    message
    field
    user {
      id
      email
      firstName
      lastName
      phoneNumber
      isActive
      isVerified
    }
  }
}
"""


@pytest.fixture
def gql(client: Client):
    """POST a GraphQL operation to the real /graphql/ endpoint."""

    def post(query, variables=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        return client.post('/graphql/', data=json.dumps(payload), content_type='application/json')

    return post


def _variables(**overrides):
    fields = {
        'firstName': 'Ada',
        'lastName': 'Lovelace',
        'email': 'ada@example.com',
        'phoneNumber': '+255712345678',
        'password': 'a-strong-unique-pass-1',
    }
    fields.update(overrides)
    return {'input': fields}


@pytest.mark.django_db
def test_register_creates_a_real_postgres_backed_user(gql):
    response = gql(REGISTER_MUTATION, _variables())

    assert response.status_code == 200
    body = response.json()
    assert 'errors' not in body
    payload = body['data']['register']
    assert payload['success'] is True
    assert payload['user']['email'] == 'ada@example.com'
    assert User.objects.filter(email='ada@example.com').exists()


@pytest.mark.django_db
def test_register_accepts_all_expected_input_fields(gql):
    response = gql(REGISTER_MUTATION, _variables())

    user = response.json()['data']['register']['user']
    assert user['firstName'] == 'Ada'
    assert user['lastName'] == 'Lovelace'
    assert user['email'] == 'ada@example.com'
    assert user['phoneNumber'] == '+255712345678'


@pytest.mark.django_db
def test_register_response_never_exposes_password_or_hash(gql):
    response = gql(REGISTER_MUTATION, _variables())

    raw_body = response.content.decode()
    body = response.json()

    # The schema doesn't expose a password field on UserType at all.
    assert 'password' not in body['data']['register']['user']
    # And the stored hash never appears anywhere in the raw response text.
    user = User.objects.get(email='ada@example.com')
    assert user.password not in raw_body


@pytest.mark.django_db
def test_confirm_password_and_terms_accepted_are_not_persisted(gql):
    gql(REGISTER_MUTATION, _variables())

    user = User.objects.get(email='ada@example.com')
    assert not hasattr(user, 'confirm_password')
    assert not hasattr(user, 'terms_accepted')


@pytest.mark.django_db
def test_duplicate_email_is_rejected_with_a_useful_error(gql):
    gql(REGISTER_MUTATION, _variables())

    response = gql(REGISTER_MUTATION, _variables(email='ADA@EXAMPLE.COM'))

    payload = response.json()['data']['register']
    assert payload['success'] is False
    assert payload['field'] == 'email'
    assert User.objects.filter(email='ada@example.com').count() == 1


@pytest.mark.django_db
def test_invalid_email_is_rejected(gql):
    response = gql(REGISTER_MUTATION, _variables(email='not-an-email'))

    payload = response.json()['data']['register']
    assert payload['success'] is False
    assert payload['field'] == 'email'


@pytest.mark.django_db
def test_weak_password_is_rejected(gql):
    response = gql(REGISTER_MUTATION, _variables(password='weak'))

    payload = response.json()['data']['register']
    assert payload['success'] is False
    assert payload['field'] == 'password'
    assert not User.objects.filter(email='ada@example.com').exists()


@pytest.mark.django_db
def test_invalid_phone_number_is_rejected(gql):
    response = gql(REGISTER_MUTATION, _variables(phoneNumber='12345'))

    payload = response.json()['data']['register']
    assert payload['success'] is False
    assert payload['field'] == 'phoneNumber'


@pytest.mark.django_db
@pytest.mark.parametrize(
    ('overrides', 'expected_field'),
    [
        ({'firstName': ''}, 'firstName'),
        ({'lastName': ''}, 'lastName'),
        ({'phoneNumber': ''}, 'phoneNumber'),
    ],
)
def test_field_errors_are_reported_using_graphql_camel_case_names(gql, overrides, expected_field):
    # RegistrationError.field is a Python snake_case name internally
    # (e.g. 'phone_number'); the client only ever sees RegisterInput's
    # camelCase field names, so the response must match those, not the
    # Python-side attribute name.
    response = gql(REGISTER_MUTATION, _variables(**overrides))

    payload = response.json()['data']['register']
    assert payload['success'] is False
    assert payload['field'] == expected_field


@pytest.mark.django_db
def test_missing_required_input_field_is_a_graphql_error(gql):
    query = 'mutation { register(input: {}) { success } }'

    response = gql(query)

    body = response.json()
    assert 'errors' in body
    assert 'data' not in body or body['data'] is None


@pytest.mark.django_db
def test_is_verified_starts_false_on_registration(gql):
    response = gql(REGISTER_MUTATION, _variables())

    payload = response.json()['data']['register']
    assert payload['user']['isVerified'] is False


@pytest.mark.django_db
def test_is_active_starts_true_on_registration(gql):
    response = gql(REGISTER_MUTATION, _variables())

    payload = response.json()['data']['register']
    assert payload['user']['isActive'] is True
