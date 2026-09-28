import type { CriterionRating, ReviewCriterion, ReviewDecision } from '../api/reviewsApi'

/**
 * Human labels for the review vocabulary. Presentation only: which values
 * exist, and which a viewer may see, is the server's answer.
 */

export const DECISION_LABELS: Record<ReviewDecision, string> = {
  CHANGES_REQUESTED: 'Changes requested',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
}

export const CRITERION_LABELS: Record<ReviewCriterion, string> = {
  PROBLEM_CLARITY: 'Problem clarity',
  AUTOMATION_SUITABILITY: 'Automation suitability',
  FEASIBILITY: 'Feasibility',
  EXPECTED_BENEFIT: 'Expected benefit',
  EVIDENCE: 'Evidence',
}

export const RATING_LABELS: Record<CriterionRating, string> = {
  MEETS: 'Meets',
  PARTIALLY_MEETS: 'Partially meets',
  DOES_NOT_MEET: 'Does not meet',
  NOT_APPLICABLE: 'Not applicable',
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}
