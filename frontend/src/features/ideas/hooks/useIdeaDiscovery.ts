import { useEffect, useState } from 'react'

import { ideasRequest, type Idea, type IdeaFilters, type IdeaPageInfo } from '../api/ideasApi'

/**
 * One page of every idea the signed-in user may read, for a given set of filters.
 *
 * **Not scoped to an organization.** An idea is filed by one person, a team or an
 * organization, and somebody with no organization still has ideas of their own; the
 * server's `ideas` query returns everything this reader may see at every level, and the
 * Owner filter (`submissionContext`) narrows it to one level when wanted.
 *
 * The fetching lives here rather than in `IdeaList` because the list also has
 * to be *re-fetchable* from two directions that are not the same thing:
 *
 * - **The filters changed.** A new query. Anything on screen from the old
 *   filters is no longer an answer, so the list is told it is loading.
 * - **Something was written.** A save or a lifecycle move. Same query, new
 *   contents, expressed by `reloadToken` so this hook does not need to know
 *   what changed or how.
 *
 * Two things it deliberately does not do. It does not filter, sort or page
 * anything itself: every one of those is the server's decision, and a client
 * that re-sorted a list would show an order the backend never promised. And
 * it does not report "no ideas" for a request that failed - a transport
 * failure is its own message, because an empty list would read as a claim
 * about what exists.
 */
export interface IdeaDiscovery {
  /** The rows from the most recent answer, which may be for an older query. */
  ideas: Idea[]
  /** As above: `null` until some query has come back at all. */
  pageInfo: IdeaPageInfo | null
  /** A query is in flight, whether or not an older answer is on screen. */
  loading: boolean
  error: string | null
}

interface Answer {
  /** The query this answer is for. See the race note below. */
  key: string
  ideas: Idea[]
  pageInfo: IdeaPageInfo
}

const FAILED = 'We could not reach the server. Please try again.'

// One shared empty list rather than a fresh `[]` per render: `useIdeaVotes`
// treats a new `ideas` array as a new answer from the server, so the fallback
// must keep one identity until a real answer replaces it.
const NO_IDEAS: Idea[] = []

export function useIdeaDiscovery(filters: IdeaFilters, reloadToken: number): IdeaDiscovery {
  // Destructured rather than used as an object, because `filters` is built
  // fresh on every render and depending on its identity would re-fetch on
  // every render, forever. These are the values the query actually is.
  const search = filters.search ?? null
  const categoryId = filters.categoryId ?? null
  const status = filters.status ?? null
  const submissionContext = filters.submissionContext ?? null
  const visibility = filters.visibility ?? null
  const mine = filters.mine ?? null
  const offset = filters.offset ?? 0
  const limit = filters.limit

  // The query, as one comparable string. Two things depend on it. Whether the
  // answer in state is the answer being waited for is decided by *comparing*
  // during render, rather than by writing a fresh "loading" state into an
  // effect - which is a second render to express something knowable already,
  // and would make the list flash an empty panel on every keystroke.
  //
  // A late answer for a query nobody is waiting for any more is discarded by
  // the effect's cleanup below, not by this comparison. The comparison only
  // says whether the answer in state is current; it cannot stop a stale
  // response from *replacing* a current one, which is what an older request
  // settling after a newer one would otherwise do (S2-008).
  const key = JSON.stringify([
    search,
    categoryId,
    status,
    submissionContext,
    visibility,
    mine,
    offset,
    limit,
    reloadToken,
  ])

  const [answer, setAnswer] = useState<Answer | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  useEffect(() => {
    // Set when this query is superseded - by new filters, a new page, or a
    // reload - so its answer, success or failure, is dropped on arrival and
    // can neither replace nor wipe the newer one.
    let cancelled = false

    ideasRequest({
      search,
      categoryId,
      status,
      submissionContext,
      visibility,
      mine,
      offset,
      limit,
    })
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
    // `reloadToken` is a trigger, not an input: it is the way to say "same
    // query, go and ask again" after a write. It changes nothing about the
    // request, so the linter is right that the effect does not read it.
    // oxlint-disable-next-line react/exhaustive-effect-dependencies
  }, [search, categoryId, status, submissionContext, visibility, mine, offset, limit, key])

  const current = answer !== null && answer.key === key
  const failed = errorKey === key

  return {
    // The last answer, not necessarily the current one: a refresh keeps the
    // rows on screen while it runs, so the reader is not shown an empty panel
    // between one keystroke and the next.
    ideas: answer?.ideas ?? NO_IDEAS,
    pageInfo: (answer?.pageInfo ?? null) as IdeaPageInfo | null,
    loading: !current && !failed,
    error: failed ? FAILED : null,
  }
}
