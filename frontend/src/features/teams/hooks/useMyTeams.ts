import { useCallback, useEffect, useState } from 'react'

import { teamsRequest, type Team } from '../api/teamsApi'

/**
 * The caller's teams, and a way to ask again after a write.
 *
 * Loaded once per mount rather than on every render: the roster of somebody's
 * own teams is reference data for this session, and the only thing that changes
 * it is a membership operation - which is exactly when `reload` is called.
 *
 * `loading` is **derived by comparing keys during render** rather than written
 * as a state transition, which is the same discipline `useIdeaDiscovery` uses:
 * setting `loading` in the effect that starts the request would be a second
 * render to express something already knowable, and the effect's own `token`
 * dependency would then re-fire for the state write rather than for the request.
 *
 * `error` is reported rather than thrown, and a failed load leaves the previous
 * teams in place. Both because a membership list that emptied itself on a
 * network blip would look like "you are in no teams", which is a much worse lie
 * than "we could not refresh that".
 */
export interface MyTeams {
  teams: Team[]
  loading: boolean
  error: string | null
  reload: () => void
}

const FAILED = 'We could not load your teams. Please try again.'

/** The query, as one comparable value. See the note on `loading` above. */
function queryKey(token: number): string {
  return `teams:${token}`
}

export function useMyTeams(): MyTeams {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{ key: string; teams: Team[] } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  const key = queryKey(token)

  useEffect(() => {
    let cancelled = false
    teamsRequest()
      .then((loaded) => {
        if (cancelled) return
        setAnswer({ key, teams: loaded })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setErrorKey(key)
      })
    return () => {
      cancelled = true
    }
    // `key` carries the token, so it *is* the query - the effect has nothing
    // else to depend on.
  }, [key])

  const reload = useCallback(() => setToken((value) => value + 1), [])

  const current = answer !== null && answer.key === key
  const failed = errorKey === key

  return {
    /*
      The last answer, whether or not it is for the query in flight — so a
      refresh keeps the list on screen, and a *failed* refresh keeps it too.
      That is the whole point of reporting `error` beside the list rather than
      emptying it: "we could not refresh that" and "you are in no teams" are very
      different facts, and only one of them is an answer.
    */
    teams: answer?.teams ?? [],
    loading: !current && !failed,
    error: failed ? FAILED : null,
    reload,
  }
}
