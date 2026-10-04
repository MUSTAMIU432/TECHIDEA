import { ReviewsWorkspace } from '../features/reviews/components/ReviewsWorkspace'

/**
 * The review area at `/app/reviews` (S3-003).
 *
 * Rendered inside `AppLayout` like `/app/ideas`, so it inherits `RequireAuth`
 * and takes its organization from the shared `OrganizationProvider` and
 * header switcher.
 */
export function ReviewsPage() {
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
