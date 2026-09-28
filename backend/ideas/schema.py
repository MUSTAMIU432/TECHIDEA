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

Comments (S2-005), votes (S2-006) and attachments (S2-007) follow the same
rules as everything else here. `AttachmentType` is the one exception to
"nothing here decides anything" in one narrow sense: it carries no binary
content and there is no mutation that accepts any - GraphQL is metadata and
lifecycle only, and `ideas/views.py` is where an upload or a download
actually happens. See that module's docstring for the split.

Paging is offset-based and bounded: a page defaults to
`ideas.pagination.DEFAULT_PAGE_SIZE` rows and is clamped to
`ideas.pagination.MAX_PAGE_SIZE`, so no argument can ask for the whole table.
The rationale for offset over cursor, and what it would cost, is in
`ideas/pagination.py`.

Status changes go through `transitionIdea` only. `updateIdea` takes content
and nothing else - there is no `status` on `IdeaInput`, and no mutation that
sets one - so "promote my own idea to Approved" is not an operation this schema
expresses, whatever a client sends.

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

from ideas import lifecycle, selectors, services
from ideas.models import Attachment, Category, Comment, Idea, IdeaTransition

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
    # The statuses *this viewer* may move this idea to right now, derived from
    # the same transition matrix that enforces the change. It saves the client
    # from hard-coding a second, drifting copy of the lifecycle - and it is a
    # convenience, not a control: `transitionIdea` asks the matrix again.
    available_transitions: list[IdeaStatus]
    # Whether this idea accepts a new comment right now (S2-005), reported for
    # the same reason as `availableTransitions`: the rule is a lifecycle rule,
    # and a client that re-derived it here would be carrying a second copy of
    # it that could disagree with the server. Not a control either - a client
    # that sends `createComment` on a closed discussion is refused, whether or
    # not it asked.
    discussion_open: bool
    # Voting (S2-006). Both are per-reader, because `viewerHasVoted` is about
    # the caller and not the world - the same reason `availableTransitions` is
    # computed per viewer rather than globally.
    #
    # Annotated onto the row by `selectors.annotate_vote_state` when the idea
    # came from a discovery query, and computed here when it did not (a single
    # idea, or the row a mutation just wrote). The fallback exists because
    # `from_model` is called from a dozen places that hold an unannotated
    # `Idea`, and defaulting to `0` instead would show a wrong count rather
    # than an obviously missing one.
    vote_count: int
    viewer_has_voted: bool
    # The row and the viewer this type was built from, for the review
    # capability fields below. Private: never part of the schema.
    _idea: strawberry.Private[Idea]
    _viewer: strawberry.Private[object]

    # Review capabilities (S3-003). Resolved lazily rather than in
    # `from_model`, so the cost is only paid by a query that asks for them,
    # and both answer from the status alone - with no query - for every idea
    # that could not have one: only a `SUBMITTED` idea can be claimed and only
    # an `UNDER_REVIEW` one has a review in progress.
    #
    # This is the one place the Ideas GraphQL adapter reads the Reviews
    # domain. The Ideas *domain* (models, services, selectors, lifecycle)
    # never imports Reviews; the adapter composes the two for the viewer,
    # which is what `docs/reviews-domain.md` §13 places on `IdeaType`.
    @strawberry.field(
        description=(
            'Whether the current viewer is eligible to start a review of this '
            'idea: a reviewer in its organization who can read it and did not '
            'write it, on an idea waiting in SUBMITTED. A capability flag only; '
            'the operation that starts a review is not available yet, and it '
            'will check eligibility again when it is.'
        )
    )
    def viewer_can_start_review(self) -> bool:
        from reviews import eligibility

        precomputed = getattr(self._idea, 'viewer_can_start_review', None)
        if precomputed is not None:
            # Set by `reviews.selectors.review_queue`, which established it
            # for the whole page in the queue's own filters.
            return precomputed
        return eligibility.can_start_review(self._viewer, self._idea)

    @strawberry.field(
        description=(
            "The id of the current viewer's own in-progress review of this "
            "idea, or null. Never reveals another reviewer's review."
        )
    )
    def viewer_active_review_id(self) -> strawberry.ID | None:
        from reviews import selectors as review_selectors

        review_id = review_selectors.active_review_id_for(self._viewer, self._idea)
        return strawberry.ID(str(review_id)) if review_id is not None else None

    @staticmethod
    def from_model(idea: Idea, user=None) -> 'IdeaType':
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
            available_transitions=[
                IdeaStatus(status) for status in lifecycle.available_transitions(user, idea)
            ],
            discussion_open=lifecycle.discussion_is_open(idea),
            **_vote_state_fields(idea, user),
            _idea=idea,
            _viewer=user,
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


@strawberry.type(description='Result of moving an idea to another lifecycle state.')
class TransitionIdeaPayload:
    success: bool
    message: str
    field: str | None = None
    idea: IdeaType | None = None


@strawberry.type(
    description=(
        'How an idea is voted on, for one reader. `voteCount` is the total '
        'across everybody; `viewerHasVoted` is about the caller alone, and is '
        'the only reason a count is reader-specific rather than global.\n\n'
        'The same two numbers are on `IdeaType`, so a card that arrived with '
        'the ideas list already has them; this type is what the vote mutations '
        "return so a client can render the server's answer rather than "
        'guessing at one.'
    )
)
class IdeaVoteState:
    idea_id: strawberry.ID
    vote_count: int
    viewer_has_voted: bool


@strawberry.type(
    description=(
        'Where the caller is in a paged result set. `offset` and `limit` are '
        'as *applied*, so a caller that asked for more than the server allows '
        'can tell from these numbers rather than having to know the maximum.'
    )
)
class PageInfo:
    offset: int
    limit: int
    total_count: int
    has_next_page: bool
    has_previous_page: bool


@strawberry.type(description='One page of ideas visible to the current user.')
class IdeaPage:
    items: list[IdeaType]
    page_info: PageInfo


@strawberry.type(
    description=(
        'One comment on an idea. Carries `authorId` and no author, for the '
        'same reason `IdeaType` does: a `PUBLIC` idea is readable platform-wide, '
        'so embedding a member here would publish their email to everybody. '
        '`authorId` is what lets the client decide which comments to offer Edit '
        'and Delete on - an offer, never a control, since the service checks '
        'authorship itself.\n\n'
        '`content` is plain text. It is stored and returned verbatim, and it is '
        'rendered as text, so a client must not interpret it as markup.'
    )
)
class CommentType:
    id: strawberry.ID
    idea_id: strawberry.ID
    author_id: strawberry.ID
    content: str
    # ISO 8601 strings, as `IdeaType` reports its own timestamps: one
    # representation across the schema rather than two.
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, comment: Comment) -> 'CommentType':
        return cls(
            id=strawberry.ID(str(comment.pk)),
            idea_id=strawberry.ID(str(comment.idea_id)),
            author_id=strawberry.ID(str(comment.author_id)),
            content=comment.content,
            created_at=comment.created_at.isoformat(),
            updated_at=comment.updated_at.isoformat(),
        )


@strawberry.type(
    description=(
        "One recorded change of an idea's status. `actorId` rather than a "
        'nested user, like every type an idea can reach.'
    )
)
class IdeaTransitionType:
    id: strawberry.ID
    idea_id: strawberry.ID
    from_status: IdeaStatus
    to_status: IdeaStatus
    actor_id: strawberry.ID
    created_at: str

    @staticmethod
    def from_model(transition: IdeaTransition) -> 'IdeaTransitionType':
        return IdeaTransitionType(
            id=strawberry.ID(str(transition.pk)),
            idea_id=strawberry.ID(str(transition.idea_id)),
            from_status=IdeaStatus(transition.from_status),
            to_status=IdeaStatus(transition.to_status),
            actor_id=strawberry.ID(str(transition.actor_id)),
            created_at=transition.created_at.isoformat(),
        )


@strawberry.type(description="One page of an idea's discussion, oldest first.")
class CommentPage:
    items: list[CommentType]
    # The same `PageInfo` as `IdeaPage`, deliberately rather than a comment-
    # shaped one: the offsets, limits and totals mean the same thing in both,
    # and a second pagination type would be a second set of conventions to
    # learn and a second thing to get wrong.
    page_info: PageInfo


@strawberry.input(
    description=(
        'Narrowing arguments for a discovery query. Every field can only '
        '*remove* rows the server has already decided are visible: there is no '
        'visibility argument, because a filter that read like a grant is one. '
        'There is deliberately no organization argument either — a tenant is '
        'scoped by the `organizationIdeas` query that names it, so a request '
        'cannot name two tenants at once and leave the server to resolve the '
        'conflict.'
    )
)
class IdeaFiltersInput:
    category_id: strawberry.ID | None = None
    status: IdeaStatus | None = None
    # Free text over the title and description. Bound as a parameter by the
    # ORM, never interpolated.
    search: str | None = None
    offset: int = 0
    limit: int | None = None


@strawberry.input(description='The text of a comment.')
class CommentInput:
    # Bound by the service (MAX_COMMENT_LENGTH), not by the schema, because the
    # limit is a product decision and the message for it belongs next to the
    # other validation failures. It is a plain `String` and not an
    # upload/scalar, so nothing here can carry a file or a rendered document.
    content: str


@strawberry.input(description='Post a comment on an idea.')
class CreateCommentInput:
    idea_id: strawberry.ID
    comment: CommentInput


@strawberry.input(description="Edit the current user's own comment.")
class UpdateCommentInput:
    id: strawberry.ID
    comment: CommentInput


def _attachment_download_path(idea_id: object, attachment_id: object) -> str:
    """
    The HTTP path that streams one attachment's bytes.

    A relative path, not an absolute URL: this process does not reliably know
    its own public origin (that is a deployment concern - reverse proxies,
    multiple hostnames), and the frontend already knows the API's base origin
    from the same configuration it uses to reach `/graphql/` (see
    `frontend/src/lib/env.ts`). Building the full URL is therefore the
    client's job; this is the one thing only the server can supply - the
    path itself, from `ideas/views.py`'s URL configuration - and it is
    supplied by the same query that already proved the caller may read this
    attachment, not looked up separately.

    Carries no token and needs none: the endpoint re-authenticates and
    re-authorizes the request itself, from the same `Authorization: Bearer`
    header every other request already carries (see `ideas/views.py`).
    Nothing about the path is secret, so it is not a signed URL and does not
    need to be one - see `docs/ideas-domain.md` for why that is the right
    call at this project's current scale rather than a shortcut.
    """
    return f'/ideas/{idea_id}/attachments/{attachment_id}/download/'


@strawberry.type(
    description=(
        "One idea's supporting evidence: a file the idea's own author "
        'attached. Carries `uploaderId` and no uploader object, for the same '
        "reason `CommentType` carries `authorId` and no author - an idea's "
        'evidence is exactly as readable as the idea itself, which for a '
        "PUBLIC idea is the whole platform, and embedding a member's email "
        'there would publish it platform-wide.\n\n'
        '`downloadUrl` is a path, not a signed URL - see '
        '`ideas/views.py`, which re-authorizes the request itself when the '
        'client fetches it. There is no field for the storage key: it is an '
        'internal implementation detail this schema never exposes.'
    )
)
class AttachmentType:
    id: strawberry.ID
    idea_id: strawberry.ID
    uploader_id: strawberry.ID
    filename: str
    content_type: str
    size: int
    created_at: str
    download_url: str

    @staticmethod
    def from_model(attachment: Attachment) -> 'AttachmentType':
        return AttachmentType(
            id=strawberry.ID(str(attachment.pk)),
            idea_id=strawberry.ID(str(attachment.idea_id)),
            uploader_id=strawberry.ID(str(attachment.uploaded_by_id)),
            filename=attachment.filename,
            content_type=attachment.content_type,
            size=attachment.size,
            created_at=attachment.created_at.isoformat(),
            download_url=_attachment_download_path(attachment.idea_id, attachment.pk),
        )


@strawberry.type(description="One page of an idea's supporting evidence, oldest first.")
class AttachmentPage:
    items: list[AttachmentType]
    # The same `PageInfo` every other page in this schema uses - see
    # `CommentPage`'s note on why a second pagination shape would be a second
    # set of conventions to get wrong.
    page_info: PageInfo


@strawberry.type(description='Result of deleting an attachment.')
class DeleteAttachmentPayload:
    success: bool
    message: str
    field: str | None = None


def _vote_state_fields(idea: Idea, user) -> dict:
    """
    `voteCount`/`viewerHasVoted` for an idea, from the row if it has them.

    An annotated row - anything from `list_discoverable_ideas` - answers both
    without a query. An unannotated one costs two, which is why the fallback is
    only reachable from the single-idea paths.

    The last branch is the one without a viewer. `createIdea`, `updateIdea` and
    `submitIdea` build their payload from an `Idea` without naming one, which is
    S2-002's existing shape for `availableTransitions` and is left exactly as it
    shipped. The *count* is still answered from the idea - it is a fact about
    the idea, not about the reader, and defaulting it to `0` would report a
    wrong number on an idea other people had already voted for. Only the
    viewer's own answer goes unanswered, which is what a missing viewer means.
    """
    if hasattr(idea, 'vote_count'):
        return {'vote_count': idea.vote_count, 'viewer_has_voted': idea.viewer_has_voted}

    state = selectors.vote_state_for(user, idea)
    if state is not None:
        return {'vote_count': state.vote_count, 'viewer_has_voted': state.viewer_has_voted}

    return {'vote_count': selectors.vote_count_for(idea), 'viewer_has_voted': False}


def _comment_payload(comment: Comment) -> CommentType:
    return CommentType.from_model(comment)


def _service_error(exc: services.IdeaError) -> tuple[str, str | None]:
    return exc.message, exc.field


def _idea_page(info: strawberry.Info, scope: selectors.IdeaFilters, filters) -> IdeaPage:
    """
    Run one discovery query and shape the result.

    The only place the GraphQL filter input is turned into a selector filter,
    which is deliberate: the two are different vocabularies for the same idea
    (camelCase optionals with enum values, versus the selector's normalized
    values) and a resolver that did the translation itself would be a place
    where the schema could disagree with the selector about what a filter
    means. The authorization decision is not here — it was made in
    `selectors.list_discoverable_ideas`, before this was called.

    `scope` is the tenant, and it arrives from the query rather than from the
    filter input: `organizationIdeas` supplies it, `ideas` supplies nothing and
    is therefore platform-wide. There is no path where a filter can re-scope a
    query that has already been scoped.
    """
    requested = filters or IdeaFiltersInput()
    applied = selectors.IdeaFilters(
        organization_id=scope.organization_id,
        category_id=requested.category_id,
        status=requested.status.value if requested.status else None,
        search=requested.search,
    )

    page = selectors.list_discoverable_ideas(
        info.context.user,
        applied,
        offset=requested.offset,
        limit=requested.limit,
    )

    return IdeaPage(
        items=[IdeaType.from_model(idea, info.context.user) for idea in page.items],
        page_info=PageInfo(
            offset=page.offset,
            limit=page.limit,
            total_count=page.total_count,
            has_next_page=page.has_next_page,
            has_previous_page=page.has_previous_page,
        ),
    )


@strawberry.type
class Query:
    @strawberry.field(description='An idea visible to the current user.')
    def idea(self, info: strawberry.Info, id: strawberry.ID) -> IdeaType | None:
        # Null for "no such idea", "another tenant's idea" and "not shared
        # with you" alike - see `ideas/selectors.get_idea`.
        idea = selectors.get_idea(info.context.user, id)
        return IdeaType.from_model(idea, info.context.user) if idea else None

    @strawberry.field(
        description=(
            'Ideas visible to the current user, newest first, narrowed by the '
            'filters given. The visibility filter is applied first and cannot '
            'be widened by any argument; the result is one page, not the whole '
            'table.'
        )
    )
    def ideas(self, info: strawberry.Info, filters: IdeaFiltersInput | None = None) -> IdeaPage:
        return _idea_page(info, selectors.IdeaFilters(), filters)

    @strawberry.field(
        description=(
            "One organization's ideas visible to the current user, newest "
            'first. Answers an empty page for an organization the caller is not '
            'an active member of, which is also the answer for one that does '
            'not exist.'
        )
    )
    def organization_ideas(
        self,
        info: strawberry.Info,
        organization_id: strawberry.ID,
        filters: IdeaFiltersInput | None = None,
    ) -> IdeaPage:
        return _idea_page(info, selectors.IdeaFilters(organization_id=organization_id), filters)

    @strawberry.field(
        description=(
            "One idea's discussion, oldest first. Answers an empty page for an "
            'idea the caller may not read, which is the same answer as for an '
            'idea that does not exist: a comment list must not be a better '
            'oracle than the idea it hangs from.\n\n'
            'Discussion is readable in every state, including one where the '
            'discussion is closed to new comments.'
        )
    )
    def comments(
        self,
        info: strawberry.Info,
        idea_id: strawberry.ID,
        offset: int = 0,
        limit: int | None = None,
    ) -> CommentPage:
        page = selectors.list_comments(info.context.user, idea_id, offset=offset, limit=limit)
        return CommentPage(
            items=[_comment_payload(comment) for comment in page.items],
            page_info=PageInfo(
                offset=page.offset,
                limit=page.limit,
                total_count=page.total_count,
                has_next_page=page.has_next_page,
                has_previous_page=page.has_previous_page,
            ),
        )

    @strawberry.field(
        description=(
            "One idea's supporting evidence, oldest first. Answers an empty "
            'page for an idea the caller may not read - the same answer as for '
            'an idea that does not exist, exactly like `comments`.'
        )
    )
    def attachments(
        self,
        info: strawberry.Info,
        idea_id: strawberry.ID,
        offset: int = 0,
        limit: int | None = None,
    ) -> AttachmentPage:
        page = selectors.list_attachments(info.context.user, idea_id, offset=offset, limit=limit)
        return AttachmentPage(
            items=[AttachmentType.from_model(attachment) for attachment in page.items],
            page_info=PageInfo(
                offset=page.offset,
                limit=page.limit,
                total_count=page.total_count,
                has_next_page=page.has_next_page,
                has_previous_page=page.has_previous_page,
            ),
        )

    @strawberry.field(
        description=(
            'One attachment, by id, if the caller may read the idea it '
            'belongs to. Null for an unknown id and for an attachment on an '
            'idea the caller may not read alike, so the id is not an oracle '
            'for either.'
        )
    )
    def attachment(self, info: strawberry.Info, id: strawberry.ID) -> AttachmentType | None:
        attachment = selectors.get_attachment(info.context.user, id)
        return AttachmentType.from_model(attachment) if attachment else None

    @strawberry.field(
        description=(
            "An idea's lifecycle history, oldest first: every status change, "
            "who made it and when (S3-007). For the idea's author and the "
            'reviewers of its organization; an empty list for anybody else, and '
            'for an idea the caller cannot read or that does not exist. Read-only: '
            'no operation writes or changes a transition.'
        )
    )
    def idea_transitions(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> list[IdeaTransitionType]:
        return [
            IdeaTransitionType.from_model(transition)
            for transition in selectors.list_idea_transitions(info.context.user, idea_id)
        ]

    @strawberry.field(description='Categories available to file an idea under.')
    def categories(self) -> list[CategoryType]:
        # Unauthenticated by design: the list is platform-wide reference data
        # with no tenant content in it, and the category picker has to render
        # before anybody decides to sign in.
        return [
            CategoryType.from_model(category) for category in selectors.list_active_categories()
        ]


# Comment payloads, shaped exactly like the idea payloads:
# `(success, message, field, entity)`. One failure convention for the whole
# API, so the frontend handles a refused comment the way it handles a refused
# idea - by reading `message` and putting `field` next to the input at fault.


@strawberry.type(
    description=(
        "Result of voting or withdrawing a vote. Carries the server's vote "
        'state on success so a client can render what the database now says, '
        'rather than adjusting a number locally and hoping.'
    )
)
class VotePayload:
    success: bool
    message: str
    field: str | None = None
    # Null on any refusal, and always: a refused vote must not report a count,
    # because the caller is not entitled to one for an idea they cannot read.
    vote_state: IdeaVoteState | None = None


@strawberry.type(description='Result of posting a comment.')
class CreateCommentPayload:
    success: bool
    message: str
    field: str | None = None
    comment: CommentType | None = None


@strawberry.type(description='Result of editing a comment.')
class UpdateCommentPayload:
    success: bool
    message: str
    field: str | None = None
    comment: CommentType | None = None


@strawberry.type(description='Result of deleting a comment.')
class DeleteCommentPayload:
    success: bool
    message: str
    field: str | None = None
    # Always null. A delete removes the row, and there is no way to describe
    # what a comment that no longer exists looks like. The client refreshes the
    # discussion on `success`, not on the payload's contents.
    comment: CommentType | None = None


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

    @strawberry.mutation(
        description=(
            'Move an idea to another state in its lifecycle. The only way a '
            'status can change: there is no mutation that sets one, so a client '
            'cannot mark its own idea reviewed or approved. The permitted pairs '
            'and the actor each one requires are in ideas/lifecycle.py, and '
            '`IdeaType.availableTransitions` reports what this viewer may do '
            'with this idea right now.'
        )
    )
    def transition_idea(
        self, info: strawberry.Info, id: strawberry.ID, to: IdeaStatus
    ) -> TransitionIdeaPayload:
        try:
            idea = lifecycle.transition_idea(info.context.user, id, to.value)
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return TransitionIdeaPayload(success=False, message=message, field=field)

        return TransitionIdeaPayload(
            success=True,
            message=f'Idea moved to {Idea.Status(idea.status).label}.',
            idea=IdeaType.from_model(idea, info.context.user),
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

    @strawberry.mutation(
        description=(
            'Post a comment on an idea. Requires being able to *read* the idea '
            '- not membership of its organization, because participation '
            'follows readability and a `PUBLIC` idea is meant to be answered '
            'across tenants. Refused on an idea whose discussion is closed.'
        )
    )
    def create_comment(
        self, info: strawberry.Info, input: CreateCommentInput
    ) -> CreateCommentPayload:
        try:
            comment = services.add_comment(info.context.user, input.idea_id, input.comment.content)
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return CreateCommentPayload(success=False, message=message, field=field)

        return CreateCommentPayload(
            success=True,
            message='Comment posted.',
            comment=_comment_payload(comment),
        )

    @strawberry.mutation(
        description=(
            "Edit the current user's own comment. There is no elevated path: "
            'no administrator and no role holder may edit a comment they did '
            'not write, because no such capability exists to check.'
        )
    )
    def update_comment(
        self, info: strawberry.Info, input: UpdateCommentInput
    ) -> UpdateCommentPayload:
        try:
            comment = services.update_comment(info.context.user, input.id, input.comment.content)
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return UpdateCommentPayload(success=False, message=message, field=field)

        return UpdateCommentPayload(
            success=True,
            message='Comment updated.',
            comment=_comment_payload(comment),
        )

    @strawberry.mutation(
        description="Delete the current user's own comment. There is no elevated path."
    )
    def delete_comment(self, info: strawberry.Info, id: strawberry.ID) -> DeleteCommentPayload:
        try:
            services.delete_comment(info.context.user, id)
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return DeleteCommentPayload(success=False, message=message, field=field)

        return DeleteCommentPayload(success=True, message='Comment deleted.', comment=None)

    @strawberry.mutation(
        description=(
            'Record that the current user finds this idea worth doing. One '
            'vote per user per idea: a second call is a no-op that returns '
            'the same vote, and the database constraint makes two concurrent '
            'attempts converge on one row.\n\n'
            'The voter is the signed-in user and is never an input, so there is '
            "no request here that can vote on somebody else's behalf. Refused "
            'for an idea the caller cannot read, in every lifecycle state - '
            'unlike commenting, a vote is not closed by a review outcome.'
        )
    )
    def vote_idea(self, info: strawberry.Info, id: strawberry.ID) -> VotePayload:
        try:
            services.vote_for_idea(info.context.user, id)
            # Read the state back from the selector rather than counting here,
            # so the number the client renders is the one the database holds.
            idea = selectors.get_idea(info.context.user, id)
            state = selectors.vote_state_for(info.context.user, idea) if idea else None
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return VotePayload(success=False, message=message, field=field)

        return VotePayload(
            success=True,
            message='Vote recorded.',
            vote_state=IdeaVoteState(
                idea_id=strawberry.ID(str(state.idea_id)),
                vote_count=state.vote_count,
                viewer_has_voted=state.viewer_has_voted,
            )
            if state
            else None,
        )

    @strawberry.mutation(
        description=(
            "Withdraw the current user's own vote. Idempotent: withdrawing a "
            "vote that is not there succeeds. Only the caller's own vote is "
            'ever removed, and an idea they cannot read is refused rather than '
            'silently accepted.'
        )
    )
    def remove_vote(self, info: strawberry.Info, id: strawberry.ID) -> VotePayload:
        try:
            services.remove_vote(info.context.user, id)
            idea = selectors.get_idea(info.context.user, id)
            state = selectors.vote_state_for(info.context.user, idea) if idea else None
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return VotePayload(success=False, message=message, field=field)

        return VotePayload(
            success=True,
            message='Vote withdrawn.',
            vote_state=IdeaVoteState(
                idea_id=strawberry.ID(str(state.idea_id)),
                vote_count=state.vote_count,
                viewer_has_voted=state.viewer_has_voted,
            )
            if state
            else None,
        )

    @strawberry.mutation(
        description=(
            "Delete one of the caller's own idea's attachments - both the "
            'metadata and its stored bytes. There is no upload mutation here: '
            'binary content never travels through GraphQL - see '
            '`ideas/views.py` for the HTTP endpoint that accepts an upload and '
            '`AttachmentType.downloadUrl` for the one that serves it back. '
            'Requires idea authorship, the same rule '
            '`updateIdea`/`submitIdea` use, not merely being able to read the '
            'idea.'
        )
    )
    def delete_attachment(
        self, info: strawberry.Info, id: strawberry.ID
    ) -> DeleteAttachmentPayload:
        try:
            services.delete_attachment(info.context.user, id)
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return DeleteAttachmentPayload(success=False, message=message, field=field)

        return DeleteAttachmentPayload(success=True, message='Attachment deleted.')
