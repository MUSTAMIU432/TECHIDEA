import { Link } from 'react-router-dom'

import type { Idea } from '../../ideas/api/ideasApi'
import { reviewHistoryVisible } from '../../ideas/utils/reviewVisibility'
import { ReviewHistory } from './ReviewHistory'

/**
 * The review part of an idea card (S3-003), behind the review cell of
 * `IdeaCardActions`.
 *
 * Drawn only for the two viewers the server would show anything to, so a card
 * does not offer an empty panel to every reader - the same condition the cell
 * itself is drawn from, read from one helper so the cell and the panel cannot
 * disagree:
 *
 * - the **author**, once the idea has been put forward, who reads the feedback
 *   on completed rounds;
 * - a **reviewer**, as the server reported through `viewerCanStartReview` or
 *   `viewerActiveReviewId`, who is pointed at the review queue.
 *
 * Neither is a control. `ideaReviews` decides again what this viewer may read,
 * and the queue is authorized on every request.
 *
 * **The links to the review workspace are shown whether or not the history is
 * open**, because they are not part of the history: they are the nudge that
 * tells a reviewer there is a round of theirs to do, and hiding it behind a
 * disclosure the nudge itself explains would be a puzzle.
 */
export function IdeaReviewSection({
  idea,
  viewerId,
  open,
}: {
  idea: Idea
  viewerId: string | null
  open: boolean
}) {
  if (!reviewHistoryVisible(idea, viewerId)) return null

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-3">
        {idea.viewerCanStartReview && (
          <Link
            to={`/app/reviews?idea=${encodeURIComponent(idea.id)}`}
            className="text-xs font-semibold text-brand-700 hover:text-brand-800 hover:underline"
          >
            {idea.status === 'UNDER_REVIEW'
              ? 'Review stalled — take it over in the review workspace'
              : 'Waiting for review — open it in the review workspace'}
          </Link>
        )}
        {idea.viewerActiveReviewId !== null && (
          <Link
            to={`/app/reviews?idea=${encodeURIComponent(idea.id)}`}
            className="text-xs font-semibold text-amber-800 hover:underline"
          >
            You are reviewing this idea — continue review
          </Link>
        )}
      </div>
      {open && (
        <div className="mt-3">
          <ReviewHistory ideaId={idea.id} viewerId={viewerId} />
        </div>
      )}
    </div>
  )
}
