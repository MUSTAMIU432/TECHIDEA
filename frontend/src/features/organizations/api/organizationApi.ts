import { graphqlClient } from '../../../graphql/client'

export interface Organization {
  id: string
  name: string
  slug: string
  createdAt: string
  updatedAt: string
}

export interface OrganizationMember {
  id: string
  email: string
  firstName: string
  lastName: string
}

export interface Permission {
  id: string
  code: string
  name: string
  description: string
  createdAt: string
  updatedAt: string
}

export interface Role {
  id: string
  name: string
  slug: string
  description: string
  isSystem: boolean
  createdAt: string
  updatedAt: string
  organization: Organization
  permissions: Permission[]
}

export interface Membership {
  id: string
  status: 'active' | 'inactive'
  createdAt: string
  updatedAt: string
  user: OrganizationMember
  organization: Organization
  roles: Role[]
}

export interface OrganizationMembership {
  organization: Organization
  membership: Membership
}

export interface CreateOrganizationInput {
  name: string
  slug?: string
}

export interface CreateOrganizationResult {
  success: boolean
  message: string
  field: string | null
  organization: Organization | null
  membership: Membership | null
}

const ORGANIZATION_FIELDS = `
  id
  name
  slug
  createdAt
  updatedAt
`

const PERMISSION_FIELDS = `
  id
  code
  name
  description
  createdAt
  updatedAt
`

const ROLE_FIELDS = `
  id
  name
  slug
  description
  isSystem
  createdAt
  updatedAt
  organization { ${ORGANIZATION_FIELDS} }
  permissions { ${PERMISSION_FIELDS} }
`

const MEMBERSHIP_FIELDS = `
  id
  status
  createdAt
  updatedAt
  user {
    id
    email
    firstName
    lastName
  }
  roles { ${ROLE_FIELDS} }
`

const ME_ORGANIZATIONS_QUERY = `
  query MeOrganizations {
    meOrganizations {
      organization { ${ORGANIZATION_FIELDS} }
      membership {
        ${MEMBERSHIP_FIELDS}
        organization { ${ORGANIZATION_FIELDS} }
      }
    }
  }
`

const CREATE_ORGANIZATION_MUTATION = `
  mutation CreateOrganization($input: CreateOrganizationInput!) {
    createOrganization(input: $input) {
      success
      message
      field
      organization { ${ORGANIZATION_FIELDS} }
      membership {
        ${MEMBERSHIP_FIELDS}
        organization { ${ORGANIZATION_FIELDS} }
      }
    }
  }
`

const ORGANIZATION_MEMBERS_QUERY = `
  query OrganizationMembers($organizationId: ID!) {
    organizationMembers(organizationId: $organizationId) { ${MEMBERSHIP_FIELDS} }
  }
`

export async function organizationsRequest(): Promise<OrganizationMembership[]> {
  const data = await graphqlClient.request<{ meOrganizations: OrganizationMembership[] }>(
    ME_ORGANIZATIONS_QUERY,
  )
  return data.meOrganizations
}

/**
 * One organization's active members, oldest join first.
 *
 * Empty - not an error - for an organization the caller cannot see, which is the
 * same answer as for one that does not exist, so an organization id cannot be
 * used to discover other tenants. The server decides what "can see" means; this
 * does not ask for anybody in particular.
 */
export async function organizationMembersRequest(organizationId: string): Promise<Membership[]> {
  const data = await graphqlClient.request<{ organizationMembers: Membership[] }>(
    ORGANIZATION_MEMBERS_QUERY,
    { organizationId },
  )
  return data.organizationMembers
}

export async function createOrganizationRequest(
  input: CreateOrganizationInput,
): Promise<CreateOrganizationResult> {
  const data = await graphqlClient.request<{ createOrganization: CreateOrganizationResult }>(
    CREATE_ORGANIZATION_MUTATION,
    { input },
  )
  return data.createOrganization
}
