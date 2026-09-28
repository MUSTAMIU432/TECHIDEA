import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Idea, IdeaPage } from '../../ideas/api/ideasApi'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { pageOf } from '../../../test/ideaPage'
import type { Review } from '../api/reviewsApi'
import { ReviewsWorkspace } from './ReviewsWorkspace'

vi.mock('../api/reviewsApi', () => ({
  viewerCanReviewInRequest: vi.fn(),
  reviewQueueRequest: vi.fn(),
  ideaReviewsRequest: vi.fn(),
}))
vi.mock('../../ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  attachmentsRequest: vi.fn(async () => pageOf([])),
}))
vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))

const { viewerCanReviewInRequest, reviewQueueRequest, ideaReviewsRequest } =
  await import('../api/reviewsApi')
const canReviewMock = vi.mocked(viewerCanReviewInRequest)
const queueMock = vi.mocked(reviewQueueRequest)
const historyMock = vi.mocked(ideaReviewsRequest)

function idea(overrides: Partial<Idea> = {}): Idea {
  return {
    id: '1',
    title: 'Automate the invoice run',
    description: 'We key every invoice in by hand.',
    status: 'SUBMITTED',
    visibility: 'ORGANIZATION',
    submittedAt: '2026-01-02T00:00:00.000Z',
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    authorId: '8',
    organizationId: '3',
    category: { id: '4', name: 'Finance', slug: 'finance', description: '' },
    availableTransitions: [],
    discussionOpen: true,
    voteCount: 0,
    viewerHasVoted: false,
    viewerCanStartReview: true,
    viewerActiveReviewId: null,
    ...overrides,
  }
}

function review(overrides: Partial<Review> = {}): Review {
  return {
    id: '9',
    ideaId: '1',
    round: 1,
    reviewerId: '7',
    decision: 'CHANGES_REQUESTED',
    feedback: 'Add the monthly volume.',
    assessments: [{ criterion: 'EVIDENCE', rating: 'DOES_NOT_MEET', note: '' }],
    createdAt: '2026-01-03T00:00:00.000Z',
    completedAt: '2026-01-04T00:00:00.000Z',
    submissionSnapshot: { title: 'Automate the invoice run' },
    ...overrides,
  }
}

function mockContext(
  organization: { id: string; name: string } | null = { id: '3', name: 'Acme' },
) {
  vi.mocked(useAuth).mockReturnValue({
    user: { id: '7', email: 'reviewer@acme.example' },
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: organization,
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
}

function renderWorkspace() {
  return render(
    <MemoryRouter>
      <ReviewsWorkspace />
    </MemoryRouter>,
  )
}

const queueList = () => screen.findByRole('list', { name: 'Ideas waiting for review' })

describe('ReviewsWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockContext()
    canReviewMock.mockResolvedValue(true)
    queueMock.mockResolvedValue(pageOf([idea()]))
    historyMock.mockResolvedValue([])
  })

  it('asks for a queue only once it knows the viewer reviews here', async () => {
    renderWorkspace()

    expect(
      await within(await queueList()).findByText('Automate the invoice run'),
    ).toBeInTheDocument()
    expect(canReviewMock).toHaveBeenCalledWith('3')
    expect(queueMock).toHaveBeenCalledWith('3', { offset: 0 })
  })

  it('tells a non-reviewer why, and never asks for the queue', async () => {
    canReviewMock.mockResolvedValue(false)

    renderWorkspace()

    expect(await screen.findByText(/You do not review ideas in Acme/)).toBeInTheDocument()
    expect(queueMock).not.toHaveBeenCalled()
  })

  it('asks the viewer to pick an organization when none is active', () => {
    mockContext(null)

    renderWorkspace()

    expect(screen.getByText(/Choose an organization/)).toBeInTheDocument()
    expect(canReviewMock).not.toHaveBeenCalled()
  })

  it('shows a loading state, then the empty queue', async () => {
    let resolve: (page: IdeaPage) => void = () => {}
    queueMock.mockReturnValue(new Promise((r) => (resolve = r)))

    renderWorkspace()

    expect(await screen.findByText('Loading the review queue…')).toBeInTheDocument()
    await act(async () => resolve(pageOf([])))
    expect(screen.getByText(/Nothing is waiting for review/)).toBeInTheDocument()
  })

  it('reports a failed request as a failure, not as an empty queue', async () => {
    queueMock.mockRejectedValue(new Error('offline'))

    renderWorkspace()

    expect(await screen.findByRole('alert')).toHaveTextContent('could not load the review queue')
    expect(screen.queryByText(/Nothing is waiting/)).not.toBeInTheDocument()
  })

  it('pages by the server’s offsets', async () => {
    queueMock.mockImplementation(async (_organizationId, page = {}) =>
      pageOf([idea({ id: String((page.offset ?? 0) + 1), title: `Idea at ${page.offset ?? 0}` })], {
        offset: page.offset ?? 0,
        limit: 20,
        totalCount: 45,
        hasNextPage: (page.offset ?? 0) + 20 < 45,
        hasPreviousPage: (page.offset ?? 0) > 0,
      }),
    )

    renderWorkspace()
    await screen.findByText('Idea at 0')

    fireEvent.click(screen.getByRole('button', { name: /next/i }))

    expect(await screen.findByText('Idea at 20')).toBeInTheDocument()
    expect(queueMock).toHaveBeenLastCalledWith('3', { offset: 20 })
  })

  it('discards a superseded answer that arrives late', async () => {
    const resolvers: Array<(page: IdeaPage) => void> = []
    queueMock.mockImplementation(() => new Promise<IdeaPage>((resolve) => resolvers.push(resolve)))

    renderWorkspace()
    await waitFor(() => expect(resolvers).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(resolvers).toHaveLength(2))

    await act(async () => resolvers[1](pageOf([idea({ title: 'Current answer' })])))
    await act(async () => resolvers[0](pageOf([idea({ title: 'Stale answer' })])))

    expect(screen.getByText('Current answer')).toBeInTheDocument()
    expect(screen.queryByText('Stale answer')).not.toBeInTheDocument()
  })

  it('opens an idea’s review context with its history', async () => {
    historyMock.mockResolvedValue([
      review(),
      review({
        id: '10',
        round: 2,
        decision: null,
        completedAt: null,
        feedback: '',
        assessments: [],
      }),
    ])

    renderWorkspace()
    fireEvent.click(
      await within(await queueList()).findByRole('button', { name: /Automate the invoice run/ }),
    )

    const context = screen.getByRole('article')
    expect(within(context).getByText('We key every invoice in by hand.')).toBeInTheDocument()
    expect(within(context).getByText(/You can review this idea/)).toBeInTheDocument()
    const history = await within(context).findByRole('list', { name: 'Review history' })
    expect(within(history).getByText('Add the monthly volume.')).toBeInTheDocument()
    expect(within(history).getByText('Changes requested')).toBeInTheDocument()
    expect(within(history).getByText('In progress')).toBeInTheDocument()
    expect(within(history).getByText('Does not meet')).toBeInTheDocument()
    expect(historyMock).toHaveBeenCalledWith('1')
  })

  it('offers no decision controls - claiming and deciding are not part of this workspace', async () => {
    renderWorkspace()
    fireEvent.click(await within(await queueList()).findByRole('button', { name: /Automate/ }))

    for (const name of [/start review/i, /approve/i, /reject/i, /request changes/i]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
  })
})
