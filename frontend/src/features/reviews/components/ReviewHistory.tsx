import { useIdeaReviews } from '../hooks/useIdeaReviews'
import { CRITERION_LABELS, DECISION_LABELS, RATING_LABELS, formatDate } from '../utils/reviewLabels'

/**
 * An idea's review rounds, as the server chose to show them to this viewer.
 *
 * Used by reviewers in the review workspace and by authors on their own idea.
 * It renders whatever `ideaReviews` returned and nothing else: an author
 * receives completed rounds, a reviewer every round, anybody else nothing -
 * so there is no client-side rule here about who may see what, and an empty
 * list is shown as "no reviews" rather than as a refusal.
 *
 * `reviewerId` is shown as "you" when it is the viewer, and otherwise not
 * named: the API carries an id, not a person, on purpose.
 */
export function ReviewHistory({ ideaId, viewerId }: { ideaId: string; viewerId: string | null }) {
  const { reviews, loading, error } = useIdeaReviews(ideaId)

  if (loading) {
    return <p className="text-sm text-gray-500">Loading review history…</p>
  }
  if (error) {
    return (
      <p role="alert" className="text-sm text-red-700">
        {error}
      </p>
    )
  }
  if (reviews.length === 0) {
    return <p className="text-sm text-gray-500">No reviews yet.</p>
  }

  return (
    <ol aria-label="Review history" className="space-y-3">
      {reviews.map((review) => (
        <li key={review.id} className="rounded-lg border border-gray-200 bg-white p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm font-semibold text-gray-900">
              Review {review.round}
              {review.decision ? (
                <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-700">
                  {DECISION_LABELS[review.decision]}
                </span>
              ) : (
                <span className="ml-2 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800">
                  In progress
                </span>
              )}
            </p>
            <p className="text-xs text-gray-500">
              {review.completedAt
                ? `Completed ${formatDate(review.completedAt)}`
                : `Started ${formatDate(review.createdAt)}`}
              {viewerId !== null && review.reviewerId === viewerId ? ' · by you' : ''}
            </p>
          </div>
          {review.feedback && (
            <p className="mt-2 whitespace-pre-line text-sm leading-6 text-gray-700">
              {review.feedback}
            </p>
          )}
          {review.assessments.length > 0 && (
            <dl className="mt-2 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
              {review.assessments.map((assessment) => (
                <div key={assessment.criterion} className="flex justify-between gap-2">
                  <dt className="text-gray-500">{CRITERION_LABELS[assessment.criterion]}</dt>
                  <dd className="font-medium text-gray-800">{RATING_LABELS[assessment.rating]}</dd>
                </div>
              ))}
            </dl>
          )}
        </li>
      ))}
    </ol>
  )
}
