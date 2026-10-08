import { Link } from 'react-router-dom'

import { useAuth } from '../features/identity/auth/AuthContext'
import { useOrganization } from '../features/organizations/context/useOrganization'
import { useCanReview } from '../features/reviews/hooks/useCanReview'

const cardClass =
  'block rounded-xl border border-gray-300 border-l-4 border-l-brand-600 bg-white p-5 shadow-sm transition-all hover:border-brand-400 hover:border-l-brand-600 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300'

/** The landing page of every signed-in user: the way into Ideas and, for reviewers, the queue. */
export function DashboardPage() {
  const { user } = useAuth()
  const { activeOrganization } = useOrganization()
  const canReview = useCanReview(activeOrganization?.id ?? null)

  const where = activeOrganization?.name ?? 'your workspace'

  return (
    <>
      <div className="max-w-2xl">
        <p className="text-sm font-semibold text-brand-700">
          Welcome back{user?.firstName ? `, ${user.firstName}` : ''}
        </p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">Home</h1>
        <p className="mt-3 text-base leading-7 text-gray-600">
          Find your ideas in Ideas, and the ideas waiting for your review in the review queue. Open
          Organizations or Teams to see what is happening inside them.
        </p>
      </div>

      <section aria-labelledby="shortcuts-heading" className="mt-8">
        <h2 id="shortcuts-heading" className="sr-only">
          Work in {where}
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <Link to="/app/ideas" className={cardClass}>
            <span className="block text-base font-bold text-gray-900">Ideas →</span>
            <span className="mt-1 block text-sm leading-6 text-gray-600">
              Put a problem forward, browse, vote and discuss ideas in {where}.
            </span>
          </Link>
          <Link to="/app/reviews" className={cardClass}>
            <span className="block text-base font-bold text-gray-900">Review queue →</span>
            <span className="mt-1 block text-sm leading-6 text-gray-600">
              {canReview === false
                ? `Ideas sent in for review in ${where}. You are not a reviewer there yet.`
                : 'Review submitted ideas, request changes and record decisions.'}
            </span>
          </Link>
        </div>
      </section>
    </>
  )
}
