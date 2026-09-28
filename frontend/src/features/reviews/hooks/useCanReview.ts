import { useEffect, useState } from 'react'

import { viewerCanReviewInRequest } from '../api/reviewsApi'

/**
 * Whether the signed-in user reviews in `organizationId`: `null` while the
 * answer is unknown (no organization, still asking, or the request failed).
 *
 * Decides what to *offer* - the queue link, the "not a reviewer" message - and
 * nothing else. The queue is authorized by the server on every request, so a
 * wrong answer here can only hide or show a link, never grant access.
 */
export function useCanReview(organizationId: string | null): boolean | null {
  const [answer, setAnswer] = useState<{ organizationId: string; canReview: boolean } | null>(null)

  useEffect(() => {
    if (organizationId === null) return

    let cancelled = false
    viewerCanReviewInRequest(organizationId)
      .then((canReview) => {
        if (!cancelled) setAnswer({ organizationId, canReview })
      })
      .catch(() => {
        // Unknown rather than "no": a failed check must not tell a reviewer
        // they are not one.
        if (!cancelled) setAnswer(null)
      })

    return () => {
      cancelled = true
    }
  }, [organizationId])

  return answer !== null && answer.organizationId === organizationId ? answer.canReview : null
}
