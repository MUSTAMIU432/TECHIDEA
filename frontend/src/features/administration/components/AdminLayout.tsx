import { Link, NavLink, Outlet } from 'react-router-dom'

import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { ErrorState, LoadingState } from './AdminUi'

const SECTIONS: Array<{ to: string; label: string; end?: boolean }> = [
  { to: '/app/admin', label: 'Dashboard', end: true },
  { to: '/app/admin/users', label: 'Users' },
  { to: '/app/admin/organizations', label: 'Organizations' },
  { to: '/app/admin/ideas', label: 'Ideas' },
  { to: '/app/admin/reviews', label: 'Reviews' },
  { to: '/app/admin/approvals', label: 'Approvals' },
  { to: '/app/admin/categories', label: 'Categories' },
]

function sectionClass({ isActive }: { isActive: boolean }): string {
  return `block rounded-lg px-3 py-2 text-sm font-semibold whitespace-nowrap transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-white/60 ${
    isActive ? 'bg-white text-slate-900' : 'text-slate-300 hover:bg-white/10 hover:text-white'
  }`
}

/**
 * The administration console's shell: a distinct, dark "Administration"
 * navigation beside the page, inside the ordinary authenticated app shell.
 *
 * **The gate here is presentation.** A user without console access sees a
 * "not authorized" panel instead of the console, and the console's pages are
 * never mounted, so they never ask for data. But the actual boundary is the
 * server: every `admin*` query and mutation is authorized there, so typing
 * `/app/admin` or calling the API directly gets a non-administrator nothing.
 * An unauthenticated visitor never reaches this component - `RequireAuth`
 * sends them to sign in first.
 */
export function AdminLayout() {
  const { status, capabilities, reload } = useAdminCapabilities()

  if (status === 'loading') {
    return <LoadingState label="Checking administrator access…" />
  }
  if (status === 'error') {
    return (
      <ErrorState
        message="We could not check your administrator access. Please try again."
        onRetry={reload}
      />
    )
  }
  if (!capabilities.canAccessConsole) {
    return (
      <section
        aria-labelledby="admin-forbidden-title"
        className="mx-auto max-w-lg rounded-xl border border-slate-200 bg-white px-6 py-10 text-center shadow-sm"
      >
        <p className="text-xs font-bold tracking-widest text-slate-500 uppercase">403</p>
        <h1 id="admin-forbidden-title" className="mt-2 text-xl font-bold text-slate-900">
          Not authorized
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          The administration console is for platform administrators. Being an owner or reviewer of
          an organization does not grant access to it.
        </p>
        <Link
          to="/app"
          className="mt-6 inline-flex rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-800"
        >
          Back to your workspace
        </Link>
      </section>
    )
  }

  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      <aside className="shrink-0 rounded-xl bg-slate-900 p-3 shadow-lg lg:sticky lg:top-[calc(var(--app-chrome-height)+1rem)] lg:w-56 lg:self-start">
        <p className="px-3 pt-1 pb-3 text-xs font-bold tracking-widest text-slate-400 uppercase">
          Administration
        </p>
        <nav aria-label="Administration" className="flex gap-1 overflow-x-auto lg:flex-col">
          {SECTIONS.map((section) => (
            <NavLink key={section.to} to={section.to} end={section.end} className={sectionClass}>
              {section.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="min-w-0 flex-1">
        <Outlet />
      </div>
    </div>
  )
}
