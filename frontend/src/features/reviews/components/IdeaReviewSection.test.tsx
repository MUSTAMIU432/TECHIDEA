import { makeIdea } from '../../../test/idea'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Idea } from '../../ideas/api/ideasApi'
import { IdeaCardActions } from '../../ideas/components/IdeaCardActions'
import { IdeaReviewSection } from './IdeaReviewSection'

vi.mock('../api/reviewsApi', () => ({
  ideaReviewsRequest: vi.fn(),
}))

const { ideaReviewsRequest } = await import('../api/reviewsApi')
const historyMock = vi.mocked(ideaReviewsRequest)

/**
 * The card's footer and its review panel, which is what the Ideas list draws.
 *
 * Both halves are rendered because they answer one question between them: the
 * bar decides whether a review cell is offered at all and the panel decides
 * what is in it, so a test that asked only one of them could pass while the
 * card offered a cell that led nowhere.
 */
function renderSection(subject: Idea, viewerId: string | null, open = false) {
  const onToggleSection = vi.fn()
  render(
    <MemoryRouter>
      <IdeaCardActions
        idea={subject}
        viewerId={viewerId}
        vote={{ voteCount: 0, viewerHasVoted: false, pending: false, error: null }}
        onToggleVote={vi.fn()}
        onDismissVoteError={vi.fn()}
        open={{ discussion: false, evidence: false, review: open }}
        onToggleSection={onToggleSection}
      />
      <IdeaReviewSection idea={subject} viewerId={viewerId} open={open} />
    </MemoryRouter>,
  )
  return onToggleSection
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
    const onToggleSection = renderSection(makeIdea({ status: 'SUBMITTED' }), '7')

    fireEvent.click(screen.getByRole('button', { name: 'Review history' }))

    expect(onToggleSection).toHaveBeenCalledWith('review')
    expect(historyMock).not.toHaveBeenCalled()
  })

  it('offers the author nothing on a draft', () => {
    renderSection(makeIdea({ status: 'DRAFT' }), '7')

    expect(screen.queryByRole('button', { name: 'Review history' })).not.toBeInTheDocument()
  })

  it('offers an ordinary reader nothing at all', () => {
    renderSection(makeIdea({ status: 'SUBMITTED' }), '99')

    expect(screen.queryByRole('button', { name: 'Review history' })).not.toBeInTheDocument()
  })

  it('points a reviewer who can start a review at it in the review workspace', () => {
    renderSection(makeIdea({ status: 'SUBMITTED', viewerCanStartReview: true }), '99')

    expect(screen.getByRole('link', { name: /open it in the review workspace/ })).toHaveAttribute(
      'href',
      '/app/reviews?idea=1',
    )
  })

  it('points a reviewer at a stalled review they may take over', () => {
    renderSection(makeIdea({ status: 'UNDER_REVIEW', viewerCanStartReview: true }), '99')

    expect(screen.getByRole('link', { name: /Review stalled — take it over/ })).toHaveAttribute(
      'href',
      '/app/reviews?idea=1',
    )
  })

  it('tells a reviewer with an active review that it is theirs', () => {
    renderSection(makeIdea({ status: 'UNDER_REVIEW', viewerActiveReviewId: '12' }), '99')

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
        scope: 'PLATFORM',
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

    renderSection(makeIdea({ status: 'SUBMITTED' }), '7', true)

    expect(await screen.findByText('Add the monthly volume.')).toBeInTheDocument()
    expect(historyMock).toHaveBeenCalledWith('1')
  })

  it('shows an empty history as "no reviews", not as a refusal', async () => {
    renderSection(makeIdea({ status: 'SUBMITTED' }), '7', true)

    expect(await screen.findByText('No reviews yet.')).toBeInTheDocument()
  })

  it('reports a failed history request as a failure', async () => {
    historyMock.mockRejectedValue(new Error('offline'))

    renderSection(makeIdea({ status: 'SUBMITTED' }), '7', true)

    expect(await screen.findByRole('alert')).toHaveTextContent('could not load the review history')
  })
})
