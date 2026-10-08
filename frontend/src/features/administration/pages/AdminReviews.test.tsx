import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AdminReview, AdminReviewDetail, AdminReviewedIdeaRow } from '../api/administrationApi'
import { pageOfItems, renderAdminPage } from '../../../test/renderAdmin'
import { AdminReviewDetailPage } from './AdminReviewDetailPage'
import { AdminApprovalsPage, AdminReviewsPage } from './AdminReviewsPage'

vi.mock('../api/administrationApi', async (importOriginal) => ({
  ...(await importOriginal()),
  adminReviewsRequest: vi.fn(),
  adminReviewedIdeasRequest: vi.fn(),
  adminReviewRequest: vi.fn(),
}))

const api = await import('../api/administrationApi')
const reviewsMock = vi.mocked(api.adminReviewsRequest)
const reviewedIdeasMock = vi.mocked(api.adminReviewedIdeasRequest)
const reviewMock = vi.mocked(api.adminReviewRequest)

function review(overrides: Partial<AdminReview> = {}): AdminReview {
  return {
    id: '40',
    round: 2,
    idea: {
      id: '7',
      title: 'Automate the invoice run',
      status: 'UNDER_REVIEW',
      visibility: 'ORGANIZATION',
      submissionContext: 'ORGANIZATION',
      organization: { id: '3', name: 'Acme' },
      teamName: null,
    },
    reviewer: { id: '9', email: 'rae@acme.example', name: 'Rae Reviewer' },
    decision: null,
    isCompleted: false,
    createdAt: '2026-01-03T00:00:00Z',
    completedAt: null,
    contentRestricted: false,
    feedback: '',
    assessments: [],
    ...overrides,
  }
}

function reviewedIdea(rounds: AdminReview[]): AdminReviewedIdeaRow {
  const latest = rounds[rounds.length - 1]
  return {
    idea: latest.idea,
    roundCount: rounds.length,
    latest,
    rounds,
    startedAt: rounds[0].createdAt,
    lastActivityAt: latest.completedAt ?? latest.createdAt,
  }
}

describe('AdminReviewsPage', () => {
  beforeEach(() => reviewedIdeasMock.mockReset())

  it('lists each reviewed idea once, where its latest round left it', async () => {
    reviewedIdeasMock.mockResolvedValue(
      pageOfItems([
        reviewedIdea([
          review({
            id: '41',
            round: 1,
            decision: 'CHANGES_REQUESTED',
            isCompleted: true,
            completedAt: '2026-01-02T00:00:00Z',
          }),
          review(),
        ]),
      ]),
    )
    renderAdminPage(<AdminReviewsPage />)

    const rows = within(await screen.findByRole('table', { name: 'Reviews' })).getAllByRole('row')
    expect(rows).toHaveLength(2) // the header and one idea, not one row per round
    expect(rows[1]).toHaveTextContent('Automate the invoice run')
    expect(rows[1]).toHaveTextContent('Rae Reviewer')
    expect(rows[1]).toHaveTextContent('In progress')
    expect(rows[1]).toHaveTextContent('round 2')
    expect(rows[1]).not.toHaveTextContent('Changes requested')
    expect(screen.queryByRole('button', { name: /edit|override|approve/i })).not.toBeInTheDocument()
  })

  it('opens the rounds that got the idea there', async () => {
    reviewedIdeasMock.mockResolvedValue(
      pageOfItems([
        reviewedIdea([
          review({ id: '41', round: 1, decision: 'CHANGES_REQUESTED', isCompleted: true }),
          review(),
        ]),
      ]),
    )
    renderAdminPage(<AdminReviewsPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Show rounds (2)' }))

    const rounds = screen.getByRole('list', { name: 'Rounds of Automate the invoice run' })
    const items = within(rounds).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent('Round 1')
    expect(items[0]).toHaveTextContent('Changes requested')
    expect(items[1]).toHaveTextContent('Round 2')
    expect(within(items[0]).getByRole('link', { name: 'Open' })).toHaveAttribute(
      'href',
      '/app/admin/reviews/41',
    )
  })

  it('filters by state on the server', async () => {
    reviewedIdeasMock.mockResolvedValue(pageOfItems([]))
    renderAdminPage(<AdminReviewsPage />)
    await screen.findByText('No reviewed ideas match.')

    fireEvent.change(screen.getByRole('combobox', { name: 'Review state' }), {
      target: { value: 'OPEN' },
    })

    await waitFor(() =>
      expect(reviewedIdeasMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ state: 'OPEN' }),
        { offset: 0 },
      ),
    )
  })

  it('shows an error state', async () => {
    reviewedIdeasMock.mockRejectedValueOnce(new Error('down'))
    renderAdminPage(<AdminReviewsPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('We could not load the reviews.')
  })
})

describe('AdminApprovalsPage', () => {
  beforeEach(() => reviewsMock.mockReset())

  it('asks only for completed lifecycle decisions and shows the idea’s status now', async () => {
    reviewsMock.mockResolvedValue(
      pageOfItems([
        review({
          decision: 'APPROVED',
          isCompleted: true,
          completedAt: '2026-01-04T00:00:00Z',
          idea: { ...review().idea, status: 'APPROVED' },
        }),
      ]),
    )
    renderAdminPage(<AdminApprovalsPage />)

    const table = await screen.findByRole('table', { name: 'Decisions' })
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map((cell) => cell.textContent),
    ).toContain('Idea now')
    expect(reviewsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        state: 'COMPLETED',
        decisions: ['CHANGES_REQUESTED', 'APPROVED', 'REJECTED'],
      }),
      { offset: 0 },
    )
    expect(screen.getByText(/cannot override one/)).toBeInTheDocument()
  })

  it('narrows to one decision', async () => {
    reviewsMock.mockResolvedValue(pageOfItems([]))
    renderAdminPage(<AdminApprovalsPage />)
    expect(await screen.findByText('No decisions match.')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('combobox', { name: 'Decision' }), {
      target: { value: 'REJECTED' },
    })

    await waitFor(() =>
      expect(reviewsMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ decisions: ['REJECTED'] }),
        { offset: 0 },
      ),
    )
  })
})

describe('AdminReviewDetailPage', () => {
  beforeEach(() => reviewMock.mockReset())

  function detail(overrides: Partial<AdminReviewDetail> = {}): AdminReviewDetail {
    return {
      ...review(),
      isStalled: false,
      submissionSnapshot: { title: 'Automate the invoice run', description: 'By hand today.' },
      history: [review()],
      ...overrides,
    }
  }

  function renderDetail() {
    return renderAdminPage(<AdminReviewDetailPage />, {
      path: '/reviews/40',
      pattern: '/reviews/:reviewId',
    })
  }

  it('shows the round, what the reviewer saw, and the idea’s history', async () => {
    reviewMock.mockResolvedValue(detail())
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Review round 2' })).toBeVisible()
    expect(screen.getByText('By hand today.')).toBeInTheDocument()
    expect(screen.queryByText(/stalled/i)).not.toBeInTheDocument()
  })

  it('flags a stalled review and explains how it is resolved', async () => {
    reviewMock.mockResolvedValue(detail({ isStalled: true }))
    renderDetail()

    expect(await screen.findByText(/This review is stalled/)).toHaveTextContent(
      'Another reviewer in Acme can take it over',
    )
  })

  it('marks the snapshot restricted when withheld', async () => {
    reviewMock.mockResolvedValue(
      detail({
        submissionSnapshot: null,
        contentRestricted: true,
        feedback: null,
        assessments: null,
      }),
    )
    renderDetail()

    expect(
      await screen.findByText('The submission snapshot needs the content-inspection permission'),
    ).toBeVisible()
  })
})
