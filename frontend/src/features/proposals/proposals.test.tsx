import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { READ_ONLY_ADMIN, renderAdminPage } from '../../test/renderAdmin'
import { AdminProposalsPage } from '../administration/pages/AdminProposalsPage'
import type { IdeaProposal, ProposalState } from './api/proposalsApi'
import { IdeaProposalPanel } from './components/IdeaProposalPanel'
import { ProposalReadPage } from './components/ProposalReadPage'
import { ProtectedView } from './components/ProtectedView'

vi.mock('./api/proposalsApi', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  proposalStateRequest: vi.fn(),
  proposalsForReleaseRequest: vi.fn(),
  recordProposalViewRequest: vi.fn(),
  startProposalRequest: vi.fn(),
  updateProposalRequest: vi.fn(),
  submitProposalRequest: vi.fn(),
  releaseProposalRequest: vi.fn(),
  requestProposalChangesRequest: vi.fn(),
  declineProposalRequest: vi.fn(),
}))
vi.mock('../ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  giveGoAheadRequest: vi.fn(),
}))

const api = await import('./api/proposalsApi')
const ideas = await import('../ideas/api/ideasApi')

function proposal(overrides: Partial<IdeaProposal> = {}): IdeaProposal {
  return {
    ideaId: '3',
    ideaTitle: 'Automate hostel payments',
    teamName: 'Team A',
    title: 'Payments dashboard',
    executiveSummary: 'Stop re-typing payments.',
    problem: 'Finance re-types every payment.',
    proposedSolution: '',
    requirementsSummary: '',
    scope: 'Import, match, report.',
    deliverables: '',
    risks: '',
    assumptions: '',
    estimatedEffort: '',
    estimatedTimeline: '6 weeks',
    acceptanceCriteria: '',
    status: 'released',
    reviewFeedback: '',
    submittedAt: null,
    decidedAt: null,
    updatedAt: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function state(overrides: Partial<ProposalState> = {}): ProposalState {
  return {
    viewerRole: 'owner',
    canStart: false,
    canEdit: false,
    canSubmit: false,
    canDecide: false,
    proposal: proposal(),
    ...overrides,
  }
}

const RECEIPT = {
  viewerName: 'Ada Lovelace',
  viewerEmail: 'ada@example.com',
  viewedAt: '2026-10-07T09:30:00Z',
}
const OK = { success: true, message: 'Done.', field: null, proposal: null }

function readPage() {
  return render(
    <MemoryRouter initialEntries={['/app/ideas/3/proposal']}>
      <Routes>
        <Route path="/app/ideas/:ideaId/proposal" element={<ProposalReadPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(api.proposalStateRequest).mockResolvedValue(state())
  vi.mocked(api.recordProposalViewRequest).mockResolvedValue({
    success: true,
    message: 'Recorded.',
    receipt: RECEIPT,
  })
})

describe('the owner reading a released proposal', () => {
  it('shows the proposal watermarked, records the reading, and is honest about the limits', async () => {
    readPage()

    expect(await screen.findByRole('heading', { name: 'Payments dashboard' })).toBeInTheDocument()
    expect(screen.getByText('Import, match, report.')).toBeInTheDocument()
    expect(screen.getByTestId('watermark')).toBeInTheDocument()
    expect(api.recordProposalViewRequest).toHaveBeenCalledWith('3')
    expect(screen.getByText(/A photo of the screen cannot be prevented/)).toBeInTheDocument()
  })

  it('offers no download, print or copy, and blocks copying and the context menu', async () => {
    readPage()
    await screen.findByRole('heading', { name: 'Payments dashboard' })

    expect(screen.queryByRole('link', { name: /download/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /download|print|copy/i })).toBeNull()
    const protectedArea = document.querySelector('[data-protected="true"]') as HTMLElement
    expect(fireEvent.copy(protectedArea)).toBe(false)
    expect(fireEvent.contextMenu(protectedArea)).toBe(false)
  })

  it('gives the go-ahead from here, and shows a refusal in the server’s words', async () => {
    vi.mocked(ideas.giveGoAheadRequest).mockResolvedValue({
      success: false,
      message: 'Only the person who submitted this idea can give the go-ahead.',
      field: null,
      idea: null,
    } as never)
    readPage()

    fireEvent.click(await screen.findByRole('button', { name: 'Give the go-ahead' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Only the person')
    expect(ideas.giveGoAheadRequest).toHaveBeenCalledWith('3')
  })

  it('says there is nothing to read when the proposal is not released to them', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({ proposal: proposal({ status: 'submitted' }) }),
    )
    readPage()

    expect(await screen.findByText('There is no proposal for you to read.')).toBeInTheDocument()
    expect(api.recordProposalViewRequest).not.toHaveBeenCalled()
  })

  it('does not show the proposal to anyone who is not the owner', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(state({ viewerRole: 'writer' }))
    readPage()

    expect(await screen.findByText('There is no proposal for you to read.')).toBeInTheDocument()
  })
})

describe('ProtectedView', () => {
  it('carries the reader’s name and the time', () => {
    render(
      <ProtectedView receipt={RECEIPT}>
        <p>Secret</p>
      </ProtectedView>,
    )

    const style = screen.getByTestId('watermark').getAttribute('style') ?? ''
    expect(decodeURIComponent(style)).toContain('Ada Lovelace')
    expect(decodeURIComponent(style)).toContain('ada@example.com')
  })
})

describe('IdeaProposalPanel', () => {
  const panel = () =>
    render(
      <MemoryRouter>
        <IdeaProposalPanel ideaId="3" status="APPROVED" />
      </MemoryRouter>,
    )

  it('lets the review team start the proposal', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({ viewerRole: 'writer', canStart: true, proposal: null }),
    )
    vi.mocked(api.startProposalRequest).mockResolvedValue(OK)
    panel()

    fireEvent.click(await screen.findByRole('button', { name: 'Start the proposal' }))

    await waitFor(() => expect(api.startProposalRequest).toHaveBeenCalledWith({ ideaId: '3' }))
  })

  it('lets any member edit, but only the lead send it to the admin', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({
        viewerRole: 'writer',
        canEdit: true,
        canSubmit: false,
        proposal: proposal({ status: 'draft' }),
      }),
    )
    panel()

    expect(await screen.findByRole('form', { name: 'Proposal' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send to the admin' })).toBeNull()
    expect(screen.getByText(/Only the team’s lead sends the proposal/)).toBeInTheDocument()
  })

  it('shows the lead the send button and reports a refusal', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({
        viewerRole: 'writer',
        canEdit: true,
        canSubmit: true,
        proposal: proposal({ status: 'draft' }),
      }),
    )
    vi.mocked(api.submitProposalRequest).mockResolvedValue({
      success: false,
      message: 'Fill in the timeline before sending it.',
      field: 'estimated_timeline',
      proposal: null,
    })
    panel()

    fireEvent.click(await screen.findByRole('button', { name: 'Send to the admin' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Fill in the timeline')
  })

  it('shows the admin’s feedback when the proposal was sent back', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({
        viewerRole: 'writer',
        canEdit: true,
        proposal: proposal({
          status: 'changes_requested',
          reviewFeedback: 'The timeline is too optimistic.',
        }),
      }),
    )
    panel()

    expect(await screen.findByText('The timeline is too optimistic.')).toBeInTheDocument()
  })

  it('points the owner to the reading page once released, and never shows the text here', async () => {
    panel()

    expect(await screen.findByRole('link', { name: /Read the proposal/ })).toHaveAttribute(
      'href',
      '/app/ideas/3/proposal',
    )
    expect(screen.queryByText('Import, match, report.')).toBeNull()
  })

  it('tells the owner it is being prepared before then', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(state({ proposal: null }))
    panel()

    expect(await screen.findByText(/The platform is preparing a proposal/)).toBeInTheDocument()
  })

  it('is silent for everyone else', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({ viewerRole: null, proposal: null }),
    )
    const { container } = panel()

    await waitFor(() => expect(api.proposalStateRequest).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})

describe('AdminProposalsPage', () => {
  beforeEach(() => {
    vi.mocked(api.proposalsForReleaseRequest).mockResolvedValue([proposal({ status: 'submitted' })])
  })

  it('lets an admin read a proposal and release it', async () => {
    vi.mocked(api.releaseProposalRequest).mockResolvedValue(OK)
    renderAdminPage(<AdminProposalsPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Read' }))
    expect(await screen.findByText('Import, match, report.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Release to the owner' }))

    await waitFor(() => expect(api.releaseProposalRequest).toHaveBeenCalledWith({ ideaId: '3' }))
  })

  it('sends it back with the reason typed', async () => {
    vi.mocked(api.requestProposalChangesRequest).mockResolvedValue(OK)
    renderAdminPage(<AdminProposalsPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Read' }))
    fireEvent.change(await screen.findByLabelText('Reason or feedback'), {
      target: { value: 'Too optimistic.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send back to the team' }))

    await waitFor(() =>
      expect(api.requestProposalChangesRequest).toHaveBeenCalledWith({
        ideaId: '3',
        feedback: 'Too optimistic.',
      }),
    )
  })

  it('shows the server’s refusal, for instance for somebody who helped write it', async () => {
    vi.mocked(api.releaseProposalRequest).mockResolvedValue({
      success: false,
      message: 'You helped write this proposal, so somebody else has to decide it.',
      field: null,
      proposal: null,
    })
    renderAdminPage(<AdminProposalsPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Read' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Release to the owner' }))

    expect(await screen.findByText(/You helped write this proposal/)).toBeInTheDocument()
  })

  it('offers no decision to an administrator without the permission', async () => {
    renderAdminPage(<AdminProposalsPage />, { capabilities: READ_ONLY_ADMIN })

    fireEvent.click(await screen.findByRole('button', { name: 'Read' }))
    await screen.findByText('Import, match, report.')
    expect(screen.queryByRole('button', { name: 'Release to the owner' })).toBeNull()
  })

  it('says plainly when nothing has reached the admin', async () => {
    vi.mocked(api.proposalsForReleaseRequest).mockResolvedValue([])
    renderAdminPage(<AdminProposalsPage />)

    expect(await screen.findByText('No proposals have reached you.')).toBeInTheDocument()
  })
})
