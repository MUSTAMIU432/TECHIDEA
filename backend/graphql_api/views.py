from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from strawberry.django.views import GraphQLView

from .context import AuthenticatedGraphQLContext
from .schema import schema


class _AuthenticatedGraphQLView(GraphQLView):
    """
    The only change from Strawberry's own `GraphQLView`: the request
    context is `AuthenticatedGraphQLContext` (adds `user`, cached) instead
    of the base `StrawberryDjangoContext` - see `graphql_api/context.py`.
    """

    def get_context(
        self, request: HttpRequest, response: HttpResponse
    ) -> AuthenticatedGraphQLContext:
        return AuthenticatedGraphQLContext(request=request, response=response)


# CSRF-exempt, revisited for Sprint 1 (S1-003): a cookie now flows through
# this endpoint (the HttpOnly refresh-token cookie - see identity/schema.py),
# so it's no longer true that there's "no cookie-based auth". It stays
# exempt anyway, deliberately: Django's CSRF middleware defends
# cookie-authenticated *form* submissions, which don't apply to a JSON-only
# GraphQL endpoint (a bare HTML <form> can't send `Content-Type:
# application/json`, so the classic CSRF vector doesn't exist here). The
# actual defenses against a cross-site request abusing the cookie are:
# CORS_ALLOWED_ORIGINS (config/settings/base.py) rejecting any origin not
# explicitly allow-listed - and, since the request body is JSON, the
# browser must run a preflight the disallowed origin cannot pass - and the
# refresh cookie's own `SameSite=Lax` (identity/schema.py), which keeps the
# browser from attaching it to a genuinely cross-site POST at all,
# independent of CORS. See docs/architecture.md for the full writeup.
graphql_view = csrf_exempt(
    _AuthenticatedGraphQLView.as_view(
        schema=schema,
        graphql_ide='graphiql' if settings.DEBUG else None,
    )
)
