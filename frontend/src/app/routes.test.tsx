import { screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setAccessToken } from '../graphql/tokenStore'
import { meRequest, refreshTokenRequest } from '../features/identity/auth/authApi'
import { ideasRequest } from '../features/ideas/api/ideasApi'
import { organizationsRequest } from '../features/organizations/api/organizationApi'
import {
  adminInvitationsRequest,
  adminMessageThreadRequest,
  adminMessageThreadsRequest,
  adminNotificationsRequest,
  adminOverviewRequest,
  adminTeamInvitationsRequest,
  adminTeamRequest,
  adminTeamsRequest,
  adminThreadMessagesRequest,
} from '../features/administration/api/administrationApi'
import { EMPTY_PAGE_INFO, pageOfItems } from '../test/renderAdmin'
import {
  adminCapabilitiesRequest,
  NO_ADMIN_CAPABILITIES,
} from '../features/administration/api/capabilitiesApi'
import { page } from '../test/ideaPage'
import { renderRoutes } from '../test/renderWithRouter'
import { router } from './routes'

/**
 * How long an assertion on a `lazy` route may wait.
 *
 * The administration console is loaded on demand (`routes.tsx`), so an
 * assertion about what it renders is really an assertion about a dynamic
 * `import()` landing *and* a capability query resolving. On a loaded CI runner
 * that pair can take longer than Testing Library's 1s default, which produced a
 * failure in roughly one full-suite run out of three and none in isolation -
 * a timing dependency in the test, not a defect in the route.
 *
 * Five seconds is long enough to be reliable and short enough that a genuinely
 * broken route still fails rather than hanging.
 */
const LAZY_ROUTE_TIMEOUT = 5000

vi.mock('../features/organizations/api/organizationApi', () => ({
  organizationsRequest: vi.fn(),
  createOrganizationRequest: vi.fn(),
}))

// The Ideas feature's own API module. Mocked wholesale because
// `/app/ideas` mounts IdeaList, which fetches on mount; the module's own
// documents are asserted in `features/ideas/api/ideasApi.test.ts`.
vi.mock('../features/ideas/api/ideasApi', () => ({
  ideasRequest: vi.fn(async () => ({
    items: [],
    pageInfo: {
      offset: 0,
      limit: 5,
      totalCount: 0,
      hasNextPage: false,
      hasPreviousPage: false,
    },
  })),
  categoriesRequest: vi.fn(async () => []),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  submitIdeaRequest: vi.fn(),
  ideaRequest: vi.fn(async () => null),
}))

// The Reviews feature's API (S3-003), mocked for the same reason: both
// `/app/ideas` (the queue link) and `/app/reviews` ask it on mount.
vi.mock('../features/reviews/api/reviewsApi', () => ({
  viewerCanReviewInRequest: vi.fn(async () => false),
  reviewQueueRequest: vi.fn(),
  ideaReviewsRequest: vi.fn(async () => []),
}))

// The administration console's API. `AppLayout` asks for the viewer's
// console capabilities on mount (to decide whether to offer the "Admin"
// link), so every `/app` route reaches it. Denied by default, like the server.
vi.mock('../features/administration/api/capabilitiesApi', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  adminCapabilitiesRequest: vi.fn(),
}))
vi.mock('../features/administration/api/administrationApi', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  adminOverviewRequest: vi.fn(),
  adminTeamsRequest: vi.fn(),
  adminTeamRequest: vi.fn(),
  adminTeamInvitationsRequest: vi.fn(),
  adminInvitationsRequest: vi.fn(),
  adminMessageThreadsRequest: vi.fn(),
  adminMessageThreadRequest: vi.fn(),
  adminThreadMessagesRequest: vi.fn(),
  adminNotificationsRequest: vi.fn(),
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
const mockedIdeas = vi.mocked(ideasRequest)
const mockedCapabilities = vi.mocked(adminCapabilitiesRequest)
const mockedOverview = vi.mocked(adminOverviewRequest)
const mockedTeams = vi.mocked(adminTeamsRequest)
const mockedTeam = vi.mocked(adminTeamRequest)
const mockedTeamInvitations = vi.mocked(adminTeamInvitationsRequest)
const mockedInvitations = vi.mocked(adminInvitationsRequest)
const mockedThreads = vi.mocked(adminMessageThreadsRequest)
const mockedThread = vi.mocked(adminMessageThreadRequest)
const mockedThreadMessages = vi.mocked(adminThreadMessagesRequest)
const mockedNotifications = vi.mocked(adminNotificationsRequest)

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
    mockedRefresh.mockResolvedValue({
      success: false,
      message: 'no session',
      session: null,
    })
    mockedMe.mockResolvedValue(null)
    mockedOrganizations.mockResolvedValue([])
    mockedIdeas.mockResolvedValue(page([]))
    mockedCapabilities.mockResolvedValue(NO_ADMIN_CAPABILITIES)
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
      session: {
        accessToken: 'token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    mockedMe.mockResolvedValue(USER)

    renderRoutes(router.routes, '/app/ideas')

    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Put a problem forward.',
      }),
    ).toBeInTheDocument()
  })

  it('renders the new-idea page at /app/ideas/new when authenticated', async () => {
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

    renderRoutes(router.routes, '/app/ideas/new')

    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Tell us about a problem.',
      }),
    ).toBeInTheDocument()
  })

  it('renders the edit page at /app/ideas/:ideaId/edit when authenticated', async () => {
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

    renderRoutes(router.routes, '/app/ideas/1/edit')

    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Edit your draft.',
      }),
    ).toBeInTheDocument()
    expect(await screen.findByText('This idea is not available.')).toBeInTheDocument()
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

  it('renders the reviews page at /app/reviews when authenticated', async () => {
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

    renderRoutes(router.routes, '/app/reviews')

    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: 'Review submitted ideas.',
      }),
    ).toBeInTheDocument()
  })

  it('redirects /app/reviews to /auth when there is no authenticated session', async () => {
    renderRoutes(router.routes, '/app/reviews')

    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument(),
    )
  })

  it('renders the dashboard at /app when authenticated', async () => {
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

    renderRoutes(router.routes, '/app')

    expect(await screen.findByRole('link', { name: 'MUSTANET home' })).toBeInTheDocument()
  })

  describe('the administration console', () => {
    function signIn() {
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

    const ADMIN = {
      ...NO_ADMIN_CAPABILITIES,
      canAccessConsole: true,
    }

    it('offers the Admin link to a platform administrator', async () => {
      signIn()
      mockedCapabilities.mockResolvedValue(ADMIN)

      renderRoutes(router.routes, '/app')

      const main = await screen.findByRole('navigation', { name: 'Main' })
      expect(await within(main).findByRole('link', { name: 'Admin' })).toHaveAttribute(
        'href',
        '/app/admin',
      )
    })

    it('does not offer the Admin link to anybody else', async () => {
      signIn()

      renderRoutes(router.routes, '/app')

      const main = await screen.findByRole('navigation', { name: 'Main' })
      await waitFor(() => expect(mockedCapabilities).toHaveBeenCalled())
      expect(within(main).queryByRole('link', { name: 'Admin' })).not.toBeInTheDocument()
    })

    it('shows a normal user who types /app/admin a forbidden state, and loads nothing', async () => {
      signIn()

      renderRoutes(router.routes, '/app/admin/users')

      expect(
        await screen.findByRole(
          'heading',
          { name: 'Not authorized' },
          { timeout: LAZY_ROUTE_TIMEOUT },
        ),
      ).toBeInTheDocument()
      expect(mockedOverview).not.toHaveBeenCalled()
    })

    it('sends an unauthenticated visitor to sign in', async () => {
      renderRoutes(router.routes, '/app/admin')

      await waitFor(() =>
        expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument(),
      )
      expect(mockedCapabilities).not.toHaveBeenCalled()
    })

    it('renders the console dashboard for an administrator', async () => {
      signIn()
      mockedCapabilities.mockResolvedValue(ADMIN)
      mockedOverview.mockReturnValue(new Promise(() => {}))

      renderRoutes(router.routes, '/app/admin')

      expect(
        await screen.findByRole('heading', { name: 'Overview' }, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument()
      expect(screen.getByRole('navigation', { name: 'Administration' })).toBeInTheDocument()
    })

    /*
      The console's four collaboration sections are lazy routes like every other
      one, so "the link is in the nav" proves nothing on its own - a section that
      is linked but not routed is a 404 behind a working-looking nav. Each is
      asserted here by its own heading, which is what somebody typing
      `/app/admin/teams` actually gets.
    */
    it.each([
      ['/app/admin/teams', 'Teams'],
      ['/app/admin/invitations', 'Invitations'],
      ['/app/admin/messages', 'Messages'],
      ['/app/admin/notifications', 'Notifications'],
    ])('routes %s to its own section', async (path, heading) => {
      signIn()
      mockedCapabilities.mockResolvedValue(ADMIN)
      mockedTeams.mockReturnValue(new Promise(() => {}))
      mockedInvitations.mockReturnValue(new Promise(() => {}))
      mockedThreads.mockReturnValue(new Promise(() => {}))
      mockedNotifications.mockReturnValue(new Promise(() => {}))

      renderRoutes(router.routes, path)

      expect(
        await screen.findByRole('heading', { name: heading }, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument()
    })

    it('routes a team to its detail page', async () => {
      signIn()
      mockedCapabilities.mockResolvedValue(ADMIN)
      mockedTeam.mockResolvedValue({
        id: '9',
        name: 'Registrar',
        slug: 'registrar',
        description: '',
        owner: { id: '7', email: 'ada@example.com', name: 'Ada Author' },
        memberCount: 1,
        inactiveMemberCount: 0,
        ideaCount: 0,
        invitationCount: 0,
        createdAt: '2026-01-01T00:00:00Z',
        roles: [],
        members: [],
        ideasByStatus: [],
      })
      mockedTeamInvitations.mockResolvedValue(pageOfItems([]))

      renderRoutes(router.routes, '/app/admin/teams/9')

      expect(
        await screen.findByRole('heading', { name: 'Registrar' }, { timeout: LAZY_ROUTE_TIMEOUT }),
      ).toBeInTheDocument()
    })

    it('routes a conversation to its detail page, with its words withheld', async () => {
      signIn()
      mockedCapabilities.mockResolvedValue(ADMIN)
      mockedThread.mockResolvedValue({
        id: '40',
        subject: null,
        ideaId: null,
        ideaTitle: null,
        startedBy: { id: '7', email: 'ada@example.com', name: 'Ada Author' },
        participantCount: 1,
        messageCount: 1,
        staleParticipantCount: 0,
        latestMessageAt: '2026-02-01T00:00:00Z',
        createdAt: '2026-02-01T00:00:00Z',
        contentRestricted: true,
        participants: [{ id: '7', email: 'ada@example.com', name: 'Ada Author' }],
      })
      mockedThreadMessages.mockResolvedValue({
        items: [],
        pageInfo: EMPTY_PAGE_INFO,
        contentRestricted: true,
      })

      renderRoutes(router.routes, '/app/admin/messages/40')

      expect(
        await screen.findByText(/cannot read message bodies/u, undefined, {
          timeout: LAZY_ROUTE_TIMEOUT,
        }),
      ).toBeInTheDocument()
    })
  })
})
