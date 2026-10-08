import { afterEach, describe, expect, it, vi } from 'vitest'

import { setAccessToken } from '../../../graphql/tokenStore'
import {
  adminCapabilitiesRequest,
  adminIdeasRequest,
  adminReviewsRequest,
  adminUserRequest,
  adminUsersRequest,
  downloadAdminAttachmentRequest,
  setUserActiveRequest,
  type AdminAttachment,
} from './administrationApi'

/**
 * The console's documents, asserted on the wire: what is sent, and that the
 * answer is handed back as the server gave it. Which fields exist is pinned on
 * the backend (`administration/tests`); this pins what the client asks for.
 */
describe('administrationApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    setAccessToken(null)
  })

  function stubFetch(data: unknown) {
    const fetchMock = vi.fn(async () =>
      Response.json({ data }, { headers: { 'content-type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  function sent(fetchMock: ReturnType<typeof stubFetch>) {
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    return JSON.parse(String(init.body)) as {
      query: string
      variables: Record<string, unknown>
    }
  }

  const PAGE = {
    items: [],
    pageInfo: { offset: 0, limit: 20, totalCount: 0, hasNextPage: false, hasPreviousPage: false },
  }

  it('asks for the viewer’s capabilities and nothing about anybody else', async () => {
    const capabilities = {
      canAccessConsole: false,
      canInspectIdeaContent: false,
      canManageUserAccounts: false,
      canManageOrganizationRoles: false,
      canManageCategories: false,
    }
    const fetchMock = stubFetch({ adminCapabilities: capabilities })

    await expect(adminCapabilitiesRequest()).resolves.toEqual(capabilities)
    expect(sent(fetchMock).variables ?? {}).toEqual({})
  })

  it('sends only the filters that are set, and the page', async () => {
    const fetchMock = stubFetch({ adminUsers: PAGE })

    await adminUsersRequest(
      { search: '', isActive: false, platformAdminsOnly: true },
      { offset: 40 },
    )

    expect(sent(fetchMock).variables).toEqual({
      filters: { isActive: false, platformAdminsOnly: true },
      offset: 40,
    })
  })

  it('never asks for a credential', async () => {
    const fetchMock = stubFetch({ adminUser: null })

    await adminUserRequest('5')

    const { query } = sent(fetchMock)
    expect(query).toContain('adminUser(id: $id)')
    expect(query).toContain('signInMethods')
    expect(query).not.toMatch(/password\b|token|secret|hash/i)
  })

  it('sends idea filters with dates as plain days', async () => {
    const fetchMock = stubFetch({ adminIdeas: PAGE })

    await adminIdeasRequest({
      status: 'SUBMITTED',
      organizationId: '3',
      createdFrom: '2026-01-01',
      authorId: null,
    })

    expect(sent(fetchMock).variables).toEqual({
      filters: { status: 'SUBMITTED', organizationId: '3', createdFrom: '2026-01-01' },
    })
  })

  it('asks for completed decisions for the approvals view', async () => {
    const fetchMock = stubFetch({ adminReviews: PAGE })

    await adminReviewsRequest({ state: 'COMPLETED', decisions: ['APPROVED'] })

    const { query, variables } = sent(fetchMock)
    expect(query).toContain('adminReviews(filters: $filters')
    expect(query).not.toContain('isStalled')
    expect(variables).toEqual({ filters: { state: 'COMPLETED', decisions: ['APPROVED'] } })
  })

  it('returns a refusal as data', async () => {
    const refusal = {
      success: false,
      message: 'You cannot change the status of your own account.',
      field: null,
      user: null,
    }
    stubFetch({ adminSetUserActive: refusal })

    await expect(setUserActiveRequest({ userId: '1', isActive: false })).resolves.toEqual(refusal)
  })

  describe('downloadAdminAttachmentRequest', () => {
    const ATTACHMENT: AdminAttachment = {
      id: '30',
      filename: 'volumes.pdf',
      contentType: 'application/pdf',
      size: 4,
      uploadedBy: { id: '5', email: 'a@x.example', name: 'A' },
      createdAt: '2026-01-01T00:00:00Z',
      downloadPath: '/administration/attachments/30/download/',
    }

    it('fetches through the console endpoint with the bearer token', async () => {
      vi.useFakeTimers()
      setAccessToken('secret-access-token', new Date(Date.now() + 60_000).toISOString())
      // A string body: jsdom's `Blob` has no `.stream()` for `Response` to read.
      const fetchMock = vi.fn(async () => new Response('%PDF-1.4', { status: 200 }))
      vi.stubGlobal('fetch', fetchMock)
      const createObjectURL = vi.fn(() => 'blob:x')
      vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() })

      await downloadAdminAttachmentRequest(ATTACHMENT)

      const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
      expect(url).toMatch(/\/administration\/attachments\/30\/download\/$/)
      expect(init.headers).toEqual({ Authorization: 'Bearer secret-access-token' })
      expect(createObjectURL).toHaveBeenCalled()
      vi.useRealTimers()
    })

    it('throws when the server refuses', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => new Response(null, { status: 404 })),
      )

      await expect(downloadAdminAttachmentRequest(ATTACHMENT)).rejects.toThrow(
        'Could not download this attachment.',
      )
    })
  })
})
