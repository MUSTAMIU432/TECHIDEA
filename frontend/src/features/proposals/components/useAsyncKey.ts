import { useCallback, useEffect, useState } from 'react'

import type { ProposalState } from '../api/proposalsApi'

/** Loads the proposal state for one idea and reloads on demand; the answer is tied to its key. */
export function useAsyncKey(load: () => Promise<ProposalState>, key: string) {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    key: string
    state: ProposalState | null
    failed: boolean
  } | null>(null)
  const current = `${key}#${token}`

  useEffect(() => {
    let cancelled = false
    load()
      .then((state) => {
        if (!cancelled) setAnswer({ key: current, state, failed: false })
      })
      .catch(() => {
        if (!cancelled) setAnswer({ key: current, state: null, failed: true })
      })
    return () => {
      cancelled = true
    }
    // `load` is a fresh closure every render; the key says the query changed.
    // oxlint-disable-next-line react/exhaustive-deps
  }, [current])

  const ready = answer !== null && answer.key === current
  return {
    state: ready ? answer.state : null,
    failed: ready && answer.failed,
    reload: useCallback(() => setToken((value) => value + 1), []),
  }
}
