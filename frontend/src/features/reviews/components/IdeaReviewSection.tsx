import { Link } from 'react-router-dom'

import type { Idea } from '../../ideas/api/ideasApi'
import { ReviewHistory } from './ReviewHistory'

/**
 * The review part of an idea card (S3-003), collapsed like the discussion and
 * the evidence next to it.
 *
 * Drawn only for the two viewers the server would show anything to, so a card
 * does not offer an empty panel to every reader:
 *
 * - the **author**, once the idea has been put forward, who reads the feedback
 *   on completed rounds;
 * - a **reviewer**, as the server reported through `viewerCanStartReview` or
 *   `viewerActiveReviewId`, who is pointed at the review queue.
 *
 * Neither is a control. `ideaReviews` decides again what this viewer may read,
 * and the queue is authorized on every request.
 */
export function IdeaReviewSection({
  idea,
  viewerId,
  open,
  onToggle,
}: {
  idea: Idea
  viewerId: string | null
  open: boolean
  onToggle: () => void
}) {
  const isAuthor = viewerId !== null && viewerId === idea.authorId
  const putForward = idea.status !== 'DRAFT'
  const isReviewer = idea.viewerCanStartReview || idea.viewerActiveReviewId !== null

  if (!(isAuthor && putForward) && !isReviewer) return null

  return (
    <div className="mt-3 border-t border-gray-100 pt-3">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          aria-expanded={open}
          onClick={onToggle}
          className="inline-flex items-center gap-1.5 rounded-lg px-1 py-0.5 text-xs font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          {open ? 'Hide review history' : 'Review history'}
        </button>
        {idea.viewerCanStartReview && (
          <Link
            to="/app/reviews"
            className="text-xs font-semibold text-brand-700 hover:text-brand-800 hover:underline"
          >
            Waiting for review — open the review queue
          </Link>
        )}
        {idea.viewerActiveReviewId !== null && (
          <span className="text-xs font-medium text-amber-800">You are reviewing this idea.</span>
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
