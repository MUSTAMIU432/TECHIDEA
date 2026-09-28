import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../features/identity/auth/AuthContext'
import { OrganizationSwitcher } from '../features/organizations/components/OrganizationSwitcher'
import { ReviewsWorkspace } from '../features/reviews/components/ReviewsWorkspace'

/**
 * The review area at `/app/reviews` (S3-003).
 *
 * An `/app` child like `/app/ideas`, so it inherits `RequireAuth` and takes
 * its organization from the shared `OrganizationProvider` and header switcher.
 */
export function ReviewsPage() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  function handleLogout() {
    void logout()
    navigate('/auth', { replace: true })
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">
              Automation Platform
            </p>
            <p className="mt-0.5 text-sm text-gray-500">
              Reviews ·{' '}
              <Link to="/app/ideas" className="font-medium text-brand-700 hover:underline">
                Ideas
              </Link>
            </p>
          </div>
          <div className="flex items-center gap-4">
            <OrganizationSwitcher />
            <span className="hidden text-sm text-gray-500 md:inline">{user?.email}</span>
            <button
              type="button"
              onClick={handleLogout}
              className="rounded-lg border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-10">
        <div className="max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-tight text-gray-900 sm:text-4xl">
            Review submitted ideas.
          </h1>
          <p className="mt-3 text-base leading-7 text-gray-600">
            Ideas your organization has put forward, oldest first. Open one to read it, its evidence
            and its review history.
          </p>
        </div>

        <ReviewsWorkspace />
      </main>
    </div>
  )
}
