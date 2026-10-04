/**
 * GraphQL operations for the Teams domain.
 *
 * A team is a **collaboration boundary, not a tenant**: people who write one
 * idea together. So this module has no idea operations and no review operations,
 * and the shapes it does have say nothing about approvals - there is nothing on
 * a team that could approve anything, which is why there is no `canApprove`
 * field to render a button from.
 *
 * Every read here is already scoped to the caller's own memberships by the
 * server. There is no filter input that could widen it: `teams` takes no
 * arguments at all, and `team(id)` resolves to null for a team the caller is not
 * in - the same answer as for one that does not exist, so a team id cannot be
 * used to discover other teams.
 */

import { graphqlClient } from '../../../graphql/client'

/** One team the caller is an active member of. */
export interface Team {
  id: string
  name: string
  slug: string
  description: string
  ownerId: string
  /** Active members only. An inactive membership is not on a roster. */
  memberCount: number
  createdAt: string
}

/** One person's place in a team, and the roles they hold there. */
export interface TeamMember {
  userId: string
  email: string
  firstName: string
  lastName: string
  roleSlugs: string[]
  joinedAt: string
}

/** A role slug rendered as a word. `owner` is "Owner", `member` is "Member". */
export function teamRoleLabel(slug: string): string {
  const trimmed = slug.replace(/_/g, ' ').trim()
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
}

export interface CreateTeamInput {
  name: string
  description?: string
}

export interface TeamMutationResult {
  success: boolean
  message: string
  field: string | null
}

export interface CreateTeamResult extends TeamMutationResult {
  team: Team | null
}

const TEAM_FIELDS = `
  id
  name
  slug
  description
  ownerId
  memberCount
  createdAt
`

const TEAMS_QUERY = `
  query Teams {
    teams { ${TEAM_FIELDS} }
  }
`

const TEAM_QUERY = `
  query Team($id: ID!) {
    team(id: $id) { ${TEAM_FIELDS} }
  }
`

const TEAM_MEMBERS_QUERY = `
  query TeamMembers($teamId: ID!) {
    teamMembers(teamId: $teamId) {
      user { id email firstName lastName }
      roleSlugs
      joinedAt
    }
  }
`

const CREATE_TEAM_MUTATION = `
  mutation CreateTeam($input: CreateTeamInput!) {
    createTeam(input: $input) {
      success
      message
      field
      team { ${TEAM_FIELDS} }
    }
  }
`

const LEAVE_TEAM_MUTATION = `
  mutation LeaveTeam($teamId: ID!) {
    leaveTeam(teamId: $teamId) { success message field }
  }
`

const ADD_TEAM_MEMBER_MUTATION = `
  mutation AddTeamMember($input: AddTeamMemberInput!) {
    addTeamMember(input: $input) { success message field }
  }
`

/** Every team the caller is an active member of. Empty is a real answer. */
export async function teamsRequest(): Promise<Team[]> {
  const data = await graphqlClient.request<{ teams: Team[] }>(TEAMS_QUERY)
  return data.teams
}

/** One team, or null for a team the caller is not in and for one that is gone. */
export async function teamRequest(id: string): Promise<Team | null> {
  const data = await graphqlClient.request<{ team: Team | null }>(TEAM_QUERY, { id })
  return data.team
}

/**
 * A team's roster, oldest join first.
 *
 * Empty - not an error - for anybody who is not a member, which is the same
 * answer as for a team that does not exist.
 */
/** The wire shape: a membership link embeds the person, this client flattens it. */
interface TeamMemberLink {
  user: { id: string; email: string; firstName: string; lastName: string }
  roleSlugs: string[]
  joinedAt: string
}

export async function teamMembersRequest(teamId: string): Promise<TeamMember[]> {
  const data = await graphqlClient.request<{ teamMembers: TeamMemberLink[] }>(TEAM_MEMBERS_QUERY, {
    teamId,
  })
  return data.teamMembers.map((link) => ({
    userId: link.user.id,
    email: link.user.email,
    firstName: link.user.firstName,
    lastName: link.user.lastName,
    roleSlugs: link.roleSlugs,
    joinedAt: link.joinedAt,
  }))
}

/**
 * Create a team, making the caller its Owner and first member.
 *
 * Needs no organization: the whole point is that somebody with no tenant can
 * still file a team idea.
 */
export async function createTeamRequest(input: CreateTeamInput): Promise<CreateTeamResult> {
  const data = await graphqlClient.request<{ createTeam: CreateTeamResult }>(CREATE_TEAM_MUTATION, {
    input: { name: input.name, description: input.description ?? '' },
  })
  return data.createTeam
}

/**
 * Leave a team.
 *
 * Refused for the only member, who would leave a team nobody can administer -
 * the server's answer, not this client's, and it comes back as an ordinary
 * business message to show.
 */
export async function leaveTeamRequest(teamId: string): Promise<TeamMutationResult> {
  const data = await graphqlClient.request<{ leaveTeam: TeamMutationResult }>(LEAVE_TEAM_MUTATION, {
    teamId,
  })
  return data.leaveTeam
}

/**
 * Add somebody who already has an account to a team, as a Member.
 *
 * For an email address with no account, use `sendTeamInvitationRequest` in the
 * invitations module instead: typing an address does not create a membership,
 * it creates an invitation somebody has to accept.
 */
export async function addTeamMemberRequest(
  teamId: string,
  userId: string,
): Promise<TeamMutationResult> {
  const data = await graphqlClient.request<{ addTeamMember: TeamMutationResult }>(
    ADD_TEAM_MEMBER_MUTATION,
    { input: { teamId, userId } },
  )
  return data.addTeamMember
}
