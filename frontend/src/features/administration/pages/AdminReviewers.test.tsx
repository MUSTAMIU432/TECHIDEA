import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { READ_ONLY_ADMIN, renderAdminPage } from '../../../test/renderAdmin'
import { AssignReviewTeam } from '../components/AssignReviewTeam'
import { AdminReviewersPage } from './AdminReviewersPage'

vi.mock('../api/reviewersApi')
const api = await import('../api/reviewersApi')

const LEAD = { id: '1', email: 'lead@example.com', name: 'Lead Person' }
const MEMBER = { id: '2', email: 'member@example.com', name: 'Member Person' }
const TEAM = {
  id: '9',
  name: 'Platform Review A',
  isActive: true,
  leadId: '1',
  members: [
    { ...LEAD, isLead: true },
    { ...MEMBER, isLead: false },
  ],
}
const OK = { success: true, message: 'Done.', field: null }

beforeEach(() => {
  vi.mocked(api.reviewersRequest).mockResolvedValue([LEAD, MEMBER])
  vi.mocked(api.reviewTeamsRequest).mockResolvedValue([TEAM])
})

describe('AdminReviewersPage', () => {
  it('lists reviewers and teams, marking the lead', async () => {
    renderAdminPage(<AdminReviewersPage />)

    expect(await screen.findByText('Platform Review A')).toBeInTheDocument()
    expect(screen.getByText(/Lead Person \(lead\), Member Person/)).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
  })

  it('makes a reviewer from an email and shows the server’s own words on a refusal', async () => {
    vi.mocked(api.grantReviewerRequest).mockResolvedValue({
      success: false,
      message: 'No account uses this email address.',
      field: 'email',
    })
    renderAdminPage(<AdminReviewersPage />)

    const form = await screen.findByRole('form', { name: 'Make a reviewer' })
    fireEvent.change(within(form).getByLabelText('Account email'), {
      target: { value: 'nobody@example.com' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Make reviewer' }))

    await waitFor(() => expect(api.grantReviewerRequest).toHaveBeenCalledWith('nobody@example.com'))
    expect(await screen.findByText('No account uses this email address.')).toBeInTheDocument()
  })

  it('forms a team with a lead and members', async () => {
    vi.mocked(api.createReviewTeamRequest).mockResolvedValue(OK)
    renderAdminPage(<AdminReviewersPage />)

    const form = await screen.findByRole('form', { name: 'Form a review team' })
    fireEvent.change(within(form).getByLabelText('Team name'), { target: { value: 'Team B' } })
    fireEvent.change(within(form).getByLabelText('Lead'), { target: { value: '1' } })
    fireEvent.click(within(form).getByLabelText('Member Person'))
    fireEvent.click(within(form).getByRole('button', { name: 'Create team' }))

    await waitFor(() =>
      expect(api.createReviewTeamRequest).toHaveBeenCalledWith('Team B', '1', ['2']),
    )
  })

  it('retires a team', async () => {
    vi.mocked(api.updateReviewTeamRequest).mockResolvedValue(OK)
    renderAdminPage(<AdminReviewersPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Retire' }))

    await waitFor(() =>
      expect(api.updateReviewTeamRequest).toHaveBeenCalledWith('9', { isActive: false }),
    )
  })

  it('offers no forms to an administrator who cannot manage reviewers', async () => {
    renderAdminPage(<AdminReviewersPage />, { capabilities: READ_ONLY_ADMIN })

    await screen.findByText('Platform Review A')
    expect(screen.queryByRole('form', { name: 'Make a reviewer' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Form a review team' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retire' })).not.toBeInTheDocument()
  })
})

describe('AssignReviewTeam', () => {
  it('assigns an idea to an active team', async () => {
    vi.mocked(api.assignIdeaToReviewTeamRequest).mockResolvedValue(OK)
    renderAdminPage(<AssignReviewTeam ideaId="31" status="SUBMITTED" />)

    fireEvent.change(await screen.findByLabelText('Review team'), { target: { value: '9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Assign' }))

    await waitFor(() => expect(api.assignIdeaToReviewTeamRequest).toHaveBeenCalledWith('31', '9'))
  })

  it('is not drawn for an idea that has not reached the platform, or for someone who cannot route', () => {
    const { container } = renderAdminPage(<AssignReviewTeam ideaId="31" status="DRAFT" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('is not drawn for an administrator who cannot route work', () => {
    const { container } = renderAdminPage(<AssignReviewTeam ideaId="31" status="SUBMITTED" />, {
      capabilities: READ_ONLY_ADMIN,
    })
    expect(container).toBeEmptyDOMElement()
  })

  it('reports a refusal in the server’s words', async () => {
    vi.mocked(api.assignIdeaToReviewTeamRequest).mockResolvedValue({
      success: false,
      message: 'This idea is already being reviewed.',
      field: null,
    })
    renderAdminPage(<AssignReviewTeam ideaId="31" status="UNDER_REVIEW" />)

    fireEvent.change(await screen.findByLabelText('Review team'), { target: { value: '9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Assign' }))

    expect(await screen.findByText('This idea is already being reviewed.')).toBeInTheDocument()
  })
})
