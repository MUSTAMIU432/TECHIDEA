import { useCallback, useEffect, useState } from 'react'

import { organizationMembersRequest, type Membership } from '../api/organizationApi'

/**
 * One organization's active members, and a way to ask again after a write.
 *
 * `null` for "no organization is open", which is different from an empty list:
 * an empty list is also what the server answers for an organization the caller
 * cannot see, and a panel that offers to add somebody has to be able to tell
 * "there is nobody here to add" from "there is no organization to look in".
 *
 * Nothing is fetched until an id arrives, so a panel rendered before the
 * organization switcher has answered asks nothing rather than asking for
 * nothing. `loading` is derived by comparing the query during render rather than
 * written inside the effect — the discipline `useMyTeams` and `useTeamMembers`
 * both use.
 */
export interface OrganizationRoster {
  members: Membership[]
  loading: boolean
  error: string | null
  reload: () => void
}

const FAILED = 'We could not load your colleagues. Please try again.'
const NONE: Membership[] = []

export function useOrganizationMembers(organizationId: string | null): OrganizationRoster {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    organizationId: string | null
    token: number
    members: Membership[]
  } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  // One organization's roster must never be rendered under another's name, so a
  // switch drops the previous answer during render rather than in an effect write.
  if (answer !== null && answer.organizationId !== organizationId) setAnswer(null)

  const shownToken = answer !== null && answer.organizationId === organizationId ? answer.token : -1
  const key = `${organizationId ?? 'none'}:${token}`

  useEffect(() => {
    if (organizationId === null) return
    let cancelled = false
    organizationMembersRequest(organizationId)
      .then((loaded) => {
        if (cancelled) return
        setAnswer({ organizationId, token, members: loaded })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setErrorKey(key)
      })
    return () => {
      cancelled = true
    }
    // `key` carries both the organization and the token, so it *is* the query;
    // `token` is named too because the answer records which one produced it.
  }, [key, organizationId, token])

  const reload = useCallback(() => setToken((value) => value + 1), [])

  const fresh = shownToken === token
  const failed = errorKey === key

  return {
    // The last answer for *this* organization, kept across a failed refresh: a
    // roster that emptied itself on a network blip would read as "your
    // colleagues have gone", which is a much worse lie than "we could not
    // refresh that".
    members: shownToken === -1 ? NONE : (answer?.members ?? NONE),
    loading: organizationId !== null && !fresh && !failed,
    error: failed ? FAILED : null,
    reload,
  }
}
