import { createContext } from 'react'

import type { Organization, OrganizationMembership } from '../api/organizationApi'

export type OrganizationStatus = 'idle' | 'loading' | 'ready' | 'error'

export interface CreateOrganizationOutcome {
  success: boolean
  message: string
  field: string | null
}

export interface OrganizationContextValue {
  status: OrganizationStatus
  memberships: OrganizationMembership[]
  activeOrganization: Organization | null
  activeMembership: OrganizationMembership['membership'] | null
  error: string | null
  hasPermission: (permissionCode: string) => boolean
  createOrganization: (name: string, slug?: string) => Promise<CreateOrganizationOutcome>
  setActiveOrganization: (organizationId: string) => void
  reload: () => Promise<void>
}

export const OrganizationContext = createContext<OrganizationContextValue | undefined>(undefined)
