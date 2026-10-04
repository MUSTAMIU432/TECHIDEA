import { TeamWorkspace } from '../features/teams/components/TeamWorkspace'

/**
 * One team at `/app/teams/:teamId`.
 *
 * A child of `/app`, so it inherits `RequireAuth` and the app chrome. It needs
 * no organization: a team is not a tenant, so this page reads nothing from
 * `useOrganization` and stays correct when the reader is in none.
 */
export function TeamDetailPage() {
  return <TeamWorkspace />
}
