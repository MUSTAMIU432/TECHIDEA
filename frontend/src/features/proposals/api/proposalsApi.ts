/**
 * The proposal an approved idea gets: written by the review team, released by an admin, read by
 * the owner. Refusals are payloads (`success: false` and the field they name); only a transport
 * failure rejects. Who may see or do what is the server's decision - the booleans in
 * `ProposalState` only decide what is drawn.
 */

import { graphqlClient } from '../../../graphql/client'

export interface IdeaProposal {
  ideaId: string
  ideaTitle: string
  teamName: string | null
  title: string
  executiveSummary: string
  problem: string
  proposedSolution: string
  requirementsSummary: string
  scope: string
  deliverables: string
  risks: string
  assumptions: string
  estimatedEffort: string
  estimatedTimeline: string
  acceptanceCriteria: string
  status: string
  reviewFeedback: string
  submittedAt: string | null
  decidedAt: string | null
  updatedAt: string
}

export type ViewerRole = 'writer' | 'admin' | 'owner' | null

export interface ProposalState {
  viewerRole: ViewerRole
  canStart: boolean
  canEdit: boolean
  canSubmit: boolean
  canDecide: boolean
  proposal: IdeaProposal | null
}

export interface ProposalOutcome {
  success: boolean
  message: string
  field: string | null
  proposal: IdeaProposal | null
}

export interface ViewReceipt {
  viewerName: string
  viewerEmail: string
  viewedAt: string
}

/** The sections a proposal is made of, in reading order. `short` ones are single-line. */
export const PROPOSAL_SECTIONS = [
  { key: 'title', label: 'Title', short: true },
  { key: 'executiveSummary', label: 'Executive summary' },
  { key: 'problem', label: 'Problem' },
  { key: 'proposedSolution', label: 'Proposed solution' },
  { key: 'requirementsSummary', label: 'Requirements' },
  { key: 'scope', label: 'Scope' },
  { key: 'deliverables', label: 'Deliverables' },
  { key: 'risks', label: 'Risks' },
  { key: 'assumptions', label: 'Assumptions' },
  { key: 'estimatedEffort', label: 'Estimated effort', short: true },
  { key: 'estimatedTimeline', label: 'Timeline', short: true },
  { key: 'acceptanceCriteria', label: 'Acceptance criteria' },
] as const

export type SectionKey = (typeof PROPOSAL_SECTIONS)[number]['key']

const FIELDS = `ideaId ideaTitle teamName title executiveSummary problem proposedSolution
  requirementsSummary scope deliverables risks assumptions estimatedEffort estimatedTimeline
  acceptanceCriteria status reviewFeedback submittedAt decidedAt updatedAt`

export async function proposalStateRequest(ideaId: string): Promise<ProposalState> {
  const data = await graphqlClient.request<{ ideaProposalState: ProposalState }>(
    `query($ideaId: ID!) {
       ideaProposalState(ideaId: $ideaId) {
         viewerRole canStart canEdit canSubmit canDecide proposal { ${FIELDS} }
       }
     }`,
    { ideaId },
  )
  return data.ideaProposalState
}

export async function proposalsForReleaseRequest(): Promise<IdeaProposal[]> {
  const data = await graphqlClient.request<{ proposalsForRelease: IdeaProposal[] }>(
    `query { proposalsForRelease { ${FIELDS} } }`,
  )
  return data.proposalsForRelease
}

function outcome(root: string, decl: string, args: string) {
  const document = `mutation(${decl}) { ${root}(${args}) { success message field proposal { ${FIELDS} } } }`
  return async (variables: Record<string, unknown>): Promise<ProposalOutcome> => {
    const data = await graphqlClient.request<Record<string, ProposalOutcome>>(document, variables)
    return data[root]
  }
}

export const startProposalRequest = outcome('startIdeaProposal', '$ideaId: ID!', 'ideaId: $ideaId')
export const submitProposalRequest = outcome(
  'submitIdeaProposal',
  '$ideaId: ID!',
  'ideaId: $ideaId',
)
export const releaseProposalRequest = outcome(
  'releaseIdeaProposal',
  '$ideaId: ID!',
  'ideaId: $ideaId',
)
export const requestProposalChangesRequest = outcome(
  'requestIdeaProposalChanges',
  '$ideaId: ID!, $feedback: String!',
  'ideaId: $ideaId, feedback: $feedback',
)
export const declineProposalRequest = outcome(
  'declineIdeaProposal',
  '$ideaId: ID!, $reason: String!',
  'ideaId: $ideaId, reason: $reason',
)

export function updateProposalRequest(
  ideaId: string,
  values: Partial<Record<SectionKey, string>>,
): Promise<ProposalOutcome> {
  return outcome(
    'updateIdeaProposal',
    '$input: UpdateIdeaProposalInput!',
    'input: $input',
  )({ input: { ideaId, ...values } })
}

/** Announces that the owner opened the released proposal; the answer feeds the watermark. */
export async function recordProposalViewRequest(
  ideaId: string,
): Promise<{ success: boolean; message: string; receipt: ViewReceipt | null }> {
  const data = await graphqlClient.request<{
    recordProposalView: { success: boolean; message: string; receipt: ViewReceipt | null }
  }>(
    `mutation($ideaId: ID!) {
       recordProposalView(ideaId: $ideaId) {
         success message receipt { viewerName viewerEmail viewedAt }
       }
     }`,
    { ideaId },
  )
  return data.recordProposalView
}

export function statusWords(status: string): string {
  return (
    {
      draft: 'Draft',
      submitted: 'With the platform admin',
      changes_requested: 'Sent back for changes',
      released: 'Released to the owner',
      declined: 'Declined',
    }[status] ?? status
  )
}
