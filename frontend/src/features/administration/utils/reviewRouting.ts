/** Routing an idea to a platform review team: shared by the idea list and the idea page. */

/** The element id the idea list links to, so the idea page opens on the review-team card. */
export const ASSIGN_REVIEW_TEAM_ANCHOR = 'assign-review-team'

/**
 * Whether an idea is waiting for somebody to route it: it has reached the platform, no
 * review round is open on it, and no review team has it yet.
 */
export function awaitsReviewTeam(status: string, reviewTeam: { id: string } | null): boolean {
  return (status === 'SUBMITTED' || status === 'CHANGES_REQUESTED') && reviewTeam === null
}
