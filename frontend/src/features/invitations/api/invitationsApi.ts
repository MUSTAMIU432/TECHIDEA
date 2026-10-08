/**
 * Invitations: the only way somebody joins an organization or a team.
 *
 * The security of the flow is the server's and this module does not restate it,
 * but two things about it are the client's business:
 *
 * - **Accepting is bound to the signed-in account's email.** So the accept page
 *   has to be able to say "this invitation was sent to somebody else" plainly,
 *   before anybody presses anything, and it cannot offer to "switch account" -
 *   there is nothing here that could.
 * - **The token lives in the URL and is read once.** It is never stored, never
 *   put in component state that outlives the render, and never sent anywhere
 *   but the server. Everything the page shows comes from `invitationDetails`,
 *   which is deliberately limited to the tenant's name and the role: an inbox is
 *   readable by more than its owner, so a link that leaked must not be a link
 *   that reveals anything about the tenant.
 */

import { graphqlClient } from '../../../graphql/client'

export type InvitationScope = 'ORGANIZATION' | 'TEAM'
export type InvitationStatus = 'PENDING' | 'ACCEPTED' | 'EXPIRED' | 'REVOKED'

/** What an invited person is shown before deciding, and nothing more. */
export interface InvitationPreview {
  scope: InvitationScope
  tenantName: string
  roleName: string
  /** The address the invitation was sent to. */
  email: string
  invitedByFirstName: string
  isOpen: boolean
  expired: boolean
  accepted: boolean
  revoked: boolean
  /**
   * The recipient turned it down. Kept apart from `expired` and `revoked`
   * because all three are dead states and each says something different to the
   * person looking at it - one link, three different reasons.
   */
  declined: boolean
}

/** One invitation on an inviter's list: what was sent and what is outstanding. */
export interface Invitation {
  id: string
  scope: InvitationScope
  status: InvitationStatus
  roleSlug: string
  email: string
  tenantName: string
  invitedById: string
  invitedByFirstName: string
  expiresAt: string
  createdAt: string
  acceptedAt: string | null
  isOpen: boolean
}

export interface InvitationMutationResult {
  success: boolean
  message: string
  field: string | null
  invitation: Invitation | null
}

export interface AcceptInvitationResult {
  success: boolean
  message: string
  field: string | null
  invitation: Invitation | null
}

const INVITATION_FIELDS = `
  id
  scope
  status
  roleSlug
  email
  tenantName
  invitedById
  invitedByFirstName
  expiresAt
  createdAt
  acceptedAt
  isOpen
`

const INVITATION_DETAILS_QUERY = `
  query InvitationDetails($token: String!) {
    invitationDetails(token: $token) {
      scope
      tenantName
      roleName
      email
      invitedByFirstName
      isOpen
      expired
      accepted
      revoked
      declined
    }
  }
`

const ORGANIZATION_INVITATIONS_QUERY = `
  query OrganizationInvitations($organizationId: ID!) {
    organizationInvitations(organizationId: $organizationId) { ${INVITATION_FIELDS} }
  }
`

const TEAM_INVITATIONS_QUERY = `
  query TeamInvitations($teamId: ID!) {
    teamInvitations(teamId: $teamId) { ${INVITATION_FIELDS} }
  }
`

const SEND_ORGANIZATION_INVITATION_MUTATION = `
  mutation SendOrganizationInvitation($input: SendOrganizationInvitationInput!) {
    sendOrganizationInvitation(input: $input) {
      success
      message
      field
      invitation { ${INVITATION_FIELDS} }
    }
  }
`

const SEND_TEAM_INVITATION_MUTATION = `
  mutation SendTeamInvitation($input: SendTeamInvitationInput!) {
    sendTeamInvitation(input: $input) {
      success
      message
      field
      invitation { ${INVITATION_FIELDS} }
    }
  }
`

const REVOKE_INVITATION_MUTATION = `
  mutation RevokeInvitation($id: ID!) {
    revokeInvitation(id: $id) {
      success
      message
      field
      invitation { ${INVITATION_FIELDS} }
    }
  }
`

const DECLINE_INVITATION_MUTATION = `
  mutation DeclineInvitation($token: String!) {
    declineInvitation(token: $token) { success message field invitation { ${INVITATION_FIELDS} } }
  }
`

const ACCEPT_INVITATION_MUTATION = `
  mutation AcceptInvitation($token: String!) {
    acceptInvitation(token: $token) {
      success
      message
      field
      invitation { ${INVITATION_FIELDS} }
    }
  }
`

/**
 * What this token names, whatever its state - or null for a token that names
 * nothing.
 *
 * Deliberately usable while signed out: the whole point is to show somebody
 * what they were invited to before they have an account. Acceptance itself
 * re-checks everything this returns, which is why it is safe to render it to
 * anybody holding the link.
 */
export async function invitationDetailsRequest(token: string): Promise<InvitationPreview | null> {
  const data = await graphqlClient.request<{ invitationDetails: InvitationPreview | null }>(
    INVITATION_DETAILS_QUERY,
    { token },
  )
  return data.invitationDetails
}

/** What one organization has sent. Empty for anybody who cannot manage members. */
export async function organizationInvitationsRequest(
  organizationId: string,
): Promise<Invitation[]> {
  const data = await graphqlClient.request<{ organizationInvitations: Invitation[] }>(
    ORGANIZATION_INVITATIONS_QUERY,
    { organizationId },
  )
  return data.organizationInvitations
}

/** What one team has sent. Empty for anybody who cannot manage its members. */
export async function teamInvitationsRequest(teamId: string): Promise<Invitation[]> {
  const data = await graphqlClient.request<{ teamInvitations: Invitation[] }>(
    TEAM_INVITATIONS_QUERY,
    { teamId },
  )
  return data.teamInvitations
}

/**
 * Invite somebody to an organization by email.
 *
 * The email is *not* a membership: the recipient gets a link, and only accepting
 * it creates the membership. So this returns immediately while nothing about the
 * roster has changed yet.
 */
export async function sendOrganizationInvitationRequest(
  organizationId: string,
  email: string,
  roleSlug = 'member',
): Promise<InvitationMutationResult> {
  const data = await graphqlClient.request<{
    sendOrganizationInvitation: InvitationMutationResult
  }>(SEND_ORGANIZATION_INVITATION_MUTATION, { input: { organizationId, email, roleSlug } })
  return data.sendOrganizationInvitation
}

/** Invite somebody to a team by email. See above: an invitation, not a member. */
export async function sendTeamInvitationRequest(
  teamId: string,
  email: string,
  roleSlug = 'member',
): Promise<InvitationMutationResult> {
  const data = await graphqlClient.request<{ sendTeamInvitation: InvitationMutationResult }>(
    SEND_TEAM_INVITATION_MUTATION,
    { input: { teamId, email, roleSlug } },
  )
  return data.sendTeamInvitation
}

/**
 * Revoke a pending invitation.
 *
 * An invitation that was already accepted cannot be revoked, and the server says
 * so rather than pretending: the membership it granted is a separate fact.
 */
export async function revokeInvitationRequest(id: string): Promise<InvitationMutationResult> {
  const data = await graphqlClient.request<{ revokeInvitation: InvitationMutationResult }>(
    REVOKE_INVITATION_MUTATION,
    { id },
  )
  return data.revokeInvitation
}

/**
 * Decline: close an invitation without joining.
 *
 * The counterpart of accepting, and the recipient's own act under the same
 * address rule - a decline is a statement *about* the invitation, so only the
 * person it was sent to may make one. Creates nothing and removes nothing:
 * a membership only ever comes from accepting, so there is nothing to undo and
 * the invitation's own terminal state is the whole record.
 */
export async function declineInvitationRequest(token: string): Promise<AcceptInvitationResult> {
  const data = await graphqlClient.request<{ declineInvitation: AcceptInvitationResult }>(
    DECLINE_INVITATION_MUTATION,
    { token },
  )
  return data.declineInvitation
}

/**
 * Accept, which is the only thing anywhere that creates a membership.
 *
 * Refused for a signed-out caller, for a deactivated one, for a token that is
 * unknown, spent, revoked or expired, and for an account whose own email is not
 * the one the invitation was sent to - each with its own message, because the
 * recipient can act on every one of them and a single "invalid" would waste
 * their time.
 */
export async function acceptInvitationRequest(token: string): Promise<AcceptInvitationResult> {
  const data = await graphqlClient.request<{ acceptInvitation: AcceptInvitationResult }>(
    ACCEPT_INVITATION_MUTATION,
    { token },
  )
  return data.acceptInvitation
}
