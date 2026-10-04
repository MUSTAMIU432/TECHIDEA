import type { Idea } from '../api/ideasApi'

/**
 * Whether this viewer is shown an idea's review history at all.
 *
 * Two viewers, and nothing else: the **author**, once the idea has been put
 * forward, who reads the feedback on completed rounds; and a **reviewer**, as
 * the server reported through `viewerCanStartReview` or
 * `viewerActiveReviewId`.
 *
 * This is presentation, not a control, on the same terms as
 * `utils/lifecycle`: `ideaReviews` decides again what a viewer may read, and
 * the review queue authorizes every request. It lives in one place because the
 * card's action bar and the panel below it both have to agree - a cell that
 * offers a history which then renders nothing is a broken promise, and two
 * copies of this rule is one too many.
 */
export function reviewHistoryVisible(idea: Idea, viewerId: string | null): boolean {
  const isAuthor = viewerId !== null && viewerId === idea.authorId
  const putForward = idea.status !== 'DRAFT'
  const isReviewer = idea.viewerCanStartReview || idea.viewerActiveReviewId !== null

  return (isAuthor && putForward) || isReviewer
}
