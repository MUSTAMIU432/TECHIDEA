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

/** Which track a review round belongs to. Mirrors `reviews.models.Review.Scope`. */
export type ReviewScope = 'ORGANIZATION' | 'PLATFORM'

/** What a reviewer may decide: the idea status the completed review leads to. */
export type ReviewVerdict = 'CHANGES_REQUESTED' | 'APPROVED' | 'REJECTED'

/**
 * A completed round's decision. `WITHDRAWN` is not a verdict: the round was
 * closed when another reviewer took over from a reviewer who could no longer
 * review, and only the server ever records it.
 */
export type ReviewDecision = ReviewVerdict | 'WITHDRAWN'

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
  /** Which track this round belongs to; rounds are numbered per scope. */
  scope: ReviewScope
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
      scope
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
  scope
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
  decision: ReviewVerdict
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

// --- the platform track -------------------------------------------------------------------
//
// Everything above is the **organization** track: a tenant's own reviewers
// deciding what that organization wants to submit. Everything below is the
// **platform** track, which is a different set of people answering with a
// different permission.
//
// The two are not two views of one review, and the client must not present them
// as one. A platform review is authorized by a platform-scoped Django permission
// and by nothing else - no organization role and no team role reaches it - so a
// tenant's Reviewer has no platform queue at all, and a platform reviewer has no
// organization queue. `platformReviewQueue` returning empty is how this client
// learns somebody is not one; it is not an error to report.

/** The organization's own two decisions. Neither is an approval. */
export type OrganizationVerdict = 'CONFIRMED' | 'CHANGES_REQUESTED'

const PLATFORM_REVIEW_QUEUE_QUERY = `
  query PlatformReviewQueue {
    platformReviewQueue { ${IDEA_FIELDS} }
  }
`

const PLATFORM_INTAKE_QUERY = `
  query PlatformIntake {
    platformIntake { ${IDEA_FIELDS} }
  }
`

const ORGANIZATION_REVIEW_QUEUE_QUERY = `
  query OrganizationReviewQueue($organizationId: ID!) {
    organizationReviewQueue(organizationId: $organizationId) { ${IDEA_FIELDS} }
  }
`

const START_ORGANIZATION_REVIEW_MUTATION = `
  mutation StartOrganizationReview($ideaId: ID!) {
    startOrganizationReview(ideaId: $ideaId) {
      success
      message
      field
      review { ${REVIEW_FIELDS} }
      idea { ${IDEA_FIELDS} }
    }
  }
`

const COMPLETE_ORGANIZATION_REVIEW_MUTATION = `
  mutation CompleteOrganizationReview($input: CompleteOrganizationReviewInput!) {
    completeOrganizationReview(input: $input) {
      success
      message
      field
      review { ${REVIEW_FIELDS} }
      idea { ${IDEA_FIELDS} }
    }
  }
`

const REVIEW_REPORT_QUERY = `
  query IdeaReviewReport($ideaId: ID!) {
    ideaReviewReport(ideaId: $ideaId) {
      id
      ideaId
      reviewId
      round
      decision
      submissionContext
      tenantName
      reviewerId
      reviewerFirstName
      criteria
      reviewSummary
      feedback
      recommendations
      importantConsiderations
      constraints
      nextSteps
      approvalSummary
      approvedAt
      generatedAt
    }
  }
`

const SUBMISSION_VERSIONS_QUERY = `
  query IdeaSubmissionVersions($ideaId: ID!) {
    ideaSubmissionVersions(ideaId: $ideaId) {
      id
      ideaId
      version
      submittedAt
      title
      description
    }
  }
`

/**
 * The platform review & approval report for an idea.
 *
 * **This is the document the author reads before deciding whether to give the
 * go-ahead**, so the client must never present it as the approval: it says what
 * the platform concluded, and the author's own decision is a separate act
 * (`giveGoAhead` in `ideasApi`) with a separate button.
 *
 * Null for an idea with no report, for one that is not approved, and for a
 * viewer who is neither the author nor a platform reviewer - the same answer for
 * all three, so this screen cannot become a way of finding out whether an idea
 * has been approved.
 */
export interface PlatformReviewReport {
  id: string
  ideaId: string
  reviewId: string
  round: number
  decision: string
  submissionContext: string
  tenantName: string
  reviewerId: string
  reviewerFirstName: string
  /** Criterion, rating and note, verbatim. Categorical - never a score. */
  criteria: Array<{ criterion: string; rating: string; note: string }>
  reviewSummary: string
  feedback: string
  recommendations: string
  importantConsiderations: string
  constraints: string
  nextSteps: string
  approvalSummary: string
  approvedAt: string
  generatedAt: string
}

/** One frozen submission: what the platform was given, at a version. */
export interface SubmissionVersion {
  id: string
  ideaId: string
  version: number
  submittedAt: string
  title: string
  description: string
}

/** Submissions waiting for **platform** review. Empty for everybody else. */
export async function platformReviewQueueRequest(): Promise<Idea[]> {
  const data = await graphqlClient.request<{ platformReviewQueue: Idea[] }>(
    PLATFORM_REVIEW_QUEUE_QUERY,
  )
  return data.platformReviewQueue
}

/**
 * Everything in platform intake: submitted, not yet under review.
 *
 * The platform's triage list rather than a reviewer's, so this is gated on the
 * permission to *assign* reviewers - a different permission from the one that
 * lets somebody decide. Empty for a reviewer without it.
 */
export async function platformIntakeRequest(): Promise<Idea[]> {
  const data = await graphqlClient.request<{ platformIntake: Idea[] }>(PLATFORM_INTAKE_QUERY)
  return data.platformIntake
}

/**
 * Ideas waiting for **organization** review in one organization.
 *
 * The same list `reviewQueue` returns, under the name that says which track it
 * is. Both are here because a screen that shows two queues has to ask for two
 * things, and a caller should not have to know which of the two names is legacy.
 */
export async function organizationReviewQueueRequest(organizationId: string): Promise<Idea[]> {
  const data = await graphqlClient.request<{ organizationReviewQueue: Idea[] }>(
    ORGANIZATION_REVIEW_QUEUE_QUERY,
    { organizationId },
  )
  return data.organizationReviewQueue
}

/**
 * Open the organization review round.
 *
 * Authorized by `idea.review` in the idea's *own* organization, plus being able
 * to read it and not having written it. `CONFIRMED` and `CHANGES_REQUESTED` are
 * both made here afterwards; neither submits anything to the platform.
 */
export async function startOrganizationReviewRequest(
  ideaId: string,
): Promise<ReviewMutationResult> {
  const data = await graphqlClient.request<{ startOrganizationReview: ReviewMutationResult }>(
    START_ORGANIZATION_REVIEW_MUTATION,
    { ideaId },
  )
  return data.startOrganizationReview
}

export interface CompleteOrganizationReviewInput {
  ideaId: string
  reviewId: string
  decision: OrganizationVerdict
  feedback: string
  assessments?: CriterionAssessmentInput[]
}

/** Record the organization's decision and move the idea. All of it, or none. */
export async function completeOrganizationReviewRequest(
  input: CompleteOrganizationReviewInput,
): Promise<ReviewMutationResult> {
  const data = await graphqlClient.request<{ completeOrganizationReview: ReviewMutationResult }>(
    COMPLETE_ORGANIZATION_REVIEW_MUTATION,
    { input },
  )
  return data.completeOrganizationReview
}

/** The report, or null for an idea without one and for a viewer who may not see it. */
export async function ideaReviewReportRequest(
  ideaId: string,
): Promise<PlatformReviewReport | null> {
  const data = await graphqlClient.request<{ ideaReviewReport: PlatformReviewReport | null }>(
    REVIEW_REPORT_QUERY,
    { ideaId },
  )
  return data.ideaReviewReport
}

/**
 * The idea's frozen submissions, oldest version first.
 *
 * A resubmission after changes creates the *next* version and leaves the earlier
 * ones alone, so this is the auditable history of what was submitted - and the
 * report is about one of them specifically.
 */
export async function ideaSubmissionVersionsRequest(ideaId: string): Promise<SubmissionVersion[]> {
  const data = await graphqlClient.request<{ ideaSubmissionVersions: SubmissionVersion[] }>(
    SUBMISSION_VERSIONS_QUERY,
    { ideaId },
  )
  return data.ideaSubmissionVersions
}

const TEAM_REVIEW_QUEUE_QUERY = `
  query TeamReviewQueue($teamId: ID!) {
    teamReviewQueue(teamId: $teamId) { ${IDEA_FIELDS} }
  }
`

/**
 * Team ideas waiting for the caller to verify, oldest first. Empty unless the
 * caller is one of the team's reviewers, and it never lists their own ideas.
 */
export async function teamReviewQueueRequest(teamId: string): Promise<Idea[]> {
  const data = await graphqlClient.request<{ teamReviewQueue: Idea[] }>(TEAM_REVIEW_QUEUE_QUERY, {
    teamId,
  })
  return data.teamReviewQueue
}
