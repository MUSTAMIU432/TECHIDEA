/**
 * GraphQL operations for the Reviews domain: the reads (S3-003) and the two
 * review operations, start and complete (S3-004).
 *
 * Every document the review workspace sends lives here, in the same shape as
 * `ideasApi`. Nothing here decides who may review what: the queue, the history
 * and the capability flags are all answered by the server for the signed-in
 * user, and an answer the user is not entitled to comes back empty rather
 * than as an error. The mutations answer with a `(success, message, field)`
 * payload: a refusal is data to show, not an exception.
 */

import { graphqlClient } from '../../../graphql/client'
import { IDEA_FIELDS, PAGE_INFO_FIELDS, type Idea, type IdeaPage } from '../../ideas/api/ideasApi'

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

// --- operations (S3-004) ---------------------------------------------------

const REVIEW_FIELDS = `
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
`

/**
 * The payload both operations answer with. On success `review` is the review
 * as its reviewer now sees it and `idea` is the idea in its new state, so the
 * client reconciles from the answer rather than guessing.
 */
export interface ReviewMutationResult {
  success: boolean
  message: string
  field: string | null
  review: Review | null
  idea: Idea | null
}

export interface CriterionAssessmentInput {
  criterion: ReviewCriterion
  rating: CriterionRating
  note: string
}

export interface CompleteReviewInput {
  ideaId: string
  reviewId: string
  decision: ReviewDecision
  feedback: string
  assessments: CriterionAssessmentInput[]
}

const START_REVIEW_MUTATION = `
  mutation StartReview($ideaId: ID!) {
    startReview(ideaId: $ideaId) {
      success
      message
      field
      review { ${REVIEW_FIELDS} }
      idea { ${IDEA_FIELDS} }
    }
  }
`

const COMPLETE_REVIEW_MUTATION = `
  mutation CompleteReview($input: CompleteReviewInput!) {
    completeReview(input: $input) {
      success
      message
      field
      review { ${REVIEW_FIELDS} }
      idea { ${IDEA_FIELDS} }
    }
  }
`

/** Start reviewing a submitted idea. The server decides whether this viewer may. */
export async function startReviewRequest(ideaId: string): Promise<ReviewMutationResult> {
  const data = await graphqlClient.request<{ startReview: ReviewMutationResult }>(
    START_REVIEW_MUTATION,
    { ideaId },
  )
  return data.startReview
}

/** Record the decision on the viewer's own open review. */
export async function completeReviewRequest(
  input: CompleteReviewInput,
): Promise<ReviewMutationResult> {
  const data = await graphqlClient.request<{ completeReview: ReviewMutationResult }>(
    COMPLETE_REVIEW_MUTATION,
    { input },
  )
  return data.completeReview
}
