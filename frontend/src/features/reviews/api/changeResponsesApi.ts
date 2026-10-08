/**
 * The owner's responses to requests for changes.
 *
 * Responding is one step with the resubmission: the server records what the owner
 * changed and puts the idea back in front of the reviewers together, or does neither.
 * Refusals are payloads (`success: false` and the field they name).
 */

import { graphqlClient } from '../../../graphql/client'

export interface ChangeResponse {
  id: string
  message: string
  authorName: string
  createdAt: string
  reviewRound: number
  reviewScope: 'platform' | 'organization'
}

export interface RespondOutcome {
  success: boolean
  message: string
  field: string | null
  ideaStatus: string | null
}

/** The responses on an idea this reader may see, newest first. */
export async function changeResponsesRequest(ideaId: string): Promise<ChangeResponse[]> {
  const data = await graphqlClient.request<{ changeResponses: ChangeResponse[] }>(
    `query($ideaId: ID!) {
       changeResponses(ideaId: $ideaId) {
         id message authorName createdAt reviewRound reviewScope
       }
     }`,
    { ideaId },
  )
  return data.changeResponses
}

export async function respondToChangesRequest(
  ideaId: string,
  message: string,
): Promise<RespondOutcome> {
  const data = await graphqlClient.request<{ respondToChanges: RespondOutcome }>(
    `mutation($ideaId: ID!, $message: String!) {
       respondToChanges(ideaId: $ideaId, message: $message) {
         success message field ideaStatus
       }
     }`,
    { ideaId, message },
  )
  return data.respondToChanges
}
