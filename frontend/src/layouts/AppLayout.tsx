import {
  Fragment,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { Logo } from '../components/Logo'
import { AdminCapabilitiesProvider } from '../features/administration/context/AdminCapabilitiesProvider'
import { useAdminCapabilities } from '../features/administration/context/useAdminCapabilities'
import { useAuth } from '../features/identity/auth/AuthContext'
import { UserMenu } from '../features/identity/components/UserMenu'
import { OrganizationProvider } from '../features/organizations/context/OrganizationProvider'
import { useOrganization } from '../features/organizations/context/useOrganization'
import { useUnreadMessageThreads } from '../features/messaging/hooks/useMessages'
import { useUnreadNotifications } from '../features/notifications/hooks/useUnreadNotifications'
import { useCanReview } from '../features/reviews/hooks/useCanReview'

/**
 * Shell for the authenticated `/app` area: one header, one navigation bar and
 * one `OrganizationProvider` shared by every `/app` child.
 *
 * The provider lives here rather than around each page so the organization
 * picked in the switcher survives moving between Workspace, Ideas and
 * Reviews - a provider per route would remount on navigation and fall back to
 * the first membership every time. `AdminCapabilitiesProvider` sits here for
 * the same reason: the "Admin" link and the console's own gate share one
 * answer to "is this a platform administrator?", asked once per user.
 */
export function AppLayout() {
  return (
    <OrganizationProvider>
      <AdminCapabilitiesProvider>
        <AppShell />
      </AdminCapabilitiesProvider>
    </OrganizationProvider>
  )
}

function navLinkClass({ isActive }: { isActive: boolean }): string {
  return `rounded-lg px-3 py-2 text-sm font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70 ${
    isActive
      ? 'bg-white text-brand-800 shadow-sm'
      : 'text-brand-100 hover:bg-white/10 hover:text-white'
  }`
}

interface Crumb {
  label: string
  /** Where the crumb links to; the last crumb is the current page and has none. */
  to?: string
}

/** The trail after "Workspace" for each `/app` sub-path. */
/*
  The trail after "Workspace" for each `/app` sub-path.

  Patterns rather than exact paths, because half these pages carry an id — a
  team, a conversation, an idea's report — and an exact lookup would give them
  no breadcrumb at all. Ordered longest-first so `/app/ideas/:id/report` matches
  its own entry rather than the bare `/app/ideas/:id` one.
*/
const SECTION_TRAILS: Array<[RegExp, Crumb[]]> = [
  [/^\/app\/ideas\/new$/, [{ label: 'Ideas', to: '/app/ideas' }, { label: 'New idea' }]],
  [/^\/app\/ideas\/[^/]+\/edit$/, [{ label: 'Ideas', to: '/app/ideas' }, { label: 'Edit idea' }]],
  [
    /^\/app\/ideas\/[^/]+\/report$/,
    [{ label: 'Ideas', to: '/app/ideas' }, { label: 'Review report' }],
  ],
  [
    /^\/app\/ideas\/[^/]+\/proposal$/,
    [{ label: 'Ideas', to: '/app/ideas' }, { label: 'Proposal' }],
  ],
  [/^\/app\/ideas\/[^/]+$/, [{ label: 'Ideas', to: '/app/ideas' }, { label: 'Idea' }]],
  [/^\/app\/ideas$/, [{ label: 'Ideas' }]],
  [/^\/app\/reviews$/, [{ label: 'Reviews' }]],
  [
    /^\/app\/reviews\/proposals\/[^/]+$/,
    [{ label: 'Reviews', to: '/app/reviews' }, { label: 'Proposal' }],
  ],
  [
    /^\/app\/automation\/opportunities\/[^/]+$/,
    [{ label: 'Automation', to: '/app/automation' }, { label: 'Opportunity' }],
  ],
  [
    /^\/app\/automation\/projects\/[^/]+$/,
    [{ label: 'Automation', to: '/app/automation' }, { label: 'Project' }],
  ],
  [/^\/app\/automation(\/.*)?$/, [{ label: 'Automation' }]],
  [
    /^\/app\/organizations\/[^/]+$/,
    [{ label: 'Organizations', to: '/app/organizations' }, { label: 'Organization' }],
  ],
  [/^\/app\/organizations$/, [{ label: 'Organizations' }]],
  [/^\/app\/teams\/[^/]+$/, [{ label: 'Teams', to: '/app/teams' }, { label: 'Team' }]],
  [/^\/app\/teams$/, [{ label: 'Teams' }]],
  [
    /^\/app\/messages\/[^/]+$/,
    [{ label: 'Messages', to: '/app/messages' }, { label: 'Conversation' }],
  ],
  [/^\/app\/messages$/, [{ label: 'Messages' }]],
  [/^\/app\/notifications$/, [{ label: 'Notifications' }]],
]

const crumbLinkClass =
  'rounded font-semibold text-brand-700 hover:text-brand-800 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300'

/**
 * "← Workspace / <page>" above every `/app` sub-page, so the way back to the
 * organization workspace sits right above the content, not only in the header.
 */
function WorkspaceBreadcrumb() {
  const { pathname } = useLocation()
  const normalized = pathname.replace(/\/+$/, '')
  const trail = SECTION_TRAILS.find(([pattern]) => pattern.test(normalized))?.[1]
  if (!trail) return null

  return (
    <nav aria-label="Breadcrumb" className="mx-auto max-w-[96rem] pt-6 pb-2">
      <ol className="flex items-center gap-2 text-sm">
        <li>
          <Link
            to="/app"
            className="group inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-white px-3 py-1.5 font-semibold text-brand-700 shadow-sm transition-colors hover:border-brand-300 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 20 20"
              className="h-4 w-4 transition-transform group-hover:-translate-x-0.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12.5 15 7.5 10l5-5" />
            </svg>
            Home
          </Link>
        </li>
        {trail.map((crumb) => (
          <Fragment key={crumb.label}>
            <li aria-hidden="true" className="text-gray-400">
              /
            </li>
            {crumb.to ? (
              <li>
                <Link to={crumb.to} className={crumbLinkClass}>
                  {crumb.label}
                </Link>
              </li>
            ) : (
              <li aria-current="page" className="font-medium text-gray-600">
                {crumb.label}
              </li>
            )}
          </Fragment>
        ))}
      </ol>
    </nav>
  )
}

/**
 * The rendered height of an element, kept current as it changes - the header
 * wraps on narrow screens, and the breadcrumb comes and goes between pages.
 */
function useElementHeight(ref: RefObject<HTMLElement | null>): number {
  const [height, setHeight] = useState(0)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const update = () => setHeight(element.offsetHeight)
    update()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return height
}

/**
 * The bell, and the number of things waiting behind it.
 *
 * Asked for once per mount rather than on every navigation: the badge is in the
 * app chrome, so it renders on every authenticated page, and a request per page
 * would be a request the reader never asked for. A write that changes the count
 * is a navigation away, so the badge is at worst one page stale — and a badge
 * that is briefly wrong is a much smaller problem than a header that re-queries
 * the server every time somebody clicks a link.
 *
 * The count is the **server's** number rather than a count of a list this
 * component does not hold, so it is right even when the notifications page has
 * never been opened.
 */
function NotificationBell() {
  const unread = useUnreadNotifications()

  return (
    <Link
      to="/app/notifications"
      aria-label={
        unread === 0 ? 'Notifications, nothing unread' : `Notifications, ${unread} unread`
      }
      className="relative inline-flex h-9 w-9 items-center justify-center rounded-lg text-white/90 transition-colors hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="h-5 w-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* A bell, with a clapper: the outline alone reads as a lamp at this size. */}
        <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </svg>
      {unread > 0 && (
        <span
          className="absolute -top-0.5 -right-0.5 min-w-[1.25rem] rounded-full bg-amber-400 px-1 text-center text-[11px] font-bold text-amber-950 tabular-nums"
          aria-hidden="true"
        >
          {unread > 99 ? '99+' : unread}
        </span>
      )}
    </Link>
  )
}

function AppShell() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const { activeOrganization } = useOrganization()
  // Offered, not granted: the queue is authorized by the server.
  const canReview = useCanReview(activeOrganization?.id ?? null)
  // Offered, not granted, like the Reviews link: every console request is
  // authorized by the server.
  const { capabilities: adminCapabilities } = useAdminCapabilities()
  // The same "the server's number, asked once" bargain as the notifications
  // bell, for the same reason: this bar renders on every authenticated page.
  const unreadThreads = useUnreadMessageThreads()
  const chromeRef = useRef<HTMLDivElement>(null)

  const chromeHeight = useElementHeight(chromeRef)

  function handleLogout() {
    void logout()
    navigate('/auth', { replace: true })
  }

  return (
    <div
      className="min-h-screen bg-slate-50"
      // Read by pages that pin their own intro under the chrome, e.g.
      // `sticky top-[var(--app-chrome-height)]` on `/app/ideas/new`.
      style={{ '--app-chrome-height': `${chromeHeight}px` } as CSSProperties}
    >
      {/*
       * The bar and <main> share one centered width (`max-w-[96rem]`) and the same
       * gutters, so the bar's edges line up with the page content at any
       * screen size. The breadcrumb is pinned with the bar: the way back stays
       * in reach however far down the page the reader is.
       */}
      <div ref={chromeRef} className="sticky top-0 z-30 bg-slate-50 px-4 pt-3 sm:px-6 lg:px-8">
        <header className="mx-auto flex max-w-[96rem] flex-wrap items-center justify-between gap-x-4 gap-y-3 bg-gradient-to-r from-brand-800 via-brand-700 to-brand-600 px-4 py-3 shadow-lg shadow-brand-900/20 ring-1 ring-white/10 sm:px-5">
          {/* Brand alone on the left; navigation, organization and account grouped on the right. */}
          <Link
            to="/app"
            aria-label="MUSTANET home"
            className="shrink-0 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
          >
            <Logo />
          </Link>
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-4 gap-y-3 sm:gap-x-6">
            <nav aria-label="Main" className="flex min-w-0 items-center gap-1 overflow-x-auto">
              <NavLink to="/app" end className={navLinkClass}>
                Home
              </NavLink>
              <NavLink to="/app/ideas" className={navLinkClass}>
                Ideas
              </NavLink>
              {(canReview || adminCapabilities.canReviewPlatformSubmissions) && (
                <NavLink to="/app/reviews" className={navLinkClass}>
                  Reviews
                </NavLink>
              )}
              {/*
                Teams and Messages are always offered, unlike Reviews and Admin.
                Both are gated by nothing but a session: a team is a
                collaboration boundary rather than a tenant, and a private
                message belongs to its participants - so there is no
                organization permission that could decide whether to show them,
                and a reader with no organization at all is exactly the reader
                the Teams link exists for.
              */}
              <NavLink to="/app/automation" className={navLinkClass}>
                Automation
              </NavLink>
              <NavLink to="/app/organizations" className={navLinkClass}>
                Organizations
              </NavLink>
              <NavLink to="/app/teams" className={navLinkClass}>
                Teams
              </NavLink>
              <NavLink
                to="/app/messages"
                className={navLinkClass}
                aria-label={unreadThreads === 0 ? 'Messages' : `Messages, ${unreadThreads} unread`}
              >
                <span className="flex items-center gap-1.5">
                  Messages
                  {unreadThreads > 0 && (
                    <span
                      aria-hidden="true"
                      className="min-w-[1.1rem] rounded-full bg-amber-400 px-1 text-center text-[10px] font-bold text-amber-950 tabular-nums"
                    >
                      {unreadThreads > 99 ? '99+' : unreadThreads}
                    </span>
                  )}
                </span>
              </NavLink>
              {adminCapabilities.canAccessConsole && (
                <NavLink to="/app/admin" className={navLinkClass}>
                  Admin
                </NavLink>
              )}
            </nav>
            <div className="flex min-w-0 items-center gap-3 sm:border-l sm:border-white/20 sm:pl-6">
              <NotificationBell />
              <UserMenu user={user} onSignOut={handleLogout} />
            </div>
          </div>
        </header>
        <WorkspaceBreadcrumb />
      </div>

      <main className="px-4 pt-8 pb-10 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-[96rem]">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
