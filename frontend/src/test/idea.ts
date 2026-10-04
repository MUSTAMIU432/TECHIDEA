import type { Idea, IdeaState, SubmissionContext } from '../features/ideas/api/ideasApi'
import type { Team } from '../features/teams/api/teamsApi'
import { EMPTY_PROBLEM_STORY } from '../features/ideas/utils/problemStory'

/**
 * One complete `Idea`, for tests.
 *
 * Eleven test files each used to hand-write an idea, and a field added to the
 * type meant editing all of them. That is not only tedious: it is the point at
 * which a test file stops reading as "what the server promises an idea looks
 * like" and becomes a list of fields its author happened to know about. So the
 * shape lives here once, and every fixture is `makeIdea({ status: 'APPROVED' })`.
 *
 * The defaults are a **draft organization idea** for an organization the signed
 * in user (id `7`) is not the author of, because that is the case most tests
 * are about - somebody else's draft in a tenant - and every field that is
 * context-dependent is therefore at its most common value. Tests about the other
 * two contexts say so by overriding `submissionContext` and the tenant ids,
 * which is also what makes them obvious to a reader.
 */

/**
 * The server's own wording for a state, so a test that renders one does not
 * have to also know what the label should be. Mirrors `ideas.states`: the
 * client is handed both, and this is the pair a test would otherwise have to
 * keep in step.
 */
function stateFor(status: IdeaState['status']): IdeaState {
  return {
    status,
    label: `Label for ${status}`,
    shortLabel: status.slice(0, 4),
    tone: 'NEUTRAL',
    stage: 'AUTHORING',
    primaryAction: 'NOTHING_TO_DO',
    primaryActionLabel: '',
    isLocked: false,
    isTerminal: false,
    stageIndex: 1,
    stageCount: 4,
  }
}

export const IDEA_ORGANIZATION_ID = '3'
export const IDEA_TEAM_ID = '9'
export const SIGNED_IN_USER_ID = '7'

/** One team the signed-in user is in, for the contexts that can name one. */
export function makeTeam(overrides: Partial<Team> = {}): Team {
  return {
    id: IDEA_TEAM_ID,
    name: 'Registrar',
    slug: 'registrar',
    description: 'The people who allocate rooms.',
    ownerId: SIGNED_IN_USER_ID,
    memberCount: 2,
    createdAt: '2026-01-01T00:00:00.000Z',
    ...overrides,
  }
}

export function makeIdea(overrides: Partial<Idea> = {}): Idea {
  const submissionContext: SubmissionContext = overrides.submissionContext ?? 'ORGANIZATION'
  const status = overrides.status ?? 'DRAFT'

  return {
    ...EMPTY_PROBLEM_STORY,
    id: '1',
    title: 'Automate the invoice run',
    description: 'A description long enough.',
    status,
    visibility: 'PRIVATE',
    submittedAt: null,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    authorId: '7',
    organizationId: submissionContext === 'ORGANIZATION' ? IDEA_ORGANIZATION_ID : null,
    teamId: submissionContext === 'TEAM' ? IDEA_TEAM_ID : null,
    submissionContext,
    tenantName: submissionContext === 'ORGANIZATION' ? 'Acme Labs' : '',
    ownerGoAheadAt: null,
    platformApprovedAt: null,
    platformVersion: 0,
    platformLockedAt: null,
    state: stateFor(status),
    category: null,
    availableTransitions: [],
    discussionOpen: true,
    voteCount: 0,
    viewerHasVoted: false,
    viewerCanStartReview: false,
    viewerCanStartOrganizationReview: false,
    viewerActiveReviewId: null,
    ...overrides,
  }
}

/** An idea already with the platform, so a report can exist for it. */
export function makeApprovedIdea(overrides: Partial<Idea> = {}): Idea {
  return makeIdea({
    status: 'APPROVED',
    submittedAt: '2026-01-02T00:00:00.000Z',
    platformVersion: 1,
    platformLockedAt: '2026-01-02T00:00:00.000Z',
    platformApprovedAt: '2026-01-03T00:00:00.000Z',
    ...overrides,
  })
}
