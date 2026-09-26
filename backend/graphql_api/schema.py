"""
Root GraphQL schema for the Automation Platform API.

This module holds only foundation/infrastructure types, plus the merge
point for business-domain schemas. Each domain (identity, organizations,
ideas, ...) owns its models and logic in its own Django app and exposes a
Query/Mutation class of its own (e.g. `identity.schema.Mutation`); this
module imports and inherits from those rather than the other way around,
so the dependency runs domain -> GraphQL adapter, never the reverse. There
is deliberately one `strawberry.Schema` instance for the whole API, not one
per domain.
"""

import django
import strawberry

from ideas.schema import Mutation as IdeasMutation
from ideas.schema import Query as IdeasQuery
from identity.schema import Mutation as IdentityMutation
from identity.schema import Query as IdentityQuery
from organizations.schema import Mutation as OrganizationsMutation
from organizations.schema import Query as OrganizationsQuery


@strawberry.type
class ApiStatus:
    """Foundation type proving the GraphQL layer is wired up end to end."""

    status: str
    version: str
    django_version: str


@strawberry.type
class Query(IdentityQuery, OrganizationsQuery, IdeasQuery):
    @strawberry.field(
        description=(
            'Infrastructure check: proves the GraphQL endpoint is reachable and resolving.'
        )
    )
    def api_status(self) -> ApiStatus:
        return ApiStatus(
            status='ok',
            version='0.1.0',
            django_version=django.get_version(),
        )


@strawberry.type
class Mutation(IdentityMutation, OrganizationsMutation, IdeasMutation):
    @strawberry.mutation(
        description=('Infrastructure check: echoes the input to prove the mutation root resolves.')
    )
    def ping(self, message: str) -> str:
        return message


schema = strawberry.Schema(query=Query, mutation=Mutation)
