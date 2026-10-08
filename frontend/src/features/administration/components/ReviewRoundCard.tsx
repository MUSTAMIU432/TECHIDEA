import { Link } from 'react-router-dom'

import { CRITERION_LABELS, DECISION_LABELS, RATING_LABELS } from '../../reviews/utils/reviewLabels'
import type { ReviewDecision } from '../../reviews/api/reviewsApi'
import type { AdminReview } from '../api/administrationApi'
import { formatDateTime } from '../utils/format'
import { Badge, Restricted } from './AdminUi'

export function DecisionBadge({ decision }: { decision: ReviewDecision | null }) {
  if (decision === null) return <Badge tone="warn">In progress</Badge>
  const tone =
    decision === 'APPROVED'
      ? 'good'
      : decision === 'REJECTED'
        ? 'bad'
        : decision === 'CHANGES_REQUESTED'
          ? 'warn'
          : 'neutral'
  return <Badge tone={tone}>{DECISION_LABELS[decision]}</Badge>
}

/**
 * One review round, read-only: who reviewed, when, what they decided and -
 * when the server returned it - what they told the author and how each
 * criterion was rated. There is no edit control anywhere in the console: a
 * completed review is history.
 */
export function ReviewRoundCard({
  review,
  isStalled,
}: {
  review: AdminReview
  isStalled?: boolean
}) {
  return (
    <article className="rounded-lg border border-slate-200 px-4 py-3">
      <header className="flex flex-wrap items-center gap-2 text-sm">
        <Link
          to={`/app/admin/reviews/${review.id}`}
          className="font-semibold text-slate-900 hover:underline"
        >
          Round {review.round}
        </Link>
        <DecisionBadge decision={review.decision} />
        {isStalled && <Badge tone="bad">Stalled</Badge>}
        <span className="text-xs text-slate-500">
          {review.reviewer.name} · started {formatDateTime(review.createdAt)}
          {review.completedAt && ` · completed ${formatDateTime(review.completedAt)}`}
        </span>
      </header>
      {review.contentRestricted ? (
        <p className="mt-2">
          <Restricted>Feedback and criteria need the content-inspection permission</Restricted>
        </p>
      ) : (
        <>
          {review.feedback && (
            <p className="mt-2 text-sm whitespace-pre-line text-slate-800">{review.feedback}</p>
          )}
          {review.assessments && review.assessments.length > 0 && (
            <ul className="mt-2 grid gap-1 sm:grid-cols-2">
              {review.assessments.map((assessment) => (
                <li key={assessment.criterion} className="text-xs text-slate-600">
                  <span className="font-semibold">{CRITERION_LABELS[assessment.criterion]}:</span>{' '}
                  {RATING_LABELS[assessment.rating]}
                  {assessment.note && ` — ${assessment.note}`}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </article>
  )
}
