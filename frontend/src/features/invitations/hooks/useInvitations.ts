import { useCallback, useEffect, useState } from 'react'

import {
  organizationInvitationsRequest,
  teamInvitationsRequest,
  type Invitation,
} from '../api/invitationsApi'

/**
 * The invitations one tenant has sent, keyed on being open.
 *
 * `scope` is not an argument with two answers but two hooks, because
 * organization invitations and team invitations are different queries against
 * different tenants: a caller that could pass either id would be able to ask for
 * the wrong one and have to be told which it got.
 *
 * Nothing is fetched until a tenant id is set, so a panel rendered for a tenant
 * whose id has not arrived asks nothing rather than asking for nothing.
 *
 * `loading` is derived by comparing the query during render rather than written
 * in the effect — the same discipline `useMyTeams` and `useTeamMembers` use.
 */
export interface InvitationList {
  invitations: Invitation[]
  loading: boolean
  error: string | null
  reload: () => void
}

const FAILED = 'We could not load these invitations. Please try again.'
const NONE: Invitation[] = []

export function useTeamInvitations(teamId: string | null): InvitationList {
  return useInvitations(teamId, teamInvitationsRequest)
}

export function useOrganizationInvitations(organizationId: string | null): InvitationList {
  return useInvitations(organizationId, organizationInvitationsRequest)
}

function useInvitations(
  tenantId: string | null,
  request: (id: string) => Promise<Invitation[]>,
): InvitationList {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    tenantId: string | null
    token: number
    invitations: Invitation[]
  } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  // One tenant's list cannot be rendered under another's name, so a switch drops
  // the previous answer during render rather than in an effect write.
  if (answer !== null && answer.tenantId !== tenantId) setAnswer(null)

  const shownToken = answer !== null && answer.tenantId === tenantId ? answer.token : -1
  const key = `${tenantId ?? 'none'}:${token}`

  useEffect(() => {
    if (tenantId === null) return
    let cancelled = false
    request(tenantId)
      .then((loaded) => {
        if (cancelled) return
        setAnswer({ tenantId, token, invitations: loaded })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setErrorKey(key)
      })
    return () => {
      cancelled = true
    }
    // `key` carries the token, so it *is* the query. `request` is one of two
    // module-level functions, chosen by the exported hook above and fixed for
    // the life of this call, so it cannot change under an effect that only
    // re-runs for a new tenant.
  }, [key, tenantId, token, request])

  const reload = useCallback(() => setToken((value) => value + 1), [])

  const fresh = shownToken === token
  const failed = errorKey === key

  return {
    invitations: shownToken === -1 ? NONE : (answer?.invitations ?? NONE),
    loading: tenantId !== null && !fresh && !failed,
    error: failed ? FAILED : null,
    reload,
  }
}
