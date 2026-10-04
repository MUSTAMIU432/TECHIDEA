"""
GraphQL request context (S1-005).

The one place a resolver gets the current authenticated user from - see
`identity.authentication.get_authenticated_user` for how it's actually
 type validation, then a fresh, `is_active`-filtered database lookup;
never a value the frontend can simply assert). Resolvers should read
`info.context.user` rather than calling that function themselves, so
there's exactly one mechanism authenticating a GraphQL request, not one
reinvented per resolver - see `identity/schema.py`'s `me` query for the
pattern later resolvers (S1-006 and beyond) should follow.
"""

from dataclasses import dataclass
from functools import cached_property

from strawberry.django.context import StrawberryDjangoContext

from identity.authentication import get_authenticated_user
from identity.models import User


@dataclass
class AuthenticatedGraphQLContext(StrawberryDjangoContext):
    """`StrawberryDjangoContext` (`request`, `response`) plus `user`.

    `user` is a `cached_property`, not a plain attribute computed eagerly
    in `__init__`: most operations (register, login, googleLogin,
    refreshToken, apiStatus, ping) don't need it at all, and a query that
    touches it from more than one place (unlikely today, increasingly
    likely once authorization checks land in S1-006+) shares one JWT
    decode and one database lookup for the whole request instead of one
    per access.
    """

    @cached_property
    def user(self) -> User | None:
        return get_authenticated_user(self.request)
