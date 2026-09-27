import { afterEach, describe, expect, it, vi } from 'vitest'

import { graphqlClient } from '../../../graphql/client'
import {
  categoriesRequest,
  createIdeaRequest,
  ideaRequest,
  ideasRequest,
  organizationIdeasRequest,
  submitIdeaRequest,
  transitionIdeaRequest,
  updateIdeaRequest,
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

  function stubFetch(data: unknown) {
    const fetchMock = vi.fn(async () =>
      Response.json({ data }, { headers: { 'content-type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
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
      createIdea: { success: true, message: 'Idea saved as a draft.', field: null, idea: IDEA },
    })

    await createIdeaRequest('3', { title: 'Automate the invoice run', description: 'Because.' })

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('CreateIdea')
    expect(request.variables).toEqual({
      input: {
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

  it('sends no author, because there is no argument to send one in', async () => {
    const fetchMock = stubFetch({
      createIdea: { success: true, message: 'ok', field: null, idea: IDEA },
    })

    await createIdeaRequest('3', { title: 'T', description: 'D' })

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

    const result = await createIdeaRequest('3', { title: 'T', description: 'D' })

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

    const result = await createIdeaRequest('3', { title: '', description: 'D' })

    expect(result).toMatchObject({ success: false, field: 'title' })
  })

  // --- updateIdea ---------------------------------------------------------

  it('sends updateIdea with the id and the content', async () => {
    const fetchMock = stubFetch({
      updateIdea: { success: true, message: 'Draft updated.', field: null, idea: IDEA },
    })

    await updateIdeaRequest('1', { title: 'Sharper', description: 'Because.', categoryId: '9' })

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

  it('reads every visible idea with no filter argument to widen the result', async () => {
    const fetchMock = stubFetch({ ideas: [IDEA] })

    const result = await ideasRequest()

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Ideas')
    // The tenancy and visibility rules are applied server-side. A filter
    // argument here would be a client-side control over what the caller may
    // read, which is the one thing this module must not offer.
    expect(request.variables).toBeUndefined()
    expect(result).toHaveLength(1)
  })

  it('reads one organization feed, passing the organization explicitly', async () => {
    const fetchMock = stubFetch({ organizationIdeas: [IDEA] })

    const result = await organizationIdeasRequest('3')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('OrganizationIdeas')
    expect(request.variables).toEqual({ organizationId: '3' })
    expect(result).toHaveLength(1)
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
})
