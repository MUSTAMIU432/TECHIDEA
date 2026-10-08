import { useContext } from 'react'

import { OrganizationContext, type OrganizationContextValue } from './OrganizationContext'

export function useOrganization(): OrganizationContextValue {
  const context = useContext(OrganizationContext)
  if (!context) {
    throw new Error('useOrganization must be used within an OrganizationProvider')
  }
  return context
}
