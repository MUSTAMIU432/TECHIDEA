import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

import {
  NO_ADMIN_CAPABILITIES,
  type AdminCapabilities,
} from '../features/administration/api/administrationApi'
import {
  AdminCapabilitiesContext,
  type AdminCapabilitiesValue,
} from '../features/administration/context/AdminCapabilitiesContext'

export const FULL_ADMIN: AdminCapabilities = {
  canAccessConsole: true,
  canInspectIdeaContent: true,
  canManageUserAccounts: true,
  canManageOrganizationRoles: true,
  canManageCategories: true,
}

export const READ_ONLY_ADMIN: AdminCapabilities = {
  ...NO_ADMIN_CAPABILITIES,
  canAccessConsole: true,
}

/**
 * Render one console page at `path` (matched against `pattern`, so route
 * params resolve) with fixed capabilities - the provider's request is not
 * what these tests are about, and the server is the real gate anyway.
 */
export function renderAdminPage(
  page: ReactElement,
  {
    path = '/',
    pattern = '/',
    capabilities = FULL_ADMIN,
    status = 'ready',
  }: {
    path?: string
    pattern?: string
    capabilities?: AdminCapabilities
    status?: AdminCapabilitiesValue['status']
  } = {},
) {
  const value: AdminCapabilitiesValue = { status, capabilities, reload: () => {} }
  return render(
    <AdminCapabilitiesContext.Provider value={value}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={pattern} element={page} />
        </Routes>
      </MemoryRouter>
    </AdminCapabilitiesContext.Provider>,
  )
}

export const EMPTY_PAGE_INFO = {
  offset: 0,
  limit: 20,
  totalCount: 0,
  hasNextPage: false,
  hasPreviousPage: false,
}

export function pageOfItems<T>(items: T[], overrides: Partial<typeof EMPTY_PAGE_INFO> = {}) {
  return { items, pageInfo: { ...EMPTY_PAGE_INFO, totalCount: items.length, ...overrides } }
}
