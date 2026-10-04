import { useContext } from 'react'

import { TeamsContext } from './TeamsContext'

/**
 * The caller's teams, as loaded by the page's `TeamsProvider`.
 *
 * Throws outside a provider rather than quietly starting its own request: two
 * consumers each loading their own copy is the duplication this provider
 * exists to remove, so a missing one is a mistake worth failing on.
 */
export function useTeams() {
  const context = useContext(TeamsContext)
  if (!context) {
    throw new Error('useTeams must be used within a TeamsProvider')
  }
  return context
}
