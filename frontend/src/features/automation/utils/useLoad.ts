import { useCallback, useEffect, useState } from 'react'

export interface Loaded<T> {
  data: T | null
  loading: boolean
  failed: boolean
  reload: () => void
}

/**
 * Loads something keyed by `key` and reloads on demand.
 *
 * The answer is stored with the key it answered, and read only while the key still
 * matches - so moving to another record shows "loading" instead of the previous
 * record's data under the new one, with no state written inside the effect.
 */
export function useLoad<T>(loader: () => Promise<T>, key: string): Loaded<T> {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    key: string
    data: T | null
    failed: boolean
  } | null>(null)
  const current = `${key}#${token}`

  useEffect(() => {
    let cancelled = false
    loader()
      .then((data) => {
        if (!cancelled) setAnswer({ key: current, data, failed: false })
      })
      .catch(() => {
        if (!cancelled) setAnswer({ key: current, data: null, failed: true })
      })
    return () => {
      cancelled = true
    }
    // `loader` is re-created each render; the key is what says the query changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current])

  const reload = useCallback(() => setToken((value) => value + 1), [])
  const ready = answer !== null && answer.key === current
  return {
    data: ready ? answer.data : null,
    loading: !ready,
    failed: ready && answer.failed,
    reload,
  }
}
