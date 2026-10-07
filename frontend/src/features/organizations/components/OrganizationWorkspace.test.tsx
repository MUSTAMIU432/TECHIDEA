import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MemoryRouter } from 'react-router-dom'

import { setAccessToken } from '../../../graphql/tokenStore'
import { meRequest, refreshTokenRequest } from '../../identity/auth/authApi'
import { AuthProvider } from '../../identity/auth/AuthContext'
import { createOrganizationRequest, organizationsRequest } from '../api/organizationApi'
import { OrganizationSwitcher } from './OrganizationSwitcher'
import { OrganizationWorkspace } from './OrganizationWorkspace'
import { OrganizationProvider } from '../context/OrganizationProvider'

vi.mock('../../identity/auth/authApi', () => ({
  loginRequest: vi.fn(),
  googleLoginRequest: vi.fn(),
  logoutRequest: vi.fn(),
  refreshTokenRequest: vi.fn(),
  meRequest: vi.fn(),
}))

vi.mock('../api/organizationApi', () => ({
  organizationsRequest: vi.fn(),
  createOrganizationRequest: vi.fn(),
}))

const mockedRefresh = vi.mocked(refreshTokenRequest)
const mockedMe = vi.mocked(meRequest)
const mockedOrganizations = vi.mocked(organizationsRequest)
const mockedCreateOrganization = vi.mocked(createOrganizationRequest)

const USER = {
  id: '1',
  email: 'ada@example.com',
  firstName: 'Ada',
  lastName: 'Lovelace',
  phoneNumber: '+255712345678',
  isActive: true,
  isVerified: false,
}

function makeOrganization(id: string, name: string, slug: string) {
  return {
    id,
    name,
    slug,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
}

function makeRole(id: string, organization: ReturnType<typeof makeOrganization>, name = 'Owner') {
  return {
    id: `role-${id}-${name.toLowerCase()}`,
    name,
    slug: name.toLowerCase(),
    description: `${name} role`,
    isSystem: name === 'Owner',
    createdAt: organization.createdAt,
    updatedAt: organization.updatedAt,
    organization,
    permissions: [
      {
        id: 'permission-organization-view',
        code: 'organization.view',
        name: 'View organization',
        description: 'View the organization.',
        createdAt: organization.createdAt,
        updatedAt: organization.updatedAt,
      },
    ],
  }
}

function makeItem(id: string, name: string, slug: string) {
  const organization = makeOrganization(id, name, slug)
  return {
    organization,
    membership: {
      id: `membership-${id}`,
      status: 'active' as const,
      createdAt: organization.createdAt,
      updatedAt: organization.updatedAt,
      user: USER,
      organization,
      roles:
        id === 'org-2'
          ? [makeRole(id, organization), makeRole(id, organization, 'Admin')]
          : [makeRole(id, organization)],
    },
  }
}

/**
 * In a `MemoryRouter` because the organization list now offers a way *into* an
 * organization - a `Link` to `/app/organizations/:id`, where its ideas and its own
 * "Create Idea" button live. The real app is always inside a router (every `/app`
 * page is a child of one), so this is the component's actual context rather than
 * a test accommodation.
 */
function renderWorkspace() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <OrganizationProvider>
          <OrganizationSwitcher />
          <OrganizationWorkspace />
        </OrganizationProvider>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('OrganizationWorkspace', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setAccessToken(null)
    mockedRefresh.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    mockedMe.mockResolvedValue(USER)
    mockedOrganizations.mockResolvedValue([
      makeItem('org-1', 'Acme Labs', 'acme-labs'),
      makeItem('org-2', 'Beta Works', 'beta-works'),
    ])
  })

  afterEach(() => {
    setAccessToken(null)
  })

  it('shows a loading state while organizations are being fetched', async () => {
    mockedOrganizations.mockReturnValue(new Promise(() => {}))

    renderWorkspace()

    expect(await screen.findByText('Loading organizations…')).toBeInTheDocument()
  })

  it('shows an empty state and the creation form for a new user', async () => {
    mockedOrganizations.mockResolvedValue([])

    renderWorkspace()

    await waitFor(() => expect(screen.getByText('No organizations yet')).toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Create an organization' })).toBeInTheDocument()
  })

  it('lists multiple organizations and switches the active one', async () => {
    renderWorkspace()

    expect(await screen.findByRole('button', { name: /Acme Labs/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Beta Works/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Acme Labs/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: /Acme Labs/ })).toHaveTextContent('Owner')

    fireEvent.change(screen.getByRole('combobox', { name: 'Active organization' }), {
      target: { value: 'org-2' },
    })

    expect(screen.getByRole('button', { name: /Beta Works/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: /Beta Works/ })).toHaveTextContent('Owner, Admin')
  })

  it('shows an error and retries loading organizations', async () => {
    mockedOrganizations
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce([makeItem('org-1', 'Acme Labs', 'acme-labs')])

    renderWorkspace()

    expect(await screen.findByText('Organizations unavailable')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('button', { name: /Acme Labs/ })).toBeInTheDocument()
  })

  it('validates the form before calling the API', async () => {
    mockedOrganizations.mockResolvedValue([])

    renderWorkspace()
    await screen.findByText('No organizations yet')

    fireEvent.click(screen.getByRole('button', { name: 'Create organization' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Organization name is required.')
    expect(mockedCreateOrganization).not.toHaveBeenCalled()
  })

  it('creates an organization and displays it in the list', async () => {
    mockedOrganizations.mockResolvedValue([])
    mockedCreateOrganization.mockResolvedValue({
      success: true,
      message: 'Organization created successfully.',
      field: null,
      organization: makeOrganization('org-3', 'New Org', 'new-org'),
      membership: {
        id: 'membership-org-3',
        status: 'active',
        createdAt: '2026-01-01T00:00:00Z',
        updatedAt: '2026-01-01T00:00:00Z',
        user: USER,
        organization: makeOrganization('org-3', 'New Org', 'new-org'),
        roles: [makeRole('org-3', makeOrganization('org-3', 'New Org', 'new-org'))],
      },
    })

    renderWorkspace()
    await screen.findByText('No organizations yet')

    fireEvent.change(screen.getByLabelText('Organization name'), {
      target: { value: 'New Org' },
    })
    fireEvent.change(screen.getByLabelText(/Slug/), {
      target: { value: 'new-org' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create organization' }))

    expect(await screen.findByRole('button', { name: /New Org/ })).toBeInTheDocument()
    expect(screen.getByText('Organization created successfully.')).toBeInTheDocument()
    expect(mockedCreateOrganization).toHaveBeenCalledWith({
      name: 'New Org',
      slug: 'new-org',
    })
  })
})
