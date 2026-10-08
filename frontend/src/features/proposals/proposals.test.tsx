import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { READ_ONLY_ADMIN, renderAdminPage } from '../../test/renderAdmin'
import { AdminProposalsPage } from '../administration/pages/AdminProposalsPage'
import type { IdeaProposal, ProposalProgress, ProposalState } from './api/proposalsApi'
import { IdeaProposalPanel } from './components/IdeaProposalPanel'
import { ProposalReadPage } from './components/ProposalReadPage'
import { ProtectedView } from './components/ProtectedView'

vi.mock('./api/proposalsApi', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  proposalStateRequest: vi.fn(),
  proposalProgressRequest: vi.fn(),
  recordProposalViewRequest: vi.fn(),
  startProposalRequest: vi.fn(),
  updateProposalRequest: vi.fn(),
  submitProposalRequest: vi.fn(),
  releaseProposalRequest: vi.fn(),
  requestProposalChangesRequest: vi.fn(),
  declineProposalRequest: vi.fn(),
  proposalAnswerRequest: vi.fn(async () => null),
  answerProposalRequest: vi.fn(),
}))

const api = await import('./api/proposalsApi')

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
    feasibility: 'Feasible: the bank exports a CSV.',
    milestones: '',
    financialRequirements: 'Hosting at 40 USD a month.',
    paymentRequired: 'no',
    paymentPlan: 'Should never show: there is no charge.',
    status: 'released',
    reviewFeedback: '',
    submittedAt: null,
    decidedAt: null,
    updatedAt: '2026-10-01T00:00:00Z',
    ...overrides,
  }
}

function progress(overrides: Partial<ProposalProgress> = {}): ProposalProgress {
  return {
    ideaId: '3',
    ideaTitle: 'Automate hostel payments',
    status: 'submitted',
    teamName: 'Team A',
    participants: [
      {
        name: 'Lena Lead',
        email: 'lead@example.com',
        role: 'lead',
        contributions: 1,
        sections: [],
        lastContributedAt: '2026-10-02T09:00:00Z',
      },
      {
        name: 'Max Member',
        email: 'member@example.com',
        role: 'member',
        contributions: 3,
        sections: ['scope', 'estimatedTimeline'],
        lastContributedAt: '2026-10-01T12:00:00Z',
      },
      {
        name: 'Quiet Quinn',
        email: 'quinn@example.com',
        role: 'member',
        contributions: 0,
        sections: [],
        lastContributedAt: null,
      },
    ],
    filledSections: ['title', 'executiveSummary', 'problem', 'scope', 'estimatedTimeline'],
    totalSections: 12,
    missingRequired: [],
    activity: [
      { name: 'Lena Lead', action: 'submitted', sections: [], at: '2026-10-02T09:00:00Z' },
      { name: 'Max Member', action: 'edited', sections: ['scope'], at: '2026-10-01T12:00:00Z' },
    ],
    proposal: proposal({ status: 'submitted' }),
    ...overrides,
  }
}

function state(overrides: Partial<ProposalState> = {}): ProposalState {
  return {
    viewerRole: 'owner',
    canStart: false,
    waitingForAdmin: false,
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

  it('shows the feasibility and the money, and no payment plan when there is no charge', async () => {
    readPage()

    await screen.findByRole('heading', { name: 'Payments dashboard' })
    expect(screen.getByText('Feasible: the bank exports a CSV.')).toBeInTheDocument()
    expect(screen.getByText('Hosting at 40 USD a month.')).toBeInTheDocument()
    expect(screen.getByText('No - no charge to the owner')).toBeInTheDocument()
    expect(screen.queryByText(/Should never show/)).toBeNull()
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

  it('asks whether to go ahead, and on what terms, and sends the answers', async () => {
    vi.mocked(api.answerProposalRequest).mockResolvedValue({
      success: false,
      message: 'Only the person who submitted this idea can give the go-ahead.',
      field: null,
    })
    readPage()

    fireEvent.click(await screen.findByLabelText('Yes, go ahead'))
    const send = screen.getByRole('button', { name: 'Give the go-ahead' })
    expect(send).toBeDisabled() // the timeline is not answered yet
    fireEvent.click(
      within(screen.getByRole('group', { name: /accept the proposed timeline/ })).getByLabelText(
        'Yes, I agree',
      ),
    )
    fireEvent.click(send)

    expect(await screen.findByRole('alert')).toHaveTextContent('Only the person')
    expect(api.answerProposalRequest).toHaveBeenCalledWith(
      expect.objectContaining({ ideaId: '3', decision: 'proceed', timeline: 'yes' }),
    )
  })

  it('asks why when the owner does not want to go ahead', async () => {
    vi.mocked(api.answerProposalRequest).mockResolvedValue({
      success: true,
      message: 'Thank you - your decision is recorded and the team has been told.',
      field: null,
    })
    readPage()

    fireEvent.click(await screen.findByLabelText('No, not now'))
    const send = screen.getByRole('button', { name: 'Send my decision' })
    expect(send).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Why not/), { target: { value: 'Too costly.' } })
    fireEvent.click(send)

    await waitFor(() =>
      expect(api.answerProposalRequest).toHaveBeenCalledWith(
        expect.objectContaining({ decision: 'decline', declineReason: 'Too costly.' }),
      ),
    )
    expect(await screen.findByText(/your decision is recorded/)).toBeInTheDocument()
  })

  it('shows the answers instead of the questions once the owner has answered', async () => {
    vi.mocked(api.proposalAnswerRequest).mockResolvedValueOnce({
      decision: 'proceed',
      timeline: 'discuss',
      payment: '',
      preferredStart: null,
      conditions: 'Avoid month end.',
      declineReason: '',
      answeredByName: 'Amina Owner',
      answeredAt: '2026-10-08T09:00:00Z',
    })
    readPage()

    expect(await screen.findByText('The owner wants to go ahead')).toBeInTheDocument()
    expect(screen.getByText('Needs discussion')).toBeInTheDocument()
    expect(screen.getByText('Avoid month end.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Yes, go ahead')).toBeNull()
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

  it('tells the team to wait while the admin has not confirmed the approval', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({ viewerRole: 'writer', waitingForAdmin: true, proposal: null }),
    )
    panel()

    expect(await screen.findByText('Approved - waiting for the platform admin')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start the proposal' })).toBeNull()
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

  it('folds written sections, and opens or closes them all at once', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({
        viewerRole: 'writer',
        canEdit: true,
        proposal: proposal({ status: 'draft', feasibility: '' }),
      }),
    )
    panel()

    // An empty section is open where the writer has work to do; a written one is folded.
    expect(await screen.findByLabelText(/^Feasibility/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/^Executive summary/)).toBeNull()
    expect(screen.getByText(/sections written/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Collapse all' }))
    expect(screen.queryByLabelText(/^Feasibility/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Expand all' }))
    expect(screen.getByLabelText(/^Executive summary/)).toBeInTheDocument()
  })

  it('asks for the payment plan only once the team says the owner pays', async () => {
    vi.mocked(api.proposalStateRequest).mockResolvedValue(
      state({
        viewerRole: 'writer',
        canEdit: true,
        proposal: proposal({ status: 'draft', paymentRequired: '', paymentPlan: '' }),
      }),
    )
    vi.mocked(api.updateProposalRequest).mockResolvedValue(OK)
    panel()
    // Written sections start folded; open them all to read every field.
    fireEvent.click(await screen.findByRole('button', { name: 'Expand all' }))

    // Each required section is marked with a "*" after its name.
    for (const label of [/^Feasibility\*$/, /^Requirements\*$/, /^Timeline and milestones\*$/]) {
      expect(await screen.findByLabelText(label)).toBeInTheDocument()
    }
    expect(screen.getByLabelText(/^Financial requirements/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/^Payment plan/)).toBeNull()

    fireEvent.change(screen.getByLabelText(/^Does the owner pay\?/), { target: { value: 'yes' } })
    fireEvent.change(screen.getByLabelText(/^Payment plan\*$/), {
      target: { value: 'Half on start, half on acceptance.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(api.updateProposalRequest).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({
          paymentRequired: 'yes',
          paymentPlan: 'Half on start, half on acceptance.',
        }),
      ),
    )
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
    vi.mocked(api.proposalProgressRequest).mockResolvedValue([progress()])
  })

  it('shows how far each proposal has got and who is taking part', async () => {
    vi.mocked(api.proposalProgressRequest).mockResolvedValue([
      progress({
        status: 'draft',
        missingRequired: ['proposedSolution', 'acceptanceCriteria'],
        proposal: proposal({ status: 'draft' }),
      }),
    ])
    renderAdminPage(<AdminProposalsPage />)

    expect(await screen.findByText('Being written')).toBeInTheDocument()
    expect(screen.getByText('5 of 12 sections written')).toBeInTheDocument()
    expect(
      screen.getByText(
        'Still needed before it can be sent: Proposed solution, Acceptance criteria.',
      ),
    ).toBeInTheDocument()
    const people = screen.getByRole('list', { name: 'Participants' })
    expect(people).toHaveTextContent('Lena Lead')
    expect(people).toHaveTextContent('Lead')
    expect(people).toHaveTextContent('3 contributions · Scope, Overall timeline')
    expect(people).toHaveTextContent('Has not contributed yet')

    // Readable while it is written, but nothing to decide until the lead sends it.
    fireEvent.click(screen.getByRole('button', { name: 'Read' }))
    expect(await screen.findByRole('list', { name: 'Recent activity' })).toHaveTextContent(
      'Max Member edited Scope',
    )
    expect(screen.queryByRole('button', { name: 'Release to the owner' })).toBeNull()
  })

  it('shows an approved idea whose team has not started, and filters by stage', async () => {
    vi.mocked(api.proposalProgressRequest).mockResolvedValue([
      progress(),
      progress({
        ideaId: '9',
        ideaTitle: 'Automate the rota',
        status: 'not_started',
        filledSections: [],
        activity: [],
        proposal: null,
      }),
    ])
    renderAdminPage(<AdminProposalsPage />)

    expect(await screen.findByText('Not started')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Waiting for you (1)' }))
    expect(screen.queryByText('Automate the rota')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Being prepared (1)' }))
    expect(screen.getByText('Automate the rota')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Read' })).toBeNull()
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

  it('says plainly when there is nothing to follow yet', async () => {
    vi.mocked(api.proposalProgressRequest).mockResolvedValue([])
    renderAdminPage(<AdminProposalsPage />)

    expect(await screen.findByText('No approved ideas yet.')).toBeInTheDocument()
  })
})
