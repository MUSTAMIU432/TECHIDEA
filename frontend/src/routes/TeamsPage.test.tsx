import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { makeTeam } from '../test/idea'
import { TeamsPage } from './TeamsPage'

vi.mock('../features/teams/api/teamsApi', () => ({
  teamsRequest: vi.fn(),
  createTeamRequest: vi.fn(),
  leaveTeamRequest: vi.fn(),
  addTeamMemberRequest: vi.fn(),
  teamRequest: vi.fn(),
  teamMembersRequest: vi.fn(),
  teamRoleLabel: (slug: string) =>
    slug.replace(/_/g, ' ').replace(/^\w/, (letter) => letter.toUpperCase()),
}))

const { teamsRequest: teamsMock, createTeamRequest: createMock } =
  await import('../features/teams/api/teamsApi')

const REGISTRAR = makeTeam({ id: '9', name: 'Registrar', memberCount: 3 })
const PLATFORM = makeTeam({
  id: '4',
  name: 'Platform team',
  slug: 'platform-team',
  description: 'The people who keep the platform up.',
  memberCount: 1,
})

function renderAt() {
  const router = createMemoryRouter(
    [
      { path: '/app/teams', element: <TeamsPage /> },
      { path: '/app/teams/:teamId', element: <p>One team</p> },
    ],
    { initialEntries: ['/app/teams'] },
  )
  render(<RouterProvider router={router} />)
  return router
}

describe('TeamsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(teamsMock).mockResolvedValue([REGISTRAR, PLATFORM])
  })

  it('lists the teams the caller is in, as links into each one', async () => {
    renderAt()

    const list = await screen.findByRole('list', { name: 'Your teams' })
    expect(within(list).getByText('Registrar')).toBeInTheDocument()
    expect(within(list).getByText('/registrar')).toBeInTheDocument()
    // A team needs no organization, so this page is reachable by somebody in
    // none - and says what a team is rather than assuming a tenant.
    expect(screen.getByRole('heading', { level: 1, name: 'Your teams.' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Registrar/ })).toHaveAttribute('href', '/app/teams/9')
  })

  it('says an empty list is not a failure, and still offers the form', async () => {
    vi.mocked(teamsMock).mockResolvedValue([])
    renderAt()

    expect(await screen.findByText('You are not in a team yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create team' })).toBeInTheDocument()
  })

  it('reports a failed load as itself, with a way to try again', async () => {
    vi.mocked(teamsMock).mockRejectedValue(new Error('offline'))
    renderAt()

    expect(await screen.findByRole('alert')).toHaveTextContent('We could not load your teams.')
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    await waitFor(() => expect(teamsMock).toHaveBeenCalledTimes(2))
  })

  it('creates a team and asks for the list again, because the new one belongs in it', async () => {
    vi.mocked(createMock).mockResolvedValue({
      success: true,
      message: 'Team created.',
      field: null,
      team: REGISTRAR,
    })
    renderAt()
    await screen.findByRole('list', { name: 'Your teams' })

    fireEvent.change(screen.getByLabelText('Team name'), { target: { value: 'Registrar' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create team' }))

    expect(await screen.findByText('Team created.')).toBeInTheDocument()
    expect(createMock).toHaveBeenCalledWith({ name: 'Registrar', description: '' })
    await waitFor(() => expect(teamsMock).toHaveBeenCalledTimes(2))
  })

  it('refuses an empty name without a request', async () => {
    renderAt()
    await screen.findByRole('list', { name: 'Your teams' })

    fireEvent.click(screen.getByRole('button', { name: 'Create team' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Give the team a name.')
    expect(createMock).not.toHaveBeenCalled()
  })
})
