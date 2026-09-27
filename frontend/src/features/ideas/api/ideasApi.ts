/**
 * GraphQL operations for the Ideas domain.
 *
 * The frontend's half of the S2-002 contract: every document sent and every
 * response shape read back live here, and components never build a query of
 * their own. A mismatch between this file and the backend's schema does not
 * fail a test in the component that uses it - it fails as a GraphQL
 * `errors` array, or as a silently empty list, in a browser.
 *
 * Three things this module is shaped around, all of them consequences of
 * decisions the *backend* made and the UI has to respect rather than
 * re-litigate:
 *
 * - **The server decides what is visible.** Every read here goes through a
 *   query that is already tenant- and visibility-filtered, and none of them
 *   takes a filter argument this client could use to widen the result. The
 *   UI's job is to render what it is given.
 * - **`authorId` is an id, not a person.** `IdeaType` deliberately does not
 *   embed a user, so the client cannot render (or leak) a member's email on
 *   an idea that is readable platform-wide. Comparing `authorId` with the
 *   signed-in user is how the UI decides what to *offer* - Edit, Submit -
 *   and offering is a presentation choice, never a control.
 * - **Business failures are payloads.** `success: false` with a `message`
 *   and a `field` is the operation's own answer; a thrown error means the
 *   request never got that far (network, server down), which is a different
 *   fact and is reported differently.
 */

import { graphqlClient } from '../../../graphql/client'

/**
 * The submission lifecycle, mirroring the backend's `IdeaStatus` enum.
 *
 * All seven values are listed even though S2-002 can only produce `DRAFT` and
 * `SUBMITTED`, because the review statuses are part of the contract from the
 * start: a union type that omitted them would be wrong by omission the
 * moment Sprint 3 lands.
 */
export type IdeaStatus =
  | 'DRAFT'
  | 'SUBMITTED'
  | 'UNDER_REVIEW'
  | 'CHANGES_REQUESTED'
  | 'REJECTED'
  | 'APPROVED'
  | 'AUTOMATION_PROPOSAL'

/**
 * Who may read an idea. `DEPARTMENT` is listed because the backend's enum
 * contains it, but the service refuses it as a *choice* - there is no
 * department tier to honour it yet - so it can never arrive on an idea this
 * client receives.
 */
export type IdeaVisibility = 'PUBLIC' | 'ORGANIZATION' | 'DEPARTMENT' | 'PRIVATE'

/**
 * The visibilities the form may offer. Deliberately a client-side copy of
 * `ideas.services.SELECTABLE_VISIBILITIES` rather than a list derived from
 * the enum: `DEPARTMENT` is in the vocabulary and not in the picker, and the
 * difference is a product decision, not a formatting one. The server refuses
 * it regardless - this copy only avoids offering something that would be
 * rejected.
 */
export const SELECTABLE_VISIBILITIES: ReadonlyArray<{
  value: IdeaVisibility
  label: string
  hint: string
}> = [
  { value: 'PRIVATE', label: 'Only me', hint: 'Nobody else can see this, not even your team.' },
  {
    value: 'ORGANIZATION',
    label: 'My organization',
    hint: 'Every active member of this organization can read it.',
  },
  {
    value: 'PUBLIC',
    label: 'Everyone on the platform',
    hint: 'Any signed-in member of the platform can read it.',
  },
]

export interface IdeaCategory {
  id: string
  name: string
  slug: string
  description: string
}

export interface Idea {
  id: string
  title: string
  description: string
  status: IdeaStatus
  visibility: IdeaVisibility
  /** Null until the draft is submitted, and never rewritten afterwards. */
  submittedAt: string | null
  createdAt: string
  updatedAt: string
  authorId: string
  organizationId: string
  category: IdeaCategory | null
  /**
   * The statuses *this viewer* may move this idea to right now, computed by the
   * backend from the same transition matrix that enforces the change.
   *
   * It exists so this client does not carry its own copy of the lifecycle,
   * which would be a second place for the rules to drift. It is a
   * convenience and never a control: `transitionIdea` asks the matrix again,
   * and a request for a status not in this list is refused by the server
   * exactly as if the list had been honoured.
   */
  availableTransitions: IdeaStatus[]
}

/** The writable content of an idea. Mirrors the backend's `IdeaInput`. */
export interface IdeaDraftInput {
  title: string
  description: string
  categoryId?: string | null
  visibility?: IdeaVisibility | null
}

/** Every payload below carries this shape; `field` names the input at fault. */
export interface IdeaMutationResult {
  success: boolean
  message: string
  field: string | null
  idea: Idea | null
}

const CATEGORY_FIELDS = `
  id
  name
  slug
  description
`

const IDEA_FIELDS = `
  id
  title
  description
  status
  visibility
  submittedAt
  createdAt
  updatedAt
  authorId
  organizationId
  availableTransitions
  category { ${CATEGORY_FIELDS} }
`

const CREATE_IDEA_MUTATION = `
  mutation CreateIdea($input: CreateIdeaInput!) {
    createIdea(input: $input) {
      success
      message
      field
      idea { ${IDEA_FIELDS} }
    }
  }
`

const UPDATE_IDEA_MUTATION = `
  mutation UpdateIdea($input: UpdateIdeaInput!) {
    updateIdea(input: $input) {
      success
      message
      field
      idea { ${IDEA_FIELDS} }
    }
  }
`

const SUBMIT_IDEA_MUTATION = `
  mutation SubmitIdea($id: ID!) {
    submitIdea(id: $id) {
      success
      message
      field
      idea { ${IDEA_FIELDS} }
    }
  }
`

const IDEA_QUERY = `
  query Idea($id: ID!) {
    idea(id: $id) { ${IDEA_FIELDS} }
  }
`

const IDEAS_QUERY = `
  query Ideas {
    ideas { ${IDEA_FIELDS} }
  }
`

const ORGANIZATION_IDEAS_QUERY = `
  query OrganizationIdeas($organizationId: ID!) {
    organizationIdeas(organizationId: $organizationId) { ${IDEA_FIELDS} }
  }
`

const TRANSITION_IDEA_MUTATION = `
  mutation TransitionIdea($id: ID!, $to: IdeaStatus!) {
    transitionIdea(id: $id, to: $to) {
      success
      message
      field
      idea { ${IDEA_FIELDS} }
    }
  }
`

const CATEGORIES_QUERY = `
  query Categories {
    categories { ${CATEGORY_FIELDS} }
  }
`

/**
 * File a new idea as a draft.
 *
 * `organizationId` is a request to act *in* that organization, not a value to
 * write onto the idea: the server authorizes it and then uses it, and the
 * author is always the signed-in user. Neither can be set from here, which is
 * why neither appears in the arguments.
 */
export async function createIdeaRequest(
  organizationId: string,
  input: IdeaDraftInput,
): Promise<IdeaMutationResult> {
  const data = await graphqlClient.request<{ createIdea: IdeaMutationResult }>(
    CREATE_IDEA_MUTATION,
    {
      input: {
        organizationId,
        idea: {
          title: input.title,
          description: input.description,
          categoryId: input.categoryId ?? null,
          visibility: input.visibility ?? null,
        },
      },
    },
  )
  return data.createIdea
}

/** Edit the signed-in user's own draft. Ownership is the server's decision. */
export async function updateIdeaRequest(
  id: string,
  input: IdeaDraftInput,
): Promise<IdeaMutationResult> {
  const data = await graphqlClient.request<{ updateIdea: IdeaMutationResult }>(
    UPDATE_IDEA_MUTATION,
    {
      input: {
        id,
        idea: {
          title: input.title,
          description: input.description,
          categoryId: input.categoryId ?? null,
          visibility: input.visibility ?? null,
        },
      },
    },
  )
  return data.updateIdea
}

/**
 * Submit a draft for review: `DRAFT` -> `SUBMITTED`, once, by its author.
 * Establishes no session and returns no token.
 */
export async function submitIdeaRequest(id: string): Promise<IdeaMutationResult> {
  const data = await graphqlClient.request<{ submitIdea: IdeaMutationResult }>(
    SUBMIT_IDEA_MUTATION,
    { id },
  )
  return data.submitIdea
}

/**
 * Move an idea to another state in its lifecycle (S2-003).
 *
 * The only way a status changes: there is no mutation that sets one, so a
 * client cannot mark its own idea reviewed or approved. The target is an enum
 * value, so a state the domain does not have cannot even be sent; whether the
 * move is legal *from where the idea is now* is the server's answer, not this
 * client's.
 */
export async function transitionIdeaRequest(
  id: string,
  to: IdeaStatus,
): Promise<IdeaMutationResult> {
  const data = await graphqlClient.request<{ transitionIdea: IdeaMutationResult }>(
    TRANSITION_IDEA_MUTATION,
    { id, to },
  )
  return data.transitionIdea
}

/** One idea, or `null` for anything the signed-in user may not read. */
export async function ideaRequest(id: string): Promise<Idea | null> {
  const data = await graphqlClient.request<{ idea: Idea | null }>(IDEA_QUERY, { id })
  return data.idea
}

/** Every idea the signed-in user may read, newest first. */
export async function ideasRequest(): Promise<Idea[]> {
  const data = await graphqlClient.request<{ ideas: Idea[] }>(IDEAS_QUERY)
  return data.ideas
}

/**
 * One organization's readable ideas, newest first.
 *
 * Empty for an organization the caller is not an active member of - the
 * server does not confirm that the organization exists, so the UI shows an
 * empty state rather than an error.
 */
export async function organizationIdeasRequest(organizationId: string): Promise<Idea[]> {
  const data = await graphqlClient.request<{ organizationIdeas: Idea[] }>(
    ORGANIZATION_IDEAS_QUERY,
    {
      organizationId,
    },
  )
  return data.organizationIdeas
}

/** Categories available to file an idea under. */
export async function categoriesRequest(): Promise<IdeaCategory[]> {
  const data = await graphqlClient.request<{ categories: IdeaCategory[] }>(CATEGORIES_QUERY)
  return data.categories
}
