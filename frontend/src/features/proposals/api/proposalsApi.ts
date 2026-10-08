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
  feasibility: string
  milestones: string
  financialRequirements: string
  /** `yes` (the owner pays), `no` (no charge), or `''` (not answered yet). */
  paymentRequired: '' | 'yes' | 'no'
  paymentPlan: string
  status: string
  reviewFeedback: string
  submittedAt: string | null
  decidedAt: string | null
  updatedAt: string
}

/** Someone on the writing team, or who wrote part of it, and what they have done. */
export interface ProposalParticipant {
  name: string
  email: string
  /** `former`: wrote part of it, no longer on the team. */
  role: 'lead' | 'member' | 'former'
  contributions: number
  /** The sections they changed. */
  sections: SectionKey[]
  lastContributedAt: string | null
}

export interface ProposalActivity {
  name: string
  action: 'started' | 'edited' | 'submitted'
  sections: SectionKey[]
  at: string
}

/** How far one approved idea's proposal has got, and who is writing it. */
export interface ProposalProgress {
  ideaId: string
  ideaTitle: string
  /** A proposal status, or `not_started`. */
  status: string
  teamName: string | null
  participants: ProposalParticipant[]
  filledSections: SectionKey[]
  totalSections: number
  missingRequired: SectionKey[]
  activity: ProposalActivity[]
  proposal: IdeaProposal | null
}

export type ViewerRole = 'writer' | 'admin' | 'owner' | null

export interface ProposalState {
  viewerRole: ViewerRole
  canStart: boolean
  /** Approved, but the platform admin has not confirmed it yet - the proposal waits for them. */
  waitingForAdmin: boolean
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

/**
 * The sections a proposal is made of, in reading order. `short` ones are single-line, `choice` is
 * the yes/no of whether the owner pays, and `required` ones must be written before the lead can
 * send it - the server's rule (`reviews.proposals._REQUIRED`), mirrored for the form. The payment
 * plan is required only when the owner pays.
 */
export const PROPOSAL_SECTIONS = [
  { key: 'title', label: 'Title', short: true },
  { key: 'executiveSummary', label: 'Executive summary', required: true },
  { key: 'problem', label: 'Problem', required: true },
  { key: 'feasibility', label: 'Feasibility', required: true },
  { key: 'proposedSolution', label: 'Proposed solution', required: true },
  { key: 'requirementsSummary', label: 'Requirements', required: true },
  { key: 'scope', label: 'Scope', required: true },
  { key: 'deliverables', label: 'Deliverables', required: true },
  { key: 'estimatedTimeline', label: 'Overall timeline', short: true, required: true },
  { key: 'milestones', label: 'Timeline and milestones', required: true },
  { key: 'estimatedEffort', label: 'Estimated effort', short: true },
  { key: 'financialRequirements', label: 'Financial requirements', required: true },
  { key: 'paymentRequired', label: 'Does the owner pay?', choice: true, required: true },
  { key: 'paymentPlan', label: 'Payment plan' },
  { key: 'risks', label: 'Risks' },
  { key: 'assumptions', label: 'Assumptions' },
  { key: 'acceptanceCriteria', label: 'Acceptance criteria', required: true },
] as const

/** Words for the answer to "Does the owner pay?". */
export const PAYMENT_WORDS = {
  yes: 'Yes - the owner pays',
  no: 'No - no charge to the owner',
} as const

/** Whether `key` is part of this proposal: the payment plan only when the owner pays. */
export function sectionApplies(key: string, paymentRequired: string): boolean {
  return key !== 'paymentPlan' || paymentRequired === 'yes'
}

export type SectionKey = (typeof PROPOSAL_SECTIONS)[number]['key']

const FIELDS = `ideaId ideaTitle teamName title executiveSummary problem proposedSolution
  requirementsSummary scope deliverables risks assumptions estimatedEffort estimatedTimeline
  acceptanceCriteria feasibility milestones financialRequirements paymentRequired paymentPlan
  status reviewFeedback submittedAt decidedAt updatedAt`

export async function proposalStateRequest(ideaId: string): Promise<ProposalState> {
  const data = await graphqlClient.request<{ ideaProposalState: ProposalState }>(
    `query($ideaId: ID!) {
       ideaProposalState(ideaId: $ideaId) {
         viewerRole canStart waitingForAdmin canEdit canSubmit canDecide proposal { ${FIELDS} }
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

/** Admin: every approved idea's proposal as far as it has got, with who is writing it. */
export async function proposalProgressRequest(): Promise<ProposalProgress[]> {
  const data = await graphqlClient.request<{ proposalProgress: ProposalProgress[] }>(
    `query {
       proposalProgress {
         ideaId ideaTitle status teamName filledSections totalSections missingRequired
         participants { name email role contributions sections lastContributedAt }
         activity { name action sections at }
         proposal { ${FIELDS} }
       }
     }`,
  )
  return data.proposalProgress
}

/** The label of a section, by its key. */
export function sectionLabel(key: string): string {
  return PROPOSAL_SECTIONS.find((section) => section.key === key)?.label ?? key
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
      not_started: 'Not started',
      draft: 'Being written',
      submitted: 'With the platform admin',
      changes_requested: 'Sent back for changes',
      released: 'Released to the owner',
      declined: 'Declined',
    }[status] ?? status
  )
}

// --- the owner's answers to the released proposal ------------------------------------

export type Agreement = 'yes' | 'discuss'

/** What the owner answered: go ahead (on these terms) or not. */
export interface ProposalAnswer {
  decision: 'proceed' | 'decline'
  timeline: Agreement | ''
  payment: Agreement | ''
  preferredStart: string | null
  conditions: string
  declineReason: string
  answeredByName: string
  answeredAt: string
}

export interface ProposalAnswerInput {
  ideaId: string
  decision: 'proceed' | 'decline'
  timeline?: Agreement | ''
  payment?: Agreement | ''
  preferredStart?: string | null
  conditions?: string
  declineReason?: string
}

const ANSWER_FIELDS =
  'decision timeline payment preferredStart conditions declineReason answeredByName answeredAt'

/** The owner's answers, for those who act on them; null before the owner has answered. */
export async function proposalAnswerRequest(ideaId: string): Promise<ProposalAnswer | null> {
  const data = await graphqlClient.request<{ proposalAnswer: ProposalAnswer | null }>(
    `query($ideaId: ID!) { proposalAnswer(ideaId: $ideaId) { ${ANSWER_FIELDS} } }`,
    { ideaId },
  )
  return data.proposalAnswer
}

/** Answer the released proposal. Going ahead is also the go-ahead itself. */
export async function answerProposalRequest(
  input: ProposalAnswerInput,
): Promise<{ success: boolean; message: string; field: string | null }> {
  const data = await graphqlClient.request<{
    answerProposal: { success: boolean; message: string; field: string | null }
  }>(
    `mutation($input: ProposalAnswerInput!) {
       answerProposal(input: $input) { success message field }
     }`,
    { input: { ...input, preferredStart: input.preferredStart || null } },
  )
  return data.answerProposal
}

export const AGREEMENT_WORDS: Record<Agreement, string> = {
  yes: 'Agreed',
  discuss: 'Needs discussion',
}
