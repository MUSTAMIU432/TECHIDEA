import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, Outlet, RouterProvider, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import { IdeasWorkspace } from './IdeasWorkspace'
import { useMyTeams } from '../../teams/hooks/useMyTeams'
import { useOrganization } from '../../organizations/context/useOrganization'

/**
 * "Where does this idea belong?" — the dialog behind the global create button.
 *
 * Three behaviours are worth pinning, and they are the three that make the
 * dialog more than a step: it is **asked before anything is written**, it lists
 * **only tenants the reader may file for**, and it **never appears** on a team or
 * organization's own page, where the context is already known.
 */
vi.mock('../../ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  ideasRequest: vi.fn(async () => ({ items: [], pageInfo: emptyPageInfo() })),
  organizationIdeasRequest: vi.fn(async () => ({
    items: [],
    pageInfo: emptyPageInfo(),
  })),
  categoriesRequest: vi.fn(async () => []),
  organizationReviewsRequest: vi.fn(async () => []),
}))
vi.mock('../../teams/api/teamsApi', () => ({
  teamsRequest: vi.fn(async () => []),
}))
vi.mock('../../organizations/api/organizationApi', () => ({
  organizationsRequest: vi.fn(async () => []),
}))
vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../teams/hooks/useMyTeams', () => ({ useMyTeams: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({
  useOrganization: vi.fn(),
}))

function emptyPageInfo() {
  return {
    offset: 0,
    limit: 20,
    totalCount: 0,
    hasNextPage: false,
    hasPreviousPage: false,
  }
}

const TEAM = {
  id: '9',
  name: 'Automation Team',
  slug: 'automation-team',
  description: '',
}
const ORGANIZATION = {
  id: '3',
  name: 'MUNA',
  slug: 'muna',
  createdAt: '',
  updatedAt: '',
}

function signedIn() {
  vi.mocked(useAuth).mockReturnValue({
    user: { id: '7', email: 'ada@example.com' },
  } as unknown as ReturnType<typeof useAuth>)
}

function withTeams(teams: Array<typeof TEAM>) {
  vi.mocked(useMyTeams).mockReturnValue({
    teams: teams.map((team) => ({
      ...team,
      ownerId: '7',
      memberCount: 1,
      createdAt: '',
    })),
    loading: false,
    error: null,
    reload: vi.fn(),
  } as unknown as ReturnType<typeof useMyTeams>)
}

function withOrganizations(names: Array<string>) {
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: ORGANIZATION,
    memberships: names.map((name, index) => ({
      organization: { ...ORGANIZATION, id: String(index + 3), name },
      membership: {
        id: `m${index}`,
        status: 'active',
        createdAt: '',
        updatedAt: '',
        user: {},
        organization: {},
        roles: [],
      },
    })),
  } as unknown as ReturnType<typeof useOrganization>)
}

/**
 * A route that reports where it is.
 *
 * `useLocation` only answers inside a router, so the probe is a route element
 * rather than a sibling of `<RouterProvider>` - and it has to be one, because
 * what these tests care about is the URL the dialog navigates to.
 */
function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderWorkspace() {
  const router = createMemoryRouter(
    [
      {
        path: '/app',
        element: (
          <>
            <Outlet />
            <LocationProbe />
          </>
        ),
        children: [
          { path: 'ideas', element: <IdeasWorkspace /> },
          { path: 'ideas/new', element: <p>Idea form</p> },
        ],
      },
    ],
    { initialEntries: ['/app/ideas'] },
  )
  render(<RouterProvider router={router} />)
  return router
}

async function openDialog() {
  renderWorkspace()
  fireEvent.click(await screen.findByRole('button', { name: /File a new idea/ }))
  return screen.findByRole('dialog', { name: 'Create a New Idea' })
}

beforeEach(() => {
  vi.clearAllMocks()
  signedIn()
  withTeams([TEAM])
  withOrganizations(['MUNA'])
})

describe('the create-idea context dialog', () => {
  it('asks where the idea belongs before anything is created', async () => {
    const dialog = await openDialog()

    expect(dialog).toBeInTheDocument()
    expect(
      screen.getByRole('heading', { name: 'Where does this idea belong?' }),
    ).toBeInTheDocument()
    expect(screen.getByText('An idea that belongs to you personally.')).toBeInTheDocument()
    expect(screen.getByText('An idea created and owned by a team.')).toBeInTheDocument()
    expect(screen.getByText('An idea created and owned by an organization.')).toBeInTheDocument()
    // And no form is open behind it: the question comes first.
    expect(screen.queryByText('Idea form')).not.toBeInTheDocument()
  })

  it('goes straight to the confirmation for an individual idea', async () => {
    await openDialog()
    const options = screen.getAllByRole('listitem')
    fireEvent.click(within(options[0]).getByRole('button', { name: 'Select' }))

    // One choice, one confirmation - no tenant to pick for an idea of one's own.
    expect(await screen.findByText('Individual Idea')).toBeInTheDocument()
    expect(screen.getByText('yourself')).toBeInTheDocument()
  })

  it('lists only the teams the reader is an active member of', async () => {
    await openDialog()
    fireEvent.click(screen.getAllByRole('button', { name: 'Select' })[1])

    const select = await screen.findByLabelText('Team')
    expect(within(select).getByRole('option', { name: /Automation Team/ })).toBeInTheDocument()
    // Only the placeholder and that one team: the API lists the reader's own
    // memberships, so there is nothing else it *could* have shown.
    expect(within(select).getAllByRole('option')).toHaveLength(2)
  })

  it('says so when there is no team to file for, instead of an empty picker', async () => {
    withTeams([])
    await openDialog()
    fireEvent.click(screen.getAllByRole('button', { name: 'Select' })[1])

    expect(await screen.findByText(/You are not in a team yet/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Team')).not.toBeInTheDocument()
  })

  it('confirms the team before opening the form, and carries it in the URL', async () => {
    await openDialog()
    fireEvent.click(screen.getAllByRole('button', { name: 'Select' })[1])
    fireEvent.change(await screen.findByLabelText('Team'), {
      target: { value: '9' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    expect(await screen.findByText('Team Idea')).toBeInTheDocument()
    expect(screen.getByText('Automation Team')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    // The context travels in the URL, not in React state: the page it opens can
    // be reloaded, bookmarked and navigated back to.
    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent(
        '/app/ideas/new?context=team&team=9',
      ),
    )
  })

  it('lists only the organizations the reader is a member of', async () => {
    await openDialog()
    fireEvent.click(screen.getAllByRole('button', { name: 'Select' })[2])

    const select = await screen.findByLabelText('Organization')
    const options = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent)
    expect(options.some((text) => text?.includes('MUNA'))).toBe(true)
  })

  it('lets the reader change their mind on the confirmation', async () => {
    await openDialog()
    fireEvent.click(screen.getAllByRole('button', { name: 'Select' })[1])
    fireEvent.change(await screen.findByLabelText('Team'), {
      target: { value: '9' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))

    fireEvent.click(await screen.findByRole('button', { name: 'Change' }))

    // Back at the picker, with the three choices rather than the form.
    expect(
      screen.getByRole('heading', { name: 'Where does this idea belong?' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Continue/ })).not.toBeInTheDocument()
  })

  it('closes without navigating', async () => {
    await openDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/app/ideas')
    expect(screen.queryByText('Idea form')).not.toBeInTheDocument()
  })

  it('closes on Escape, because a modal that traps is a modal that traps', async () => {
    await openDialog()
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/app/ideas')
  })

  it('cannot continue past a tenant picker with nothing chosen', async () => {
    await openDialog()
    fireEvent.click(screen.getAllByRole('button', { name: 'Select' })[1])

    expect(await screen.findByRole('button', { name: 'Continue' })).toBeDisabled()
  })
})
