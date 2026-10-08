/**
 * Decision letters: how a platform approval or rejection reaches the idea's owner.
 *
 * The reviewer decides; a platform administrator sends the letter from the console;
 * only then does the owner learn the outcome. Refusals are payloads (`success: false`
 * and the field they name); only a transport failure rejects.
 */

import { graphqlClient } from '../../../graphql/client'

export type LetterDecision = 'approved' | 'rejected'

export interface DecisionLetter {
  id: string
  decision: LetterDecision
  status: 'pending' | 'sent'
  message: string
  createdAt: string
  sentAt: string | null
  sentByName: string | null
  ideaId: string
  ideaTitle: string
  ownerName: string
  ownerEmail: string
  reviewerName: string
  reviewRound: number
  reviewFeedback: string
}

export interface OwnerDecisionLetter {
  decision: LetterDecision
  message: string
  sentAt: string
}

export interface LetterOutcome {
  success: boolean
  message: string
  field: string | null
}

const LETTER_FIELDS = `
  id decision status message createdAt sentAt sentByName ideaId ideaTitle
  ownerName ownerEmail reviewerName reviewRound reviewFeedback
`

/** Every letter for the console, waiting ones first. Empty without the permission. */
export async function decisionLettersRequest(): Promise<DecisionLetter[]> {
  const data = await graphqlClient.request<{ decisionLetters: DecisionLetter[] }>(
    `query { decisionLetters { ${LETTER_FIELDS} } }`,
  )
  return data.decisionLetters
}

export async function sendDecisionLetterRequest(
  id: string,
  message: string,
): Promise<LetterOutcome> {
  const data = await graphqlClient.request<{ sendDecisionLetter: LetterOutcome }>(
    `mutation($id: ID!, $message: String) {
       sendDecisionLetter(id: $id, message: $message) { success message field }
     }`,
    { id, message },
  )
  return data.sendDecisionLetter
}

/** The letter the owner was sent about this idea; null for anybody else, or before it is sent. */
export async function ideaDecisionLetterRequest(
  ideaId: string,
): Promise<OwnerDecisionLetter | null> {
  const data = await graphqlClient.request<{ ideaDecisionLetter: OwnerDecisionLetter | null }>(
    'query($ideaId: ID!) { ideaDecisionLetter(ideaId: $ideaId) { decision message sentAt } }',
    { ideaId },
  )
  return data.ideaDecisionLetter
}
