/**
 * The one console request the ordinary app shell makes: what the signed-in
 * user may do in the administration console. Split from `administrationApi`
 * so every user's bundle carries this and not the whole console.
 *
 * An offer, never a grant - see `AdminCapabilitiesProvider`.
 */

import { graphqlClient } from '../../../graphql/client'

export interface AdminCapabilities {
  canAccessConsole: boolean
  canInspectIdeaContent: boolean
  canManageUserAccounts: boolean
  canManageOrganizationRoles: boolean
  canManageCategories: boolean
  canAssignPlatformReviewers: boolean
  canManageReviewers: boolean
  canReleaseProposals: boolean
}

export const NO_ADMIN_CAPABILITIES: AdminCapabilities = {
  canAccessConsole: false,
  canInspectIdeaContent: false,
  canManageUserAccounts: false,
  canManageOrganizationRoles: false,
  canManageCategories: false,
  canAssignPlatformReviewers: false,
  canManageReviewers: false,
  canReleaseProposals: false,
}

const CAPABILITIES_QUERY = `
  query AdminCapabilities {
    adminCapabilities {
      canAccessConsole
      canInspectIdeaContent
      canManageUserAccounts
      canManageOrganizationRoles
      canManageCategories
      canAssignPlatformReviewers
      canManageReviewers
      canReleaseProposals
    }
  }
`

export async function adminCapabilitiesRequest(): Promise<AdminCapabilities> {
  const data = await graphqlClient.request<{ adminCapabilities: AdminCapabilities }>(
    CAPABILITIES_QUERY,
  )
  return data.adminCapabilities
}
