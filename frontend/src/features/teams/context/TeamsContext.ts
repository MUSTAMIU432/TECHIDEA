import { createContext } from 'react'

import type { MyTeams } from '../hooks/useMyTeams'

/**
 * The caller's teams, shared by everything on `/app/teams`.
 *
 * A provider rather than two hooks because that page has two consumers - the
 * list and the form beside it - and each one asking `teams` on its own would be
 * two requests for one answer. The form needs the list only to refresh it after
 * a create, and the list needs nothing the form does not already have.
 *
 * `undefined` when there is no provider, so a consumer rendered outside one says
 * so in development instead of quietly reading `undefined.teams`.
 */
export const TeamsContext = createContext<MyTeams | undefined>(undefined)
