import { Link, useParams } from 'react-router-dom'

import { adminReviewRequest } from '../api/administrationApi'
import {
  AdminCard,
  AdminPageHeader,
  DetailList,
  EmptyState,
  ErrorState,
  IdeaStatusBadge,
  LoadingState,
  Restricted,
} from '../components/AdminUi'
import { BackLink } from '../components/BackLink'
import { DecisionBadge, ReviewRoundCard } from '../components/ReviewRoundCard'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useBackTarget } from '../hooks/useBackTarget'
import { formatDateTime, ideaHomeLabel, RESTRICTED_TITLE } from '../utils/format'

/** Pull a string out of the snapshot the server stored when the round started. */
function snapshotText(snapshot: Record<string, unknown>, key: string): string {
  const value = snapshot[key]
  return typeof value === 'string' ? value : ''
}

/**
 * One review round, with every round of the same idea. A round still in
 * progress whose reviewer can no longer review is flagged as stalled: another
 * reviewer in the organization can take it over through the normal review
 * flow - the console does not reassign it.
 */
export function AdminReviewDetailPage() {
  const { reviewId = '' } = useParams()
  const {
    data: review,
    loading,
    error,
    reload,
  } = useAdminQuery(
    `review:${reviewId}`,
    () => adminReviewRequest(reviewId),
    'We could not load this review.',
  )

  const backTo = useBackTarget('/app/admin/reviews')
  if (error) return <ErrorState message={error} onRetry={reload} />
  if (loading && !review) return <LoadingState label="Loading review…" />
  if (!review) return <EmptyState title="Review not found." />

  return (
    <>
      <AdminPageHeader
        title={`Review round ${review.round}`}
        description={review.idea.title ?? RESTRICTED_TITLE}
        back={<BackLink to={backTo}>All reviews</BackLink>}
      />
      <div className="space-y-6">
        {review.isStalled && (
          <output className="block rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
            This review is stalled: its reviewer can no longer review this idea. Another reviewer
            {review.idea.organization ? ` in ${review.idea.organization.name}` : ''} can take it
            over from their review queue.
          </output>
        )}
        <AdminCard title="Round">
          <DetailList
            items={[
              [
                'Idea',
                <Link
                  key="idea"
                  to={`/app/admin/ideas/${review.idea.id}`}
                  className="font-semibold hover:underline"
                >
                  {review.idea.title ?? RESTRICTED_TITLE}
                </Link>,
              ],
              ['Idea status now', <IdeaStatusBadge key="status" status={review.idea.status} />],
              ['Belongs to', ideaHomeLabel(review.idea)],
              [
                'Reviewer',
                <Link
                  key="reviewer"
                  to={`/app/admin/users/${review.reviewer.id}`}
                  className="hover:underline"
                >
                  {review.reviewer.name} ({review.reviewer.email})
                </Link>,
              ],
              ['Decision', <DecisionBadge key="decision" decision={review.decision} />],
              ['Started', formatDateTime(review.createdAt)],
              ['Completed', formatDateTime(review.completedAt)],
            ]}
          />
        </AdminCard>

        <AdminCard title="Feedback and criteria">
          <ReviewRoundCard review={review} isStalled={review.isStalled} />
        </AdminCard>

        <AdminCard title="What the reviewer saw">
          {review.submissionSnapshot ? (
            <div className="space-y-1 text-sm">
              <p className="font-semibold text-slate-900">
                {snapshotText(review.submissionSnapshot, 'title')}
              </p>
              <p className="whitespace-pre-line text-slate-700">
                {snapshotText(review.submissionSnapshot, 'description')}
              </p>
            </div>
          ) : (
            <Restricted>The submission snapshot needs the content-inspection permission</Restricted>
          )}
        </AdminCard>

        <AdminCard title="Every round of this idea">
          <div className="space-y-3">
            {review.history.map((round) => (
              <ReviewRoundCard key={round.id} review={round} />
            ))}
          </div>
        </AdminCard>
      </div>
    </>
  )
}
