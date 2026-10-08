import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { EMPTY_PROBLEM_STORY } from '../../ideas/utils/problemStory'
import type { AdminIdea, AdminIdeaDetail, AdminReview } from '../api/administrationApi'
import { pageOfItems, READ_ONLY_ADMIN, renderAdminPage } from '../../../test/renderAdmin'
import { AdminIdeaDetailPage } from './AdminIdeaDetailPage'
import { AdminIdeasPage } from './AdminIdeasPage'

vi.mock('../api/reviewersApi', () => ({
  reviewTeamsRequest: vi.fn(async () => [
    {
      id: '9',
      name: 'Review Team 1',
      isActive: true,
      leadId: '1',
      members: [{ id: '1', email: 'lead@example.com', name: 'Helper Test', isLead: true }],
    },
  ]),
  assignIdeaToReviewTeamRequest: vi.fn(),
}))
vi.mock('../api/administrationApi', async (importOriginal) => ({
  ...(await importOriginal()),
  adminIdeasRequest: vi.fn(),
  adminIdeaRequest: vi.fn(),
  adminCategoriesRequest: vi.fn(async () => []),
  downloadAdminAttachmentRequest: vi.fn(),
}))

const api = await import('../api/administrationApi')
const ideasMock = vi.mocked(api.adminIdeasRequest)
const ideaMock = vi.mocked(api.adminIdeaRequest)
const downloadMock = vi.mocked(api.downloadAdminAttachmentRequest)

function idea(overrides: Partial<AdminIdea> = {}): AdminIdea {
  return {
    id: '7',
    title: 'Automate the invoice run',
    contentRestricted: false,
    status: 'APPROVED',
    visibility: 'PUBLIC',
    submissionContext: 'ORGANIZATION',
    organization: { id: '3', name: 'Acme' },
    teamName: null,
    reviewTeam: null,
    author: { id: '5', email: 'ada@acme.example', name: 'Ada Author' },
    categoryName: 'Finance',
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-02T00:00:00Z',
    submittedAt: '2026-01-01T12:00:00Z',
    voteCount: 3,
    commentCount: 1,
    attachmentCount: 1,
    ...overrides,
  }
}

const REVIEW: AdminReview = {
  id: '40',
  round: 1,
  idea: {
    id: '7',
    title: 'Automate the invoice run',
    status: 'APPROVED',
    visibility: 'PUBLIC',
    submissionContext: 'ORGANIZATION',
    organization: { id: '3', name: 'Acme' },
    teamName: null,
  },
  reviewer: { id: '9', email: 'rae@acme.example', name: 'Rae Reviewer' },
  decision: 'APPROVED',
  isCompleted: true,
  createdAt: '2026-01-03T00:00:00Z',
  completedAt: '2026-01-04T00:00:00Z',
  contentRestricted: false,
  feedback: 'Clear and worth doing.',
  assessments: [{ criterion: 'EVIDENCE', rating: 'MEETS', note: '' }],
}

function detail(overrides: Partial<AdminIdeaDetail> = {}): AdminIdeaDetail {
  return {
    ...idea(),
    canInspectContent: true,
    content: {
      ...EMPTY_PROBLEM_STORY,
      description: 'We key every invoice in by hand.',
      currentProcess: 'Copy from email to the ledger.',
    },
    attachments: [
      {
        id: '30',
        filename: 'volumes.pdf',
        contentType: 'application/pdf',
        size: 2048,
        uploadedBy: { id: '5', email: 'ada@acme.example', name: 'Ada Author' },
        createdAt: '2026-01-01T00:00:00Z',
        downloadPath: '/administration/attachments/30/download/',
      },
    ],
    reviews: [REVIEW],
    transitions: [
      {
        id: '1',
        fromStatus: 'DRAFT',
        toStatus: 'SUBMITTED',
        actor: { id: '5', email: 'ada@acme.example', name: 'Ada Author' },
        createdAt: '2026-01-01T12:00:00Z',
      },
    ],
    ...overrides,
  }
}

describe('AdminIdeasPage', () => {
  beforeEach(() => ideasMock.mockReset())

  it('lists ideas across organizations with their metadata', async () => {
    ideasMock.mockResolvedValue(pageOfItems([idea()]))
    renderAdminPage(<AdminIdeasPage />)

    const row = within(await screen.findByRole('table', { name: 'Ideas' })).getAllByRole('row')[1]
    expect(row).toHaveTextContent('Automate the invoice run')
    expect(row).toHaveTextContent('Acme')
    expect(row).toHaveTextContent('Ada Author')
    expect(row).toHaveTextContent('Finance')
    // The console renders the same label the member-facing UI does, including
    // the part that says the author's own confirmation is still outstanding -
    // an administrator reading "Approved" alone would be told the idea is done.
    expect(row).toHaveTextContent('Platform approved — your confirmation needed')
  })

  it('lists individual and team ideas, which belong to no organization', async () => {
    ideasMock.mockResolvedValue(
      pageOfItems([
        idea({ id: '8', submissionContext: 'INDIVIDUAL', organization: null }),
        idea({ id: '9', submissionContext: 'TEAM', organization: null, teamName: 'Finance Crew' }),
      ]),
    )
    renderAdminPage(<AdminIdeasPage />)

    const rows = within(await screen.findByRole('table', { name: 'Ideas' })).getAllByRole('row')
    expect(rows[1]).toHaveTextContent('Individual')
    expect(rows[2]).toHaveTextContent('Team: Finance Crew')
  })

  it('offers an Assign button for a submitted idea no team has, and names the team otherwise', async () => {
    ideasMock.mockResolvedValue(
      pageOfItems([
        idea({ id: '8', status: 'SUBMITTED' }),
        idea({ id: '9', status: 'SUBMITTED', reviewTeam: { id: '4', name: 'Review Team 1' } }),
        idea({ id: '10', status: 'APPROVED' }),
      ]),
    )
    renderAdminPage(<AdminIdeasPage />)

    const rows = within(await screen.findByRole('table', { name: 'Ideas' })).getAllByRole('row')
    const assign = within(rows[1]).getByRole('link', { name: 'Assign to review team' })
    expect(assign).toHaveAttribute('href', '/app/admin/ideas/8#assign-review-team')
    expect(rows[2]).toHaveTextContent('Review Team 1')
    expect(within(rows[2]).queryByRole('link', { name: 'Assign to review team' })).toBeNull()
    expect(within(rows[3]).queryByRole('link', { name: 'Assign to review team' })).toBeNull()
  })

  it('offers no Assign button to an administrator who cannot route work', async () => {
    ideasMock.mockResolvedValue(pageOfItems([idea({ status: 'SUBMITTED' })]))
    renderAdminPage(<AdminIdeasPage />, { capabilities: READ_ONLY_ADMIN })

    await screen.findByRole('table', { name: 'Ideas' })
    expect(screen.queryByRole('link', { name: 'Assign to review team' })).toBeNull()
  })

  it('labels a withheld title as restricted rather than leaving it blank', async () => {
    ideasMock.mockResolvedValue(
      pageOfItems([idea({ title: null, contentRestricted: true, visibility: 'PRIVATE' })]),
    )
    renderAdminPage(<AdminIdeasPage />)

    expect(await screen.findByText('Restricted idea')).toBeInTheDocument()
  })

  it('reads its filters from the URL and applies them on the server', async () => {
    ideasMock.mockResolvedValue(pageOfItems([]))
    renderAdminPage(<AdminIdeasPage />, { path: '/?status=SUBMITTED&organizationId=3' })

    await waitFor(() =>
      expect(ideasMock).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'SUBMITTED', organizationId: '3' }),
        { offset: 0 },
      ),
    )
    expect(screen.getByRole('button', { name: 'One organization ✕' })).toBeInTheDocument()
  })

  it('changing a filter asks the server again', async () => {
    ideasMock.mockResolvedValue(pageOfItems([]))
    renderAdminPage(<AdminIdeasPage />)
    await screen.findByText('No ideas match.')

    fireEvent.change(screen.getByRole('combobox', { name: 'Visibility' }), {
      target: { value: 'PRIVATE' },
    })

    await waitFor(() =>
      expect(ideasMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ visibility: 'PRIVATE' }),
        { offset: 0 },
      ),
    )
  })

  it('shows an error state', async () => {
    ideasMock.mockRejectedValueOnce(new Error('down'))
    renderAdminPage(<AdminIdeasPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent('We could not load the ideas.')
  })
})

describe('AdminIdeaDetailPage', () => {
  beforeEach(() => {
    ideaMock.mockReset()
    downloadMock.mockReset()
  })

  function renderDetail() {
    return renderAdminPage(<AdminIdeaDetailPage />, { path: '/ideas/7', pattern: '/ideas/:ideaId' })
  }

  it('shows content, evidence, reviews and lifecycle', async () => {
    ideaMock.mockResolvedValue(detail())
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Automate the invoice run' })).toBeVisible()
    expect(screen.getByText('We key every invoice in by hand.')).toBeInTheDocument()
    expect(screen.getByText('Copy from email to the ledger.')).toBeInTheDocument()
    expect(screen.getByText('volumes.pdf')).toBeInTheDocument()
    // No discussion panel: an administrator can see that an idea was discussed,
    // but the conversation itself is not fetched and not drawn here.
    expect(screen.queryByRole('heading', { name: /^Discussion/ })).not.toBeInTheDocument()
    expect(screen.getByText('Clear and worth doing.')).toBeInTheDocument()
    expect(screen.getByText('Draft → Submitted')).toBeInTheDocument()
    expect(screen.getByText(/recorded in the administrative audit trail/)).toBeInTheDocument()
  })

  it('opens on the review-team picker when arriving from the Assign button', async () => {
    ideaMock.mockResolvedValue(detail({ status: 'SUBMITTED' }))
    renderAdminPage(<AdminIdeaDetailPage />, {
      path: '/ideas/7#assign-review-team',
      pattern: '/ideas/:ideaId',
    })

    const picker = await screen.findByLabelText('Review team')
    await screen.findByRole('option', { name: 'Review Team 1 (lead: Helper Test)' })
    await waitFor(() => expect(picker).toHaveFocus())
    expect(screen.getByText('No review team has this idea yet.')).toBeInTheDocument()
  })

  it('shows the review team an idea is with', async () => {
    ideaMock.mockResolvedValue(
      detail({ status: 'SUBMITTED', reviewTeam: { id: '9', name: 'Review Team 1' } }),
    )
    renderDetail()

    expect(await screen.findByText(/Currently with/)).toHaveTextContent('Review Team 1')
  })

  it('downloads evidence through the console endpoint', async () => {
    ideaMock.mockResolvedValue(detail())
    downloadMock.mockResolvedValue()
    renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }))

    await waitFor(() =>
      expect(downloadMock).toHaveBeenCalledWith(
        expect.objectContaining({ downloadPath: '/administration/attachments/30/download/' }),
      ),
    )
  })

  it('says a failed download failed', async () => {
    ideaMock.mockResolvedValue(detail())
    downloadMock.mockRejectedValue(new Error('404'))
    renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: 'Download' }))

    expect(await screen.findByText('Download failed.')).toBeInTheDocument()
  })

  it('marks every content section restricted when the server withheld it', async () => {
    ideaMock.mockResolvedValue(
      detail({
        title: null,
        contentRestricted: true,
        canInspectContent: false,
        content: null,
        attachments: [],
        reviews: [{ ...REVIEW, contentRestricted: true, feedback: null, assessments: null }],
      }),
    )
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Restricted idea' })).toBeVisible()
    expect(screen.getByText(/You can see its metadata and lifecycle only/)).toBeInTheDocument()
    expect(screen.getByText('Evidence needs the content-inspection permission')).toBeVisible()
    expect(
      screen.getByText('Feedback and criteria need the content-inspection permission'),
    ).toBeVisible()
    expect(screen.queryByText('Clear and worth doing.')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download' })).not.toBeInTheDocument()
  })

  it('says so when the idea does not exist', async () => {
    ideaMock.mockResolvedValue(null)
    renderDetail()

    expect(await screen.findByText('Idea not found.')).toBeInTheDocument()
  })
})
