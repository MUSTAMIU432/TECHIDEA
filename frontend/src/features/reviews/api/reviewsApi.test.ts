import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  completeReviewRequest,
  ideaReviewsRequest,
  reviewQueueRequest,
  startReviewRequest,
  viewerCanReviewInRequest,
} from './reviewsApi'

/**
 * The Reviews documents (S3-003), asserted on the wire: what is sent, and
 * that the answer is handed back as the server gave it.
 */
describe('reviewsApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
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

  it('asks for one organization’s queue with the full idea shape', async () => {
    const fetchMock = stubFetch({ reviewQueue: PAGE })

    const result = await reviewQueueRequest('3', { offset: 20 })

    const request = sent(fetchMock)
    expect(request.query).toContain('reviewQueue(organizationId: $organizationId')
    expect(request.query).toContain('viewerCanStartReview')
    expect(request.query).toContain('viewerActiveReviewId')
    expect(request.variables).toEqual({ organizationId: '3', offset: 20 })
    expect(result).toEqual(PAGE)
  })

  it('omits paging arguments that were not given', async () => {
    const fetchMock = stubFetch({ reviewQueue: PAGE })

    await reviewQueueRequest('3')

    expect(sent(fetchMock).variables).toEqual({ organizationId: '3' })
  })

  it('asks for an idea’s review history by the idea, never by a review id', async () => {
    const reviews = [
      {
        id: '9',
        ideaId: '1',
        round: 1,
        reviewerId: '4',
        decision: 'CHANGES_REQUESTED',
        feedback: 'Add numbers.',
        assessments: [],
        createdAt: '2026-01-01T00:00:00.000Z',
        completedAt: '2026-01-02T00:00:00.000Z',
        submissionSnapshot: null,
      },
    ]
    const fetchMock = stubFetch({ ideaReviews: reviews })

    const result = await ideaReviewsRequest('1')

    const request = sent(fetchMock)
    expect(request.query).toContain('ideaReviews(ideaId: $ideaId)')
    expect(request.variables).toEqual({ ideaId: '1' })
    expect(result).toEqual(reviews)
  })

  const REFUSED = { success: false, message: 'No.', field: null, review: null, idea: null }

  it('starts a review by the idea id and hands back the payload', async () => {
    const fetchMock = stubFetch({ startReview: REFUSED })

    await expect(startReviewRequest('1')).resolves.toEqual(REFUSED)

    const request = sent(fetchMock)
    expect(request.query).toContain('startReview(ideaId: $ideaId)')
    expect(request.query).toContain('viewerActiveReviewId')
    expect(request.variables).toEqual({ ideaId: '1' })
  })

  it('completes a review with every assessment in one input', async () => {
    const fetchMock = stubFetch({ completeReview: REFUSED })
    const input = {
      ideaId: '1',
      reviewId: '12',
      decision: 'APPROVED' as const,
      feedback: '',
      assessments: [{ criterion: 'EVIDENCE' as const, rating: 'MEETS' as const, note: '' }],
    }

    await completeReviewRequest(input)

    const request = sent(fetchMock)
    expect(request.query).toContain('completeReview(input: $input)')
    expect(request.variables).toEqual({ input })
  })

  it('asks whether the viewer reviews in an organization', async () => {
    const fetchMock = stubFetch({ viewerCanReviewIn: true })

    await expect(viewerCanReviewInRequest('3')).resolves.toBe(true)
    expect(sent(fetchMock).variables).toEqual({ organizationId: '3' })
  })
})
