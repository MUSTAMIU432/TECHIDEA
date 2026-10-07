import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PlatformReviewReport } from '../api/reviewsApi'
import { IdeaReportPage } from './IdeaReportPage'

vi.mock('../api/reviewsApi', () => ({
  ideaReviewReportRequest: vi.fn(),
  ideaSubmissionVersionsRequest: vi.fn(async () => []),
}))

vi.mock('../../proposals/api/proposalsApi', () => ({ proposalStateRequest: vi.fn() }))

const { ideaReviewReportRequest: reportMock, ideaSubmissionVersionsRequest: versionsMock } =
  await import('../api/reviewsApi')
const { proposalStateRequest: proposalMock } = await import('../../proposals/api/proposalsApi')

const REPORT: PlatformReviewReport = {
  id: 'r1',
  ideaId: '1',
  reviewId: '9',
  round: 1,
  decision: 'APPROVED',
  submissionContext: 'ORGANIZATION',
  tenantName: 'Acme Labs',
  reviewerId: '8',
  reviewerFirstName: 'Rae',
  criteria: [{ criterion: 'EVIDENCE', rating: 'MEETS', note: 'The figures are there.' }],
  reviewSummary: 'Clear, and worth doing.',
  feedback: '',
  recommendations: 'Start with the monthly run.',
  importantConsiderations: '',
  constraints: '',
  nextSteps: 'A developer picks it up.',
  approvalSummary: 'Approved for implementation, subject to the author’s go-ahead.',
  approvedAt: '2026-02-01T00:00:00.000Z',
  generatedAt: '2026-02-01T00:00:00.000Z',
}

/**
 * Rendered through a real route so the page reads the idea id from the URL the
 * way it does in the app, rather than through a prop it does not have.
 */
function renderAt(ideaId = '1') {
  return render(
    <RouterProvider
      router={createMemoryRouter(
        [{ path: '/app/ideas/:ideaId/report', element: <IdeaReportPage /> }],
        {
          initialEntries: [`/app/ideas/${ideaId}/report`],
        },
      )}
    />,
  )
}

describe('IdeaReportPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(proposalMock).mockResolvedValue({ proposal: null } as never)
    vi.mocked(reportMock).mockResolvedValue(REPORT)
    vi.mocked(versionsMock).mockResolvedValue([])
  })

  it('presents the report as the reviewer’s conclusion, not as the approval', async () => {
    renderAt()

    expect(
      await screen.findByRole('heading', { level: 1, name: 'What the platform concluded.' }),
    ).toBeInTheDocument()
    // The distinction is the whole reason this page exists as its own thing.
    expect(screen.getByText(/not the approval/i)).toBeInTheDocument()
  })

  it('shows the criteria as ratings and notes, with no arithmetic over them', async () => {
    renderAt()

    const assessed = await screen.findByRole('region', { name: 'How it was assessed' })
    expect(assessed).toHaveTextContent('EVIDENCE')
    expect(assessed).toHaveTextContent('MEETS')
    expect(assessed).toHaveTextContent('The figures are there.')
    // A score card would be read as a measurement the platform did not make.
    expect(assessed.textContent).not.toMatch(/\b\d+\s*\/\s*\d+\b/)
  })

  it('points the author to the proposal once it is released, and has no go-ahead button of its own', async () => {
    vi.mocked(proposalMock).mockResolvedValue({ proposal: { status: 'released' } } as never)
    renderAt()

    expect(await screen.findByRole('link', { name: /Read the proposal/ })).toHaveAttribute(
      'href',
      '/app/ideas/1/proposal',
    )
    expect(screen.queryByRole('button', { name: 'Give the go-ahead' })).toBeNull()
  })

  it('says the proposal is being prepared before it is released', async () => {
    vi.mocked(proposalMock).mockResolvedValue({ proposal: null } as never)
    renderAt()

    expect(await screen.findByText(/The review team is preparing a proposal/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Read the proposal/ })).toBeNull()
  })

  it('offers nothing about a proposal on a report that did not recommend approval', async () => {
    vi.mocked(reportMock).mockResolvedValue({ ...REPORT, decision: 'CHANGES_REQUESTED' })
    renderAt()

    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByRole('region', { name: 'Your proposal' })).toBeNull()
  })

  it('says there is nothing here without saying why', async () => {
    // Null is the answer for no report, for a report that is not approved, and
    // for a viewer who may not see it — one answer, so the page cannot be used
    // to find out whether an idea has been approved.
    vi.mocked(reportMock).mockResolvedValue(null)
    renderAt()

    expect(
      await screen.findByText('There is no review report for this idea yet.'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/not approved/i)).not.toBeInTheDocument()
  })

  it('lists each frozen submission as its own version', async () => {
    vi.mocked(versionsMock).mockResolvedValue([
      {
        id: 'v1',
        ideaId: '1',
        version: 1,
        submittedAt: '2026-01-01T00:00:00.000Z',
        title: 'Automate the invoice run',
        description: 'The first version.',
      },
      {
        id: 'v2',
        ideaId: '1',
        version: 2,
        submittedAt: '2026-01-20T00:00:00.000Z',
        title: 'Automate the invoice run',
        description: 'After changes.',
      },
    ])
    renderAt()

    const submitted = await screen.findByRole('region', { name: 'What was submitted' })
    expect(submitted).toHaveTextContent('Version 1')
    expect(submitted).toHaveTextContent('Version 2')
  })
})
