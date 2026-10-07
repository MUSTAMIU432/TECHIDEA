/**
 * Platform reviewers and review teams, for the console.
 *
 * Refusals are payloads (`success: false` and the field they name); only a transport
 * failure rejects. Who may do what is the server's decision - the page narrows what it
 * draws from `adminCapabilities` and nothing more.
 */

import { graphqlClient } from '../../../graphql/client'

export interface Reviewer {
  id: string
  email: string
  name: string
}

export interface ReviewTeamMember {
  id: string
  email: string
  name: string
  isLead: boolean
}

export interface ReviewTeam {
  id: string
  name: string
  isActive: boolean
  leadId: string
  members: ReviewTeamMember[]
}

export interface Outcome {
  success: boolean
  message: string
  field: string | null
}

const TEAM = 'id name isActive leadId members { id email name isLead }'

export async function reviewersRequest(): Promise<Reviewer[]> {
  const data = await graphqlClient.request<{ reviewers: Reviewer[] }>(
    'query { reviewers { id email name } }',
  )
  return data.reviewers
}

export async function reviewTeamsRequest(): Promise<ReviewTeam[]> {
  const data = await graphqlClient.request<{ reviewTeams: ReviewTeam[] }>(
    `query { reviewTeams { ${TEAM} } }`,
  )
  return data.reviewTeams
}

export async function grantReviewerRequest(email: string): Promise<Outcome> {
  const data = await graphqlClient.request<{ grantReviewer: Outcome }>(
    'mutation($email: String!) { grantReviewer(email: $email) { success message field } }',
    { email },
  )
  return data.grantReviewer
}

export async function revokeReviewerRequest(userId: string): Promise<Outcome> {
  const data = await graphqlClient.request<{ revokeReviewer: Outcome }>(
    'mutation($userId: ID!) { revokeReviewer(userId: $userId) { success message field } }',
    { userId },
  )
  return data.revokeReviewer
}

export async function createReviewTeamRequest(
  name: string,
  leadId: string,
  memberIds: string[],
): Promise<Outcome> {
  const data = await graphqlClient.request<{ createReviewTeam: Outcome }>(
    `mutation($name: String!, $leadId: ID!, $memberIds: [ID!]) {
       createReviewTeam(name: $name, leadId: $leadId, memberIds: $memberIds) {
         success message field
       }
     }`,
    { name, leadId, memberIds },
  )
  return data.createReviewTeam
}

export async function updateReviewTeamRequest(
  id: string,
  changes: { name?: string; leadId?: string; memberIds?: string[]; isActive?: boolean },
): Promise<Outcome> {
  const data = await graphqlClient.request<{ updateReviewTeam: Outcome }>(
    `mutation($id: ID!, $name: String, $leadId: ID, $memberIds: [ID!], $isActive: Boolean) {
       updateReviewTeam(id: $id, name: $name, leadId: $leadId, memberIds: $memberIds,
         isActive: $isActive) { success message field }
     }`,
    { id, ...changes },
  )
  return data.updateReviewTeam
}

export async function assignIdeaToReviewTeamRequest(
  ideaId: string,
  teamId: string,
): Promise<Outcome> {
  const data = await graphqlClient.request<{ assignIdeaToReviewTeam: Outcome }>(
    `mutation($ideaId: ID!, $teamId: ID!) {
       assignIdeaToReviewTeam(ideaId: $ideaId, teamId: $teamId) { success message field }
     }`,
    { ideaId, teamId },
  )
  return data.assignIdeaToReviewTeam
}
