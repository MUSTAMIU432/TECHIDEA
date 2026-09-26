import { screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setAccessToken } from '../graphql/tokenStore'
import { meRequest, refreshTokenRequest } from '../features/identity/auth/authApi'
import { organizationIdeasRequest } from '../features/ideas/api/ideasApi'
import { organizationsRequest } from '../features/organizations/api/organizationApi'
import { renderRoutes } from '../test/renderWithRouter'
import { router } from './routes'

vi.mock('../features/organizations/api/organizationApi', () => ({
  organizationsRequest: vi.fn(),
  createOrganizationRequest: vi.fn(),
}))

// The Ideas feature's own API module. Mocked wholesale because
// `/app/ideas` mounts IdeaList, which fetches on mount; the module's own
// documents are asserted in `features/ideas/api/ideasApi.test.ts`.
vi.mock('../features/ideas/api/ideasApi', () => ({
  organizationIdeasRequest: vi.fn(),
  categoriesRequest: vi.fn(),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  submitIdeaRequest: vi.fn(),
}))

vi.mock('../features/identity/auth/authApi', () => ({
  loginRequest: vi.fn(),
  googleLoginRequest: vi.fn(),
  logoutRequest: vi.fn(),
  refreshTokenRequest: vi.fn(),
  meRequest: vi.fn(),
  registerRequest: vi.fn(),
  requestPasswordResetRequest: vi.fn(),
  resetPasswordRequest: vi.fn(),
  activateAccountRequest: vi.fn(),
  resendActivationEmailRequest: vi.fn(),
}))

const mockedRefresh = vi.mocked(refreshTokenRequest)
const mockedMe = vi.mocked(meRequest)
const mockedOrganizations = vi.mocked(organizationsRequest)
const mockedIdeas = vi.mocked(organizationIdeasRequest)

const USER = {
  id: '1',
  email: 'ada@example.com',
  firstName: 'Ada',
  lastName: 'Lovelace',
  phoneNumber: '+255712345678',
  isActive: true,
  isVerified: false,
}

// Reuse the real route tree with an in-memory router.
describe('route tree', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setAccessToken(null)
    mockedRefresh.mockResolvedValue({ success: false, message: 'no session', session: null })
    mockedMe.mockResolvedValue(null)
    mockedOrganizations.mockResolvedValue([])
    mockedIdeas.mockResolvedValue([])
  })

  afterEach(() => {
    setAccessToken(null)
  })

  it('renders the home page at /', () => {
    renderRoutes(router.routes, '/')

    expect(screen.getByText('Frontend foundation is running.')).toBeInTheDocument()
  })

  it('renders the not-found page for unknown paths, inside the layout', () => {
    renderRoutes(router.routes, '/does-not-exist')

    expect(screen.getByText('Page not found.')).toBeInTheDocument()
    expect(screen.getByRole('banner')).toBeInTheDocument()
  })

  it('renders the auth page at /auth, defaulting to sign in', () => {
    renderRoutes(router.routes, '/auth')

    expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument()
  })

  it('renders the reset password page at /reset-password', () => {
    renderRoutes(router.routes, '/reset-password?token=sample-token')

    expect(
      screen.getByRole('heading', { level: 1, name: 'Reset your password' }),
    ).toBeInTheDocument()
  })

  it('renders the activate account page at /activate-account', () => {
    // The confirmation email links here, so the path is a contract with the
    // backend (identity.email.ACTIVATION_PATH) rather than a free choice: a
    // link that 404s would leave a new account with no way to confirm at all.
    renderRoutes(router.routes, '/activate-account?token=sample-token')

    expect(
      screen.getByRole('heading', { level: 1, name: 'Confirm your email' }),
    ).toBeInTheDocument()
  })

  it('redirects /app to /auth when there is no authenticated session', async () => {
    renderRoutes(router.routes, '/app')

    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument(),
    )
  })

  it('renders the ideas page at /app/ideas when authenticated', async () => {
    mockedRefresh.mockResolvedValue({
      success: true,
      message: 'ok',
      session: { accessToken: 'token', accessTokenExpiresAt: '2099-01-01', user: USER },
    })
    mockedMe.mockResolvedValue(USER)

    renderRoutes(router.routes, '/app/ideas')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Put a problem forward.' }),
    ).toBeInTheDocument()
  })

  it('redirects /app/ideas to /auth when there is no authenticated session', async () => {
    renderRoutes(router.routes, '/app/ideas')

    // Inherits `RequireAuth` by being an `/app` child: an ideas screen
    // reachable without a session would be a screen whose every write the
    // server refuses.
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument(),
    )
  })

  it('renders the dashboard at /app when authenticated', async () => {
    mockedRefresh.mockResolvedValue({
      success: true,
      message: 'ok',
      session: { accessToken: 'token', accessTokenExpiresAt: '2099-01-01', user: USER },
    })
    mockedMe.mockResolvedValue(USER)

    renderRoutes(router.routes, '/app')

    expect(await screen.findByText(/ada@example.com/)).toBeInTheDocument()
  })
})
