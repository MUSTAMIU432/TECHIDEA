"""
Root GraphQL schema for the Automation Platform API.

This module holds only foundation/infrastructure types, plus the merge
point for business-domain schemas. Each domain (identity, organizations,
ideas, reviews, administration, ...) owns its models and logic in its own Django app and exposes a
Query/Mutation class of its own (e.g. `identity.schema.Mutation`); this
module imports and inherits from those rather than the other way around,
so the dependency runs domain -> GraphQL adapter, never the reverse. There
is deliberately one `strawberry.Schema` instance for the whole API, not one
per domain.
"""

import django
import strawberry

from administration.schema import Mutation as AdministrationMutation
from administration.schema import Query as AdministrationQuery
from automation.delivery_schema import Mutation as DeliveryMutation
from automation.delivery_schema import Query as DeliveryQuery
from automation.schema import Mutation as AutomationMutation
from automation.schema import Query as AutomationQuery
from ideas.schema import Mutation as IdeasMutation
from ideas.schema import Query as IdeasQuery
from identity.schema import Mutation as IdentityMutation
from identity.schema import Query as IdentityQuery
from invitations.schema import Mutation as InvitationsMutation
from invitations.schema import Query as InvitationsQuery
from messaging.schema import Mutation as MessagingMutation
from messaging.schema import Query as MessagingQuery
from notifications.schema import Mutation as NotificationsMutation
from notifications.schema import Query as NotificationsQuery
from organizations.schema import Mutation as OrganizationsMutation
from organizations.schema import Query as OrganizationsQuery
from reviews.proposal_schema import Mutation as ProposalMutation
from reviews.proposal_schema import Query as ProposalQuery
from reviews.schema import Mutation as ReviewsMutation
from reviews.schema import PlatformTrackMutation, PlatformTrackQuery
from reviews.schema import Query as ReviewsQuery
from reviews.team_schema import Mutation as ReviewTeamMutation
from reviews.team_schema import Query as ReviewTeamQuery
from teams.schema import Mutation as TeamsMutation
from teams.schema import Query as TeamsQuery


@strawberry.type
class ApiStatus:
    """Foundation type proving the GraphQL layer is wired up end to end."""

    status: str
    version: str
    django_version: str


@strawberry.type
class Query(
    IdentityQuery,
    OrganizationsQuery,
    TeamsQuery,
    IdeasQuery,
    ReviewsQuery,
    PlatformTrackQuery,
    ReviewTeamQuery,
    ProposalQuery,
    InvitationsQuery,
    NotificationsQuery,
    MessagingQuery,
    AutomationQuery,
    DeliveryQuery,
    AdministrationQuery,
):
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
class Mutation(
    IdentityMutation,
    OrganizationsMutation,
    TeamsMutation,
    IdeasMutation,
    ReviewsMutation,
    PlatformTrackMutation,
    ReviewTeamMutation,
    ProposalMutation,
    InvitationsMutation,
    NotificationsMutation,
    MessagingMutation,
    AutomationMutation,
    DeliveryMutation,
    AdministrationMutation,
):
    @strawberry.mutation(
        description=('Infrastructure check: echoes the input to prove the mutation root resolves.')
    )
    def ping(self, message: str) -> str:
        return message


schema = strawberry.Schema(query=Query, mutation=Mutation)
