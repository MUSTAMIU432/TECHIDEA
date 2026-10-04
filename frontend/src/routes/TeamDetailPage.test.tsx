import { render, screen } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { makeTeam } from '../test/idea'
import { TeamDetailPage } from './TeamDetailPage'

vi.mock('../features/teams/api/teamsApi', () => ({
  teamsRequest: vi.fn(async () => []),
  teamRequest: vi.fn(),
  teamMembersRequest: vi.fn(async () => []),
  createTeamRequest: vi.fn(),
  leaveTeamRequest: vi.fn(),
  addTeamMemberRequest: vi.fn(),
  teamRoleLabel: (slug: string) =>
    slug.replace(/_/g, ' ').replace(/^\w/, (letter) => letter.toUpperCase()),
}))
vi.mock('../features/invitations/api/invitationsApi', () => ({
  teamInvitationsRequest: vi.fn(async () => []),
  organizationInvitationsRequest: vi.fn(async () => []),
  sendTeamInvitationRequest: vi.fn(),
  revokeInvitationRequest: vi.fn(),
  acceptInvitationRequest: vi.fn(),
  invitationDetailsRequest: vi.fn(),
}))
// A team page reads no organization of its own, so the panel that would name a
// colleague from one has nothing to name and says so instead of asking.
vi.mock('../features/organizations/context/useOrganization', () => ({
  useOrganization: vi.fn(() => ({ activeOrganization: null })),
}))
vi.mock('../features/identity/auth/AuthContext', () => ({
  useAuth: vi.fn(() => ({ user: { id: '7', email: 'ada@example.com' } })),
}))

const { teamRequest: teamMock } = await import('../features/teams/api/teamsApi')

/**
 * `/app/teams/:teamId` rendered as the router renders it, so this test covers
 * the route as much as the page: the team id is read from the URL, not passed
 * in, and a wrapper that took a prop would test a component the app lacks.
 */
function renderAt(teamId = '9') {
  const router = createMemoryRouter([{ path: '/app/teams/:teamId', element: <TeamDetailPage /> }], {
    initialEntries: [`/app/teams/${teamId}`],
  })
  render(<RouterProvider router={router} />)
}

describe('TeamDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(teamMock).mockResolvedValue(makeTeam({ id: '9', name: 'Registrar' }))
  })

  it('shows the team named in the URL', async () => {
    renderAt()

    expect(await screen.findByRole('heading', { level: 1, name: 'Registrar' })).toBeInTheDocument()
    expect(teamMock).toHaveBeenCalledWith('9')
  })

  it('asks about the team in the URL, so moving between teams cannot show one under another', async () => {
    vi.mocked(teamMock).mockImplementation(async (id: string) =>
      id === '4' ? makeTeam({ id: '4', name: 'Platform team', slug: 'platform-team' }) : null,
    )
    const router = createMemoryRouter(
      [{ path: '/app/teams/:teamId', element: <TeamDetailPage /> }],
      {
        initialEntries: ['/app/teams/4', '/app/teams/9'],
        initialIndex: 0,
      },
    )
    render(<RouterProvider router={router} />)
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Platform team' }),
    ).toBeInTheDocument()

    // One team is not available to this reader, and that is the only answer:
    // the previous team's name must not survive the move.
    await router.navigate('/app/teams/9')

    expect(await screen.findByText('This team is not available.')).toBeInTheDocument()
    expect(screen.queryByText('Platform team')).not.toBeInTheDocument()
    expect(teamMock).toHaveBeenNthCalledWith(1, '4')
    expect(teamMock).toHaveBeenNthCalledWith(2, '9')
  })
})
