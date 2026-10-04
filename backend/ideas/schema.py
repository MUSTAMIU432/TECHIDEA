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

from ideas import go_ahead, lifecycle, selectors, services, states
from ideas.models import Attachment, Category, Comment, Idea, IdeaTransition

IdeaStatus = strawberry.enum(Idea.Status, name='IdeaStatus')
IdeaVisibility = strawberry.enum(Idea.Visibility, name='IdeaVisibility')
SubmissionContext = strawberry.enum(Idea.SubmissionContext, name='SubmissionContext')
IdeaAction = strawberry.enum(states.Action, name='IdeaAction')
IdeaStage = strawberry.enum(states.Stage, name='IdeaStage')
IdeaTone = strawberry.enum(states.Tone, name='IdeaTone')
# The problem story's closed vocabularies, built from the model's own
# `TextChoices` for the same reason as the two above.
IdeaFrequency = strawberry.enum(Idea.Frequency, name='IdeaFrequency')
IdeaImpact = strawberry.enum(Idea.Impact, name='IdeaImpact')
IdeaCurrentTool = strawberry.enum(Idea.CurrentTool, name='IdeaCurrentTool')


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


@strawberry.type(
    description=(
        "An idea's state, in words, with the one thing this viewer should do "
        'next. The counterpart of `IdeaType.status`, which stays the technical '
        'vocabulary the server enforces; this is the human one the interface '
        'renders. A client should never translate a status value itself.'
    )
)
class IdeaStateType:
    status: IdeaStatus
    label: str
    short_label: str
    tone: IdeaTone
    stage: IdeaStage
    primary_action: IdeaAction
    primary_action_label: str
    is_locked: bool
    is_terminal: bool
    # `(current step, total steps)` for the progress indicator, one-based. Only
    # an organization-context idea has the organization steps in its journey; an
    # individual or team idea's bar is shorter rather than showing two steps it
    # will never reach.
    stage_index: int
    stage_count: int

    @staticmethod
    def from_model(summary: states.IdeaStateSummary, idea: Idea | None = None) -> 'IdeaStateType':
        stage_index, stage_count = (1, 1)
        if idea is not None:
            stage_index, stage_count = states.stage_index(idea)
        return IdeaStateType(
            status=IdeaStatus(summary.status),
            label=summary.label,
            short_label=summary.short_label,
            tone=summary.tone,
            stage=summary.stage,
            primary_action=summary.primary_action,
            primary_action_label=summary.primary_action_label,
            is_locked=summary.is_locked,
            is_terminal=summary.is_terminal,
            stage_index=stage_index,
            stage_count=stage_count,
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
    # --- the submission context -------------------------------------------------
    # Which of the three shapes this idea was filed in, and the tenant it names.
    # `organizationId` is now **null** for an individual or team idea: an
    # organization is optional, and a client that assumes it is always present
    # is a client that will show an empty organization to somebody who filed an
    # idea entirely on their own.
    submission_context: SubmissionContext
    organization_id: strawberry.ID | None
    team_id: strawberry.ID | None
    # The organization's or team's name, for a header or a card. Derived, so it
    # cannot disagree with the row; empty for an individual idea.
    tenant_name: str
    # --- the platform stage -------------------------------------------------------
    # Set when the official submission was frozen, and never cleared. Paired with
    # `platformVersion`: "the submission the platform is holding does not move"
    # is a fact about the idea, reported rather than inferred from the status.
    platform_locked_at: str | None
    platform_version: int
    platform_approved_at: str | None
    # The owner's explicit go-ahead. Null unless the owner has pressed it -
    # receiving the report, the notification or the email never sets this.
    owner_go_ahead_at: str | None
    category: CategoryType | None
    # The problem story (the guided intake form). Content like `description`,
    # so readable by exactly whoever may read the idea - the visibility filter
    # that returns the row is the only gate, and there is no second one here.
    # Blank strings, empty lists and nulls mean "not answered".
    current_process: str
    current_tools: list[IdeaCurrentTool]
    current_tools_other: str
    performed_by: str
    affected_people: str
    frequency: IdeaFrequency | None
    time_required: str
    people_involved: int | None
    impacts: list[IdeaImpact]
    impact_details: str
    improvement_goal: str
    desired_outcome: str
    easier_for_people: str
    expected_benefit: str
    important_considerations: str
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

    @strawberry.field(
        description=(
            'What this state means in words, and the single action this viewer '
            'should take next. Resolved on demand from `ideas.states`, so the '
            'wording and the offered action come from one table that also '
            'derives what the server will accept - the client never has to '
            'translate an enum value itself. Null-invoked never: a viewer with '
            'no rights gets `NOTHING_TO_DO`, not an error.\n\n'
            '`primaryActionLabel` is the button text. It is empty when there is '
            'nothing for this viewer to do, which is how the client knows not to '
            'render a button at all.'
        )
    )
    def state(self) -> 'IdeaStateType':
        return IdeaStateType.from_model(states.summarize(self._viewer, self._idea), self._idea)

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
            'Whether the current viewer may **claim this submission for platform '
            'review**.\n\n'
            'Authorized by the platform-scoped '
            '`administration.review_platform_submissions` permission and by '
            'nothing else: no organization role and no team role reaches it. Also '
            'requires being able to read the idea and not having written it.\n\n'
            'True on an idea waiting in SUBMITTED, and on an UNDER_REVIEW idea '
            'whose reviewer can no longer review it - which `startReview` then '
            'takes over. A capability flag only; `startReview` checks eligibility '
            'again.'
        )
    )
    def viewer_can_start_review(self) -> bool:
        from reviews import eligibility

        precomputed = getattr(self._idea, 'viewer_can_start_review', None)
        if precomputed is not None:
            # Set by the platform queue, which established it for the whole page
            # in its own filters rather than asking per idea.
            return precomputed
        return eligibility.can_start_review(self._viewer, self._idea)

    @strawberry.field(
        description=(
            'Whether the current viewer may **open this organization review**.\n\n'
            "Requires being an active member of the idea's own organization "
            'holding `idea.review` there, being able to read the idea, and not '
            'having written it - so an author never sees this as true for their '
            'own idea. Only ever true for an ORGANIZATION-context idea waiting in '
            'SUBMITTED_TO_ORGANIZATION: a team or individual idea has no '
            'organization to confirm it.\n\n'
            'Organization confirmation is not platform approval, and this '
            'permission grants nothing on the platform track.'
        )
    )
    def viewer_can_start_organization_review(self) -> bool:
        from reviews import eligibility

        precomputed = getattr(self._idea, 'viewer_can_start_organization_review', None)
        if precomputed is not None:
            # Set by `reviews.selectors.review_queue`, which established it for
            # the whole page in the queue's own filters.
            return precomputed
        return eligibility.can_start_organization_review(self._viewer, self._idea)

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
            submission_context=SubmissionContext(idea.submission_context),
            organization_id=(
                strawberry.ID(str(idea.organization_id)) if idea.organization_id else None
            ),
            team_id=strawberry.ID(str(idea.team_id)) if idea.team_id else None,
            tenant_name=idea.tenant_label,
            platform_locked_at=(
                idea.platform_locked_at.isoformat() if idea.platform_locked_at else None
            ),
            platform_version=idea.platform_version,
            platform_approved_at=(
                idea.platform_approved_at.isoformat() if idea.platform_approved_at else None
            ),
            owner_go_ahead_at=(
                idea.owner_go_ahead_at.isoformat() if idea.owner_go_ahead_at else None
            ),
            category=CategoryType.from_model(idea.category) if idea.category_id else None,
            current_process=idea.current_process,
            current_tools=[IdeaCurrentTool(tool) for tool in idea.current_tools],
            current_tools_other=idea.current_tools_other,
            performed_by=idea.performed_by,
            affected_people=idea.affected_people,
            frequency=IdeaFrequency(idea.frequency) if idea.frequency else None,
            time_required=idea.time_required,
            people_involved=idea.people_involved,
            impacts=[IdeaImpact(impact) for impact in idea.impacts],
            impact_details=idea.impact_details,
            improvement_goal=idea.improvement_goal,
            desired_outcome=idea.desired_outcome,
            easier_for_people=idea.easier_for_people,
            expected_benefit=idea.expected_benefit,
            important_considerations=idea.important_considerations,
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
    # The problem story. All optional - a draft is incomplete by definition -
    # and an update writes the whole input, so an omitted answer is cleared.
    current_process: str = ''
    current_tools: list[IdeaCurrentTool] = strawberry.field(default_factory=list)
    current_tools_other: str = ''
    performed_by: str = ''
    affected_people: str = ''
    frequency: IdeaFrequency | None = None
    time_required: str = ''
    people_involved: int | None = None
    impacts: list[IdeaImpact] = strawberry.field(default_factory=list)
    impact_details: str = ''
    improvement_goal: str = ''
    desired_outcome: str = ''
    easier_for_people: str = ''
    expected_benefit: str = ''
    important_considerations: str = ''

    def to_service_input(self) -> services.IdeaInput:
        """The service's input: enums unwrapped, nothing else decided here."""
        return services.IdeaInput(
            title=self.title,
            description=self.description,
            category_id=self.category_id,
            visibility=self.visibility.value if self.visibility else None,
            current_process=self.current_process,
            current_tools=[tool.value for tool in self.current_tools],
            current_tools_other=self.current_tools_other,
            performed_by=self.performed_by,
            affected_people=self.affected_people,
            frequency=self.frequency.value if self.frequency else None,
            time_required=self.time_required,
            people_involved=self.people_involved,
            impacts=[impact.value for impact in self.impacts],
            impact_details=self.impact_details,
            improvement_goal=self.improvement_goal,
            desired_outcome=self.desired_outcome,
            easier_for_people=self.easier_for_people,
            expected_benefit=self.expected_benefit,
            important_considerations=self.important_considerations,
        )


@strawberry.input(
    description=(
        'Fields for filing a new idea.\n\n'
        '`submissionContext` is the choice the interface offers as "How are you '
        'submitting this idea?", and it decides which of `organizationId` and '
        '`teamId` must be present: `INDIVIDUAL` needs neither (a user does not '
        'need an organization to file an idea), `TEAM` needs `teamId`, and '
        '`ORGANIZATION` needs `organizationId`.\n\n'
        'It is an input to a decision, not a value to be written: the service '
        'proves the caller belongs to whichever tenant is named and then uses it. '
        'The author is never an input at all - it is the authenticated user. And '
        'the context cannot be changed later, so it is not on the update input: '
        'filing for a different audience means filing a new idea.'
    )
)
class CreateIdeaInput:
    submission_context: SubmissionContext
    organization_id: strawberry.ID | None = None
    team_id: strawberry.ID | None = None
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
        'rendered as text, so a client must not interpret it as markup.\n\n'
        '`parentId` is the comment this one answers, and is `null` for a '
        'top-level comment. A thread is exactly one level deep - a reply cannot '
        'be replied to - so a client can group the page it was given by this '
        'one field alone, with no nested query and no second page of replies.'
    )
)
class CommentType:
    id: strawberry.ID
    idea_id: strawberry.ID
    author_id: strawberry.ID
    content: str
    parent_id: strawberry.ID | None
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
            parent_id=None if comment.parent_id is None else strawberry.ID(str(comment.parent_id)),
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


@strawberry.input(
    description=(
        'Post a comment on an idea. Omit `parentId` for a top-level comment; '
        'set it to make this a reply to that comment, which the server checks '
        'is readable, on the same idea, and not itself a reply.'
    )
)
class CreateCommentInput:
    idea_id: strawberry.ID
    comment: CommentInput
    # Nullable rather than a separate `replyToComment` mutation, so "a comment"
    # stays one thing in the API and a client needs no second code path to
    # answer somebody. The service resolves it against the caller's own
    # readable comments; it is not an existence oracle.
    parent_id: strawberry.ID | None = None


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
            idea = services.create_idea_in_context(
                info.context.user,
                input.idea.to_service_input(),
                submission_context=input.submission_context.value,
                organization_id=input.organization_id,
                team_id=input.team_id,
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
                input.idea.to_service_input(),
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

        # The message names the stage the idea actually reached, because "submit"
        # means two different things depending on the context and a client that
        # prints one fixed string tells an organization Owner their idea is with
        # the platform when it is in front of their own organization.
        return SubmitIdeaPayload(
            success=True,
            message=_submitted_message(idea),
            idea=IdeaType.from_model(idea),
        )

    @strawberry.mutation(
        description=(
            'Submit a confirmed organization idea to the platform.\n\n'
            'A separate mutation from `submitIdea` because this is a genuinely '
            'different act rather than a variant of the same one: the '
            'organization has already confirmed the idea, and now the **owner** '
            'is choosing to put it in front of the platform. Organization '
            'confirmation is not platform submission, and the two are never done '
            'by the same click.\n\n'
            'This freezes the submission: the content the platform receives is '
            'copied into an immutable version, and no edit can change it. '
            'Answering a later request for changes produces a new version rather '
            'than overwriting this one.'
        )
    )
    def submit_to_platform(self, info: strawberry.Info, id: strawberry.ID) -> SubmitIdeaPayload:
        try:
            idea = services.submit_to_platform(info.context.user, id)
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return SubmitIdeaPayload(success=False, message=message, field=field)

        return SubmitIdeaPayload(
            success=True,
            message='Idea submitted to the platform. It is now locked for review.',
            idea=IdeaType.from_model(idea),
        )

    @strawberry.mutation(
        description=(
            'Give your go-ahead on an approved idea.\n\n'
            '**Platform approval is not owner go-ahead**, and this is the only '
            'operation that provides it. It requires you to be the author of the '
            'idea - not an organization Owner, not a team Owner, not a platform '
            'administrator - and a platform review report to exist, which the '
            "reviewer's approval generated.\n\n"
            'Opening the report, receiving the email and receiving the '
            'notification do **not** count as this decision and none of them '
            'changes the idea. Only this mutation does.\n\n'
            '`READY_FOR_IMPLEMENTATION` means the owner has authorized the idea '
            'to proceed toward implementation. It does not mean a developer has '
            'been selected, a project exists, or any work has started.'
        )
    )
    def give_go_ahead(self, info: strawberry.Info, id: strawberry.ID) -> SubmitIdeaPayload:
        try:
            idea = go_ahead.confirm_go_ahead(info.context.user, id)
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return SubmitIdeaPayload(success=False, message=message, field=field)

        return SubmitIdeaPayload(
            success=True,
            message=(
                'Thank you. Your idea is now ready for implementation, which means it '
                'will be made available to developers.'
            ),
            idea=IdeaType.from_model(idea),
        )

    @strawberry.mutation(
        description=(
            'Post a comment on an idea. Requires being able to *read* the idea '
            '- not membership of its organization, because participation '
            'follows readability and a `PUBLIC` idea is meant to be answered '
            'across tenants. Refused on an idea whose discussion is closed. '
            'With `parentId` it is a reply to that comment, which must be '
            'readable, on the same idea, and not itself a reply.'
        )
    )
    def create_comment(
        self, info: strawberry.Info, input: CreateCommentInput
    ) -> CreateCommentPayload:
        try:
            comment = services.add_comment(
                info.context.user,
                input.idea_id,
                input.comment.content,
                parent_id=input.parent_id,
            )
        except services.IdeaError as exc:
            message, field = _service_error(exc)
            return CreateCommentPayload(success=False, message=message, field=field)

        return CreateCommentPayload(
            success=True,
            message='Reply posted.' if comment.parent_id is not None else 'Comment posted.',
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


def _submitted_message(idea: Idea) -> str:
    """
    What to tell the author after a submission, in their own vocabulary.

    Two different things happen under the word "submit", so one fixed string
    would be wrong for one of them: an organization idea stops at its own
    organization, and telling its owner it is "submitted for review" - which is
    what this used to say - leaves somebody wondering where to look.
    """
    if idea.status == Idea.Status.SUBMITTED_TO_ORGANIZATION:
        return 'Idea submitted to your organization for review.'
    return 'Idea submitted to the platform. It is now locked for review.'
