/**
 * The platform's roles - developer, delivery manager, intake, proposal approver - for
 * the console.
 *
 * Refusals are payloads (`success: false` and the field they name); only a transport
 * failure rejects. Who may do what is the server's decision - the page narrows what it
 * draws from `adminCapabilities` and nothing more.
 */

import { graphqlClient } from '../../../graphql/client'

export interface RoleHolder {
  id: string
  email: string
  name: string
}

export interface PlatformRole {
  key: string
  label: string
  description: string
  holders: RoleHolder[]
}

export interface Outcome {
  success: boolean
  message: string
  field: string | null
}

export async function platformRolesRequest(): Promise<PlatformRole[]> {
  const data = await graphqlClient.request<{ platformRoles: PlatformRole[] }>(
    'query { platformRoles { key label description holders { id email name } } }',
  )
  return data.platformRoles
}

export async function grantPlatformRoleRequest(role: string, email: string): Promise<Outcome> {
  const data = await graphqlClient.request<{ grantPlatformRole: Outcome }>(
    `mutation($role: String!, $email: String!) {
       grantPlatformRole(role: $role, email: $email) { success message field }
     }`,
    { role, email },
  )
  return data.grantPlatformRole
}

export async function revokePlatformRoleRequest(role: string, userId: string): Promise<Outcome> {
  const data = await graphqlClient.request<{ revokePlatformRole: Outcome }>(
    `mutation($role: String!, $userId: ID!) {
       revokePlatformRole(role: $role, userId: $userId) { success message field }
     }`,
    { role, userId },
  )
  return data.revokePlatformRole
}
