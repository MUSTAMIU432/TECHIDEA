/**
 * A platform reviewer's work list: every idea they or their review team is handling, where
 * it stands, and the page where the next action is taken. Read-only.
 */

import { graphqlClient } from '../../../graphql/client'

export interface WorkItem {
  ideaId: string
  title: string
  ownerLabel: string
  stage: string
  stageLabel: string
  /** Index into `steps`: how far along the journey the idea is. */
  step: number
  needsMe: boolean
  actionLabel: string | null
  actionPath: string | null
  teamName: string | null
  isLead: boolean
  updatedAt: string
}

export interface ReviewerWork {
  steps: string[]
  items: WorkItem[]
}

export async function reviewerWorkRequest(): Promise<ReviewerWork> {
  const data = await graphqlClient.request<{
    reviewerWorkSteps: string[]
    reviewerWork: WorkItem[]
  }>(
    `query {
       reviewerWorkSteps
       reviewerWork {
         ideaId title ownerLabel stage stageLabel step needsMe actionLabel actionPath
         teamName isLead updatedAt
       }
     }`,
  )
  return { steps: data.reviewerWorkSteps, items: data.reviewerWork }
}
