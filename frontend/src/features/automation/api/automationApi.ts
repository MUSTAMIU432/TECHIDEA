/**
 * GraphQL operations for the automation delivery lifecycle (Sprint 4).
 *
 * Every mutation here is an explicit business action and resolves to a payload
 * `{ success, message, field, ... }`: a refusal is an answer, never an exception,
 * so a screen shows the server's own wording next to the field it names. Only a
 * transport failure rejects. The server is the authority on every rule; the
 * `capabilities` it returns decide which buttons are drawn, nothing more.
 */

import { graphqlClient } from '../../../graphql/client'

export type Priority = 'high' | 'medium' | 'low'

export interface OpportunityCapabilities {
  canManageRequirements: boolean
  canEditSolution: boolean
  canTransition: boolean
}

export interface Opportunity {
  id: string
  ideaId: string
  ideaTitle: string
  title: string
  summary: string
  problemStatement: string
  automationGoal: string
  expectedBenefit: string
  priority: Priority
  status: string
  submissionContext: 'individual' | 'team' | 'organization'
  ownerName: string
  tenantName: string | null
  approvedAt: string | null
  readyAt: string | null
  createdAt: string
  updatedAt: string
  capabilities: OpportunityCapabilities
}

export interface Requirement {
  id: string
  opportunityId: string
  title: string
  description: string
  type: string
  priority: Priority
  status: string
  acceptanceCriteria: string
  createdAt: string
  updatedAt: string
}

export interface Solution {
  opportunityId: string
  summary: string
  businessWorkflow: string
  inScope: string
  outOfScope: string
  systemsIntegrations: string
  technicalConsiderations: string
  assumptions: string
  constraints: string
  risks: string
  expectedOutput: string
  updatedAt: string
}

export interface ActivityEvent {
  id: string
  action: string
  entityType: string
  fromStatus: string
  toStatus: string
  actorId: string
  createdAt: string
}

export interface Proposal {
  id: string
  opportunityId: string
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
  paymentRequired: string
  paymentPlan: string
  status: string
  reviewFeedback: string
  submittedAt: string | null
  reviewedAt: string | null
}

export interface Assignment {
  id: string
  opportunityId: string
  assigneeName: string
  status: string
  assignedAt: string
}

export interface PipelineStage {
  stage: string
  state: 'done' | 'current' | 'pending'
}

export interface Project {
  id: string
  opportunityId: string
  ideaId: string
  title: string
  description: string
  status: string
  submissionContext: string
  ownerName: string
  tenantName: string | null
  assignedName: string
  startDate: string | null
  targetDate: string | null
  completedAt: string | null
  progress: {
    stages: PipelineStage[]
    taskCompletionPercent: number | null
    tasksTotal: number
    tasksDone: number
    tasksBlocked: number
  }
  capabilities: {
    canManage: boolean
    canPerformUat: boolean
    canRecordImpact: boolean
  }
}

export interface Milestone {
  id: string
  projectId: string
  title: string
  description: string
  dueDate: string | null
  status: string
  completedAt: string | null
}

export interface Task {
  id: string
  projectId: string
  milestoneId: string | null
  title: string
  description: string
  status: string
  priority: Priority
  dueDate: string | null
}

export interface TestCase {
  id: string
  title: string
  description: string
  expectedResult: string
  actualResult: string
  status: string
  isRequired: boolean
  executedAt: string | null
  notes: string
}

export interface UatRecord {
  id: string
  scenario: string
  expectedOutcome: string
  actualOutcome: string
  result: string
  isRequired: boolean
  feedback: string
  recordedAt: string | null
  createdAt: string
}

export interface Deployment {
  id: string
  environment: string
  version: string
  status: string
  deploymentDate: string | null
  deploymentNotes: string
  rollbackInformation: string
  verifiedAt: string | null
}

export interface ImpactResults {
  timeSavedPercent: string | null
  peopleReduced: string | null
  errorReductionPercent: string | null
  workloadReductionPercent: string | null
  costDifference: string | null
  requestsDifference: string | null
  estimatedHoursSavedPerWeek: string | null
  measuredHoursSavedPerWeek: string | null
  hoursSavedVariance: string | null
}

export type ImpactValues = Record<string, string | null>

export interface Impact {
  id: string
  status: string
  qualitativeOutcome: string
  notes: string
  usersAffected: number | null
  beforeProcessingMinutes: string | null
  afterProcessingMinutes: string | null
  beforePeopleInvolved: number | null
  afterPeopleInvolved: number | null
  beforeErrorRate: string | null
  afterErrorRate: string | null
  beforeCost: string | null
  afterCost: string | null
  estimatedHoursSavedPerWeek: string | null
  measuredHoursSavedPerWeek: string | null
  estimatedCostSavings: string | null
  measuredCostSavings: string | null
  results: ImpactResults
}

export interface Traceability {
  ideaId: string
  opportunityId: string | null
  proposalId: string | null
  projectId: string | null
  deploymentId: string | null
  impactId: string | null
}

export interface Outcome {
  success: boolean
  message: string
  field: string | null
}

export type Payload<K extends string, T> = Outcome & { [P in K]: T | null }

// --- field selections ---------------------------------------------------------

const OPPORTUNITY = `id ideaId ideaTitle title summary problemStatement automationGoal
  expectedBenefit priority status submissionContext ownerName tenantName approvedAt readyAt
  createdAt updatedAt capabilities { canManageRequirements canEditSolution canTransition }`
const REQUIREMENT = `id opportunityId title description type priority status acceptanceCriteria
  createdAt updatedAt`
const SOLUTION = `opportunityId summary businessWorkflow inScope outOfScope systemsIntegrations
  technicalConsiderations assumptions constraints risks expectedOutput updatedAt`
const PROPOSAL = `id opportunityId title executiveSummary problem proposedSolution
  requirementsSummary scope deliverables risks assumptions estimatedEffort estimatedTimeline
  acceptanceCriteria feasibility milestones financialRequirements paymentRequired paymentPlan
  status reviewFeedback submittedAt reviewedAt`
const PROJECT = `id opportunityId ideaId title description status submissionContext ownerName
  tenantName assignedName startDate targetDate completedAt
  progress { stages { stage state } taskCompletionPercent tasksTotal tasksDone tasksBlocked }
  capabilities { canManage canPerformUat canRecordImpact }`
const MILESTONE = 'id projectId title description dueDate status completedAt'
const TASK = 'id projectId milestoneId title description status priority dueDate'
const TEST_CASE =
  'id title description expectedResult actualResult status isRequired executedAt notes'
const UAT = `id scenario expectedOutcome actualOutcome result isRequired feedback recordedAt
  createdAt`
const DEPLOYMENT =
  'id environment version status deploymentDate deploymentNotes rollbackInformation verifiedAt'
const IMPACT = `id status qualitativeOutcome notes usersAffected beforeProcessingMinutes
  afterProcessingMinutes beforePeopleInvolved afterPeopleInvolved beforeErrorRate afterErrorRate
  beforeCost afterCost estimatedHoursSavedPerWeek measuredHoursSavedPerWeek estimatedCostSavings
  measuredCostSavings results { timeSavedPercent peopleReduced errorReductionPercent
  workloadReductionPercent costDifference requestsDifference estimatedHoursSavedPerWeek
  measuredHoursSavedPerWeek hoursSavedVariance }`

// --- helpers ------------------------------------------------------------------------

async function query<T>(root: string, decl: string, args: string, selection: string, vars = {}) {
  const document = `query ${decl ? `(${decl})` : ''} { ${root}${args ? `(${args})` : ''} ${
    selection ? `{ ${selection} }` : ''
  } }`
  const data = await graphqlClient.request<Record<string, T>>(document, vars)
  return data[root]
}

/** One mutation as a function: `root(args) { success message field <selection> }`. */
function action<K extends string, T>(root: string, decl: string, args: string, selection: string) {
  const document = `mutation (${decl}) { ${root}(${args}) { success message field ${selection} } }`
  return async (vars: Record<string, unknown>): Promise<Payload<K, T>> => {
    const data = await graphqlClient.request<Record<string, Payload<K, T>>>(document, vars)
    return data[root]
  }
}

const ID = (name: string) => `$${name}: ID!`

// --- reads ----------------------------------------------------------------------------

export const opportunitiesRequest = (status?: string) =>
  query<Opportunity[]>(
    'automationOpportunities',
    '$status: String',
    'status: $status',
    OPPORTUNITY,
    { status: status ?? null },
  )
export const opportunityRequest = (id: string) =>
  query<Opportunity | null>('automationOpportunity', ID('id'), 'id: $id', OPPORTUNITY, { id })
export const opportunityForIdeaRequest = (ideaId: string) =>
  query<Opportunity | null>(
    'automationOpportunityForIdea',
    ID('ideaId'),
    'ideaId: $ideaId',
    OPPORTUNITY,
    { ideaId },
  )
export const requirementsRequest = (opportunityId: string) =>
  query<Requirement[]>(
    'requirements',
    ID('opportunityId'),
    'opportunityId: $opportunityId',
    REQUIREMENT,
    {
      opportunityId,
    },
  )
export const solutionRequest = (opportunityId: string) =>
  query<Solution | null>(
    'opportunitySolution',
    ID('opportunityId'),
    'opportunityId: $opportunityId',
    SOLUTION,
    { opportunityId },
  )
export const activityRequest = (opportunityId: string) =>
  query<ActivityEvent[]>(
    'opportunityActivity',
    ID('opportunityId'),
    'opportunityId: $opportunityId',
    'id action entityType fromStatus toStatus actorId createdAt',
    { opportunityId },
  )
export const proposalRequest = (opportunityId: string) =>
  query<Proposal | null>(
    'proposal',
    ID('opportunityId'),
    'opportunityId: $opportunityId',
    PROPOSAL,
    {
      opportunityId,
    },
  )
export const developerQueueRequest = () =>
  query<Opportunity[]>('developerQueue', '', '', OPPORTUNITY)
export const assignmentsRequest = (opportunityId: string) =>
  query<Assignment[]>(
    'opportunityAssignments',
    ID('opportunityId'),
    'opportunityId: $opportunityId',
    'id opportunityId assigneeName status assignedAt',
    { opportunityId },
  )
export const projectsRequest = (status?: string) =>
  query<Project[]>('projects', '$status: String', 'status: $status', PROJECT, {
    status: status ?? null,
  })
export const projectRequest = (id: string) =>
  query<Project | null>('project', ID('id'), 'id: $id', PROJECT, { id })
export const projectForOpportunityRequest = (opportunityId: string) =>
  query<Project | null>(
    'projectForOpportunity',
    ID('opportunityId'),
    'opportunityId: $opportunityId',
    PROJECT,
    { opportunityId },
  )
export const milestonesRequest = (projectId: string) =>
  query<Milestone[]>('projectMilestones', ID('projectId'), 'projectId: $projectId', MILESTONE, {
    projectId,
  })
export const tasksRequest = (projectId: string) =>
  query<Task[]>('projectTasks', ID('projectId'), 'projectId: $projectId', TASK, { projectId })
export const testCasesRequest = (projectId: string) =>
  query<TestCase[]>('projectTestCases', ID('projectId'), 'projectId: $projectId', TEST_CASE, {
    projectId,
  })
export const uatRecordsRequest = (projectId: string) =>
  query<UatRecord[]>('projectUatRecords', ID('projectId'), 'projectId: $projectId', UAT, {
    projectId,
  })
export const deploymentsRequest = (projectId: string) =>
  query<Deployment[]>('projectDeployments', ID('projectId'), 'projectId: $projectId', DEPLOYMENT, {
    projectId,
  })
export const impactRequest = (projectId: string) =>
  query<Impact | null>('projectImpact', ID('projectId'), 'projectId: $projectId', IMPACT, {
    projectId,
  })
export const adminOpportunitiesRequest = (status?: string) =>
  query<Opportunity[]>(
    'adminAutomationOpportunities',
    '$status: String',
    'status: $status',
    OPPORTUNITY,
    { status: status ?? null },
  )
export const adminProjectsRequest = (status?: string) =>
  query<Project[]>('adminAutomationProjects', '$status: String', 'status: $status', PROJECT, {
    status: status ?? null,
  })

export interface AssigneeOption {
  id: string
  name: string
  detail: string
}
export const assigneeOptionsRequest = () =>
  query<{ users: AssigneeOption[]; teams: AssigneeOption[] }>(
    'assignableAssignees',
    '',
    '',
    'users { id name detail } teams { id name detail }',
  )
export const traceabilityRequest = (ideaId: string) =>
  query<Traceability | null>(
    'ideaTraceability',
    ID('ideaId'),
    'ideaId: $ideaId',
    'ideaId opportunityId proposalId projectId deploymentId impactId',
    { ideaId },
  )

// --- opportunity actions -----------------------------------------------------------------

type O = Opportunity
export const updateOpportunity = action<'opportunity', O>(
  'updateAutomationOpportunity',
  '$input: UpdateOpportunityInput!',
  'input: $input',
  `opportunity { ${OPPORTUNITY} }`,
)

/** An opportunity can be cancelled until its project exists; nothing else moves it by hand. */
export const cancelOpportunity = (id: string) =>
  action<'opportunity', O>(
    'cancelAutomationOpportunity',
    ID('id'),
    'id: $id',
    `opportunity { ${OPPORTUNITY} }`,
  )({ id })

export const createRequirement = action<'requirement', Requirement>(
  'createRequirement',
  `${ID('opportunityId')}, $input: RequirementInput!`,
  'opportunityId: $opportunityId, input: $input',
  `requirement { ${REQUIREMENT} }`,
)
export const updateRequirement = action<'requirement', Requirement>(
  'updateRequirement',
  `${ID('id')}, $input: RequirementInput!`,
  'id: $id, input: $input',
  `requirement { ${REQUIREMENT} }`,
)
export const updateSolution = action<'solution', Solution>(
  'updateOpportunitySolution',
  '$input: UpdateSolutionInput!',
  'input: $input',
  `solution { ${SOLUTION} }`,
)

// --- assignment and project ----------------------------------------------------------------------

export const assignOpportunity = action<'assignment', Assignment>(
  'assignOpportunity',
  `${ID('opportunityId')}, $assigneeUserId: ID, $assigneeTeamId: ID`,
  'opportunityId: $opportunityId, assigneeUserId: $assigneeUserId, assigneeTeamId: $assigneeTeamId',
  'assignment { id opportunityId assigneeName status assignedAt }',
)
export const createProject = action<'project', Project>(
  'createProject',
  `${ID('opportunityId')}, $startDate: String, $targetDate: String`,
  'opportunityId: $opportunityId, startDate: $startDate, targetDate: $targetDate',
  `project { ${PROJECT} }`,
)
export type ProjectStep = 'start' | 'testing' | 'uat' | 'complete'
const PROJECT_ROOT: Record<ProjectStep, string> = {
  start: 'startProject',
  testing: 'startProjectTesting',
  uat: 'submitProjectForUat',
  complete: 'completeProject',
}
export const moveProject = (step: ProjectStep, id: string) =>
  action<'project', Project>(
    PROJECT_ROOT[step],
    ID('id'),
    'id: $id',
    `project { ${PROJECT} }`,
  )({ id })

export const createMilestone = action<'milestone', Milestone>(
  'createMilestone',
  `${ID('projectId')}, $title: String!, $dueDate: String`,
  'projectId: $projectId, title: $title, dueDate: $dueDate',
  `milestone { ${MILESTONE} }`,
)
export const completeMilestone = action<'milestone', Milestone>(
  'completeMilestone',
  ID('id'),
  'id: $id',
  `milestone { ${MILESTONE} }`,
)
export const createTask = action<'task', Task>(
  'createTask',
  `${ID('projectId')}, $title: String!, $priority: String, $milestoneId: ID, $dueDate: String`,
  'projectId: $projectId, title: $title, priority: $priority, milestoneId: $milestoneId, dueDate: $dueDate',
  `task { ${TASK} }`,
)
export const updateTask = action<'task', Task>(
  'updateTask',
  '$input: UpdateTaskInput!',
  'input: $input',
  `task { ${TASK} }`,
)

// --- testing, UAT, deployment, impact ---------------------------------------------------------------

export const createTestCase = action<'testCase', TestCase>(
  'createTestCase',
  `${ID('projectId')}, $title: String!, $expectedResult: String, $isRequired: Boolean`,
  'projectId: $projectId, title: $title, expectedResult: $expectedResult, isRequired: $isRequired',
  `testCase { ${TEST_CASE} }`,
)
export const recordTestResult = action<'testCase', TestCase>(
  'recordTestResult',
  `${ID('id')}, $status: String!, $actualResult: String, $notes: String`,
  'id: $id, status: $status, actualResult: $actualResult, notes: $notes',
  `testCase { ${TEST_CASE} }`,
)
export const createUatScenario = action<'record', UatRecord>(
  'createUatScenario',
  `${ID('projectId')}, $scenario: String!, $expectedOutcome: String, $isRequired: Boolean`,
  'projectId: $projectId, scenario: $scenario, expectedOutcome: $expectedOutcome, isRequired: $isRequired',
  `record { ${UAT} }`,
)
export const recordUatResult = action<'record', UatRecord>(
  'recordUatResult',
  `${ID('id')}, $result: String!, $actualOutcome: String, $feedback: String`,
  'id: $id, result: $result, actualOutcome: $actualOutcome, feedback: $feedback',
  `record { ${UAT} }`,
)
export const createDeployment = action<'deployment', Deployment>(
  'createDeployment',
  `${ID('projectId')}, $environment: String!, $version: String!, $notes: String, $rollbackInformation: String`,
  'projectId: $projectId, environment: $environment, version: $version, notes: $notes, rollbackInformation: $rollbackInformation',
  `deployment { ${DEPLOYMENT} }`,
)
export const recordDeployment = action<'deployment', Deployment>(
  'recordDeployment',
  `${ID('id')}, $status: String!, $notes: String`,
  'id: $id, status: $status, notes: $notes',
  `deployment { ${DEPLOYMENT} }`,
)
export const verifyDeployment = action<'deployment', Deployment>(
  'verifyDeployment',
  ID('id'),
  'id: $id',
  `deployment { ${DEPLOYMENT} }`,
)
export const recordImpact = action<'impact', Impact>(
  'recordImpact',
  `${ID('projectId')}, $status: String, $values: ImpactInput`,
  'projectId: $projectId, status: $status, values: $values',
  `impact { ${IMPACT} }`,
)
