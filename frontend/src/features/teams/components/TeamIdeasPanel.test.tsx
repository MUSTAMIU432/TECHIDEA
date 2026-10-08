import { render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { useMyTeams } from '../hooks/useMyTeams'
import { makeIdea } from '../../../test/idea'
import { OrganizationDetailPage } from '../../organizations/components/OrganizationDetailPage'
import { TeamIdeasPanel } from './TeamIdeasPanel'

vi.mock('../../ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  teamIdeasRequest: vi.fn(),
  organizationIdeasRequest: vi.fn(),
}))
vi.mock('../../organizations/api/organizationApi', async (importOriginal) => ({
  ...(await importOriginal()),
  organizationMembersRequest: vi.fn(),
  organizationsRequest: vi.fn(),
}))
vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../hooks/useTeamIdeas', () => ({ useTeamIdeas: vi.fn() }))
vi.mock('../hooks/useMyTeams', () => ({ useMyTeams: vi.fn() }))
vi.mock('../../reviews/hooks/useCanReview', () => ({
  useCanReview: () => false,
}))
vi.mock('../../organizations/context/useOrganization', () => ({
  useOrganization: vi.fn(),
}))

const { teamIdeasRequest, organizationIdeasRequest } = await import('../../ideas/api/ideasApi')
const { organizationMembersRequest } = await import('../../organizations/api/organizationApi')
const { useTeamIdeas } = await import('../hooks/useTeamIdeas')

function emptyPage() {
  return {
    offset: 0,
    limit: 20,
    totalCount: 0,
    hasNextPage: false,
    hasPreviousPage: false,
  }
}

function page(items: unknown[]) {
  return { items, pageInfo: { ...emptyPage(), totalCount: items.length } }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuth).mockReturnValue({
    user: { id: '7', email: 'ada@example.com' },
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useMyTeams).mockReturnValue({
    teams: [],
    loading: false,
    error: null,
    reload: vi.fn(),
  } as never)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: null,
    memberships: [],
  } as never)
  vi.mocked(useTeamIdeas).mockReturnValue({
    ideas: [],
    pageInfo: emptyPage(),
    loading: false,
    error: null,
    reload: vi.fn(),
  })
  vi.mocked(organizationMembersRequest).mockResolvedValue([])
  vi.mocked(organizationIdeasRequest).mockResolvedValue(page([]) as never)
})

describe('TeamIdeasPanel', () => {
  it('says whose ideas these are when there are none', async () => {
    render(
      <RouterProvider
        router={createMemoryRouter([{ path: '/', element: <TeamIdeasPanel teamId="9" /> }])}
      />,
    )

    expect(await screen.findByText('This team has no ideas yet.')).toBeInTheDocument()
    // The sentence that matters: filing from here decides the owner, whatever
    // the audience ends up being.
    expect(screen.getByText(/belongs to this team/)).toBeInTheDocument()
  })

  it('links the empty state straight at the form, with the context decided', async () => {
    render(
      <RouterProvider
        router={createMemoryRouter([{ path: '/', element: <TeamIdeasPanel teamId="9" /> }])}
      />,
    )

    const links = await screen.findAllByRole('link', { name: /Create/ })
    for (const link of links) {
      expect(link).toHaveAttribute('href', '/app/ideas/new?context=team&team=9')
    }
  })

  it('shows ownership and audience separately on each row', async () => {
    vi.mocked(useTeamIdeas).mockReturnValue({
      ideas: [
        makeIdea({
          id: '1',
          title: 'Automate the invoice run',
          submissionContext: 'TEAM',
          teamId: '9',
          tenantName: 'Automation Team',
          visibility: 'TEAM',
        }),
      ],
      pageInfo: emptyPage(),
      loading: false,
      error: null,
      reload: vi.fn(),
    } as never)
    render(
      <RouterProvider
        router={createMemoryRouter([{ path: '/', element: <TeamIdeasPanel teamId="9" /> }])}
      />,
    )

    expect(await screen.findByText('Team Idea')).toBeInTheDocument()
    // A public team idea is still the team's - the two facts must not merge.
    expect(screen.getByText('Automation Team')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Join/ })).not.toBeInTheDocument()
  })

  it('asks the server for this team, not for every idea', async () => {
    // The hook is mocked here, so this asserts the *contract* the hook keeps:
    // the panel asks for one team and never for the platform.
    vi.mocked(teamIdeasRequest).mockResolvedValue(page([]) as never)
    await teamIdeasRequest('9')

    expect(teamIdeasRequest).toHaveBeenCalledWith('9')
  })
})

describe('OrganizationDetailPage', () => {
  function renderPage(organizationId = '3') {
    return render(
      <RouterProvider
        router={createMemoryRouter(
          [
            {
              path: '/app/organizations/:organizationId',
              element: <OrganizationDetailPage />,
            },
          ],
          { initialEntries: [`/app/organizations/${organizationId}`] },
        )}
      />,
    )
  }

  function withMembership() {
    vi.mocked(useOrganization).mockReturnValue({
      activeOrganization: null,
      setActiveOrganization: vi.fn(),
      memberships: [
        {
          organization: {
            id: '3',
            name: 'MUNA',
            slug: 'muna',
            createdAt: '',
            updatedAt: '',
          },
          membership: {
            id: 'm1',
            status: 'active',
            createdAt: '',
            updatedAt: '',
            user: {},
            organization: {},
            roles: [
              {
                id: 'r1',
                name: 'Owner',
                slug: 'owner',
                isSystem: true,
                permissions: [],
              },
            ],
          },
        },
      ],
    } as never)
  }

  it('says an organization the reader is not in is unavailable, without saying which', async () => {
    renderPage()

    // Both "not a member" and "does not exist" answer the same way, so an id
    // cannot be used to find out about somebody else's organization.
    expect(await screen.findByText('This organization is not available.')).toBeInTheDocument()
    expect(organizationMembersRequest).not.toHaveBeenCalled()
  })

  it('sends the reader straight to the form, with the organization decided', async () => {
    withMembership()
    renderPage()

    expect(await screen.findByRole('heading', { name: 'MUNA' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Create Idea/ })).toHaveAttribute(
      'href',
      '/app/ideas/new?context=organization&organization=3',
    )
  })

  it('lists the organization’s ideas, ownership and audience on each', async () => {
    withMembership()
    vi.mocked(organizationIdeasRequest).mockResolvedValue(
      page([
        makeIdea({
          id: '1',
          title: 'Automate hostel payments',
          submissionContext: 'ORGANIZATION',
          organizationId: '3',
          tenantName: 'MUNA',
          visibility: 'PUBLIC',
        }),
      ]) as never,
    )
    renderPage()

    expect(await screen.findByText('Automate hostel payments')).toBeInTheDocument()
    expect(screen.getByText('Organization Idea')).toBeInTheDocument()
    // Public audience, organization owner - the case that must not read as one
    // fact.
    expect(screen.getByText('Public')).toBeInTheDocument()
    expect(screen.getByText('Owned by')).toBeInTheDocument()
    expect(organizationIdeasRequest).toHaveBeenCalledWith('3')
  })

  it('offers the empty state with the organization’s own call to action', async () => {
    withMembership()
    renderPage()

    expect(await screen.findByText('This organization has no ideas yet.')).toBeInTheDocument()
    expect(
      screen.getByText(/it is the organization that confirms it before the platform sees it/),
    ).toBeInTheDocument()
  })

  it('reports a failed load as a failure, with a way to try again', async () => {
    withMembership()
    vi.mocked(organizationMembersRequest).mockRejectedValue(new Error('offline'))
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not load this organization',
    )
    await waitFor(() => expect(organizationMembersRequest).toHaveBeenCalledTimes(1))
  })
})
