import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { makeTeam } from '../../../test/idea'
import { useAuth } from '../../identity/auth/AuthContext'
import type { Team } from '../api/teamsApi'
import { TeamWorkspace } from './TeamWorkspace'

vi.mock('../api/teamsApi', () => ({
  teamsRequest: vi.fn(async () => []),
  teamRequest: vi.fn(),
  teamMembersRequest: vi.fn(async () => []),
  createTeamRequest: vi.fn(),
  leaveTeamRequest: vi.fn(),
  addTeamMemberRequest: vi.fn(),
  setTeamReviewerRequest: vi.fn(),
  teamRoleLabel: (slug: string) =>
    slug.replace(/_/g, ' ').replace(/^\w/, (letter) => letter.toUpperCase()),
}))

vi.mock('../../invitations/api/invitationsApi', () => ({
  teamInvitationsRequest: vi.fn(async () => []),
  organizationInvitationsRequest: vi.fn(async () => []),
  sendTeamInvitationRequest: vi.fn(),
  revokeInvitationRequest: vi.fn(),
  acceptInvitationRequest: vi.fn(),
  invitationDetailsRequest: vi.fn(),
}))

vi.mock('../../reviews/api/reviewsApi', () => ({ teamReviewQueueRequest: vi.fn(async () => []) }))
vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))

// The add-a-colleague panel draws its candidates from the reader's organization
// roster, and a team page is deliberately reachable with no organization at
// all - so both answers are exercised here rather than one being assumed.
vi.mock('../../organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))
vi.mock('../../organizations/api/organizationApi', () => ({
  organizationMembersRequest: vi.fn(async () => []),
}))

const {
  teamRequest: teamMock,
  teamMembersRequest: teamMembersMock,
  leaveTeamRequest: leaveMock,
  addTeamMemberRequest: addMemberMock,
  setTeamReviewerRequest: setReviewerMock,
} = await import('../api/teamsApi')
const { teamReviewQueueRequest: queueMock } = await import('../../reviews/api/reviewsApi')
const { sendTeamInvitationRequest: inviteMock } =
  await import('../../invitations/api/invitationsApi')
const { useOrganization } = await import('../../organizations/context/useOrganization')
const { organizationMembersRequest: orgMembersMock } =
  await import('../../organizations/api/organizationApi')

const TEAM: Team = makeTeam({ id: '9', memberCount: 2 })
const SIGNED_IN = { id: '7', email: 'ada@example.com' }
const ORGANIZATION = { id: '3', name: 'Acme Labs', slug: 'acme-labs', createdAt: '', updatedAt: '' }

/** One membership in the organization roster the add panel reads. */
function membership(overrides: { id: string; email: string; firstName: string }) {
  return {
    id: `m-${overrides.id}`,
    status: 'active' as const,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    user: { ...overrides, lastName: '' },
    organization: ORGANIZATION,
    roles: [],
  }
}

function withOrganization(organization: typeof ORGANIZATION | null) {
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: organization,
  } as unknown as ReturnType<typeof useOrganization>)
}

/**
 * Rendered through a real route so `useParams` resolves the team id from the
 * URL, which is where this page reads it from — a wrapper that passed the id as
 * a prop would test a component the app does not have.
 */
function renderAt(teamId = '9') {
  const router = createMemoryRouter(
    [
      { path: '/app/teams', element: <p>Teams list</p> },
      { path: '/app/teams/:teamId', element: <TeamWorkspace /> },
    ],
    { initialEntries: [`/app/teams/${teamId}`] },
  )
  render(<RouterProvider router={router} />)
  return router
}

describe('TeamWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useAuth).mockReturnValue({ user: SIGNED_IN } as unknown as ReturnType<typeof useAuth>)
    vi.mocked(teamMock).mockResolvedValue(TEAM)
    vi.mocked(teamMembersMock).mockResolvedValue([
      {
        userId: '7',
        email: 'ada@example.com',
        firstName: 'Ada',
        lastName: 'A',
        roleSlugs: ['owner'],
        joinedAt: '2026-01-01T00:00:00.000Z',
      },
      {
        userId: '8',
        email: 'rae@example.com',
        firstName: 'Rae',
        lastName: 'B',
        roleSlugs: ['member'],
        joinedAt: '2026-02-01T00:00:00.000Z',
      },
    ])
    vi.mocked(leaveMock).mockResolvedValue({
      success: true,
      message: 'You have left the team.',
      field: null,
    })
    withOrganization(null)
  })

  it('shows the team the server returned', async () => {
    renderAt()

    expect(await screen.findByRole('heading', { level: 1, name: 'Registrar' })).toBeInTheDocument()
    expect(screen.getByText('The people who allocate rooms.')).toBeInTheDocument()
    expect(teamMock).toHaveBeenCalledWith('9')
  })

  it('says a team is unavailable without saying which kind of unavailable it is', async () => {
    // Null is the answer for "no such team" and for "you are not in it", so the
    // page must not choose between them - a team id could not be used to find
    // out whether somebody else's team exists.
    vi.mocked(teamMock).mockResolvedValue(null)
    renderAt()

    expect(await screen.findByText('This team is not available.')).toBeInTheDocument()
    expect(screen.queryByText(/not a member/)).not.toBeInTheDocument()
  })

  it('reports a failed load as a failure rather than as absence', async () => {
    vi.mocked(teamMock).mockRejectedValue(new Error('offline'))
    renderAt()

    expect(
      await screen.findByText('We could not load this team. Please refresh the page.'),
    ).toBeInTheDocument()
  })

  it('lists the roster, and who owns it', async () => {
    renderAt()

    await screen.findByRole('heading', { level: 1, name: 'Registrar' })
    const roster = await screen.findByRole('list', { name: 'Members' })
    expect(within(roster).getByText('Ada A')).toBeInTheDocument()
    expect(within(roster).getByText('rae@example.com')).toBeInTheDocument()
    expect(within(roster).getByText('Team owner')).toBeInTheDocument()
    expect(teamMembersMock).toHaveBeenCalledWith('9')
  })

  it('offers an invitation form that sends rather than adds', async () => {
    vi.mocked(inviteMock).mockResolvedValue({
      success: true,
      message: 'Invitation sent.',
      field: null,
      invitation: null,
    })
    renderAt()
    await screen.findByRole('heading', { level: 1, name: 'Registrar' })

    fireEvent.change(screen.getByLabelText('Email address'), {
      target: { value: 'new@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send invitation' }))

    await waitFor(() => expect(inviteMock).toHaveBeenCalledWith('9', 'new@example.com'))
    // The server's own wording, and the box is cleared because the address is
    // now somebody else's problem.
    expect(await screen.findByText('Invitation sent.')).toBeInTheDocument()
  })

  it('refuses an empty invitation client-side, without a request', async () => {
    renderAt()
    await screen.findByRole('heading', { level: 1, name: 'Registrar' })

    fireEvent.click(screen.getByRole('button', { name: 'Send invitation' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Enter the address to invite.')
    expect(inviteMock).not.toHaveBeenCalled()
  })

  it('reports a refused leave and stays on the team', async () => {
    vi.mocked(leaveMock).mockResolvedValue({
      success: false,
      message: 'A team needs at least one member.',
      field: null,
    })
    renderAt()
    await screen.findByRole('heading', { level: 1, name: 'Registrar' })

    fireEvent.click(screen.getByRole('button', { name: 'Leave team' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('at least one member')
  })

  it('goes back to the list after leaving, because every request here would now be refused', async () => {
    const router = renderAt()
    await screen.findByRole('heading', { level: 1, name: 'Registrar' })

    fireEvent.click(screen.getByRole('button', { name: 'Leave team' }))

    expect(await screen.findByText('Teams list')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/app/teams')
  })

  describe('adding somebody who already has an account', () => {
    // The owner is the only role the server lets do this, and `ownerId` is the
    // only statement of that this client has - so the panel is drawn from it and
    // the server is still asked to agree.
    const NOT_THE_OWNER = { ...SIGNED_IN, id: '8' }

    beforeEach(() => {
      withOrganization(ORGANIZATION)
      vi.mocked(orgMembersMock).mockResolvedValue([
        // Ada is already on the roster, so she must not be offered again - the
        // server would refuse it, and a picker full of refusals is worse than
        // one that never asked.
        membership({ id: '7', email: 'ada@example.com', firstName: 'Ada' }),
        membership({ id: '8', email: 'rae@example.com', firstName: 'Rae' }),
        // Nia is in the organization and not on the team: the one case this
        // panel exists for.
        membership({ id: '11', email: 'nia@example.com', firstName: 'Nia' }),
      ])
    })

    it('offers the colleagues who are not already on the team', async () => {
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      const picker = await screen.findByLabelText('Colleague')
      expect(within(picker).getByRole('option', { name: /Nia/ })).toBeInTheDocument()
      // Both of the others are on the roster already: Ada is the reader, Rae is
      // a member, and the server would refuse either.
      expect(within(picker).queryByRole('option', { name: /Rae/ })).not.toBeInTheDocument()
      expect(within(picker).queryByRole('option', { name: /Ada/ })).not.toBeInTheDocument()
    })

    it("adds the chosen colleague and says so in the server's words", async () => {
      vi.mocked(addMemberMock).mockResolvedValue({
        success: true,
        message: 'nia@example.com added to the team.',
        field: null,
      })
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      fireEvent.change(await screen.findByLabelText('Colleague'), { target: { value: '11' } })
      fireEvent.click(screen.getByRole('button', { name: 'Add to team' }))

      expect(await screen.findByText('nia@example.com added to the team.')).toBeInTheDocument()
      expect(addMemberMock).toHaveBeenCalledWith('9', '11')
      // The roster is now one person short of the truth, so it is asked again
      // rather than left showing the colleague as still uninvited.
      await waitFor(() => expect(teamMembersMock).toHaveBeenCalledTimes(2))
    })

    it("shows a refusal as the server's message and offers nobody again", async () => {
      vi.mocked(addMemberMock).mockResolvedValue({
        success: false,
        message: 'That person is not available.',
        field: 'userId',
      })
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      fireEvent.change(await screen.findByLabelText('Colleague'), { target: { value: '11' } })
      fireEvent.click(screen.getByRole('button', { name: 'Add to team' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('not available')
      expect(teamMembersMock).toHaveBeenCalledOnce()
    })

    it('reports a transport failure without claiming anybody was added', async () => {
      vi.mocked(addMemberMock).mockRejectedValue(new Error('offline'))
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      fireEvent.change(await screen.findByLabelText('Colleague'), { target: { value: '11' } })
      fireEvent.click(screen.getByRole('button', { name: 'Add to team' }))

      expect(await screen.findByRole('alert')).toHaveTextContent('nobody was added')
    })

    it('will not add anybody until somebody is chosen', async () => {
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      expect(await screen.findByRole('button', { name: 'Add to team' })).toBeDisabled()
      expect(addMemberMock).not.toHaveBeenCalled()
    })

    it('says so when everybody in the organization is already on the team', async () => {
      vi.mocked(orgMembersMock).mockResolvedValue([
        membership({ id: '7', email: 'ada@example.com', firstName: 'Ada' }),
        membership({ id: '8', email: 'rae@example.com', firstName: 'Rae' }),
      ])
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      expect(
        await screen.findByText(/Everybody in Acme Labs is already on this team/),
      ).toBeInTheDocument()
      expect(screen.queryByLabelText('Colleague')).not.toBeInTheDocument()
    })

    it('reports a failed roster read rather than offering an empty picker', async () => {
      vi.mocked(orgMembersMock).mockRejectedValue(new Error('offline'))
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'We could not load your colleagues.',
      )
      expect(screen.queryByLabelText('Colleague')).not.toBeInTheDocument()
    })

    it('points at the invitation panel when there is no organization to add from', async () => {
      withOrganization(null)
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      expect(await screen.findByText(/no organization, so there is no roster/)).toBeInTheDocument()
      expect(screen.queryByLabelText('Colleague')).not.toBeInTheDocument()
      // The way in is still on the page: the invitation form is a screen down.
      expect(screen.getByLabelText('Email address')).toBeInTheDocument()
    })

    it('is not offered to somebody who does not own the team', async () => {
      vi.mocked(useAuth).mockReturnValue({
        user: NOT_THE_OWNER,
      } as unknown as ReturnType<typeof useAuth>)
      renderAt()
      await screen.findByRole('heading', { level: 1, name: 'Registrar' })

      expect(screen.queryByLabelText('Colleague')).not.toBeInTheDocument()
      expect(orgMembersMock).not.toHaveBeenCalled()
    })
  })

  describe('team review', () => {
    it('lets the owner make a member a reviewer, and says what the server refuses', async () => {
      vi.mocked(setReviewerMock).mockResolvedValue({
        success: false,
        message: 'You cannot change who reviews for this team.',
        field: null,
      })
      renderAt()

      fireEvent.click(await screen.findByRole('button', { name: 'Make reviewer' }))

      await waitFor(() => expect(setReviewerMock).toHaveBeenCalledWith('9', '8', true))
      expect(await screen.findByRole('alert')).toHaveTextContent('cannot change who reviews')
    })

    it('offers to take the role back from somebody who already holds it', async () => {
      vi.mocked(teamMembersMock).mockResolvedValue([
        {
          userId: '7',
          email: 'ada@example.com',
          firstName: 'Ada',
          lastName: 'A',
          roleSlugs: ['owner'],
          joinedAt: '2026-01-01T00:00:00.000Z',
        },
        {
          userId: '8',
          email: 'rae@example.com',
          firstName: 'Rae',
          lastName: 'B',
          roleSlugs: ['member', 'reviewer'],
          joinedAt: '2026-02-01T00:00:00.000Z',
        },
      ])
      renderAt()

      expect(await screen.findByRole('button', { name: 'Remove as reviewer' })).toBeInTheDocument()
    })

    it('shows the review queue to a reviewer, and not to a plain member', async () => {
      vi.mocked(queueMock).mockResolvedValue([
        {
          id: '31',
          title: 'Automate hostel payments',
          submittedAt: '2026-03-01T00:00:00Z',
        } as never,
      ])
      renderAt()

      expect(
        await screen.findByRole('heading', { name: 'Waiting for your review' }),
      ).toBeInTheDocument()
      expect(await screen.findByRole('link', { name: /Automate hostel payments/ })).toHaveAttribute(
        'href',
        '/app/ideas/31',
      )
    })

    it('does not show the queue, or the reviewer controls, to an ordinary member', async () => {
      vi.mocked(useAuth).mockReturnValue({
        user: { id: '8', email: 'rae@example.com' },
      } as unknown as ReturnType<typeof useAuth>)
      renderAt()

      await screen.findByRole('heading', { level: 1, name: 'Registrar' })
      expect(
        screen.queryByRole('heading', { name: 'Waiting for your review' }),
      ).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Make reviewer' })).not.toBeInTheDocument()
    })
  })
})
