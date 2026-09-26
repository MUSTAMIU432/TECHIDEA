"""
The Ideas GraphQL adapter (S2-002).

A thin translation layer, in the project's established shape: types with a
`from_model` static method, `@strawberry.input` dataclasses, payloads carrying
`(success, message, field)`, and resolvers that do nothing but move data
between the schema and `ideas/services.py` / `ideas/selectors.py`. The rules
live behind those two modules; nothing here decides anything.

The dependency direction is the one the rest of the API uses - this module
imports the domain, never the reverse - and it imports nothing from
`graphql_api`, which is what merges this class into the root schema.

What is exposed, and what is not
--------------------------------
`IdeaType` carries the content, the state (`status`, `visibility`,
`submittedAt`) and two ids: `authorId` and `organizationId`. It does **not**
carry an `author: UserType`. That is deliberate, and it is the one place this
schema is stricter than the organizations schema next door: a `PUBLIC` idea is
readable by any signed-in member of the platform, so embedding a member's
email address in its payload would publish it platform-wide. An id is enough
for the client to answer the only question it asks - "is this mine?" - which
is what decides whether to offer "Edit" and "Submit".

No comments, votes or attachments appear here. They are later sprints; the
types are not stubbed, so nothing in this schema promises a surface that does
not work.

Enums rather than strings
-------------------------
`status` and `visibility` are real GraphQL enums, built from the model's own
`TextChoices` so the GraphQL names cannot drift from the database's. The three
review-only statuses are part of the contract from the start and the
frontend's union type is therefore not wrong by omission - the reason
`docs/ideas-domain.md` asked for it. A value the enum does not contain cannot
be sent, and `DEPARTMENT` is refused by the service even though the enum
contains it, because the enum describes the domain's vocabulary while the
service describes what may be *used* today.
"""

import strawberry

from ideas import selectors, services
from ideas.models import Category, Idea

IdeaStatus = strawberry.enum(Idea.Status, name='IdeaStatus')
IdeaVisibility = strawberry.enum(Idea.Visibility, name='IdeaVisibility')


@strawberry.type(description='A platform-wide classification an idea can be filed under.')
class CategoryType:
    id: strawberry.ID
    name: str
    slug: str
    description: str

    @staticmethod
    def from_model(category: Category) -> 'CategoryType':
        return CategoryType(
            id=strawberry.ID(str(category.pk)),
            name=category.name,
            slug=category.slug,
            description=category.description,
        )


@strawberry.type(description='A problem put forward for possible automation.')
class IdeaType:
    id: strawberry.ID
    title: str
    description: str
    status: IdeaStatus
    visibility: IdeaVisibility
    # Null until the DRAFT -> SUBMITTED transition happens, and never rewritten
    # afterwards: "when was this put forward" is an audit fact, and it is a
    # different moment from `createdAt`.
    submitted_at: str | None
    created_at: str
    updated_at: str
    # Ids rather than nested objects. See this module's docstring: a PUBLIC
    # idea is platform-readable, so it must not carry a member's email address
    # in its payload.
    author_id: strawberry.ID
    organization_id: strawberry.ID
    category: CategoryType | None

    @staticmethod
    def from_model(idea: Idea) -> 'IdeaType':
        return IdeaType(
            id=strawberry.ID(str(idea.pk)),
            title=idea.title,
            description=idea.description,
            status=IdeaStatus(idea.status),
            visibility=IdeaVisibility(idea.visibility),
            submitted_at=idea.submitted_at.isoformat() if idea.submitted_at else None,
            created_at=idea.created_at.isoformat(),
            updated_at=idea.updated_at.isoformat(),
            author_id=strawberry.ID(str(idea.author_id)),
            organization_id=strawberry.ID(str(idea.organization_id)),
            category=CategoryType.from_model(idea.category) if idea.category_id else None,
        )


@strawberry.input(description='The writable content of an idea.')
class IdeaInput:
    title: str
    # Blank rather than required: a draft is incomplete by definition, and
    # requiring the description here would make a half-written idea unsaveable.
    description: str = ''
    category_id: strawberry.ID | None = None
    # Omit to keep the model default, which is PRIVATE - fail closed.
    visibility: IdeaVisibility | None = None


@strawberry.input(description='Fields for filing a new idea.')
class CreateIdeaInput:
    # An input to a decision, not a value to be written: the service
    # authorizes this organization and then uses it. The author is never an
    # input at all - it is the authenticated user.
    organization_id: strawberry.ID
    idea: IdeaInput


@strawberry.input(description='Fields for editing an existing draft.')
class UpdateIdeaInput:
    id: strawberry.ID
    idea: IdeaInput


@strawberry.type(description='Result of filing an idea.')
class CreateIdeaPayload:
    success: bool
    message: str
    field: str | None = None
    idea: IdeaType | None = None


@strawberry.type(description='Result of editing a draft.')
class UpdateIdeaPayload:
    success: bool
    message: str
    field: str | None = None
    idea: IdeaType | None = None


@strawberry.type(description='Result of submitting a draft.')
class SubmitIdeaPayload:
    success: bool
    message: str
    field: str | None = None
    idea: IdeaType | None = None


def _service_error(exc: services.IdeaError) -> tuple[str, str | None]:
    return exc.message, exc.field


@strawberry.type
class Query:
    @strawberry.field(description='An idea visible to the current user.')
    def idea(self, info: strawberry.Info, id: strawberry.ID) -> IdeaType | None:
        # Null for "no such idea", "another tenant's idea" and "not shared
        # with you" alike - see `ideas/selectors.get_idea`.
        idea = selectors.get_idea(info.context.user, id)
        return IdeaType.from_model(idea) if idea else None

    @strawberry.field(description='Ideas visible to the current user, newest first.')
    def ideas(self, info: strawberry.Info) -> list[IdeaType]:
        return [IdeaType.from_model(idea) for idea in selectors.list_ideas(info.context.user)]

    @strawberry.field(
        description="One organization's ideas visible to the current user, newest first."
    )
    def organization_ideas(
        self, info: strawberry.Info, organization_id: strawberry.ID
    ) -> list[IdeaType]:
        return [
            IdeaType.from_model(idea)
            for idea in selectors.list_organization_ideas(info.context.user, organization_id)
        ]

    @strawberry.field(description='Categories available to file an idea under.')
    def categories(self) -> list[CategoryType]:
        # Unauthenticated by design: the list is platform-wide reference data
        # with no tenant content in it, and the category picker has to render
        # before anybody decides to sign in.
        return [
            CategoryType.from_model(category) for category in selectors.list_active_categories()
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation(description='File a new idea as a draft.')
    def create_idea(self, info: strawberry.Info, input: CreateIdeaInput) -> CreateIdeaPayload:
        try:
            idea = services.create_idea(
                info.context.user,
                input.organization_id,
                services.IdeaInput(
                    title=input.idea.title,
                    description=input.idea.description,
                    category_id=input.idea.category_id,
                    visibility=input.idea.visibility.value if input.idea.visibility else None,
                ),
            )
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return CreateIdeaPayload(success=False, message=message, field=field)

        return CreateIdeaPayload(
            success=True,
            message='Idea saved as a draft.',
            idea=IdeaType.from_model(idea),
        )

    @strawberry.mutation(description="Edit the current user's own draft.")
    def update_idea(self, info: strawberry.Info, input: UpdateIdeaInput) -> UpdateIdeaPayload:
        try:
            idea = services.update_idea(
                info.context.user,
                input.id,
                services.IdeaInput(
                    title=input.idea.title,
                    description=input.idea.description,
                    category_id=input.idea.category_id,
                    visibility=input.idea.visibility.value if input.idea.visibility else None,
                ),
            )
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return UpdateIdeaPayload(success=False, message=message, field=field)

        return UpdateIdeaPayload(
            success=True,
            message='Draft updated.',
            idea=IdeaType.from_model(idea),
        )

    @strawberry.mutation(description="Submit the current user's own draft for review.")
    def submit_idea(self, info: strawberry.Info, id: strawberry.ID) -> SubmitIdeaPayload:
        try:
            idea = services.submit_idea(info.context.user, id)
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return SubmitIdeaPayload(success=False, message=message, field=field)

        return SubmitIdeaPayload(
            success=True,
            message='Idea submitted for review.',
            idea=IdeaType.from_model(idea),
        )
