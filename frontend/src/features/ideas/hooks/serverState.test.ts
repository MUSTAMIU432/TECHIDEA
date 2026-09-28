import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { pageOf } from '../../../test/ideaPage'
import type { Idea, IdeaAttachment, IdeaComment } from '../api/ideasApi'
import { useAttachments } from './useAttachments'
import { useComments } from './useComments'
import { useIdeaVotes } from './useIdeaVotes'

/**
 * S2-008 regressions: a hook's state must always be the server's *latest*
 * answer.
 *
 * Two failure modes, both about time rather than about any one response:
 *
 * - **A superseded request settling last.** Closing and reopening a discussion
 *   (or reloading it after a write) asks the same question twice. Keys cannot
 *   tell the two answers apart, so without the effect-cleanup guard the older
 *   one - or its failure - overwrote the newer one.
 * - **Local state outliving the server's next answer.** A vote response is the
 *   server's word only until the list is fetched again.
 */

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  commentsRequest: vi.fn(),
  attachmentsRequest: vi.fn(),
  voteIdeaRequest: vi.fn(),
  removeVoteRequest: vi.fn(),
}))

const { attachmentsRequest, commentsRequest, voteIdeaRequest } = await import('../api/ideasApi')
const commentsMock = vi.mocked(commentsRequest)
const attachmentsMock = vi.mocked(attachmentsRequest)
const voteMock = vi.mocked(voteIdeaRequest)

afterEach(() => {
  vi.clearAllMocks()
})

/** A request whose answer the test decides, and when. */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function comment(id: string, content: string): IdeaComment {
  return {
    id,
    ideaId: '1',
    authorId: '7',
    content,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
  }
}

function attachment(id: string, filename: string): IdeaAttachment {
  return {
    id,
    ideaId: '1',
    uploaderId: '7',
    filename,
    contentType: 'application/pdf',
    size: 10,
    createdAt: '2026-01-01T00:00:00.000Z',
    downloadUrl: `/ideas/1/attachments/${id}/download/`,
  }
}

function idea(overrides: Partial<Idea> = {}): Idea {
  return {
    id: '1',
    title: 'Automate the invoice run',
    description: 'A description long enough.',
    status: 'SUBMITTED',
    visibility: 'ORGANIZATION',
    submittedAt: '2026-01-01T00:00:00.000Z',
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    authorId: '8',
    organizationId: '3',
    category: null,
    availableTransitions: [],
    discussionOpen: true,
    voteCount: 0,
    viewerHasVoted: false,
    viewerCanStartReview: false,
    viewerActiveReviewId: null,
    ...overrides,
  }
}

/**
 * Open the thread (request A), close it, reopen it (request B), with both
 * still in flight - the same question asked twice, which is exactly the case a
 * key comparison cannot resolve.
 */
function askTwice<Hook>(useHook: (ideaId: string | null) => Hook) {
  const view = renderHook(({ ideaId }: { ideaId: string | null }) => useHook(ideaId), {
    initialProps: { ideaId: '1' as string | null },
  })
  view.rerender({ ideaId: null })
  view.rerender({ ideaId: '1' })
  return view
}

describe('useComments', () => {
  const useThread = (ideaId: string | null) => useComments(ideaId, true)

  it('discards an older answer that settles after the newer one', async () => {
    const first = deferred<ReturnType<typeof pageOf<IdeaComment>>>()
    const second = deferred<ReturnType<typeof pageOf<IdeaComment>>>()
    commentsMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { result } = askTwice(useThread)
    expect(commentsMock).toHaveBeenCalledTimes(2)

    await act(async () => second.resolve(pageOf([comment('1', 'Old'), comment('2', 'New')])))
    await waitFor(() => expect(result.current.loading).toBe(false))

    await act(async () => first.resolve(pageOf([comment('1', 'Old')])))

    expect(result.current.comments.map((c) => c.content)).toEqual(['Old', 'New'])
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('does not let an older request’s failure wipe the newer answer', async () => {
    const first = deferred<ReturnType<typeof pageOf<IdeaComment>>>()
    const second = deferred<ReturnType<typeof pageOf<IdeaComment>>>()
    commentsMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { result } = askTwice(useThread)

    await act(async () => second.resolve(pageOf([comment('2', 'New')])))
    await act(async () => first.reject(new Error('Failed to fetch')))

    expect(result.current.error).toBeNull()
    expect(result.current.loading).toBe(false)
    expect(result.current.comments.map((c) => c.content)).toEqual(['New'])
  })
})

describe('useAttachments', () => {
  it('discards an older answer that settles after the newer one', async () => {
    const first = deferred<ReturnType<typeof pageOf<IdeaAttachment>>>()
    const second = deferred<ReturnType<typeof pageOf<IdeaAttachment>>>()
    attachmentsMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { result } = askTwice(useAttachments)

    await act(async () => second.resolve(pageOf([attachment('2', 'current.pdf')])))
    await act(async () => first.resolve(pageOf([attachment('1', 'deleted.pdf')])))

    expect(result.current.attachments.map((a) => a.filename)).toEqual(['current.pdf'])
    expect(result.current.loading).toBe(false)
  })

  it('does not let an older request’s failure wipe the newer answer', async () => {
    const first = deferred<ReturnType<typeof pageOf<IdeaAttachment>>>()
    const second = deferred<ReturnType<typeof pageOf<IdeaAttachment>>>()
    attachmentsMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)

    const { result } = askTwice(useAttachments)

    await act(async () => second.resolve(pageOf([attachment('2', 'current.pdf')])))
    await act(async () => first.reject(new Error('Failed to fetch')))

    expect(result.current.error).toBeNull()
    expect(result.current.attachments.map((a) => a.filename)).toEqual(['current.pdf'])
  })
})

describe('useIdeaVotes', () => {
  it('lets a refreshed list replace the numbers a vote response left behind', async () => {
    // 1. Initial server state.
    const view = renderHook(({ ideas }: { ideas: Idea[] }) => useIdeaVotes(ideas), {
      initialProps: { ideas: [idea({ voteCount: 2, viewerHasVoted: false })] },
    })
    expect(view.result.current.votes['1']).toMatchObject({ voteCount: 2, viewerHasVoted: false })

    // 2. The reader votes; the UI shows the server's answer to the mutation.
    voteMock.mockResolvedValue({
      success: true,
      message: 'Vote recorded.',
      field: null,
      voteState: { ideaId: '1', voteCount: 3, viewerHasVoted: true },
    })
    act(() => view.result.current.toggle('1'))
    await waitFor(() =>
      expect(view.result.current.votes['1']).toMatchObject({ voteCount: 3, pending: false }),
    )

    // 3. The list is fetched again and the server now says something else -
    //    other people voted, and this reader's vote was withdrawn elsewhere.
    view.rerender({ ideas: [idea({ voteCount: 5, viewerHasVoted: false })] })

    // 4. The refreshed server state wins; the old response is not pinned.
    expect(view.result.current.votes['1']).toMatchObject({
      voteCount: 5,
      viewerHasVoted: false,
      pending: false,
      error: null,
    })
  })

  it('keeps an undismissed error across a refresh, but not stale numbers', async () => {
    const view = renderHook(({ ideas }: { ideas: Idea[] }) => useIdeaVotes(ideas), {
      initialProps: { ideas: [idea({ voteCount: 1 })] },
    })
    voteMock.mockResolvedValue({
      success: false,
      message: 'Idea is unavailable.',
      field: null,
      voteState: null,
    })
    act(() => view.result.current.toggle('1'))
    await waitFor(() => expect(view.result.current.votes['1'].error).toBe('Idea is unavailable.'))

    view.rerender({ ideas: [idea({ voteCount: 4 })] })

    expect(view.result.current.votes['1']).toMatchObject({
      voteCount: 4,
      error: 'Idea is unavailable.',
    })
  })

  it('does not reset anything when it is handed the same answer again', async () => {
    const ideas = [idea({ voteCount: 2 })]
    const view = renderHook(({ list }: { list: Idea[] }) => useIdeaVotes(list), {
      initialProps: { list: ideas },
    })
    voteMock.mockResolvedValue({
      success: true,
      message: 'Vote recorded.',
      field: null,
      voteState: { ideaId: '1', voteCount: 3, viewerHasVoted: true },
    })
    act(() => view.result.current.toggle('1'))
    await waitFor(() => expect(view.result.current.votes['1'].voteCount).toBe(3))

    // A re-render with the same answer is not a new answer.
    view.rerender({ list: ideas })
    expect(view.result.current.votes['1']).toMatchObject({ voteCount: 3, viewerHasVoted: true })
  })
})
