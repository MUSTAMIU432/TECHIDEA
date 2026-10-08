import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setAccessToken } from '../graphql/tokenStore'
import { AuthProvider } from '../features/identity/auth/AuthContext'
import {
  createOrganizationRequest,
  organizationsRequest,
} from '../features/organizations/api/organizationApi'
import { logoutRequest, meRequest, refreshTokenRequest } from '../features/identity/auth/authApi'
import { viewerCanReviewInRequest } from '../features/reviews/api/reviewsApi'
import { AppLayout } from '../layouts/AppLayout'
import { useOrganization } from '../features/organizations/context/useOrganization'
import { DashboardPage } from './DashboardPage'

vi.mock('../features/organizations/api/organizationApi', () => ({
  organizationsRequest: vi.fn(),
  createOrganizationRequest: vi.fn(),
}))

vi.mock('../features/ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  ideasRequest: () =>
    Promise.resolve({
      items: [],
      pageInfo: {
        offset: 0,
        limit: 5,
        totalCount: 0,
        hasNextPage: false,
        hasPreviousPage: false,
      },
    }),
}))
vi.mock('../features/reviews/api/reviewsApi', () => ({
  viewerCanReviewInRequest: vi.fn(),
}))

vi.mock('../features/identity/auth/authApi', () => ({
  loginRequest: vi.fn(),
  googleLoginRequest: vi.fn(),
  logoutRequest: vi.fn(),
  refreshTokenRequest: vi.fn(),
  meRequest: vi.fn(),
}))

const mockedRefresh = vi.mocked(refreshTokenRequest)
const mockedMe = vi.mocked(meRequest)
const mockedLogout = vi.mocked(logoutRequest)
const mockedOrganizations = vi.mocked(organizationsRequest)
const mockedCreateOrganization = vi.mocked(createOrganizationRequest)
const mockedCanReview = vi.mocked(viewerCanReviewInRequest)

const USER = {
  id: '1',
  email: 'ada@example.com',
  firstName: 'Ada',
  lastName: 'Lovelace',
  phoneNumber: '+255712345678',
  isActive: true,
  isVerified: false,
}

const ORGANIZATION = {
  id: '10',
  name: 'Acme Labs',
  slug: 'acme-labs',
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-01T00:00:00Z',
}

const OWNER_ROLE = {
  id: '30',
  name: 'Owner',
  slug: 'owner',
  description: 'Owner role',
  isSystem: true,
  createdAt: ORGANIZATION.createdAt,
  updatedAt: ORGANIZATION.updatedAt,
  organization: ORGANIZATION,
  permissions: [
    {
      id: '40',
      code: 'organization.view',
      name: 'View organization',
      description: 'View the organization.',
      createdAt: ORGANIZATION.createdAt,
      updatedAt: ORGANIZATION.updatedAt,
    },
  ],
}

const MEMBERSHIP = {
  organization: ORGANIZATION,
  membership: {
    id: '20',
    status: 'active' as const,
    createdAt: ORGANIZATION.createdAt,
    updatedAt: ORGANIZATION.updatedAt,
    user: USER,
    organization: ORGANIZATION,
    roles: [OWNER_ROLE],
  },
}

/** Stands in for a real app page: shows which organization the layout hands it. */
function OrganizationProbe({ label }: { label: string }) {
  const { activeOrganization } = useOrganization()
  return (
    <p>
      {label} for {activeOrganization?.name ?? 'none'}
    </p>
  )
}

function renderDashboard(path = '/app') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/auth" element={<p>Sign-in page</p>} />
          <Route path="/app" element={<AppLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="ideas" element={<OrganizationProbe label="Ideas page" />} />
            <Route path="ideas/new" element={<OrganizationProbe label="New idea page" />} />
            <Route path="reviews" element={<OrganizationProbe label="Reviews page" />} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('DashboardPage', () => {
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
    mockedCanReview.mockResolvedValue(false)
    mockedLogout.mockResolvedValue(undefined)
    mockedOrganizations.mockResolvedValue([MEMBERSHIP])
    mockedCreateOrganization.mockResolvedValue({
      success: true,
      message: 'Organization created successfully.',
      field: null,
      organization: ORGANIZATION,
      membership: {
        id: '20',
        status: 'active',
        createdAt: ORGANIZATION.createdAt,
        updatedAt: ORGANIZATION.updatedAt,
        user: USER,
        organization: ORGANIZATION,
        roles: [OWNER_ROLE],
      },
    })
  })

  afterEach(() => {
    setAccessToken(null)
  })

  it('shows the brand logo, without the user email', async () => {
    renderDashboard()

    expect(await screen.findByRole('link', { name: 'MUSTANET home' })).toHaveAttribute(
      'href',
      '/app',
    )
    expect(screen.queryByText(/ada@example.com/)).not.toBeInTheDocument()
  })

  it('signing out calls the logout API and navigates back to /auth', async () => {
    renderDashboard()
    await screen.findByText('Welcome back, Ada')

    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Sign out' }))

    await waitFor(() => expect(screen.getByText('Sign-in page')).toBeInTheDocument())
    expect(mockedLogout).toHaveBeenCalledOnce()
  })

  it('links to the ideas area from the navigation and the dashboard', async () => {
    renderDashboard()
    await screen.findByText('Welcome back, Ada')

    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/app')
    expect(within(nav).getByRole('link', { name: 'Ideas' })).toHaveAttribute('href', '/app/ideas')
    expect(await screen.findByRole('link', { name: /Put a problem forward/ })).toHaveAttribute(
      'href',
      '/app/ideas',
    )
  })

  it('offers the review queue only to reviewers', async () => {
    mockedCanReview.mockResolvedValue(true)
    renderDashboard()

    const nav = screen.getByRole('navigation', { name: 'Main' })
    expect(await within(nav).findByRole('link', { name: 'Reviews' })).toHaveAttribute(
      'href',
      '/app/reviews',
    )
    expect(await screen.findByRole('link', { name: /Review queue/ })).toHaveAttribute(
      'href',
      '/app/reviews',
    )
  })

  it('hides the review queue from non-reviewers', async () => {
    renderDashboard()
    await screen.findByRole('link', { name: /Put a problem forward/ })
    await waitFor(() => expect(mockedCanReview).toHaveBeenCalledWith('10'))

    expect(screen.queryByRole('link', { name: 'Reviews' })).not.toBeInTheDocument()
    // The card is always there so Home has both; it says plainly when you cannot review.
    expect(await screen.findByText(/You are not a reviewer there yet/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Put a problem forward/ })).toHaveAttribute(
      'href',
      '/app/ideas',
    )
  })

  it('offers a breadcrumb back to the workspace from app sub-pages only', async () => {
    renderDashboard()
    await screen.findByText('Welcome back, Ada')
    expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).not.toBeInTheDocument()

    fireEvent.click(
      within(screen.getByRole('navigation', { name: 'Main' })).getByRole('link', { name: 'Ideas' }),
    )
    const breadcrumb = await screen.findByRole('navigation', {
      name: 'Breadcrumb',
    })
    expect(within(breadcrumb).getByText('Ideas')).toHaveAttribute('aria-current', 'page')

    fireEvent.click(within(breadcrumb).getByRole('link', { name: 'Home' }))
    expect(await screen.findByText('Welcome back, Ada')).toBeInTheDocument()
  })

  it('leads from the new-idea page back to the ideas list through the breadcrumb', async () => {
    renderDashboard('/app/ideas/new')

    const breadcrumb = await screen.findByRole('navigation', {
      name: 'Breadcrumb',
    })
    expect(within(breadcrumb).getByText('New idea')).toHaveAttribute('aria-current', 'page')
    expect(within(breadcrumb).getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/app')

    fireEvent.click(within(breadcrumb).getByRole('link', { name: 'Ideas' }))
    expect(await screen.findByText(/^Ideas page/)).toBeInTheDocument()
  })

  it('shows the account menu from the avatar, and closes it on Escape', async () => {
    renderDashboard()
    await screen.findByText('Welcome back, Ada')

    const avatar = screen.getByRole('button', { name: 'Account menu' })
    expect(avatar).toHaveTextContent('AL')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()

    fireEvent.click(avatar)
    const menu = screen.getByRole('menu', { name: 'Account' })
    expect(avatar).toHaveAttribute('aria-expanded', 'true')
    expect(within(menu).getByText('Ada Lovelace')).toBeInTheDocument()
    expect(within(menu).getByText('ada@example.com')).toBeInTheDocument()
    expect(within(menu).getByRole('menuitem', { name: 'Profile' })).toHaveFocus()
    expect(within(menu).getByRole('menuitem', { name: 'Security' })).toHaveAttribute(
      'href',
      '/app/settings/security',
    )
    expect(within(menu).getByRole('menuitem', { name: 'Change password' })).toBeInTheDocument()
    expect(within(menu).getByRole('menuitem', { name: 'Sign out' })).toBeInTheDocument()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(avatar).toHaveFocus()
  })
})
