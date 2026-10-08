"""
Tests for `graphql_api/context.py`'s `AuthenticatedGraphQLContext` -
the one mechanism a GraphQL resolver uses to obtain the authenticated user
(see `identity/schema.py`'s `me` query for the intended usage pattern).
`identity/tests/test_authentication_schema.py` covers the end-to-end
behavior through the real `/graphql/` endpoint; these tests are scoped to
the context object itself, in isolation from any particular resolver.
"""

from unittest.mock import patch

import pytest
from django.test import RequestFactory

from graphql_api.context import AuthenticatedGraphQLContext
from identity.models import User
from identity.tokens import issue_access_token

VALID_PASSWORD = 'a-strong-unique-pass-1'


def _make_user(**overrides):
    fields = {
        'email': 'ada@example.com',
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    password = fields.pop('password', VALID_PASSWORD)
    return User.objects.create_user(password=password, **fields)


def _context_with_auth_header(header_value):
    request = RequestFactory().post('/graphql/')
    if header_value is not None:
        request.META['HTTP_AUTHORIZATION'] = header_value
    return AuthenticatedGraphQLContext(request=request, response=None)


@pytest.mark.django_db
class TestAuthenticatedGraphQLContext:
    def test_user_resolves_the_authenticated_user_from_a_valid_token(self):
        user = _make_user()
        token, _ = issue_access_token(user.pk)

        context = _context_with_auth_header(f'Bearer {token}')

        assert context.user is not None
        assert context.user.pk == user.pk

    def test_user_is_none_for_a_missing_header(self):
        context = _context_with_auth_header(None)

        assert context.user is None

    def test_user_is_none_for_an_invalid_token(self):
        context = _context_with_auth_header('Bearer not-a-real-token')

        assert context.user is None

    def test_user_is_none_for_an_inactive_users_token(self):
        user = _make_user(is_active=False)
        token, _ = issue_access_token(user.pk)

        context = _context_with_auth_header(f'Bearer {token}')

        assert context.user is None

    def test_user_is_cached_per_context_not_recomputed_on_every_access(self):
        # A single query touching `info.context.user` from more than one
        # resolver must not decode the JWT or hit the database again for
        # each access.
        user = _make_user()
        token, _ = issue_access_token(user.pk)
        context = _context_with_auth_header(f'Bearer {token}')

        with patch(
            'graphql_api.context.get_authenticated_user', wraps=lambda request: user
        ) as mocked:
            first_access = context.user
            second_access = context.user

        assert first_access is user
        assert second_access is user
        mocked.assert_called_once()
