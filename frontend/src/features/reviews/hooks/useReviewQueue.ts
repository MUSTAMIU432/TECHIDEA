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
 *
 * **One answer is never shown for another organization.** Keeping the previous
 * answer on screen while a new page loads is the right behaviour *within* an
 * organization: the reader keeps their place and does not watch an empty panel
 * flash between two keystrokes. Across an organization it is a different thing
 * entirely - it is one tenant's queue drawn under another tenant's name - so an
 * answer is only returned while the organization it was fetched for is still
 * the selected one. The organization is carried on the answer rather than parsed
 * back out of `key`, so the check cannot drift away from what was requested.
 *
 * The result is that the client learns nothing about why a queue is empty. The
 * server excludes a reviewer's own ideas from their own queue and returns the
 * same empty page either way, on purpose; this hook preserves that by never
 * asking a second question.
 */
export interface ReviewQueue {
  ideas: Idea[]
  pageInfo: IdeaPageInfo | null
  loading: boolean
  error: string | null
}

interface Answer {
  key: string
  /** The organization this page was fetched for. See the note above. */
  organizationId: string
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
        setAnswer({ key, organizationId, ideas: page.items, pageInfo: page.pageInfo })
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
  // Null for a different organization, so the workspace reads this as "no answer
  // yet" and shows its loading state rather than an empty queue that belongs to
  // somebody else.
  const mine = answer !== null && answer.organizationId === organizationId

  return {
    ideas: mine ? answer.ideas : NO_IDEAS,
    pageInfo: mine ? answer.pageInfo : null,
    loading: organizationId !== null && !current && !failed,
    error: failed ? FAILED : null,
  }
}
