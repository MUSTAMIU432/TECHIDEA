import type { IdeaStatus, IdeaVisibility } from '../api/ideasApi'

/**
 * The lifecycle, as the UI needs it: a human label for each state, and the
 * label for the button that performs each transition.
 *
 * Every entry here is presentation. Which moves are *legal* comes from the
 * backend - `Idea.availableTransitions`, computed from the same transition
 * matrix that enforces the change - so this module never decides that a
 * particular move is allowed. It only decides what to call it, and a state
 * missing from these records renders as its raw enum name rather than being
 * hidden: a vocabulary the backend knows and this file does not is a
 * documentation bug here, not a reason to show somebody nothing.
 */

export const STATUS_LABELS: Record<IdeaStatus, string> = {
  DRAFT: 'Draft',
  SUBMITTED: 'Submitted',
  UNDER_REVIEW: 'Under review',
  CHANGES_REQUESTED: 'Changes requested',
  REJECTED: 'Rejected',
  APPROVED: 'Approved',
  AUTOMATION_PROPOSAL: 'Automation proposal',
}

export const STATUS_DESCRIPTIONS: Record<IdeaStatus, string> = {
  DRAFT: 'Only you can edit this. Who can read it is set by its visibility.',
  SUBMITTED: 'Put forward. Waiting for a reviewer to pick it up.',
  UNDER_REVIEW: 'A reviewer is looking at it.',
  CHANGES_REQUESTED:
    'Sent back to you with changes to make. Revise it, then submit it again when it is ready for the next review.',
  REJECTED: 'Not going forward.',
  APPROVED: 'Accepted as worth automating.',
  AUTOMATION_PROPOSAL: 'Handed to the automation-opportunity track.',
}

export const VISIBILITY_LABELS: Record<IdeaVisibility, string> = {
  PUBLIC: 'Everyone on the platform',
  ORGANIZATION: 'This organization',
  DEPARTMENT: 'A department',
  PRIVATE: 'Only you',
}

/** The verb on the button that performs each transition. */
export const TRANSITION_LABELS: Partial<Record<IdeaStatus, string>> = {
  SUBMITTED: 'Submit for review',
  UNDER_REVIEW: 'Start review',
  CHANGES_REQUESTED: 'Request changes',
  APPROVED: 'Approve',
  REJECTED: 'Reject',
  AUTOMATION_PROPOSAL: 'Hand off',
}

export function statusLabel(status: IdeaStatus): string {
  return STATUS_LABELS[status] ?? status
}

export function statusDescription(status: IdeaStatus): string {
  return STATUS_DESCRIPTIONS[status] ?? ''
}

export function visibilityLabel(visibility: IdeaVisibility): string {
  return VISIBILITY_LABELS[visibility] ?? visibility
}

/**
 * The label for a transition, or `null` when there is nothing to offer.
 *
 * `AUTOMATION_PROPOSAL` deliberately has no button: the transition exists so
 * the lifecycle is complete, and nothing in the product acts on an idea once
 * it reaches that state. Offering a button that leads nowhere would be worse
 * than not offering it, so a reviewer approves and stops there.
 */
export function transitionLabel(target: IdeaStatus): string | null {
  if (target === 'AUTOMATION_PROPOSAL') return null
  return TRANSITION_LABELS[target] ?? null
}

/** Tailwind classes per status, so a state is recognisable at a glance. */
export function statusClasses(status: IdeaStatus): string {
  switch (status) {
    case 'DRAFT':
      return 'bg-gray-100 text-gray-700'
    case 'SUBMITTED':
      return 'bg-blue-50 text-blue-700'
    case 'UNDER_REVIEW':
      return 'bg-amber-50 text-amber-800'
    case 'CHANGES_REQUESTED':
      return 'bg-orange-50 text-orange-800'
    case 'REJECTED':
      return 'bg-red-50 text-red-700'
    case 'APPROVED':
      return 'bg-green-50 text-green-800'
    case 'AUTOMATION_PROPOSAL':
      return 'bg-purple-50 text-purple-800'
    default:
      return 'bg-gray-100 text-gray-700'
  }
}
