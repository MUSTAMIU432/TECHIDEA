/**
 * GraphQL operations for the Ideas domain.
 *
 * The frontend's half of the S2-002 contract: every document sent and every
 * response shape read back live here, and components never build a query of
 * their own. A mismatch between this file and the backend's schema does not
 * fail a test in the component that uses it - it fails as a GraphQL
 * `errors` array, or as a silently empty list, in a browser.
 *
 * Three things this module is shaped around, all of them consequences of
 * decisions the *backend* made and the UI has to respect rather than
 * re-litigate:
 *
 * - **The server decides what is visible.** Every read here goes through a
 *   query that is already tenant- and visibility-filtered. S2-004 added
 *   filters, and they are the same rule rather than an exception to it: every
 *   one of them can only *remove* rows the server already decided were
 *   readable, there is no `visibility` or `authorId` filter in the input type
 *   to send, and the client cannot widen anything. The UI's job is to render
 *   what it is given.
 * - **A page, not a list.** Discovery returns `{ items, pageInfo }` so the
 *   client cannot ask for the whole table by forgetting an argument.
 * - **`authorId` is an id, not a person.** `IdeaType` deliberately does not
 *   embed a user, so the client cannot render (or leak) a member's email on
 *   an idea that is readable platform-wide. Comparing `authorId` with the
 *   signed-in user is how the UI decides what to *offer* - Edit, Submit -
 *   and offering is a presentation choice, never a control.
 * - **Business failures are payloads.** `success: false` with a `message`
 *   and a `field` is the operation's own answer; a thrown error means the
 *   request never got that far (network, server down), which is a different
 *   fact and is reported differently.
 */

import { graphqlClient } from '../../../graphql/client'
import { getAccessToken } from '../../../graphql/tokenStore'
import { env } from '../../../lib/env'

/**
 * The submission lifecycle, mirroring the backend's `IdeaStatus` enum.
 *
 * All seven values are listed even though S2-002 can only produce `DRAFT` and
 * `SUBMITTED`, because the review statuses are part of the contract from the
 * start: a union type that omitted them would be wrong by omission the
 * moment Sprint 3 lands.
 */
export type IdeaStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'UNDER_REVIEW'
  | 'CHANGES_REQUESTED'
  | 'REJECTED'
  | 'APPROVED'
  | 'AUTOMATION_PROPOSAL'

/**
 * Who may read an idea. `DEPARTMENT` is listed because the backend's enum
 * contains it, but the service refuses it as a *choice* - there is no
 * department tier to honour it yet - so it can never arrive on an idea this
 * client receives.
 */
export type IdeaVisibility = 'PUBLIC' | 'ORGANIZATION' | 'DEPARTMENT' | 'PRIVATE'

/**
 * The visibilities the form may offer. Deliberately a client-side copy of
 * `ideas.services.SELECTABLE_VISIBILITIES` rather than a list derived from
 * the enum: `DEPARTMENT` is in the vocabulary and not in the picker, and the
 * difference is a product decision, not a formatting one. The server refuses
 * it regardless - this copy only avoids offering something that would be
 * rejected.
 */
export const SELECTABLE_VISIBILITIES: ReadonlyArray<{
  value: IdeaVisibility
  label: string
  hint: string
}> = [
  {
    value: 'PRIVATE',
    label: 'Only me',
    hint: 'Nobody else can see this, not even your team or a reviewer - so it cannot be reviewed.',
  },
  {
    value: 'ORGANIZATION',
    label: 'My organization',
    hint: 'Every active member of this organization can read it.',
  },
  {
    value: 'PUBLIC',
    label: 'Everyone on the platform',
    hint: 'Any signed-in member of the platform can read it.',
  },
]

/**
 * Where the caller is in a paged result, as the server applied it.
 *
 * `offset` and `limit` are echoed back *as applied* rather than as requested,
 * so a client that asked for 1000 rows and got 50 can see that without
 * knowing the server's ceiling. `IdeaPageInfo` therefore describes the
 * response, never the request: the request is this module's business.
 */
export interface IdeaPageInfo {
  offset: number
  limit: number
  totalCount: number
  hasNextPage: boolean
  hasPreviousPage: boolean
}

export interface IdeaPage {
  items: Idea[]
  pageInfo: IdeaPageInfo
}

/**
 * The narrowing arguments of a discovery query. Mirrors the backend's
 * `IdeaFiltersInput`, and is missing the same things on purpose: no
 * `visibility`, no `authorId`, and no `organizationId` (a tenant is scoped by
 * the `organizationIdeas` query that names it, so a request cannot name two
 * tenants at once).
 *
 * Every field is optional and every field is a narrowing one, which is what
 * makes the whole object safe to build from form fields. `undefined` and
 * `null` are both normalized to `null` before sending, because a GraphQL
 * input that carries `undefined` is a document that carries a field the
 * server did not ask for.
 */
export interface IdeaFilters {
  categoryId?: string | null
  status?: IdeaStatus | null
  search?: string | null
  offset?: number
  limit?: number
}

export interface IdeaCategory {
  id: string
  name: string
  slug: string
  description: string
}

export interface Idea {
  id: string
  title: string
  description: string
  status: IdeaStatus
  visibility: IdeaVisibility
  /** Null until the draft is submitted, and never rewritten afterwards. */
  submittedAt: string | null
  createdAt: string
  updatedAt: string
  authorId: string
  organizationId: string
  category: IdeaCategory | null
  /**
   * The statuses *this viewer* may move this idea to right now, computed by the
   * backend from the same transition matrix that enforces the change.
   *
   * It exists so this client does not carry its own copy of the lifecycle,
   * which would be a second place for the rules to drift. It is a
   * convenience and never a control: `transitionIdea` asks the matrix again,
   * and a request for a status not in this list is refused by the server
   * exactly as if the list had been honoured.
   */
  availableTransitions: IdeaStatus[]
  /**
   * Whether this idea accepts a new comment right now, reported by the server
   * from the same rule `createComment` enforces.
   *
   * Present so this client does not carry a second copy of a lifecycle rule
   * that could disagree with the server's - the same reason
   * `availableTransitions` exists. It decides what to *offer*: the composer is
   * not drawn on a closed idea, and a client that sends the request anyway is
   * refused by the server.
   */
  discussionOpen: boolean
  /**
   * How many people have voted for this idea (S2-006), and whether the signed-in
   * user is one of them.
   *
   * Two numbers rather than one because they answer different questions: the
   * count is global and the flag is personal. Both arrive on the idea itself, so
   * a card renders its vote control from the page it was already given - there
   * is no per-idea request, and no second query to count twenty ideas in.
   *
   * `0` and `false` rather than null when nobody has voted, which is the normal
   * case and not an absence of data.
   */
  voteCount: number
  viewerHasVoted: boolean
}

/** The writable content of an idea. Mirrors the backend's `IdeaInput`. */
export interface IdeaDraftInput {
  title: string
  description: string
  categoryId?: string | null
  visibility?: IdeaVisibility | null
}

/** Every payload below carries this shape; `field` names the input at fault. */
export interface IdeaMutationResult {
  success: boolean
  message: string
  field: string | null
  idea: Idea | null
}

const CATEGORY_FIELDS = `
  id
  name
  slug
  description
`

const IDEA_FIELDS = `
  id
  title
  description
  status
  visibility
  submittedAt
  createdAt
  updatedAt
  authorId
  organizationId
  availableTransitions
  discussionOpen
  voteCount
  viewerHasVoted
  category { ${CATEGORY_FIELDS} }
`

const CREATE_IDEA_MUTATION = `
  mutation CreateIdea($input: CreateIdeaInput!) {
    createIdea(input: $input) {
      success
      message
      field
      idea { ${IDEA_FIELDS} }
    }
  }
`

const UPDATE_IDEA_MUTATION = `
  mutation UpdateIdea($input: UpdateIdeaInput!) {
    updateIdea(input: $input) {
      success
      message
      field
      idea { ${IDEA_FIELDS} }
    }
  }
`

const SUBMIT_IDEA_MUTATION = `
  mutation SubmitIdea($id: ID!) {
    submitIdea(id: $id) {
      success
      message
      field
      idea { ${IDEA_FIELDS} }
    }
  }
`

const IDEA_QUERY = `
  query Idea($id: ID!) {
    idea(id: $id) { ${IDEA_FIELDS} }
  }
`

const PAGE_INFO_FIELDS = `
  offset
  limit
  totalCount
  hasNextPage
  hasPreviousPage
`

const IDEAS_QUERY = `
  query Ideas($filters: IdeaFiltersInput) {
    ideas(filters: $filters) {
      items { ${IDEA_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const ORGANIZATION_IDEAS_QUERY = `
  query OrganizationIdeas($organizationId: ID!, $filters: IdeaFiltersInput) {
    organizationIdeas(organizationId: $organizationId, filters: $filters) {
      items { ${IDEA_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const TRANSITION_IDEA_MUTATION = `
  mutation TransitionIdea($id: ID!, $to: IdeaStatus!) {
    transitionIdea(id: $id, to: $to) {
      success
      message
      field
      idea { ${IDEA_FIELDS} }
    }
  }
`

const CATEGORIES_QUERY = `
  query Categories {
    categories { ${CATEGORY_FIELDS} }
  }
`

/**
 * File a new idea as a draft.
 *
 * `organizationId` is a request to act *in* that organization, not a value to
 * write onto the idea: the server authorizes it and then uses it, and the
 * author is always the signed-in user. Neither can be set from here, which is
 * why neither appears in the arguments.
 */
export async function createIdeaRequest(
  organizationId: string,
  input: IdeaDraftInput,
): Promise<IdeaMutationResult> {
  const data = await graphqlClient.request<{ createIdea: IdeaMutationResult }>(
    CREATE_IDEA_MUTATION,
    {
      input: {
        organizationId,
        idea: {
          title: input.title,
          description: input.description,
          categoryId: input.categoryId ?? null,
          visibility: input.visibility ?? null,
        },
      },
    },
  )
  return data.createIdea
}

/** Edit the signed-in user's own draft. Ownership is the server's decision. */
export async function updateIdeaRequest(
  id: string,
  input: IdeaDraftInput,
): Promise<IdeaMutationResult> {
  const data = await graphqlClient.request<{ updateIdea: IdeaMutationResult }>(
    UPDATE_IDEA_MUTATION,
    {
      input: {
        id,
        idea: {
          title: input.title,
          description: input.description,
          categoryId: input.categoryId ?? null,
          visibility: input.visibility ?? null,
        },
      },
    },
  )
  return data.updateIdea
}

/**
 * Submit a draft for review: `DRAFT` -> `SUBMITTED`, once, by its author.
 * Establishes no session and returns no token.
 */
export async function submitIdeaRequest(id: string): Promise<IdeaMutationResult> {
  const data = await graphqlClient.request<{ submitIdea: IdeaMutationResult }>(
    SUBMIT_IDEA_MUTATION,
    { id },
  )
  return data.submitIdea
}

/**
 * Move an idea to another state in its lifecycle (S2-003).
 *
 * The only way a status changes: there is no mutation that sets one, so a
 * client cannot mark its own idea reviewed or approved. The target is an enum
 * value, so a state the domain does not have cannot even be sent; whether the
 * move is legal *from where the idea is now* is the server's answer, not this
 * client's.
 */
export async function transitionIdeaRequest(
  id: string,
  to: IdeaStatus,
): Promise<IdeaMutationResult> {
  const data = await graphqlClient.request<{ transitionIdea: IdeaMutationResult }>(
    TRANSITION_IDEA_MUTATION,
    { id, to },
  )
  return data.transitionIdea
}

/** One idea, or `null` for anything the signed-in user may not read. */
export async function ideaRequest(id: string): Promise<Idea | null> {
  const data = await graphqlClient.request<{ idea: Idea | null }>(IDEA_QUERY, { id })
  return data.idea
}

/**
 * Send only the filters that were actually set.
 *
 * A GraphQL input cannot carry `undefined`, and sending every key with a null
 * value would be a different request from sending none: it would make the
 * server's "was this filter supplied?" checks depend on JSON rather than on
 * omission, and `offset: null` is not the same as an absent offset. So an
 * unset filter is *absent*, which is also why the request cannot accidentally
 * carry a `visibility` the backend would refuse.
 */
function filterVariables(filters: IdeaFilters): Record<string, unknown> {
  const variables: Record<string, unknown> = {}
  if (filters.categoryId) variables.categoryId = filters.categoryId
  if (filters.status) variables.status = filters.status
  if (filters.search) variables.search = filters.search
  if (filters.offset !== undefined) variables.offset = filters.offset
  if (filters.limit !== undefined) variables.limit = filters.limit
  return variables
}

/** One page of every idea the signed-in user may read, newest first. */
export async function ideasRequest(filters: IdeaFilters = {}): Promise<IdeaPage> {
  const data = await graphqlClient.request<{ ideas: IdeaPage }>(IDEAS_QUERY, {
    filters: filterVariables(filters),
  })
  return data.ideas
}

/**
 * One page of one organization's readable ideas, newest first.
 *
 * Empty for an organization the caller is not an active member of - the
 * server does not confirm that the organization exists, so the UI shows an
 * empty state rather than an error.
 */
export async function organizationIdeasRequest(
  organizationId: string,
  filters: IdeaFilters = {},
): Promise<IdeaPage> {
  const data = await graphqlClient.request<{ organizationIdeas: IdeaPage }>(
    ORGANIZATION_IDEAS_QUERY,
    {
      organizationId,
      filters: filterVariables(filters),
    },
  )
  return data.organizationIdeas
}

/** Categories available to file an idea under. */
export async function categoriesRequest(): Promise<IdeaCategory[]> {
  const data = await graphqlClient.request<{ categories: IdeaCategory[] }>(CATEGORIES_QUERY)
  return data.categories
}

/**
 * Comments & discussion (S2-005).
 *
 * A comment has no tenancy, no visibility and no state of its own, so every
 * question about one is a question about the idea it hangs from - and the three
 * rules that follow are the client half of decisions the backend already made:
 *
 * - **Reading is reading the idea.** `comments(ideaId)` is already filtered by
 *   the idea's visibility, and it answers an *empty page* for an idea the
 *   caller may not read - the same answer as for an idea that does not exist.
 *   So this client has no way to distinguish "no discussion" from "not yours
 *   to read", and must not try to.
 * - **`authorId` is an id, not a person.** `CommentType` carries no member
 *   object, for the same reason `IdeaType` does not: a `PUBLIC` idea is
 *   readable platform-wide, so an embedded author would publish their email to
 *   everybody. Comparing `authorId` with the signed-in user is how this client
 *   decides what to *offer* - Edit, Delete - and offering is never a control.
 *   The server checks authorship itself, and would refuse regardless of whether
 *   a button was drawn.
 * - **There is no elevated path.** No argument here can say "as a moderator",
 *   because no such capability exists to check. Edit and Delete are refused for
 *   anybody who is not the author, including the idea's own author and including
 *   an organization Owner.
 */

/**
 * One comment, as the server stores it.
 *
 * `content` is plain text and must be rendered as text. The backend stores and
 * returns it verbatim - it does not escape and does not strip markup - because
 * escaping belongs at render time; a client that renders this with
 * `dangerouslySetInnerHTML` would be introducing the injection the backend
 * deliberately did not.
 */
export interface IdeaComment {
  id: string
  ideaId: string
  authorId: string
  content: string
  createdAt: string
  updatedAt: string
}

/** One page of a discussion. Reuses the S2-004 `PageInfo` type on purpose. */
export interface IdeaCommentPage {
  items: IdeaComment[]
  pageInfo: IdeaPageInfo
}

/** Every comment payload carries this shape; `field` names the input at fault. */
export interface CommentMutationResult {
  success: boolean
  message: string
  field: string | null
  comment: IdeaComment | null
}

const COMMENT_FIELDS = `
  id
  ideaId
  authorId
  content
  createdAt
  updatedAt
`

const COMMENTS_QUERY = `
  query Comments($ideaId: ID!, $offset: Int, $limit: Int) {
    comments(ideaId: $ideaId, offset: $offset, limit: $limit) {
      items { ${COMMENT_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const CREATE_COMMENT_MUTATION = `
  mutation CreateComment($input: CreateCommentInput!) {
    createComment(input: $input) {
      success
      message
      field
      comment { ${COMMENT_FIELDS} }
    }
  }
`

const UPDATE_COMMENT_MUTATION = `
  mutation UpdateComment($input: UpdateCommentInput!) {
    updateComment(input: $input) {
      success
      message
      field
      comment { ${COMMENT_FIELDS} }
    }
  }
`

const DELETE_COMMENT_MUTATION = `
  mutation DeleteComment($id: ID!) {
    deleteComment(id: $id) { success message field }
  }
`

/**
 * One page of an idea's discussion, oldest first.
 *
 * An unreadable idea is an empty page rather than an error, so a client cannot
 * tell "you may not read this" from "there is nothing here" - which is the
 * point.
 */
export async function commentsRequest(
  ideaId: string,
  filters: { offset?: number; limit?: number } = {},
): Promise<IdeaCommentPage> {
  // Only the arguments that were set are sent, for the same reason the
  // discovery filters are: an explicit `null` is a different request from an
  // absent field, and this client has no business asserting paging defaults the
  // server already applies.
  const data = await graphqlClient.request<{ comments: IdeaCommentPage }>(COMMENTS_QUERY, {
    ideaId,
    ...(filters.offset !== undefined ? { offset: filters.offset } : {}),
    ...(filters.limit !== undefined ? { limit: filters.limit } : {}),
  })
  return data.comments
}

/**
 * Post a comment. The author is the signed-in user and the idea is authorized
 * by the server, so neither is an argument here.
 */
export async function createCommentRequest(
  ideaId: string,
  content: string,
): Promise<CommentMutationResult> {
  const data = await graphqlClient.request<{ createComment: CommentMutationResult }>(
    CREATE_COMMENT_MUTATION,
    { input: { ideaId, comment: { content } } },
  )
  return data.createComment
}

/**
 * Edit a comment. Only the comment's own author may do this, and the server
 * says so - this function asks.
 */
export async function updateCommentRequest(
  id: string,
  content: string,
): Promise<CommentMutationResult> {
  const data = await graphqlClient.request<{ updateComment: CommentMutationResult }>(
    UPDATE_COMMENT_MUTATION,
    { input: { id, comment: { content } } },
  )
  return data.updateComment
}

/**
 * Delete a comment. `comment` is always null on success - a deleted row has no
 * shape to return - so the caller refreshes on `success`, not on the payload.
 */
export async function deleteCommentRequest(id: string): Promise<CommentMutationResult> {
  const data = await graphqlClient.request<{ deleteComment: CommentMutationResult }>(
    DELETE_COMMENT_MUTATION,
    { id },
  )
  return data.deleteComment
}

/**
 * Voting & engagement (S2-006).
 *
 * One vote per user per idea, and the rules that make that true are all
 * server-side. What follows from that on this side:
 *
 * - **The voter is the signed-in user and is never an argument.** `voteIdea`
 *   takes an idea and nothing else, so this client cannot express "vote on
 *   somebody else's behalf" - the document has no field to carry it.
 * - **Both mutations return the server's `voteState`.** That is the whole
 *   reason this client does no optimistic arithmetic: the count it renders after
 *   a vote is the count the database holds, including when a concurrent vote
 *   from somebody else landed in between.
 * - **A refusal is a payload, and its `voteState` is null.** A caller who may
 *   not read an idea is not entitled to its vote count either, so there is
 *   nothing to render and nothing to accidentally leak.
 * - **No "who voted" query exists.** This client cannot enumerate supporters,
 *   and does not try: the domain has no policy for that aggregate.
 */

/** The count and the viewer's own answer, as the server reports them. */
export interface IdeaVoteState {
  ideaId: string
  voteCount: number
  viewerHasVoted: boolean
}

export interface VoteMutationResult {
  success: boolean
  message: string
  field: string | null
  /** Null on any refusal - see the note above. */
  voteState: IdeaVoteState | null
}

const VOTE_STATE_FIELDS = `
  ideaId
  voteCount
  viewerHasVoted
`

const VOTE_IDEA_MUTATION = `
  mutation VoteIdea($id: ID!) {
    voteIdea(id: $id) {
      success
      message
      field
      voteState { ${VOTE_STATE_FIELDS} }
    }
  }
`

const REMOVE_VOTE_MUTATION = `
  mutation RemoveVote($id: ID!) {
    removeVote(id: $id) {
      success
      message
      field
      voteState { ${VOTE_STATE_FIELDS} }
    }
  }
`

/**
 * Record that the signed-in user finds this idea worth doing.
 *
 * Idempotent on the server: a second call is a no-op returning the same state,
 * because a double-clicked button and a retried request are ordinary.
 */
export async function voteIdeaRequest(id: string): Promise<VoteMutationResult> {
  const data = await graphqlClient.request<{ voteIdea: VoteMutationResult }>(VOTE_IDEA_MUTATION, {
    id,
  })
  return data.voteIdea
}

/**
 * Withdraw the signed-in user's own vote. Idempotent too, so a second click on
 * the toggle is not an error about a state the reader already reached.
 *
 * Only the caller's own vote can be removed; the mutation has no argument that
 * could name somebody else's.
 */
export async function removeVoteRequest(id: string): Promise<VoteMutationResult> {
  const data = await graphqlClient.request<{ removeVote: VoteMutationResult }>(
    REMOVE_VOTE_MUTATION,
    { id },
  )
  return data.removeVote
}

/**
 * Attachments & supporting evidence (S2-007).
 *
 * The one place in this domain where GraphQL is *not* the whole story:
 *
 * - **Metadata, listing and deletion are GraphQL**, exactly like everything
 *   else here - `attachments(ideaId)`, `attachment(id)` and
 *   `deleteAttachment(id)` follow this file's existing conventions.
 * - **The bytes are HTTP**, on two endpoints this client calls with a plain
 *   `fetch` rather than through `graphqlClient`: `graphql-request` is a
 *   GraphQL client and has no multipart-upload or streamed-download
 *   support, and there is no second GraphQL client introduced to get it -
 *   see `uploadAttachmentRequest`/`downloadAttachmentRequest` below. Both
 *   send the same `Authorization: Bearer <token>` header `graphqlClient`
 *   attaches automatically, read directly from the same `tokenStore` - one
 *   authentication mechanism for the whole app, on two transports.
 * - **The server decides the storage key, the content type and the display
 *   filename's safety.** This client never invents any of the three; it
 *   sends the raw file and renders back exactly what the server answers.
 */

/** One piece of supporting evidence attached to an idea. */
export interface IdeaAttachment {
  id: string
  ideaId: string
  uploaderId: string
  /**
   * Safe to render as text. The server has already reduced whatever the
   * uploader's browser sent to a bare name with no directory component -
   * see the backend's `ideas.attachments.safe_display_filename` - so this
   * client does not re-sanitize it, but it is still rendered as text, never
   * as markup.
   */
  filename: string
  contentType: string
  size: number
  createdAt: string
  /**
   * A path, not a full URL - see the backend's
   * `ideas.schema._attachment_download_path`. `downloadAttachmentRequest`
   * resolves it against `env.apiBaseUrl`.
   */
  downloadUrl: string
}

export interface IdeaAttachmentPage {
  items: IdeaAttachment[]
  pageInfo: IdeaPageInfo
}

/** Every attachment mutation payload carries this shape. */
export interface AttachmentMutationResult {
  success: boolean
  message: string
  field: string | null
}

/** The server's answer to an upload attempt - a payload, not a thrown error. */
export interface UploadAttachmentResult {
  success: boolean
  message: string
  field: string | null
  attachment: IdeaAttachment | null
}

const ATTACHMENT_FIELDS = `
  id
  ideaId
  uploaderId
  filename
  contentType
  size
  createdAt
  downloadUrl
`

const ATTACHMENTS_QUERY = `
  query Attachments($ideaId: ID!, $offset: Int, $limit: Int) {
    attachments(ideaId: $ideaId, offset: $offset, limit: $limit) {
      items { ${ATTACHMENT_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const DELETE_ATTACHMENT_MUTATION = `
  mutation DeleteAttachment($id: ID!) {
    deleteAttachment(id: $id) { success message field }
  }
`

/**
 * One page of an idea's supporting evidence, oldest first.
 *
 * An unreadable idea is an empty page rather than an error - the same rule
 * `commentsRequest` follows, for the same reason: this client has no way to
 * tell "you may not read this" from "there is nothing here", and must not
 * try to.
 */
export async function attachmentsRequest(
  ideaId: string,
  filters: { offset?: number; limit?: number } = {},
): Promise<IdeaAttachmentPage> {
  const data = await graphqlClient.request<{ attachments: IdeaAttachmentPage }>(ATTACHMENTS_QUERY, {
    ideaId,
    ...(filters.offset !== undefined ? { offset: filters.offset } : {}),
    ...(filters.limit !== undefined ? { limit: filters.limit } : {}),
  })
  return data.attachments
}

/**
 * Delete one of the signed-in user's own idea's attachments. Refused for
 * anyone else's, including a colleague who could read it - the server's
 * rule, not this client's to soften.
 */
export async function deleteAttachmentRequest(id: string): Promise<AttachmentMutationResult> {
  const data = await graphqlClient.request<{ deleteAttachment: AttachmentMutationResult }>(
    DELETE_ATTACHMENT_MUTATION,
    { id },
  )
  return data.deleteAttachment
}

/**
 * Upload `file` as supporting evidence on `ideaId`.
 *
 * Plain `fetch`, not `graphqlClient`: this is a `multipart/form-data` POST
 * to an HTTP endpoint, not a GraphQL document. `credentials: 'include'`
 * matches `graphqlClient`'s own config (the refresh-token cookie flows the
 * same way here), and the bearer token is read fresh from `tokenStore` on
 * every call rather than captured once, for the same reason `graphqlClient`'s
 * `headers` is a function.
 *
 * Never throws for a business refusal - a `success: false` response is
 * returned like any other payload here, so a caller handles it the same way
 * it handles a refused GraphQL mutation. A thrown error means the request
 * never reached a decision (the network, the server being down), which is a
 * different fact.
 */
export async function uploadAttachmentRequest(
  ideaId: string,
  file: File,
): Promise<UploadAttachmentResult> {
  const body = new FormData()
  body.append('file', file)

  const token = getAccessToken()
  const response = await fetch(`${env.apiBaseUrl}/ideas/${ideaId}/attachments/`, {
    method: 'POST',
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body,
  })

  const payload = (await response.json()) as UploadAttachmentResult
  return payload
}

/**
 * Fetch `attachment`'s bytes and hand the browser a save-as download.
 *
 * A plain `<a href>` cannot carry the `Authorization` header this endpoint
 * requires, so the bytes are fetched here (with the header, like every other
 * authenticated request this app makes) and turned into a blob URL the
 * browser downloads from. The URL is revoked on a timer rather than straight
 * after `click()`: the click only *starts* the download, and some browsers
 * read the blob asynchronously, so revoking synchronously can cancel it
 * (S2-008). The timer still bounds how long the blob is held, so nothing
 * lingers.
 *
 * Throws on any failure (a refusal, a network error) rather than returning a
 * payload: unlike the metadata operations above, there is no partial
 * "business failure" shape to report here - the server answers with the
 * file or with an HTTP error, and a caller shows the same transport-failure
 * message for either.
 */
/**
 * How long a download's blob URL outlives the click that starts it. Long
 * enough for any browser to have begun reading the blob - FileSaver.js settled
 * on the same 40 seconds - and short enough that a file is not held in memory
 * for the rest of the session.
 */
export const BLOB_URL_LIFETIME_MS = 40_000

export async function downloadAttachmentRequest(attachment: IdeaAttachment): Promise<void> {
  const token = getAccessToken()
  const response = await fetch(`${env.apiBaseUrl}${attachment.downloadUrl}`, {
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })

  if (!response.ok) {
    throw new Error('Could not download this attachment.')
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = attachment.filename
    // Never appended visibly and never left in the DOM: this is a one-shot
    // trigger for the browser's own download UI, not a rendered element.
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  } finally {
    // Scheduled in `finally` so the blob is released even if the click threw.
    setTimeout(() => URL.revokeObjectURL(url), BLOB_URL_LIFETIME_MS)
  }
}
