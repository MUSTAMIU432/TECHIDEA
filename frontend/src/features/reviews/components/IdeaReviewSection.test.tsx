import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Idea } from '../../ideas/api/ideasApi'
import { IdeaReviewSection } from './IdeaReviewSection'

vi.mock('../api/reviewsApi', () => ({
  ideaReviewsRequest: vi.fn(),
}))

const { ideaReviewsRequest } = await import('../api/reviewsApi')
const historyMock = vi.mocked(ideaReviewsRequest)

function idea(overrides: Partial<Idea> = {}): Idea {
  return {
    id: '1',
    title: 'Automate the invoice run',
    description: 'A description long enough.',
    status: 'CHANGES_REQUESTED',
    visibility: 'ORGANIZATION',
    submittedAt: '2026-01-02T00:00:00.000Z',
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    authorId: '7',
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

function renderSection(subject: Idea, viewerId: string | null, open = false) {
  const onToggle = vi.fn()
  render(
    <MemoryRouter>
      <IdeaReviewSection idea={subject} viewerId={viewerId} open={open} onToggle={onToggle} />
    </MemoryRouter>,
  )
  return onToggle
}

/**
 * The card's review section is drawn from the server's capability fields and
 * authorship alone: no role is checked here, and nothing is fetched until the
 * section is opened.
 */
describe('IdeaReviewSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    historyMock.mockResolvedValue([])
  })

  it('offers the author the history of an idea they have put forward', () => {
    const onToggle = renderSection(idea(), '7')

    fireEvent.click(screen.getByRole('button', { name: 'Review history' }))

    expect(onToggle).toHaveBeenCalled()
    expect(historyMock).not.toHaveBeenCalled()
  })

  it('offers the author nothing on a draft', () => {
    renderSection(idea({ status: 'DRAFT' }), '7')

    expect(screen.queryByRole('button', { name: 'Review history' })).not.toBeInTheDocument()
  })

  it('offers an ordinary reader nothing at all', () => {
    renderSection(idea(), '99')

    expect(screen.queryByRole('button', { name: 'Review history' })).not.toBeInTheDocument()
  })

  it('points a reviewer who can start a review at it in the review workspace', () => {
    renderSection(idea({ status: 'SUBMITTED', viewerCanStartReview: true }), '99')

    expect(screen.getByRole('link', { name: /open it in the review workspace/ })).toHaveAttribute(
      'href',
      '/app/reviews?idea=1',
    )
  })

  it('tells a reviewer with an active review that it is theirs', () => {
    renderSection(idea({ status: 'UNDER_REVIEW', viewerActiveReviewId: '12' }), '99')

    expect(screen.getByRole('link', { name: /continue review/ })).toHaveAttribute(
      'href',
      '/app/reviews?idea=1',
    )
  })

  it('fetches and shows the history once open', async () => {
    historyMock.mockResolvedValue([
      {
        id: '9',
        ideaId: '1',
        round: 1,
        reviewerId: '4',
        decision: 'CHANGES_REQUESTED',
        feedback: 'Add the monthly volume.',
        assessments: [],
        createdAt: '2026-01-03T00:00:00.000Z',
        completedAt: '2026-01-04T00:00:00.000Z',
        submissionSnapshot: null,
      },
    ])

    renderSection(idea(), '7', true)

    expect(await screen.findByText('Add the monthly volume.')).toBeInTheDocument()
    expect(historyMock).toHaveBeenCalledWith('1')
  })

  it('shows an empty history as "no reviews", not as a refusal', async () => {
    renderSection(idea(), '7', true)

    expect(await screen.findByText('No reviews yet.')).toBeInTheDocument()
  })

  it('reports a failed history request as a failure', async () => {
    historyMock.mockRejectedValue(new Error('offline'))

    renderSection(idea(), '7', true)

    expect(await screen.findByRole('alert')).toHaveTextContent('could not load the review history')
  })
})
