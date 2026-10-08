import { useCallback, useState } from 'react'

import type { Outcome } from '../api/automationApi'

export interface Notice {
  kind: 'success' | 'error'
  text: string
  field: string | null
}

/**
 * Runs one server action and keeps its answer: busy while it runs, the server's own
 * message when it refuses (with the field it names), and a plain transport message
 * when the request never got an answer.
 */
export function useOutcome() {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)

  const run = useCallback(
    async <P extends Outcome>(action: () => Promise<P>, onSuccess?: (payload: P) => void) => {
      setBusy(true)
      setNotice(null)
      try {
        const payload = await action()
        if (payload.success) {
          onSuccess?.(payload)
        } else {
          setNotice({
            kind: 'error',
            text: payload.message,
            field: payload.field,
          })
        }
        return payload.success
      } catch {
        setNotice({
          kind: 'error',
          text: 'We could not reach the server. Please try again.',
          field: null,
        })
        return false
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  return { busy, notice, run, clear: () => setNotice(null) }
}
