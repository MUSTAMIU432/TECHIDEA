import type { ReactNode } from 'react'

import { useMyTeams } from '../hooks/useMyTeams'
import { TeamsContext } from './TeamsContext'

/**
 * Loads the caller's teams once for the page and hands the same answer to every
 * consumer under it.
 *
 * Mounted by `TeamsPage` rather than by `AppLayout`, because a team is not a
 * tenant: there is no "active team" for the chrome to keep alive across
 * navigation (see `TeamList`), so the answer belongs to the page that shows it
 * and is re-asked when the reader comes back to it.
 */
export function TeamsProvider({ children }: { children: ReactNode }) {
  return <TeamsContext.Provider value={useMyTeams()}>{children}</TeamsContext.Provider>
}
