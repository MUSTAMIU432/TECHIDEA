/**
 * GraphQL operations for the platform administration console.
 *
 * Every document the console sends lives here, in the same shape as
 * `ideasApi` and `reviewsApi`. Nothing here decides who is an administrator:
 * every `admin*` field is authorized by the server on every request, and a
 * caller who is not allowed gets `null` or an empty page back rather than an
 * error. `adminCapabilitiesRequest` is what the UI uses to decide what to
 * *offer* - the navigation link, the action buttons - and nothing else.
 *
 * Content an administrator may not see arrives as `null` with
 * `contentRestricted: true`; the UI says "restricted" rather than rendering an
 * empty answer.
 */

import { graphqlClient } from '../../../graphql/client'
import { getAccessToken } from '../../../graphql/tokenStore'
import { env } from '../../../lib/env'
import type {
  IdeaCurrentTool,
  IdeaFrequency,
  IdeaImpact,
  IdeaPageInfo,
  IdeaStatus,
  IdeaVisibility,
} from '../../ideas/api/ideasApi'
import type { CriterionAssessment, ReviewDecision } from '../../reviews/api/reviewsApi'

export type PageInfo = IdeaPageInfo

// Types only from the ideas feature: this module owns its own documents, so
// the console does not depend on another feature's runtime values.
const PAGE_INFO_FIELDS = 'offset limit totalCount hasNextPage hasPreviousPage'

/** How long a download's blob URL is kept; the same 40 s `ideasApi` uses. */
const BLOB_URL_LIFETIME_MS = 40_000

export interface Page<T> {
  items: T[]
  pageInfo: PageInfo
}

export interface PageRequest {
  offset?: number
  limit?: number
}

/** The `(success, message, field)` answer every console mutation gives. */
export interface AdminPayload {
  success: boolean
  message: string
  field: string | null
}

// --- capabilities -----------------------------------------------------------

// In their own module so the app shell can ask "is this an administrator?"
// without pulling the whole console API into every user's bundle.
export {
  adminCapabilitiesRequest,
  NO_ADMIN_CAPABILITIES,
  type AdminCapabilities,
} from './capabilitiesApi'

// --- shared references ------------------------------------------------------

export interface AdminPerson {
  id: string
  email: string
  name: string
}

export interface AdminOrganizationRef {
  id: string
  name: string
}

export interface AdminRoleRef {
  id: string
  name: string
  slug: string
  isSystem: boolean
}

export interface AdminStatusCount {
  status: IdeaStatus
  count: number
}

export type AdminAuditAction =
  | 'USER_ACTIVATED'
  | 'USER_DEACTIVATED'
  | 'MEMBERSHIP_ROLE_ASSIGNED'
  | 'MEMBERSHIP_ROLE_REMOVED'
  | 'CATEGORY_CREATED'
  | 'CATEGORY_UPDATED'
  | 'CATEGORY_ACTIVATED'
  | 'CATEGORY_DEACTIVATED'
  | 'ATTACHMENT_DOWNLOADED'
  | 'PLATFORM_ADMIN_GRANTED'
  | 'PLATFORM_ADMIN_REVOKED'

export interface AdminAuditEntry {
  id: string
  action: AdminAuditAction
  result: 'SUCCEEDED' | 'REFUSED'
  actor: AdminPerson | null
  targetType: string
  targetId: string
  targetLabel: string
  message: string
  createdAt: string
}

const PERSON = 'id email name'
const AUDIT_ENTRY_FIELDS = `
  id action result actor { ${PERSON} } targetType targetId targetLabel message createdAt
`

// --- overview ---------------------------------------------------------------

export interface AdminActivity {
  id: string
  ideaId: string
  ideaTitle: string | null
  organization: AdminOrganizationRef
  fromStatus: IdeaStatus
  toStatus: IdeaStatus
  actor: AdminPerson
  createdAt: string
}

export interface AdminOverview {
  userCount: number
  activeUserCount: number
  organizationCount: number
  ideaCount: number
  ideasByStatus: AdminStatusCount[]
  openReviewCount: number
  completedReviewCount: number
  recentActivity: AdminActivity[]
  recentAdminActions: AdminAuditEntry[]
}

const OVERVIEW_QUERY = `
  query AdminOverview {
    adminOverview {
      userCount
      activeUserCount
      organizationCount
      ideaCount
      ideasByStatus { status count }
      openReviewCount
      completedReviewCount
      recentActivity {
        id ideaId ideaTitle organization { id name } fromStatus toStatus
        actor { ${PERSON} } createdAt
      }
      recentAdminActions { ${AUDIT_ENTRY_FIELDS} }
    }
  }
`

export async function adminOverviewRequest(): Promise<AdminOverview | null> {
  const data = await graphqlClient.request<{ adminOverview: AdminOverview | null }>(OVERVIEW_QUERY)
  return data.adminOverview
}

// --- users ------------------------------------------------------------------

export interface AdminUserMembership {
  id: string
  organization: AdminOrganizationRef
  status: string
  roles: AdminRoleRef[]
}

export interface AdminUser {
  id: string
  email: string
  firstName: string
  lastName: string
  isActive: boolean
  isVerified: boolean
  isPlatformAdmin: boolean
  isSuperuser: boolean
  createdAt: string
  lastActiveAt: string | null
  memberships: AdminUserMembership[]
}

export interface AdminUserDetail extends AdminUser {
  phoneNumber: string
  signInMethods: string[]
  ideaCount: number
  reviewCount: number
  commentCount: number
  auditEntries: AdminAuditEntry[]
}

export interface AdminUserFilters {
  search?: string
  isActive?: boolean | null
  platformAdminsOnly?: boolean
  organizationId?: string | null
}

const USER_FIELDS = `
  id email firstName lastName isActive isVerified isPlatformAdmin isSuperuser
  createdAt lastActiveAt
  memberships { id status organization { id name } roles { id name slug isSystem } }
`

const USERS_QUERY = `
  query AdminUsers($filters: AdminUserFiltersInput, $offset: Int, $limit: Int) {
    adminUsers(filters: $filters, offset: $offset, limit: $limit) {
      items { ${USER_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const USER_QUERY = `
  query AdminUser($id: ID!) {
    adminUser(id: $id) {
      ${USER_FIELDS}
      phoneNumber signInMethods ideaCount reviewCount commentCount
      auditEntries { ${AUDIT_ENTRY_FIELDS} }
    }
  }
`

function pageVariables(page: PageRequest): Record<string, unknown> {
  const variables: Record<string, unknown> = {}
  if (page.offset !== undefined) variables.offset = page.offset
  if (page.limit !== undefined) variables.limit = page.limit
  return variables
}

/** Drop blank values so an empty search box is "no filter", not "match ''". */
function compact<T extends object>(filters: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(filters).filter(
      ([, value]) => value !== undefined && value !== null && value !== '',
    ),
  ) as Partial<T>
}

export async function adminUsersRequest(
  filters: AdminUserFilters = {},
  page: PageRequest = {},
): Promise<Page<AdminUser>> {
  const data = await graphqlClient.request<{ adminUsers: Page<AdminUser> }>(USERS_QUERY, {
    filters: compact(filters),
    ...pageVariables(page),
  })
  return data.adminUsers
}

export async function adminUserRequest(id: string): Promise<AdminUserDetail | null> {
  const data = await graphqlClient.request<{ adminUser: AdminUserDetail | null }>(USER_QUERY, {
    id,
  })
  return data.adminUser
}

const SET_USER_ACTIVE_MUTATION = `
  mutation AdminSetUserActive($input: AdminSetUserActiveInput!) {
    adminSetUserActive(input: $input) {
      success message field
      user { ${USER_FIELDS} }
    }
  }
`

export interface SetUserActiveResult extends AdminPayload {
  user: AdminUser | null
}

export async function setUserActiveRequest(input: {
  userId: string
  isActive: boolean
  reason?: string
}): Promise<SetUserActiveResult> {
  const data = await graphqlClient.request<{ adminSetUserActive: SetUserActiveResult }>(
    SET_USER_ACTIVE_MUTATION,
    { input },
  )
  return data.adminSetUserActive
}

// --- organizations ----------------------------------------------------------

export interface AdminOrganization {
  id: string
  name: string
  slug: string
  createdAt: string
  memberCount: number
  ideaCount: number
  ownerCount: number
  reviewerCount: number
}

export interface AdminRole {
  id: string
  name: string
  slug: string
  description: string
  isSystem: boolean
  permissions: string[]
  holderCount: number
}

export interface AdminOrganizationDetail extends AdminOrganization {
  roles: AdminRole[]
  ideasByStatus: AdminStatusCount[]
}

export interface AdminMember {
  id: string
  status: string
  joinedAt: string
  user: AdminPerson
  userIsActive: boolean
  roles: AdminRoleRef[]
}

const ORGANIZATION_FIELDS = 'id name slug createdAt memberCount ideaCount ownerCount reviewerCount'
const MEMBER_FIELDS = `
  id status joinedAt user { ${PERSON} } userIsActive roles { id name slug isSystem }
`

const ORGANIZATIONS_QUERY = `
  query AdminOrganizations($search: String, $offset: Int, $limit: Int) {
    adminOrganizations(search: $search, offset: $offset, limit: $limit) {
      items { ${ORGANIZATION_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const ORGANIZATION_QUERY = `
  query AdminOrganization($id: ID!) {
    adminOrganization(id: $id) {
      ${ORGANIZATION_FIELDS}
      roles { id name slug description isSystem permissions holderCount }
      ideasByStatus { status count }
    }
  }
`

const MEMBERS_QUERY = `
  query AdminOrganizationMembers(
    $organizationId: ID!, $search: String, $offset: Int, $limit: Int
  ) {
    adminOrganizationMembers(
      organizationId: $organizationId, search: $search, offset: $offset, limit: $limit
    ) {
      items { ${MEMBER_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

export async function adminOrganizationsRequest(
  search = '',
  page: PageRequest = {},
): Promise<Page<AdminOrganization>> {
  const data = await graphqlClient.request<{ adminOrganizations: Page<AdminOrganization> }>(
    ORGANIZATIONS_QUERY,
    { ...(search ? { search } : {}), ...pageVariables(page) },
  )
  return data.adminOrganizations
}

export async function adminOrganizationRequest(
  id: string,
): Promise<AdminOrganizationDetail | null> {
  const data = await graphqlClient.request<{
    adminOrganization: AdminOrganizationDetail | null
  }>(ORGANIZATION_QUERY, { id })
  return data.adminOrganization
}

export async function adminOrganizationMembersRequest(
  organizationId: string,
  search = '',
  page: PageRequest = {},
): Promise<Page<AdminMember>> {
  const data = await graphqlClient.request<{ adminOrganizationMembers: Page<AdminMember> }>(
    MEMBERS_QUERY,
    { organizationId, ...(search ? { search } : {}), ...pageVariables(page) },
  )
  return data.adminOrganizationMembers
}

export interface MembershipRoleResult extends AdminPayload {
  member: AdminMember | null
}

const ASSIGN_ROLE_MUTATION = `
  mutation AdminAssignMembershipRole($input: AdminMembershipRoleInput!) {
    adminAssignMembershipRole(input: $input) { success message field member { ${MEMBER_FIELDS} } }
  }
`

const REMOVE_ROLE_MUTATION = `
  mutation AdminRemoveMembershipRole($input: AdminMembershipRoleInput!) {
    adminRemoveMembershipRole(input: $input) { success message field member { ${MEMBER_FIELDS} } }
  }
`

export interface MembershipRoleInput {
  membershipId: string
  roleId: string
  reason?: string
}

export async function assignMembershipRoleRequest(
  input: MembershipRoleInput,
): Promise<MembershipRoleResult> {
  const data = await graphqlClient.request<{ adminAssignMembershipRole: MembershipRoleResult }>(
    ASSIGN_ROLE_MUTATION,
    { input },
  )
  return data.adminAssignMembershipRole
}

export async function removeMembershipRoleRequest(
  input: MembershipRoleInput,
): Promise<MembershipRoleResult> {
  const data = await graphqlClient.request<{ adminRemoveMembershipRole: MembershipRoleResult }>(
    REMOVE_ROLE_MUTATION,
    { input },
  )
  return data.adminRemoveMembershipRole
}

// --- ideas ------------------------------------------------------------------

export interface AdminIdea {
  id: string
  title: string | null
  contentRestricted: boolean
  status: IdeaStatus
  visibility: IdeaVisibility
  organization: AdminOrganizationRef
  author: AdminPerson
  categoryName: string | null
  createdAt: string
  updatedAt: string
  submittedAt: string | null
  voteCount: number
  commentCount: number
  attachmentCount: number
}

export interface AdminIdeaContent {
  description: string
  currentProcess: string
  currentTools: IdeaCurrentTool[]
  currentToolsOther: string
  performedBy: string
  affectedPeople: string
  frequency: IdeaFrequency | null
  timeRequired: string
  peopleInvolved: number | null
  impacts: IdeaImpact[]
  impactDetails: string
  improvementGoal: string
  desiredOutcome: string
  easierForPeople: string
  expectedBenefit: string
  importantConsiderations: string
}

export interface AdminAttachment {
  id: string
  filename: string
  contentType: string
  size: number
  uploadedBy: AdminPerson
  createdAt: string
  downloadPath: string
}

export interface AdminTransition {
  id: string
  fromStatus: IdeaStatus
  toStatus: IdeaStatus
  actor: AdminPerson
  createdAt: string
}

export interface AdminReviewedIdea {
  id: string
  title: string | null
  status: IdeaStatus
  visibility: IdeaVisibility
  organization: AdminOrganizationRef
}

export interface AdminReview {
  id: string
  round: number
  idea: AdminReviewedIdea
  reviewer: AdminPerson
  decision: ReviewDecision | null
  isCompleted: boolean
  createdAt: string
  completedAt: string | null
  contentRestricted: boolean
  feedback: string | null
  assessments: CriterionAssessment[] | null
}

export interface AdminIdeaDetail extends AdminIdea {
  canInspectContent: boolean
  content: AdminIdeaContent | null
  attachments: AdminAttachment[]
  reviews: AdminReview[]
  transitions: AdminTransition[]
}

export interface AdminIdeaFilters {
  search?: string
  status?: IdeaStatus | null
  visibility?: IdeaVisibility | null
  organizationId?: string | null
  categoryId?: string | null
  authorId?: string | null
  /** `YYYY-MM-DD`, inclusive. */
  createdFrom?: string | null
  /** `YYYY-MM-DD`, inclusive. */
  createdTo?: string | null
}

const IDEA_FIELDS = `
  id title contentRestricted status visibility organization { id name }
  author { ${PERSON} } categoryName createdAt updatedAt submittedAt
  voteCount commentCount attachmentCount
`

const REVIEW_FIELDS = `
  id round
  idea { id title status visibility organization { id name } }
  reviewer { ${PERSON} }
  decision isCompleted createdAt completedAt contentRestricted feedback
  assessments { criterion rating note }
`

const IDEAS_QUERY = `
  query AdminIdeas($filters: AdminIdeaFiltersInput, $offset: Int, $limit: Int) {
    adminIdeas(filters: $filters, offset: $offset, limit: $limit) {
      items { ${IDEA_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const IDEA_QUERY = `
  query AdminIdea($id: ID!) {
    adminIdea(id: $id) {
      ${IDEA_FIELDS}
      canInspectContent
      content {
        description currentProcess currentTools currentToolsOther performedBy
        affectedPeople frequency timeRequired peopleInvolved impacts impactDetails
        improvementGoal desiredOutcome easierForPeople expectedBenefit
        importantConsiderations
      }
      attachments {
        id filename contentType size uploadedBy { ${PERSON} } createdAt downloadPath
      }
      reviews { ${REVIEW_FIELDS} }
      transitions { id fromStatus toStatus actor { ${PERSON} } createdAt }
    }
  }
`

export async function adminIdeasRequest(
  filters: AdminIdeaFilters = {},
  page: PageRequest = {},
): Promise<Page<AdminIdea>> {
  const data = await graphqlClient.request<{ adminIdeas: Page<AdminIdea> }>(IDEAS_QUERY, {
    filters: compact(filters),
    ...pageVariables(page),
  })
  return data.adminIdeas
}

export async function adminIdeaRequest(id: string): Promise<AdminIdeaDetail | null> {
  const data = await graphqlClient.request<{ adminIdea: AdminIdeaDetail | null }>(IDEA_QUERY, {
    id,
  })
  return data.adminIdea
}

/**
 * Download one piece of evidence through the console's own endpoint, which
 * checks the content-inspection permission and records the download in the
 * audit trail. Same blob-URL technique as `downloadAttachmentRequest`, for the
 * same reason: a plain link cannot carry the bearer token.
 */
export async function downloadAdminAttachmentRequest(attachment: AdminAttachment): Promise<void> {
  const token = getAccessToken()
  const response = await fetch(`${env.apiBaseUrl}${attachment.downloadPath}`, {
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) {
    throw new Error('Could not download this attachment.')
  }

  const url = URL.createObjectURL(await response.blob())
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = attachment.filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), BLOB_URL_LIFETIME_MS)
  }
}

// --- reviews and approvals --------------------------------------------------

export type AdminReviewState = 'OPEN' | 'COMPLETED'

export interface AdminReviewFilters {
  search?: string
  state?: AdminReviewState | null
  decisions?: ReviewDecision[] | null
  organizationId?: string | null
  reviewerId?: string | null
}

export interface AdminReviewDetail extends AdminReview {
  isStalled: boolean
  submissionSnapshot: Record<string, unknown> | null
  history: AdminReview[]
}

const REVIEWS_QUERY = `
  query AdminReviews($filters: AdminReviewFiltersInput, $offset: Int, $limit: Int) {
    adminReviews(filters: $filters, offset: $offset, limit: $limit) {
      items { ${REVIEW_FIELDS} }
      pageInfo { ${PAGE_INFO_FIELDS} }
    }
  }
`

const REVIEW_QUERY = `
  query AdminReview($id: ID!) {
    adminReview(id: $id) {
      ${REVIEW_FIELDS}
      isStalled
      submissionSnapshot
      history { ${REVIEW_FIELDS} }
    }
  }
`

export async function adminReviewsRequest(
  filters: AdminReviewFilters = {},
  page: PageRequest = {},
): Promise<Page<AdminReview>> {
  const data = await graphqlClient.request<{ adminReviews: Page<AdminReview> }>(REVIEWS_QUERY, {
    filters: compact(filters),
    ...pageVariables(page),
  })
  return data.adminReviews
}

export async function adminReviewRequest(id: string): Promise<AdminReviewDetail | null> {
  const data = await graphqlClient.request<{ adminReview: AdminReviewDetail | null }>(
    REVIEW_QUERY,
    { id },
  )
  return data.adminReview
}

// --- categories -------------------------------------------------------------

export interface AdminCategory {
  id: string
  name: string
  slug: string
  description: string
  isActive: boolean
  ideaCount: number
  createdAt: string
  updatedAt: string
}

const CATEGORY_FIELDS = 'id name slug description isActive ideaCount createdAt updatedAt'

const CATEGORIES_QUERY = `
  query AdminCategories { adminCategories { ${CATEGORY_FIELDS} } }
`

export async function adminCategoriesRequest(): Promise<AdminCategory[]> {
  const data = await graphqlClient.request<{ adminCategories: AdminCategory[] }>(CATEGORIES_QUERY)
  return data.adminCategories
}

export interface CategoryResult extends AdminPayload {
  category: AdminCategory | null
}

const CREATE_CATEGORY_MUTATION = `
  mutation AdminCreateCategory($input: AdminCreateCategoryInput!) {
    adminCreateCategory(input: $input) { success message field category { ${CATEGORY_FIELDS} } }
  }
`

const UPDATE_CATEGORY_MUTATION = `
  mutation AdminUpdateCategory($input: AdminUpdateCategoryInput!) {
    adminUpdateCategory(input: $input) { success message field category { ${CATEGORY_FIELDS} } }
  }
`

const SET_CATEGORY_ACTIVE_MUTATION = `
  mutation AdminSetCategoryActive($input: AdminSetCategoryActiveInput!) {
    adminSetCategoryActive(input: $input) {
      success message field category { ${CATEGORY_FIELDS} }
    }
  }
`

export async function createCategoryRequest(input: {
  name: string
  description: string
}): Promise<CategoryResult> {
  const data = await graphqlClient.request<{ adminCreateCategory: CategoryResult }>(
    CREATE_CATEGORY_MUTATION,
    { input },
  )
  return data.adminCreateCategory
}

export async function updateCategoryRequest(input: {
  id: string
  name: string
  description: string
}): Promise<CategoryResult> {
  const data = await graphqlClient.request<{ adminUpdateCategory: CategoryResult }>(
    UPDATE_CATEGORY_MUTATION,
    { input },
  )
  return data.adminUpdateCategory
}

export async function setCategoryActiveRequest(input: {
  id: string
  isActive: boolean
}): Promise<CategoryResult> {
  const data = await graphqlClient.request<{ adminSetCategoryActive: CategoryResult }>(
    SET_CATEGORY_ACTIVE_MUTATION,
    { input },
  )
  return data.adminSetCategoryActive
}
