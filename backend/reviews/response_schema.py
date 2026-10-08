"""
GraphQL for the owner's responses to requests for changes (`reviews.change_responses`).

Refusals are `success: false` payloads with the field they name, like every other domain.
"""

import strawberry

from reviews import change_responses


@strawberry.type(description="The owner's answer to a request for changes.")
class ChangeResponseType:
    id: strawberry.ID
    message: str
    author_name: str
    created_at: str
    review_round: int
    review_scope: str = strawberry.field(description='`platform` or `organization`.')

    @staticmethod
    def from_view(view: change_responses.ResponseView) -> 'ChangeResponseType':
        response = view.response
        return ChangeResponseType(
            id=strawberry.ID(str(response.pk)),
            message=response.message,
            author_name=response.author.get_full_name() or response.author.email,
            created_at=response.created_at.isoformat(),
            review_round=view.review.round,
            review_scope=view.review.scope,
        )


@strawberry.type
class ChangeResponsePayload:
    success: bool
    message: str
    field: str | None = None
    idea_status: str | None = None


@strawberry.type
class Query:
    @strawberry.field(
        description="An idea's responses to requests for changes, newest first: for its "
        'author and for the reviewers of the track that asked. Empty for anybody else.'
    )
    def change_responses(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> list[ChangeResponseType]:
        return [
            ChangeResponseType.from_view(view)
            for view in change_responses.responses_for(info.context.user, idea_id)
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description='Answer a request for changes - what was changed, in your words - and '
        'resubmit the idea for the next round, in one step.'
    )
    def respond_to_changes(
        self, info: strawberry.Info, idea_id: strawberry.ID, message: str
    ) -> ChangeResponsePayload:
        try:
            _, idea = change_responses.respond(info.context.user, idea_id, message)
        except change_responses.ChangeResponseError as exc:
            return ChangeResponsePayload(success=False, message=exc.message, field=exc.field)
        return ChangeResponsePayload(
            success=True,
            message='Your response was sent and the idea is back with the reviewers.',
            idea_status=idea.status.upper(),
        )
