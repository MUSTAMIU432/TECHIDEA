import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { graphqlClient } from '../../../graphql/client'
import { setAccessToken } from '../../../graphql/tokenStore'
import {
  attachmentsRequest,
  categoriesRequest,
  commentsRequest,
  createCommentRequest,
  createIdeaRequest,
  deleteAttachmentRequest,
  deleteCommentRequest,
  BLOB_URL_LIFETIME_MS,
  downloadAttachmentRequest,
  ideaRequest,
  ideasRequest,
  organizationIdeasRequest,
  removeVoteRequest,
  submitIdeaRequest,
  transitionIdeaRequest,
  updateCommentRequest,
  updateIdeaRequest,
  uploadAttachmentRequest,
  voteIdeaRequest,
  type IdeaAttachment,
} from './ideasApi'

/**
 * `ideasApi` is the frontend's half of the S2-002 contract: every document
 * sent and every response shape read back. A mismatch here does not fail a
 * test - it fails as a GraphQL `errors` array, or as a silently empty list, in
 * a browser - so the documents are asserted here rather than left to the
 * components that use them.
 *
 * The assertions that are about *authorization* rather than plumbing are the
 * interesting ones: that no mutation sends an author, and that none of them
 * can be pointed at an organization the caller is not in. Both are properties
 * of what this client is able to ask for, so they are pinned from this side as
 * well as from the backend suite.
 */
describe('ideasApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  /**
   * The organization-context target, spelled once: an idea filed for an
   * organization names the organization and the context, and the tests below
   * are all about the payload rather than about the three ways to file one.
   * The other two contexts have their own tests further down.
   */
  const ORGANIZATION_TARGET = {
    context: 'ORGANIZATION',
    organizationId: '3',
  } as const

  const IDEA = {
    id: '1',
    title: 'Automate the invoice run',
    description: 'A description long enough.',
    status: 'DRAFT' as const,
    visibility: 'PRIVATE' as const,
    submittedAt: null,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    authorId: '7',
    organizationId: '3',
    category: null,
    availableTransitions: ['SUBMITTED'],
  }

  /** What the backend answers with for a page, echoed back in assertions. */
  const PAGE_INFO = {
    offset: 0,
    limit: 20,
    totalCount: 1,
    hasNextPage: false,
    hasPreviousPage: false,
  }

  const COMMENT = {
    id: 'c1',
    ideaId: '1',
    authorId: '7',
    content: 'We do this by hand every month.',
    parentId: null,
    createdAt: '2026-02-01T09:00:00.000Z',
    updatedAt: '2026-02-01T09:00:00.000Z',
  }

  function stubFetch(data: unknown) {
    const fetchMock = vi.fn(async () =>
      Response.json({ data }, { headers: { 'content-type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  /** The `filters` an outgoing document carried, if any. */
  function sentFilter(request: { variables?: Record<string, unknown> }) {
    const filters = request.variables?.filters
    return (filters ?? {}) as Record<string, unknown>
  }

  function sentRequests(fetchMock: ReturnType<typeof stubFetch>) {
    return fetchMock.mock.calls.map((call) => {
      const [, init] = call as unknown as [URL | string, RequestInit]
      return JSON.parse(String(init.body)) as {
        query: string
        operationName?: string
        variables?: Record<string, unknown>
      }
    })
  }

  // --- createIdea ---------------------------------------------------------

  it('sends createIdea with the organization and the content', async () => {
    const fetchMock = stubFetch({
      createIdea: {
        success: true,
        message: 'Idea saved as a draft.',
        field: null,
        idea: IDEA,
      },
    })

    await createIdeaRequest(ORGANIZATION_TARGET, {
      title: 'Automate the invoice run',
      description: 'Because.',
    })

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('CreateIdea')
    expect(request.variables).toEqual({
      input: {
        submissionContext: 'ORGANIZATION',
        organizationId: '3',
        idea: {
          title: 'Automate the invoice run',
          description: 'Because.',
          categoryId: null,
          visibility: null,
        },
      },
    })
  })

  it('sends the problem story with the content', async () => {
    const fetchMock = stubFetch({
      createIdea: { success: true, message: 'ok', field: null, idea: null },
    })

    await createIdeaRequest(ORGANIZATION_TARGET, {
      title: 'T',
      description: 'D',
      currentProcess: 'Paper, then Excel.',
      currentTools: ['PAPER_FORMS', 'EXCEL'],
      frequency: 'WEEKLY',
      peopleInvolved: 0,
      impacts: [],
      expectedBenefit: 'Fewer mistakes.',
    })

    const [request] = sentRequests(fetchMock)
    expect(request.variables).toEqual({
      input: {
        submissionContext: 'ORGANIZATION',
        organizationId: '3',
        idea: {
          title: 'T',
          description: 'D',
          categoryId: null,
          visibility: null,
          currentProcess: 'Paper, then Excel.',
          currentTools: ['PAPER_FORMS', 'EXCEL'],
          frequency: 'WEEKLY',
          // Zero and an empty list are answers, not absences.
          peopleInvolved: 0,
          impacts: [],
          expectedBenefit: 'Fewer mistakes.',
        },
      },
    })
    expect(request.query).toContain('importantConsiderations')
  })

  it('sends the problem story on update too', async () => {
    const fetchMock = stubFetch({
      updateIdea: { success: true, message: 'ok', field: null, idea: null },
    })

    await updateIdeaRequest('9', {
      title: 'T',
      description: 'D',
      frequency: null,
      impacts: ['DELAYS'],
    })

    const [request] = sentRequests(fetchMock)
    expect(request.variables).toEqual({
      input: {
        id: '9',
        idea: {
          title: 'T',
          description: 'D',
          categoryId: null,
          visibility: null,
          frequency: null,
          impacts: ['DELAYS'],
        },
      },
    })
  })

  it('carries nothing but the input’s own fields into the request', async () => {
    const fetchMock = stubFetch({
      createIdea: { success: true, message: 'ok', field: null, idea: null },
    })

    // A whole idea handed in as the input, with its id, author and status.
    await createIdeaRequest(
      ORGANIZATION_TARGET,
      IDEA as unknown as Parameters<typeof createIdeaRequest>[1],
    )

    const idea = (sentRequests(fetchMock)[0].variables as { input: { idea: object } }).input.idea
    expect(Object.keys(idea)).not.toEqual(expect.arrayContaining(['id']))
    expect(idea).not.toHaveProperty('authorId')
    expect(idea).not.toHaveProperty('status')
    expect(idea).not.toHaveProperty('organizationId')
  })

  it('sends no author, because there is no argument to send one in', async () => {
    const fetchMock = stubFetch({
      createIdea: { success: true, message: 'ok', field: null, idea: IDEA },
    })

    await createIdeaRequest(ORGANIZATION_TARGET, {
      title: 'T',
      description: 'D',
    })

    const [request] = sentRequests(fetchMock)
    const serialized = JSON.stringify(request.variables)
    // The author is the signed-in user, resolved server-side from the access
    // token. A client that could name one would be a client that could file
    // an idea as somebody else.
    expect(serialized).not.toContain('author')
    expect(request.query).not.toContain('authorId:')
  })

  it('returns the created idea with its state', async () => {
    stubFetch({
      createIdea: { success: true, message: 'ok', field: null, idea: IDEA },
    })

    const result = await createIdeaRequest(ORGANIZATION_TARGET, {
      title: 'T',
      description: 'D',
    })

    expect(result.success).toBe(true)
    expect(result.idea?.id).toBe('1')
    expect(result.idea?.status).toBe('DRAFT')
  })

  it('passes a business failure through as a payload, not a throw', async () => {
    stubFetch({
      createIdea: {
        success: false,
        message: 'Give the idea a title.',
        field: 'title',
        idea: null,
      },
    })

    const result = await createIdeaRequest(ORGANIZATION_TARGET, {
      title: '',
      description: 'D',
    })

    expect(result).toMatchObject({ success: false, field: 'title' })
  })

  // --- updateIdea ---------------------------------------------------------

  it('sends updateIdea with the id and the content', async () => {
    const fetchMock = stubFetch({
      updateIdea: {
        success: true,
        message: 'Draft updated.',
        field: null,
        idea: IDEA,
      },
    })

    await updateIdeaRequest('1', {
      title: 'Sharper',
      description: 'Because.',
      categoryId: '9',
    })

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('UpdateIdea')
    expect(request.variables).toEqual({
      input: {
        id: '1',
        idea: {
          title: 'Sharper',
          description: 'Because.',
          categoryId: '9',
          visibility: null,
        },
      },
    })
  })

  it('sends nothing that could change the tenant, the author or the status', async () => {
    const fetchMock = stubFetch({
      updateIdea: { success: true, message: 'ok', field: null, idea: IDEA },
    })

    await updateIdeaRequest('1', { title: 'T', description: 'D' })

    const [request] = sentRequests(fetchMock)
    // There is no argument for any of these, which is the point: an update
    // cannot move an idea to another organization, hand it to another author,
    // or mark it submitted by hand.
    for (const forbidden of ['organizationId', 'authorId', 'status', 'submittedAt']) {
      expect(JSON.stringify(request.variables)).not.toContain(forbidden)
    }
  })

  // --- submitIdea ---------------------------------------------------------

  it('sends submitIdea with only the id', async () => {
    const fetchMock = stubFetch({
      submitIdea: { success: true, message: 'ok', field: null, idea: IDEA },
    })

    await submitIdeaRequest('1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('SubmitIdea')
    expect(request.variables).toEqual({ id: '1' })
  })

  it('asks for no access token, because submitting establishes no session', async () => {
    const fetchMock = stubFetch({
      submitIdea: { success: true, message: 'ok', field: null, idea: IDEA },
    })

    await submitIdeaRequest('1')

    const [request] = sentRequests(fetchMock)
    expect(request.query).not.toContain('accessToken')
  })

  // --- transitionIdea -----------------------------------------------------

  it('sends transitionIdea with the id and the target status', async () => {
    const fetchMock = stubFetch({
      transitionIdea: {
        success: true,
        message: 'Idea moved to Under review.',
        field: null,
        idea: IDEA,
      },
    })

    await transitionIdeaRequest('1', 'UNDER_REVIEW')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('TransitionIdea')
    expect(request.variables).toEqual({ id: '1', to: 'UNDER_REVIEW' })
  })

  it('asks for the transitions the viewer may make', async () => {
    const fetchMock = stubFetch({ ideas: [] })

    await ideasRequest()

    const [request] = sentRequests(fetchMock)
    // The client does not carry its own copy of the lifecycle: the server says
    // what this viewer may do with each idea, so the rules cannot drift.
    expect(request.query).toContain('availableTransitions')
  })

  it('sends no author and no permission anywhere in a transition', async () => {
    const fetchMock = stubFetch({
      transitionIdea: { success: true, message: 'ok', field: null, idea: IDEA },
    })

    await transitionIdeaRequest('1', 'APPROVED')

    const [request] = sentRequests(fetchMock)
    // Who is allowed to make the move is the server's decision, derived from
    // the caller's membership and role. Anything a client could assert about
    // that would be an invitation.
    const serialized = JSON.stringify(request.variables)
    expect(serialized).not.toContain('author')
    expect(serialized).not.toContain('role')
    expect(serialized).not.toContain('permission')
  })

  it('passes a refused transition through as a payload', async () => {
    stubFetch({
      transitionIdea: {
        success: false,
        message: 'You are not allowed to make that change to this idea.',
        field: null,
        idea: null,
      },
    })

    const result = await transitionIdeaRequest('1', 'APPROVED')

    // A refusal is the operation's own answer, not a crash: the UI has to be
    // able to show it to the user.
    expect(result).toMatchObject({
      success: false,
      field: null,
      message: 'You are not allowed to make that change to this idea.',
    })
  })

  it('sends the target as an enum value, not a free string', async () => {
    const fetchMock = stubFetch({
      transitionIdea: { success: true, message: 'ok', field: null, idea: IDEA },
    })

    await transitionIdeaRequest('1', 'CHANGES_REQUESTED')

    const [request] = sentRequests(fetchMock)
    expect(request.variables).toMatchObject({ to: 'CHANGES_REQUESTED' })
  })

  // --- reads --------------------------------------------------------------

  it('reads one idea by id', async () => {
    const fetchMock = stubFetch({ idea: IDEA })

    const result = await ideaRequest('1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Idea')
    expect(request.variables).toEqual({ id: '1' })
    expect(result?.id).toBe('1')
  })

  it('returns null rather than throwing for an unreadable idea', async () => {
    stubFetch({ idea: null })

    expect(await ideaRequest('999')).toBeNull()
  })

  it('reads every visible idea with no filter that could widen the result', async () => {
    const fetchMock = stubFetch({
      ideas: { items: [IDEA], pageInfo: PAGE_INFO },
    })

    const result = await ideasRequest()

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Ideas')
    // The tenancy and visibility rules are applied server-side. A narrowing
    // filter here is fine; what this module must not be able to send is
    // anything that *widens* a result, so the assertion is on the shape of
    // what was sent rather than on there being no argument at all - an empty
    // filter object carries no more authority than no argument did.
    expect(sentFilter(request)).toEqual({})
    expect(result.items).toHaveLength(1)
    expect(result.pageInfo).toEqual(PAGE_INFO)
  })

  it('reads one organization feed, passing the organization explicitly', async () => {
    const fetchMock = stubFetch({
      organizationIdeas: { items: [IDEA], pageInfo: PAGE_INFO },
    })

    const result = await organizationIdeasRequest('3')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('OrganizationIdeas')
    expect(request.variables).toEqual({ organizationId: '3', filters: {} })
    expect(result.items).toHaveLength(1)
  })

  it('sends the filters it was given, and only the ones that were set', async () => {
    const fetchMock = stubFetch({
      organizationIdeas: { items: [], pageInfo: PAGE_INFO },
    })

    await organizationIdeasRequest('3', {
      categoryId: '4',
      search: 'invoicing',
      offset: 20,
      limit: 10,
      // Unset filters must be absent rather than null: a GraphQL input that
      // carries an explicit null is a different request from one that omits
      // the field, and "omitted" is what "this client is not narrowing" means.
      status: undefined,
    })

    const [request] = sentRequests(fetchMock)
    expect(sentFilter(request)).toEqual({
      categoryId: '4',
      search: 'invoicing',
      offset: 20,
      limit: 10,
    })
  })

  it('cannot be given a filter that would widen a result', async () => {
    const fetchMock = stubFetch({ ideas: { items: [], pageInfo: PAGE_INFO } })

    // The call sites are typed, so this is a cast: it asks what the module
    // does with an argument the schema would refuse, which is the case worth
    // pinning - it must be passed through as an unknown field for the server
    // to reject, not quietly dropped, and never translated into something that
    // means "show me more".
    await ideasRequest({
      visibility: 'PUBLIC',
      authorId: '7',
    } as unknown as Parameters<typeof ideasRequest>[0])

    const [request] = sentRequests(fetchMock)
    // `visibility` is now a real filter and is passed through, because it can
    // only remove rows the server already allowed this reader to see.
    // `authorId` is still dropped: it is a caller-chosen id, which is the
    // difference between "public ideas" and a way of asking for somebody
    // else's rows.
    expect(sentFilter(request)).toEqual({ visibility: 'PUBLIC' })
  })

  it('reads the category list for the picker', async () => {
    const fetchMock = stubFetch({ categories: [] })

    await categoriesRequest()

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Categories')
  })

  it('selects the fields the UI renders and no user object', async () => {
    const fetchMock = stubFetch({ ideas: [] })

    await ideasRequest()

    const [request] = sentRequests(fetchMock)
    // Ids rather than nested users: a PUBLIC idea is readable platform-wide,
    // so an embedded user object would publish a member's email address with
    // it.
    expect(request.query).toContain('authorId')
    expect(request.query).not.toMatch(/\bemail\b/)
    expect(request.query).toContain('submittedAt')
    expect(request.query).toContain('status')
  })

  it('goes through the shared client, so the refresh cookie travels with it', async () => {
    const fetchMock = stubFetch({ ideas: [] })

    await ideasRequest()

    const [, init] = fetchMock.mock.calls[0] as unknown as [URL | string, RequestInit]
    expect(init.credentials).toBe('include')
    expect(graphqlClient).toBeDefined()
  })
  // --- comments (S2-005) ---------------------------------------------------

  it('reads one page of a discussion, naming the idea', async () => {
    const fetchMock = stubFetch({
      comments: { items: [COMMENT], pageInfo: PAGE_INFO },
    })

    const result = await commentsRequest('1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Comments')
    expect(request.variables).toEqual({ ideaId: '1' })
    expect(result.items).toHaveLength(1)
    expect(result.pageInfo).toEqual(PAGE_INFO)
  })

  it('sends paging arguments only when they were set', async () => {
    const fetchMock = stubFetch({
      comments: { items: [], pageInfo: PAGE_INFO },
    })

    await commentsRequest('1', { offset: 20, limit: 10 })

    const [request] = sentRequests(fetchMock)
    // Absent rather than null: an explicit null is a different request from an
    // omitted field, and the client has no business asserting paging defaults
    // the server already applies.
    expect(request.variables).toEqual({ ideaId: '1', offset: 20, limit: 10 })
  })

  it('posts a comment with the idea and the content, and no author', async () => {
    const fetchMock = stubFetch({
      createComment: {
        success: true,
        message: 'Comment posted.',
        field: null,
        comment: COMMENT,
      },
    })

    const result = await createCommentRequest('1', 'A comment.')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('CreateComment')
    expect(request.variables).toEqual({
      input: { ideaId: '1', comment: { content: 'A comment.' } },
    })
    // The author is the signed-in user and the idea is authorized server-side,
    // so neither can be supplied. Asserted on the variables rather than the
    // document, because `authorId` legitimately appears in the selection set -
    // the client reads it to decide what to offer - and only its *absence from
    // the request* is the property.
    expect(JSON.stringify(request.variables)).not.toContain('authorId')
    expect(result.success).toBe(true)
  })

  it('sends no parent at all for a top-level comment', async () => {
    /*
      Omitted rather than sent as `null`, for the same reason the discovery
      filters are: an explicit `null` is a different request from an absent
      field, and this client has no business asserting a server default.
    */
    const fetchMock = stubFetch({
      createComment: {
        success: true,
        message: 'Comment posted.',
        field: null,
        comment: COMMENT,
      },
    })

    await createCommentRequest('1', 'A comment.')

    const [request] = sentRequests(fetchMock)
    expect(request.variables).toEqual({
      input: { ideaId: '1', comment: { content: 'A comment.' } },
    })
  })

  it('posts a reply with the comment it answers', async () => {
    const fetchMock = stubFetch({
      createComment: {
        success: true,
        message: 'Reply posted.',
        field: null,
        comment: { ...COMMENT, id: 'c2', parentId: 'c1' },
      },
    })

    const result = await createCommentRequest('1', 'An answer.', 'c1')

    const [request] = sentRequests(fetchMock)
    expect(request.variables).toEqual({
      input: {
        ideaId: '1',
        comment: { content: 'An answer.' },
        parentId: 'c1',
      },
    })
    // The parent comes back on the comment, which is what lets a client group
    // a page it already has rather than asking for the replies again.
    expect(result.comment?.parentId).toBe('c1')
  })

  it('edits a comment by id', async () => {
    const fetchMock = stubFetch({
      updateComment: {
        success: true,
        message: 'Comment updated.',
        field: null,
        comment: { ...COMMENT, content: 'Edited.' },
      },
    })

    const result = await updateCommentRequest('c1', 'Edited.')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('UpdateComment')
    expect(request.variables).toEqual({
      input: { id: 'c1', comment: { content: 'Edited.' } },
    })
    expect(result.comment?.content).toBe('Edited.')
  })

  it('deletes a comment by id, and asks for no entity back', async () => {
    const fetchMock = stubFetch({
      deleteComment: {
        success: true,
        message: 'Comment deleted.',
        field: null,
        comment: null,
      },
    })

    const result = await deleteCommentRequest('c1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('DeleteComment')
    expect(request.variables).toEqual({ id: 'c1' })
    // A deleted row has no shape to return, and the client refreshes on
    // `success` rather than on the payload's contents.
    expect(request.query).not.toMatch(/\bcomment\s*\{/)
    expect(result.comment).toBeNull()
  })

  it('returns a refused comment as the payload it is', async () => {
    stubFetch({
      createComment: {
        success: false,
        message: 'This idea is no longer open for discussion.',
        field: null,
        comment: null,
      },
    })

    const result = await createCommentRequest('1', 'One more thought')

    // A business refusal, not a thrown error: the request got a decision, and
    // the frontend reports the two differently.
    expect(result.success).toBe(false)
    expect(result.message).toBe('This idea is no longer open for discussion.')
    expect(result.field).toBeNull()
  })

  it('selects the fields the discussion renders and no user object', async () => {
    const fetchMock = stubFetch({
      comments: { items: [], pageInfo: PAGE_INFO },
    })

    await commentsRequest('1')

    const [request] = sentRequests(fetchMock)
    // Ids rather than nested users, for the same reason ideas carry no author:
    // a PUBLIC idea is readable platform-wide, so an embedded user would publish
    // a member's email address alongside the comment.
    expect(request.query).toContain('authorId')
    expect(request.query).not.toMatch(/\bemail\b/)
    expect(request.query).toContain('content')
    expect(request.query).toContain('createdAt')
    expect(request.query).toContain('totalCount')
  })

  it('sends no voting or attachment argument', async () => {
    const fetchMock = stubFetch({
      comments: { items: [], pageInfo: PAGE_INFO },
    })

    await commentsRequest('1')

    const [request] = sentRequests(fetchMock)
    // S2-006 and S2-007 are not implemented, and this client cannot ask for
    // either even by accident.
    expect(request.query).not.toMatch(/vote|attachment|upload/i)
  })
  // --- votes (S2-006) -------------------------------------------------------

  it('selects the vote fields on the ideas it reads', async () => {
    const fetchMock = stubFetch({ ideas: { items: [], pageInfo: PAGE_INFO } })

    await ideasRequest()

    const [request] = sentRequests(fetchMock)
    // A card renders its vote control from the page it was given, so the count
    // and the viewer's own answer have to be in the same document. Two fields
    // rather than one: the count is global and the flag is personal.
    expect(request.query).toContain('voteCount')
    expect(request.query).toContain('viewerHasVoted')
  })

  it('votes for an idea by id, sending no voter', async () => {
    const fetchMock = stubFetch({
      voteIdea: {
        success: true,
        message: 'Vote recorded.',
        field: null,
        voteState: { ideaId: '1', voteCount: 3, viewerHasVoted: true },
      },
    })

    const result = await voteIdeaRequest('1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('VoteIdea')
    expect(request.variables).toEqual({ id: '1' })
    // The signed-in user is the voter and there is no argument that could say
    // otherwise, so this client cannot express voting for somebody else.
    expect(JSON.stringify(request.variables)).not.toMatch(/user/i)
    expect(result.voteState).toEqual({
      ideaId: '1',
      voteCount: 3,
      viewerHasVoted: true,
    })
  })

  it('withdraws a vote by id, sending no voter', async () => {
    const fetchMock = stubFetch({
      removeVote: {
        success: true,
        message: 'Vote withdrawn.',
        field: null,
        voteState: { ideaId: '1', voteCount: 2, viewerHasVoted: false },
      },
    })

    const result = await removeVoteRequest('1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('RemoveVote')
    expect(request.variables).toEqual({ id: '1' })
    expect(JSON.stringify(request.variables)).not.toMatch(/user/i)
    expect(result.voteState).toEqual({
      ideaId: '1',
      voteCount: 2,
      viewerHasVoted: false,
    })
  })

  it('asks for the vote state back on both mutations', async () => {
    const fetchMock = stubFetch({
      voteIdea: {
        success: true,
        message: 'Vote recorded.',
        field: null,
        voteState: null,
      },
    })

    await voteIdeaRequest('1')

    const [request] = sentRequests(fetchMock)
    // The server's numbers, not a locally adjusted count: the response already
    // accounts for concurrent votes and for the idempotency rule.
    expect(request.query).toContain('voteState')
    expect(request.query).toContain('voteCount')
    expect(request.query).toContain('viewerHasVoted')
  })

  it('returns a refused vote as the payload it is', async () => {
    stubFetch({
      voteIdea: {
        success: false,
        message: 'Idea is unavailable.',
        field: null,
        voteState: null,
      },
    })

    const result = await voteIdeaRequest('1')

    // A refusal, not a thrown error - and with no vote state, because a caller
    // who may not read an idea is not entitled to its count.
    expect(result.success).toBe(false)
    expect(result.message).toBe('Idea is unavailable.')
    expect(result.voteState).toBeNull()
  })

  it('carries no list-of-voters query', async () => {
    const fetchMock = stubFetch({ ideas: { items: [], pageInfo: PAGE_INFO } })

    await ideasRequest()

    const [request] = sentRequests(fetchMock)
    // This client cannot enumerate who supported an idea, and does not try:
    // the domain has no policy for that aggregate.
    expect(request.query).not.toMatch(/voters|whoVoted|votes\s*\{/)
  })

  // --- attachments (S2-007) --------------------------------------------------

  const ATTACHMENT: IdeaAttachment = {
    id: 'a1',
    ideaId: '1',
    uploaderId: '7',
    filename: 'evidence.pdf',
    contentType: 'application/pdf',
    size: 2048,
    createdAt: '2026-03-01T09:00:00.000Z',
    downloadUrl: '/ideas/1/attachments/a1/download/',
  }

  it('lists an ideas attachments, oldest first, through GraphQL', async () => {
    const fetchMock = stubFetch({
      attachments: { items: [ATTACHMENT], pageInfo: PAGE_INFO },
    })

    const page = await attachmentsRequest('1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Attachments')
    expect(request.variables).toEqual({ ideaId: '1' })
    expect(page.items).toEqual([ATTACHMENT])
  })

  it('carries the download URL but never the storage key', async () => {
    const fetchMock = stubFetch({
      attachments: { items: [ATTACHMENT], pageInfo: PAGE_INFO },
    })

    await attachmentsRequest('1')

    const [request] = sentRequests(fetchMock)
    expect(request.query).toContain('downloadUrl')
    expect(request.query.toLowerCase()).not.toContain('storagekey')
  })

  it('deletes an attachment by id', async () => {
    const fetchMock = stubFetch({
      deleteAttachment: {
        success: true,
        message: 'Attachment deleted.',
        field: null,
      },
    })

    const result = await deleteAttachmentRequest('a1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('DeleteAttachment')
    expect(request.variables).toEqual({ id: 'a1' })
    expect(result.success).toBe(true)
  })

  it('returns a refused deletion as a payload, not a throw', async () => {
    stubFetch({
      deleteAttachment: {
        success: false,
        message: 'Attachment is unavailable.',
        field: null,
      },
    })

    const result = await deleteAttachmentRequest('a1')

    expect(result.success).toBe(false)
    expect(result.message).toBe('Attachment is unavailable.')
  })

  it('carries no upload mutation - uploads are HTTP, not GraphQL', async () => {
    const fetchMock = stubFetch({ ideas: { items: [], pageInfo: PAGE_INFO } })

    await ideasRequest()

    const [request] = sentRequests(fetchMock)
    expect(request.query).not.toMatch(/addAttachment|createAttachment|uploadAttachment/i)
  })

  describe('uploadAttachmentRequest', () => {
    function stubHttp(status: number, body: unknown) {
      const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
        Response.json(body, { status }),
      )
      vi.stubGlobal('fetch', fetchMock)
      return fetchMock
    }

    /**
     * A response that is *not* our JSON payload - which is the case that used to
     * throw. `Response.json` cannot build one, so the body is written as text.
     */
    function stubRawHttp(status: number, body: string) {
      const fetchMock = vi.fn(
        async (_url: string, _init?: RequestInit) => new Response(body, { status }),
      )
      vi.stubGlobal('fetch', fetchMock)
      return fetchMock
    }

    afterEach(() => {
      setAccessToken(null)
    })

    it('POSTs the file as multipart form data to the ideas attachment endpoint', async () => {
      setAccessToken('a-token', new Date(Date.now() + 60_000).toISOString())
      const fetchMock = stubHttp(201, {
        success: true,
        message: 'Attachment uploaded.',
        field: null,
        attachment: ATTACHMENT,
      })
      const file = new File(['%PDF-1.4'], 'evidence.pdf', {
        type: 'application/pdf',
      })

      const result = await uploadAttachmentRequest('1', file)

      expect(fetchMock).toHaveBeenCalledTimes(1)
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toMatch(/\/ideas\/1\/attachments\/$/)
      expect(init.method).toBe('POST')
      expect(init.body).toBeInstanceOf(FormData)
      expect((init.body as FormData).get('file')).toBe(file)
      expect(result.success).toBe(true)
      expect(result.attachment).toEqual(ATTACHMENT)
    })

    it('attaches the bearer token from tokenStore, not a captured value', async () => {
      setAccessToken('fresh-token', new Date(Date.now() + 60_000).toISOString())
      const fetchMock = stubHttp(201, {
        success: true,
        message: 'ok',
        field: null,
        attachment: ATTACHMENT,
      })
      const file = new File(['x'], 'evidence.pdf', { type: 'application/pdf' })

      await uploadAttachmentRequest('1', file)

      const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      const headers = init.headers as Record<string, string>
      expect(headers.Authorization).toBe('Bearer fresh-token')
    })

    it('sends no Authorization header when signed out', async () => {
      const fetchMock = stubHttp(401, {
        success: false,
        message: 'You must be signed in to work with ideas.',
        field: null,
      })
      const file = new File(['x'], 'evidence.pdf', { type: 'application/pdf' })

      await uploadAttachmentRequest('1', file)

      const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      const headers = (init.headers ?? {}) as Record<string, string>
      expect(headers.Authorization).toBeUndefined()
    })

    it('returns a business refusal as a payload, not a throw', async () => {
      stubHttp(400, {
        success: false,
        message: 'That file type is not supported.',
        field: 'file',
      })
      const file = new File(['x'], 'script.exe', {
        type: 'application/octet-stream',
      })

      const result = await uploadAttachmentRequest('1', file)

      expect(result.success).toBe(false)
      expect(result.field).toBe('file')
    })

    it('reports the server’s own message for a refusal', async () => {
      stubHttp(400, {
        success: false,
        message: 'That file type is not supported.',
        field: 'file',
      })
      const file = new File(['x'], 'script.exe', {
        type: 'application/octet-stream',
      })

      // The reason the person uploading is shown, in the server's words. A
      // generic message here is the whole complaint this function exists to fix.
      const result = await uploadAttachmentRequest('1', file)

      expect(result.message).toBe('That file type is not supported.')
    })

    /*
      The bug this replaced: `await response.json()` on an answer that is not our
      JSON rejected, and the rejection was reported as a transport failure - so a
      server which was answering perfectly well was described to the reader as
      unreachable. Anything can answer here, and none of it may throw.
    */

    it('reports an HTML error page as the status it is, not as a dead network', async () => {
      stubRawHttp(500, '<html><body><h1>Server Error (500)</h1></body></html>')
      const file = new File(['x'], 'evidence.pdf', { type: 'application/pdf' })

      const result = await uploadAttachmentRequest('1', file)

      expect(result.success).toBe(false)
      expect(result.message).toContain('500')
      // The wording that sent people to look at the server instead of the
      // request: it is a fact only a rejected fetch can support.
      expect(result.message).not.toMatch(/could not reach/i)
    })

    it('reports a proxy error with its status rather than throwing', async () => {
      stubRawHttp(502, '<html>502 Bad Gateway</html>')
      const file = new File(['x'], 'evidence.pdf', { type: 'application/pdf' })

      const result = await uploadAttachmentRequest('1', file)

      expect(result.success).toBe(false)
      expect(result.message).toContain('502')
      expect(result.attachment).toBeNull()
    })

    it('reports a 404 as a 404', async () => {
      stubRawHttp(404, '<h1>Not Found</h1>')
      const file = new File(['x'], 'evidence.pdf', { type: 'application/pdf' })

      const result = await uploadAttachmentRequest('1', file)

      expect(result.success).toBe(false)
      expect(result.message).toContain('404')
    })

    it('still throws when the request never reached the server', async () => {
      const fetchMock = vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      })
      vi.stubGlobal('fetch', fetchMock)
      const file = new File(['x'], 'evidence.pdf', { type: 'application/pdf' })

      // The one case the transport message is for, and the only thing that may
      // still throw: there is no status, because nothing answered.
      await expect(uploadAttachmentRequest('1', file)).rejects.toThrow('Failed to fetch')
    })

    it('treats a 200 without a payload as a failure rather than a success', async () => {
      stubRawHttp(200, '')
      const file = new File(['x'], 'evidence.pdf', { type: 'application/pdf' })

      const result = await uploadAttachmentRequest('1', file)

      expect(result.success).toBe(false)
      expect(result.attachment).toBeNull()
    })
  })

  describe('downloadAttachmentRequest', () => {
    // Fake timers throughout, so the scheduled revocation of a blob URL never
    // outlives the test that created it.
    beforeEach(() => {
      vi.useFakeTimers()
    })

    afterEach(() => {
      setAccessToken(null)
      vi.useRealTimers()
    })

    it('fetches the attachments download URL with the bearer token', async () => {
      // A string body, not a `Blob`: jsdom's `Blob` does not implement
      // `.stream()`, which the real `Response` constructor needs - a test
      // environment limitation, not something this test is asserting about.
      const fetchMock = vi.fn(
        async (_url: string, _init?: RequestInit) => new Response('%PDF-1.4', { status: 200 }),
      )
      vi.stubGlobal('fetch', fetchMock)
      const createObjectURL = vi.fn(() => 'blob:mock-url')
      const revokeObjectURL = vi.fn()
      vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
      setAccessToken('a-token', new Date(Date.now() + 60_000).toISOString())

      await downloadAttachmentRequest(ATTACHMENT)

      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
      expect(url).toContain(ATTACHMENT.downloadUrl)
      const headers = init.headers as Record<string, string>
      expect(headers.Authorization).toBe('Bearer a-token')
      expect(createObjectURL).toHaveBeenCalledTimes(1)
    })

    it('keeps the blob URL alive past the click, then revokes it', async () => {
      // Regression (S2-008): the URL used to be revoked synchronously after
      // `click()`, which some browsers treat as cancelling a download they
      // have only just started.
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => new Response('%PDF-1.4', { status: 200 })),
      )
      const createObjectURL = vi.fn(() => 'blob:mock-url')
      const revokeObjectURL = vi.fn()
      vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
        // At the moment the browser is asked to download, the URL is live.
        expect(revokeObjectURL).not.toHaveBeenCalled()
      })

      await downloadAttachmentRequest(ATTACHMENT)

      expect(click).toHaveBeenCalledTimes(1)
      expect(revokeObjectURL).not.toHaveBeenCalled()

      vi.advanceTimersByTime(BLOB_URL_LIFETIME_MS - 1)
      expect(revokeObjectURL).not.toHaveBeenCalled()

      // ...and it is not leaked: it is released once the lifetime is up.
      vi.advanceTimersByTime(1)
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
      click.mockRestore()
    })

    it('throws on a failed download rather than returning a payload', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => new Response(null, { status: 404 })),
      )

      await expect(downloadAttachmentRequest(ATTACHMENT)).rejects.toThrow(
        'Could not download this attachment.',
      )
    })
  })
})
