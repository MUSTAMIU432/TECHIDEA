import type { IdeaStatus, IdeaVisibility, SubmissionContext } from '../api/ideasApi'

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
  SUBMITTED_TO_ORGANIZATION: 'Waiting for your organization',
  ORGANIZATION_CHANGES_REQUESTED: 'Changes asked for by your organization',
  ORGANIZATION_CONFIRMED: 'Confirmed by your organization',
  SUBMITTED: 'Submitted',
  UNDER_REVIEW: 'Under review',
  CHANGES_REQUESTED: 'Changes requested',
  REJECTED: 'Rejected',
  // Deliberately **not** "Approved" on its own: platform approval is one thing
  // and the author's go-ahead is another, and this client renders the second as
  // the thing that is still outstanding. The server's own wording
  // (`states.friendly_label`) says the same, and `Idea.state.shortLabel` is
  // preferred wherever it is available.
  APPROVED: 'Platform approved — your confirmation needed',
  READY_FOR_IMPLEMENTATION: 'Ready for implementation',
  AUTOMATION_PROPOSAL: 'Automation proposal',
}

export const STATUS_DESCRIPTIONS: Record<IdeaStatus, string> = {
  DRAFT: 'Only you can edit this. Who can read it is set by its visibility.',
  SUBMITTED_TO_ORGANIZATION:
    'Your organization is checking this represents what it wants to put forward. It has not reached the platform yet.',
  ORGANIZATION_CHANGES_REQUESTED:
    'Your organization asked for changes. Make them, then send it back to your organization.',
  ORGANIZATION_CONFIRMED:
    'Your organization confirmed this is what it wants to submit. Read it once more, then submit it to the platform.',
  SUBMITTED: 'Put forward to the platform. Waiting for a reviewer to pick it up.',
  UNDER_REVIEW: 'A platform reviewer is looking at it.',
  CHANGES_REQUESTED:
    'Sent back to you with changes to make. Revise it, then submit it again when it is ready for the next review.',
  REJECTED: 'Not going forward.',
  APPROVED:
    'The platform approved it and wrote a report. Read the report, then decide whether to give the go-ahead — that decision is yours, and nobody else’s.',
  READY_FOR_IMPLEMENTATION:
    'You gave the go-ahead. No developer has been selected and no work has started yet.',
  AUTOMATION_PROPOSAL: 'Handed to the automation-opportunity track.',
}

/** The three ways an idea can be put forward, in the words the picker uses. */
export const CONTEXT_LABELS: Record<SubmissionContext, string> = {
  INDIVIDUAL: 'Just me',
  TEAM: 'My team',
  ORGANIZATION: 'My organization',
}

/** One line explaining where each context's idea goes next. */
export const CONTEXT_DESCRIPTIONS: Record<SubmissionContext, string> = {
  INDIVIDUAL:
    'Filed by you alone. The platform reviews it directly — there is no organization to check it first.',
  TEAM: 'Filed by you and your team together. The platform reviews it directly.',
  ORGANIZATION:
    'Your organization checks it represents what the organization wants to submit before the platform sees it.',
}

export function contextLabel(context: SubmissionContext): string {
  return CONTEXT_LABELS[context] ?? context
}

export function contextDescription(context: SubmissionContext): string {
  return CONTEXT_DESCRIPTIONS[context] ?? ''
}

/** Which review track a state belongs to, for wording and for the stage trail. */
export function trackOf(status: IdeaStatus): 'organization' | 'platform' | null {
  if (
    status === 'SUBMITTED_TO_ORGANIZATION' ||
    status === 'ORGANIZATION_CHANGES_REQUESTED' ||
    status === 'ORGANIZATION_CONFIRMED'
  ) {
    return 'organization'
  }
  if (
    status === 'SUBMITTED' ||
    status === 'UNDER_REVIEW' ||
    status === 'CHANGES_REQUESTED' ||
    status === 'REJECTED' ||
    status === 'APPROVED'
  ) {
    return 'platform'
  }
  return status === 'DRAFT' ||
    status === 'READY_FOR_IMPLEMENTATION' ||
    status === 'AUTOMATION_PROPOSAL'
    ? null
    : null
}

export const VISIBILITY_LABELS: Record<IdeaVisibility, string> = {
  PUBLIC: 'Everyone on the platform',
  ORGANIZATION: 'This organization',
  DEPARTMENT: 'A department',
  PRIVATE: 'Only you',
}

/**
 * The verb on the button that performs each transition.
 *
 * `SUBMITTED_TO_ORGANIZATION` reads "Send to my organization" and `SUBMITTED`
 * reads "Submit to the platform" because the *same* author action opens a
 * different door in the two contexts. One generic "Submit" would leave a reader
 * having to remember which of the two they are in, and the whole submission
 * context split exists so they do not have to.
 *
 * The review-owned targets are absent on purpose: starting and deciding a review
 * happen in the review workspace, so `transitionIdea` refuses them and this
 * client must never offer them as a plain status change.
 */
export const TRANSITION_LABELS: Partial<Record<IdeaStatus, string>> = {
  SUBMITTED_TO_ORGANIZATION: 'Send to my organization',
  SUBMITTED: 'Submit to the platform',
  // The author re-submitting after each track asked for changes. The wording is
  // the same on purpose: pressing it sends the idea back where it came from,
  // and the idea's own state says which track that is.
  CHANGES_REQUESTED: 'Submit again',
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
 * `null` for every review-owned move and for the developer hand-off: the first
 * group is made in the review workspace because it must leave a `Review` row,
 * and `AUTOMATION_PROPOSAL` leads nowhere in this product - Sprint 4 acts on it.
 * Offering either as a plain status change would be offering a button the server
 * refuses (`transition_idea` says so), so the rule is "no label" rather than a
 * second list of buttons that do not work.
 */
export function transitionLabel(target: IdeaStatus): string | null {
  return TRANSITION_LABELS[target] ?? null
}

/** Whether this move is the author's own re-submission, for the button's wording. */
export function isResubmission(status: IdeaStatus): boolean {
  return status === 'CHANGES_REQUESTED' || status === 'ORGANIZATION_CHANGES_REQUESTED'
}

/** Tailwind classes per status, so a state is recognisable at a glance. */
/**
 * Tailwind classes per status, so a state is recognisable at a glance.
 *
 * The organization stage gets its own colours rather than borrowing the platform
 * ones: a reader who cannot tell "waiting for my organization" from "waiting for
 * the platform" from a badge colour has learned nothing from the badge, and those
 * two are the states this split exists to separate. `APPROVED` stays amber rather
 * than green because the author's own decision is still outstanding — green is
 * reserved for `READY_FOR_IMPLEMENTATION`, where nothing is left to do.
 */
export function statusClasses(status: IdeaStatus): string {
  switch (status) {
    case 'DRAFT':
      return 'bg-gray-100 text-gray-700'
    case 'SUBMITTED_TO_ORGANIZATION':
      return 'bg-teal-50 text-teal-800'
    case 'ORGANIZATION_CHANGES_REQUESTED':
      return 'bg-orange-50 text-orange-800'
    case 'ORGANIZATION_CONFIRMED':
      return 'bg-teal-100 text-teal-900'
    case 'SUBMITTED':
      return 'bg-blue-50 text-blue-700'
    case 'UNDER_REVIEW':
      return 'bg-amber-50 text-amber-800'
    case 'CHANGES_REQUESTED':
      return 'bg-orange-50 text-orange-800'
    case 'REJECTED':
      return 'bg-red-50 text-red-700'
    case 'APPROVED':
      return 'bg-amber-100 text-amber-900'
    case 'READY_FOR_IMPLEMENTATION':
      return 'bg-green-50 text-green-800'
    case 'AUTOMATION_PROPOSAL':
      return 'bg-purple-50 text-purple-800'
    default:
      return 'bg-gray-100 text-gray-700'
  }
}

/**
 * The word for who is reviewing it now, for a card's second line.
 *
 * Null where nobody is: a draft and a handed-over idea are not waiting on a
 * review, and saying "with your organization" about either would be a lie.
 */
export function reviewerLabelFor(status: IdeaStatus): string | null {
  switch (status) {
    case 'SUBMITTED_TO_ORGANIZATION':
    case 'ORGANIZATION_CHANGES_REQUESTED':
    case 'ORGANIZATION_CONFIRMED':
      return 'With your organization'
    case 'SUBMITTED':
    case 'UNDER_REVIEW':
      return 'With the platform'
    case 'CHANGES_REQUESTED':
    case 'REJECTED':
    case 'APPROVED':
      return 'Platform review done'
    default:
      return null
  }
}
