import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setAccessToken } from '../../../graphql/tokenStore'
import { meRequest, refreshTokenRequest } from '../../identity/auth/authApi'
import { AuthProvider } from '../../identity/auth/AuthContext'
import { createOrganizationRequest, organizationsRequest } from '../api/organizationApi'
import { OrganizationProvider } from './OrganizationProvider'
import { useOrganization } from './useOrganization'

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

function organization(id: string, name: string, slug: string) {
  return {
    id,
    name,
    slug,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  }
}

function role(id: string, org: ReturnType<typeof organization>, name = 'Owner') {
  return {
    id: `role-${id}-${name.toLowerCase()}`,
    name,
    slug: name.toLowerCase(),
    description: `${name} role`,
    isSystem: name === 'Owner',
    createdAt: org.createdAt,
    updatedAt: org.updatedAt,
    organization: org,
    permissions: [
      {
        id: 'permission-organization-view',
        code: 'organization.view',
        name: 'View organization',
        description: 'View the organization.',
        createdAt: org.createdAt,
        updatedAt: org.updatedAt,
      },
      ...(name === 'Owner'
        ? [
            {
              id: 'permission-organization-members-manage',
              code: 'organization.members.manage',
              name: 'Manage organization members',
              description: 'Manage members.',
              createdAt: org.createdAt,
              updatedAt: org.updatedAt,
            },
          ]
        : []),
    ],
  }
}

function membershipItem(id: string, name: string, slug: string) {
  const org = organization(id, name, slug)
  return {
    organization: org,
    membership: {
      id: `membership-${id}`,
      status: 'active' as const,
      createdAt: org.createdAt,
      updatedAt: org.updatedAt,
      user: USER,
      organization: org,
      roles:
        id === 'org-2'
          ? [role(id, org, 'Admin'), role(id, org, 'Viewer')]
          : [role(id, org, 'Owner')],
    },
  }
}

function ContextProbe() {
  const {
    status,
    memberships,
    activeOrganization,
    activeMembership,
    error,
    hasPermission,
    createOrganization,
    setActiveOrganization,
    reload,
  } = useOrganization()

  return (
    <div>
      <p data-testid="status">{status}</p>
      <p data-testid="count">{memberships.length}</p>
      <p data-testid="active">{activeOrganization?.name ?? 'none'}</p>
      <p data-testid="roles">
        {activeMembership?.roles.map((item) => item.name).join(', ') ?? 'none'}
      </p>
      <p data-testid="error">{error ?? ''}</p>
      <p data-testid="can-view">{String(hasPermission('organization.view'))}</p>
      <p data-testid="can-manage">{String(hasPermission('organization.members.manage'))}</p>
      <button type="button" onClick={() => setActiveOrganization('org-2')}>
        Select second
      </button>
      <button type="button" onClick={() => void createOrganization('New Org')}>
        Create
      </button>
      <button type="button" onClick={() => void reload()}>
        Reload
      </button>
    </div>
  )
}

function renderContext(strict = false) {
  const content = (
    <AuthProvider>
      <OrganizationProvider>
        <ContextProbe />
      </OrganizationProvider>
    </AuthProvider>
  )

  return render(strict ? <StrictMode>{content}</StrictMode> : content)
}

function setAuthenticatedSession() {
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
}

describe('OrganizationProvider', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setAccessToken(null)
    setAuthenticatedSession()
    mockedOrganizations.mockResolvedValue([
      membershipItem('org-1', 'Acme Labs', 'acme-labs'),
      membershipItem('org-2', 'Beta Works', 'beta-works'),
    ])
    mockedCreateOrganization.mockResolvedValue({
      success: true,
      message: 'Organization created successfully.',
      field: null,
      organization: organization('org-3', 'New Org', 'new-org'),
      membership: {
        id: 'membership-org-3',
        status: 'active',
        createdAt: '2026-01-01T00:00:00Z',
        updatedAt: '2026-01-01T00:00:00Z',
        user: USER,
        organization: organization('org-3', 'New Org', 'new-org'),
        roles: [role('org-3', organization('org-3', 'New Org', 'new-org'))],
      },
    })
  })

  afterEach(() => {
    setAccessToken(null)
  })

  it('loads organizations once and selects the first one', async () => {
    renderContext()

    expect(await screen.findByText('ready')).toBeInTheDocument()
    expect(screen.getByTestId('count')).toHaveTextContent('2')
    expect(screen.getByTestId('active')).toHaveTextContent('Acme Labs')
    expect(screen.getByTestId('roles')).toHaveTextContent('Owner')
    expect(screen.getByTestId('can-view')).toHaveTextContent('true')
    expect(screen.getByTestId('can-manage')).toHaveTextContent('true')
    expect(mockedOrganizations).toHaveBeenCalledOnce()
  })

  it('switches the active organization without another request', async () => {
    renderContext()
    await screen.findByText('ready')

    fireEvent.click(screen.getByRole('button', { name: 'Select second' }))

    expect(screen.getByTestId('active')).toHaveTextContent('Beta Works')
    expect(screen.getByTestId('roles')).toHaveTextContent('Admin, Viewer')
    expect(screen.getByTestId('can-view')).toHaveTextContent('true')
    expect(screen.getByTestId('can-manage')).toHaveTextContent('false')
    expect(mockedOrganizations).toHaveBeenCalledOnce()
  })

  it('preserves an empty organization state', async () => {
    mockedOrganizations.mockResolvedValue([])

    renderContext()

    expect(await screen.findByText('ready')).toBeInTheDocument()
    expect(screen.getByTestId('count')).toHaveTextContent('0')
    expect(screen.getByTestId('active')).toHaveTextContent('none')
  })

  it('adds and selects a newly created organization', async () => {
    renderContext()
    await screen.findByText('ready')

    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(screen.getByTestId('active')).toHaveTextContent('New Org'))
    expect(screen.getByTestId('count')).toHaveTextContent('3')
    expect(screen.getByTestId('roles')).toHaveTextContent('Owner')
    expect(mockedCreateOrganization).toHaveBeenCalledWith({ name: 'New Org' })
  })

  it('does not let an initial load overwrite a newly created organization', async () => {
    let resolveOrganizations: (value: ReturnType<typeof membershipItem>[]) => void = () => undefined
    mockedOrganizations.mockReturnValue(
      new Promise((resolve) => {
        resolveOrganizations = resolve
      }),
    )

    renderContext()
    await waitFor(() => expect(mockedOrganizations).toHaveBeenCalledOnce())
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(screen.getByTestId('active')).toHaveTextContent('New Org'))

    resolveOrganizations([membershipItem('org-1', 'Acme Labs', 'acme-labs')])

    await waitFor(() => expect(screen.getByTestId('active')).toHaveTextContent('New Org'))
    expect(screen.getByTestId('count')).toHaveTextContent('1')
  })

  it('exposes an error and can reload after a failed request', async () => {
    mockedOrganizations
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce([membershipItem('org-1', 'Acme Labs', 'acme-labs')])

    renderContext()

    await waitFor(() =>
      expect(screen.getByTestId('error')).toHaveTextContent('We could not load your organizations'),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }))

    await waitFor(() => expect(screen.getByText('ready')).toBeInTheDocument())
    expect(screen.getByTestId('count')).toHaveTextContent('1')
    expect(mockedOrganizations).toHaveBeenCalledTimes(2)
  })

  it('does not load organization data without an authenticated session', async () => {
    mockedRefresh.mockResolvedValue({
      success: false,
      message: 'no session',
      session: null,
    })
    mockedMe.mockResolvedValue(null)

    renderContext()

    await waitFor(() => expect(screen.getByText('idle')).toBeInTheDocument())
    expect(mockedOrganizations).not.toHaveBeenCalled()
  })

  it('deduplicates the initial request in StrictMode', async () => {
    renderContext(true)

    expect(await screen.findByText('ready')).toBeInTheDocument()
    expect(mockedOrganizations).toHaveBeenCalledOnce()
  })
})
