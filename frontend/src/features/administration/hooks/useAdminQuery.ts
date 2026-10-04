import { useCallback, useEffect, useState } from 'react'

export interface AdminQuery<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}

interface Answer<T> {
  key: string
  data: T
}

/**
 * One console read, keyed by everything it depends on.
 *
 * The same discipline as `useReviewQueue` and `useIdeaDiscovery`: an answer is
 * matched to the request it answers by comparing keys during render, and a
 * superseded request - new filters, a new page, a reload - has its answer,
 * success or failure, dropped on arrival so it can neither replace nor wipe a
 * newer one. `key` must change whenever `load` would ask something different.
 *
 * `data` is only ever the answer to *this* `key`: a new filter or page shows
 * the loading state rather than the previous query's rows under new headings.
 */
export function useAdminQuery<T>(
  key: string,
  load: () => Promise<T>,
  errorMessage = 'We could not load this. Please try again.',
): AdminQuery<T> {
  const [reloadToken, setReloadToken] = useState(0)
  const requestKey = `${key}#${reloadToken}`
  const [answer, setAnswer] = useState<Answer<T> | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    load()
      .then((data) => {
        if (cancelled) return
        setAnswer({ key: requestKey, data })
        setErrorKey(null)
      })
      .catch(() => {
        if (!cancelled) setErrorKey(requestKey)
      })
    return () => {
      cancelled = true
    }
    // `load` is a fresh closure every render; `requestKey` is what it depends on.
    // oxlint-disable-next-line react/exhaustive-deps
  }, [requestKey])

  const reload = useCallback(() => setReloadToken((token) => token + 1), [])
  const current = answer !== null && answer.key === requestKey
  // After `reload()` the previous answer to the *same* query stays on screen
  // until the fresh one lands, so an action's refresh does not blank the page.
  const sameQuery = answer !== null && answer.key.startsWith(`${key}#`)
  const failed = errorKey === requestKey

  return {
    data: sameQuery ? answer.data : null,
    loading: !current && !failed,
    error: failed ? errorMessage : null,
    reload,
  }
}
