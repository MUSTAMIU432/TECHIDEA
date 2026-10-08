import { useSearchParams } from 'react-router-dom'

import { useAdminCapabilities } from '../features/administration/context/useAdminCapabilities'
import { MyWork } from '../features/reviews/components/MyWork'
import {
  PlatformReviewWorkspace,
  ReviewsWorkspace,
} from '../features/reviews/components/ReviewsWorkspace'

type Tab = 'work' | 'queue'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'work', label: 'My work' },
  { id: 'queue', label: 'Review queue' },
]

/**
 * The review area at `/app/reviews` (S3-003).
 *
 * Rendered inside `AppLayout` like `/app/ideas`, so it inherits `RequireAuth`
 * and takes its organization from the shared `OrganizationProvider` and
 * header switcher.
 *
 * A **platform reviewer** gets two tabs: *My work* - every idea their review teams are
 * handling, from the review to delivery, and what is waiting on them - and the *Review
 * queue*. A link to one idea (`?idea=`, where notifications point) opens the queue on it.
 * An **organization reviewer** has the organization's queue. Which to draw is an offer
 * read from the capabilities; each list is authorized by the server on every request.
 */
export function ReviewsPage() {
  const platformReviewer = useAdminCapabilities().capabilities.canReviewPlatformSubmissions
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const tab: Tab = requested === 'queue' || params.has('idea') ? 'queue' : 'work'

  if (!platformReviewer) {
    return (
      <>
        <div className="max-w-2xl">
          <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
            Review submitted ideas.
          </h1>
          <p className="mt-3 text-base leading-7 text-gray-600">
            Ideas your organization has put forward, oldest first. Open one to read it, its evidence
            and its review history.
          </p>
        </div>
        <ReviewsWorkspace />
      </>
    )
  }

  return (
    <>
      <div className="max-w-2xl">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">Reviews</h1>
        <p className="mt-3 text-base leading-7 text-gray-600">
          Your ideas from review to delivery, and the queue of submissions waiting for review.
        </p>
      </div>

      <div role="tablist" aria-label="Reviews" className="mt-6 flex gap-1 border-b border-gray-200">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setParams(item.id === 'work' ? {} : { tab: item.id })}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-semibold ${
              tab === item.id
                ? 'border-brand-600 text-brand-700'
                : 'border-transparent text-gray-600 hover:text-gray-900'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'work' ? (
        <MyWork />
      ) : (
        <>
          <PlatformReviewWorkspace />
          <ReviewsWorkspace quiet />
        </>
      )}
    </>
  )
}
