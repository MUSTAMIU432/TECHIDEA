import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { NO_ADMIN_CAPABILITIES } from '../api/administrationApi'
import { FULL_ADMIN, renderAdminPage } from '../../../test/renderAdmin'
import { AdminLayout } from './AdminLayout'

vi.mock('../api/administrationApi', async (importOriginal) => ({
  ...(await importOriginal()),
  adminOverviewRequest: vi.fn(),
}))

const { adminOverviewRequest } = await import('../api/administrationApi')

/**
 * The console's presentational gate. The server is the security boundary -
 * these tests pin that a non-administrator is shown a forbidden state and the
 * console's pages are never mounted (so never ask for data), not that this
 * component keeps anyone out of the API.
 */
describe('AdminLayout', () => {
  it('shows a forbidden state to a user who is not a platform administrator', () => {
    renderAdminPage(<AdminLayout />, { capabilities: NO_ADMIN_CAPABILITIES })

    expect(screen.getByRole('heading', { name: 'Not authorized' })).toBeInTheDocument()
    expect(screen.getByText(/owner or reviewer of an organization does not grant/i)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to your workspace' })).toHaveAttribute(
      'href',
      '/app',
    )
    expect(screen.queryByRole('navigation', { name: 'Administration' })).not.toBeInTheDocument()
    expect(adminOverviewRequest).not.toHaveBeenCalled()
  })

  it('shows the administration navigation to an administrator', () => {
    renderAdminPage(<AdminLayout />, { capabilities: FULL_ADMIN })

    const nav = screen.getByRole('navigation', { name: 'Administration' })
    for (const label of [
      'Dashboard',
      'Users',
      'Organizations',
      'Ideas',
      'Reviews',
      'Approvals',
      'Categories',
    ]) {
      expect(nav).toHaveTextContent(label)
    }
    expect(screen.queryByText('Not authorized')).not.toBeInTheDocument()
  })

  it('waits for the access check rather than flashing a verdict', () => {
    renderAdminPage(<AdminLayout />, { status: 'loading', capabilities: NO_ADMIN_CAPABILITIES })

    expect(screen.getByText('Checking administrator access…')).toBeInTheDocument()
    expect(screen.queryByText('Not authorized')).not.toBeInTheDocument()
  })

  it('says it could not check, rather than "not allowed", when the check fails', () => {
    renderAdminPage(<AdminLayout />, { status: 'error', capabilities: NO_ADMIN_CAPABILITIES })

    expect(screen.getByRole('alert')).toHaveTextContent(
      'We could not check your administrator access.',
    )
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })
})
