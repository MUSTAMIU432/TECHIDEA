import { useCallback, useEffect, useState } from 'react'

import { teamMembersRequest, type TeamMember } from '../api/teamsApi'

/**
 * One team's roster, and a way to ask again after a membership changes.
 *
 * Fetched only while the roster is open, for the same reason the idea card's
 * sections are: a list of teams on a page must not become one request per team
 * for content nobody asked to read. The caller passes `null` for "no team is
 * open", which is what makes this hook usable for several rosters on one page.
 *
 * **Two things are decided during render rather than in the effect**, which is
 * the discipline `useMyTeams` and `useIdeaDiscovery` both use:
 *
 * - `loading` is *derived*. Writing it inside the effect that starts the request
 *   would be a second render to express something already knowable.
 * - closing a roster clears it. The alternative — an early `return` in the
 *   effect — would leave the last answer in state, and the next render would
 *   show one team's members under another team's name.
 */
export interface TeamRoster {
  members: TeamMember[]
  loading: boolean
  error: string | null
  reload: () => void
}

const FAILED = 'We could not load this team’s members. Please try again.'
const NONE: TeamMember[] = []

export function useTeamMembers(teamId: string | null): TeamRoster {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    teamId: string | null
    token: number
    members: TeamMember[]
  } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  // The roster a *different* team left behind is dropped during render rather
  // than by an effect write, so it can never be rendered against this one.
  if (answer !== null && answer.teamId !== teamId) setAnswer(null)

  const shownToken = answer !== null && answer.teamId === teamId ? answer.token : -1
  const key = `${teamId ?? 'none'}:${token}`

  useEffect(() => {
    if (teamId === null) return
    let cancelled = false
    teamMembersRequest(teamId)
      .then((loaded) => {
        if (cancelled) return
        setAnswer({ teamId, token, members: loaded })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setErrorKey(key)
      })
    return () => {
      cancelled = true
    }
    // `key` carries both the team and the token, so it *is* the query; `token`
    // is named too because the answer records which token produced it.
  }, [key, teamId, token])

  const reload = useCallback(() => setToken((value) => value + 1), [])

  const fresh = shownToken === token
  const failed = errorKey === key

  return {
    // The last answer for *this* team, kept across a failed refresh: a roster
    // that emptied itself on a network blip would read as "your team is empty",
    // which is a much worse lie than "we could not refresh that".
    members: shownToken === -1 ? NONE : (answer?.members ?? NONE),
    loading: teamId !== null && !fresh && !failed,
    error: failed ? FAILED : null,
    reload,
  }
}
