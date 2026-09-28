import { useEffect, useState } from 'react'

import { ideaReviewsRequest, type Review } from '../api/reviewsApi'

/**
 * One idea's review history, fetched only while `ideaId` is set - a closed
 * history panel passes `null` and costs nothing.
 *
 * The server decides what is in it (every round for a reviewer, completed
 * rounds for the author, nothing for anybody else), so an empty list is an
 * ordinary answer, not an error. A superseded request is discarded, as in
 * `useReviewQueue`.
 */
export interface IdeaReviews {
  reviews: Review[]
  loading: boolean
  error: string | null
}

const FAILED = 'We could not load the review history. Please try again.'

export function useIdeaReviews(ideaId: string | null): IdeaReviews {
  const [answer, setAnswer] = useState<{ ideaId: string; reviews: Review[] } | null>(null)
  const [failedFor, setFailedFor] = useState<string | null>(null)

  useEffect(() => {
    if (ideaId === null) return

    let cancelled = false
    ideaReviewsRequest(ideaId)
      .then((reviews) => {
        if (cancelled) return
        setAnswer({ ideaId, reviews })
        setFailedFor(null)
      })
      .catch(() => {
        if (cancelled) return
        setFailedFor(ideaId)
      })

    return () => {
      cancelled = true
    }
  }, [ideaId])

  const current = ideaId !== null && answer?.ideaId === ideaId
  const failed = ideaId !== null && failedFor === ideaId

  return {
    reviews: current ? answer.reviews : [],
    loading: ideaId !== null && !current && !failed,
    error: failed ? FAILED : null,
  }
}
