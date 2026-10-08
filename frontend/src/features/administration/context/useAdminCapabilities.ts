import { useContext } from 'react'

import { AdminCapabilitiesContext, type AdminCapabilitiesValue } from './AdminCapabilitiesContext'

export function useAdminCapabilities(): AdminCapabilitiesValue {
  const context = useContext(AdminCapabilitiesContext)
  if (!context) {
    throw new Error('useAdminCapabilities must be used within an AdminCapabilitiesProvider')
  }
  return context
}
