import { useEffect, useState } from 'react'

import type { Idea, IdeaPageInfo } from '../../ideas/api/ideasApi'
import { reviewQueueRequest } from '../api/reviewsApi'

/**
 * One page of an organization's review queue.
 *
 * The same discipline as `useIdeaDiscovery`, for the same reasons: an answer
 * is matched to the request it answers by comparing keys during render, and a
 * request that has been superseded - by a new page, a new organization or a
 * reload - has its answer, success or failure, dropped on arrival so it can
 * neither replace nor wipe a newer one.
 */
export interface ReviewQueue {
  ideas: Idea[]
  pageInfo: IdeaPageInfo | null
  loading: boolean
  error: string | null
}

interface Answer {
  key: string
  ideas: Idea[]
  pageInfo: IdeaPageInfo
}

const FAILED = 'We could not load the review queue. Please try again.'
const NO_IDEAS: Idea[] = []

export function useReviewQueue(
  organizationId: string | null,
  offset: number,
  reloadToken: number,
): ReviewQueue {
  const key = JSON.stringify([organizationId, offset, reloadToken])
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  useEffect(() => {
    if (organizationId === null) return

    let cancelled = false
    reviewQueueRequest(organizationId, { offset })
      .then((page) => {
        if (cancelled) return
        setAnswer({ key, ideas: page.items, pageInfo: page.pageInfo })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setAnswer(null)
        setErrorKey(key)
      })

    return () => {
      cancelled = true
    }
    // `reloadToken` reaches the effect through `key`: same query, ask again.
  }, [organizationId, offset, key])

  const current = answer !== null && answer.key === key
  const failed = errorKey === key

  return {
    ideas: answer?.ideas ?? NO_IDEAS,
    pageInfo: answer?.pageInfo ?? null,
    loading: organizationId !== null && !current && !failed,
    error: failed ? FAILED : null,
  }
}
