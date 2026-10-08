import { createContext } from 'react'

import type { AdminCapabilities } from '../api/capabilitiesApi'

export type AdminCapabilitiesStatus = 'loading' | 'ready' | 'error'

export interface AdminCapabilitiesValue {
  status: AdminCapabilitiesStatus
  capabilities: AdminCapabilities
  reload: () => void
}

export const AdminCapabilitiesContext = createContext<AdminCapabilitiesValue | undefined>(undefined)
