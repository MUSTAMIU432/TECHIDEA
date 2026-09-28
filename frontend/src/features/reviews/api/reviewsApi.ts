/**
 * GraphQL operations for the Reviews domain (S3-003): read-only.
 *
 * Every document the review workspace sends lives here, in the same shape as
 * `ideasApi`. Nothing here decides who may review what: the queue, the history
 * and the capability flags are all answered by the server for the signed-in
 * user, and an answer the user is not entitled to comes back empty rather
 * than as an error. There are no mutations yet: claiming a review and
 * recording a decision arrive in S3-004.
 */

import { graphqlClient } from '../../../graphql/client'
import { IDEA_FIELDS, PAGE_INFO_FIELDS, type IdeaPage } from '../../ideas/api/ideasApi'

export type ReviewDecision = 'CHANGES_REQUESTED' | 'APPROVED' | 'REJECTED'

export type ReviewCriterion =
  | 'PROBLEM_CLARITY'
  | 'AUTOMATION_SUITABILITY'
  | 'FEASIBILITY'
  | 'EXPECTED_BENEFIT'
  | 'EVIDENCE'

export type CriterionRating = 'MEETS' | 'PARTIALLY_MEETS' | 'DOES_NOT_MEET' | 'NOT_APPLICABLE'

export interface CriterionAssessment {
  criterion: ReviewCriterion
  rating: CriterionRating
  note: string
}

/**
 * One review round. `decision` and `completedAt` are null while the round is
 * in progress, which only reviewers are ever shown; an author receives
 * completed rounds only. `submissionSnapshot` is null unless the viewer is a
 * reviewer of the idea.
 */
export interface Review {
  id: string
  ideaId: string
  round: number
  reviewerId: string
  decision: ReviewDecision | null
  feedback: string
  assessments: CriterionAssessment[]
  createdAt: string
  completedAt: string | null
  submissionSnapshot: Record<string, unknown> | null
}

export interface ReviewQueuePageRequest {
  offset?: number
  limit?: number
}

const REVIEW_QUEUE_QUERY = `
  query ReviewQueue($organizationId: ID!, $offset: Int, $limit: Int) {
    reviewQueue(organizationId: $organizationId, offset: $offset, limit: $limit) {
      items { ${IDEA_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const IDEA_REVIEWS_QUERY = `
  query IdeaReviews($ideaId: ID!) {
    ideaReviews(ideaId: $ideaId) {
      id
      ideaId
      round
      reviewerId
      decision
      feedback
      createdAt
      completedAt
      submissionSnapshot
      assessments { criterion rating note }
    }
  }
`

const VIEWER_CAN_REVIEW_IN_QUERY = `
  query ViewerCanReviewIn($organizationId: ID!) {
    viewerCanReviewIn(organizationId: $organizationId)
  }
`

/**
 * One page of the ideas waiting for review in an organization, oldest first.
 *
 * Empty for anybody who is not a reviewer there - the server does not say
 * why, so an empty page is not by itself proof of an empty queue. Use
 * `viewerCanReviewInRequest` to tell the two apart.
 */
export async function reviewQueueRequest(
  organizationId: string,
  page: ReviewQueuePageRequest = {},
): Promise<IdeaPage> {
  const variables: Record<string, unknown> = { organizationId }
  if (page.offset !== undefined) variables.offset = page.offset
  if (page.limit !== undefined) variables.limit = page.limit
  const data = await graphqlClient.request<{ reviewQueue: IdeaPage }>(REVIEW_QUEUE_QUERY, variables)
  return data.reviewQueue
}

/** An idea's review history the signed-in user may see, in round order. */
export async function ideaReviewsRequest(ideaId: string): Promise<Review[]> {
  const data = await graphqlClient.request<{ ideaReviews: Review[] }>(IDEA_REVIEWS_QUERY, {
    ideaId,
  })
  return data.ideaReviews
}

/** Whether the signed-in user reviews ideas in this organization. */
export async function viewerCanReviewInRequest(organizationId: string): Promise<boolean> {
  const data = await graphqlClient.request<{ viewerCanReviewIn: boolean }>(
    VIEWER_CAN_REVIEW_IN_QUERY,
    { organizationId },
  )
  return data.viewerCanReviewIn
}
