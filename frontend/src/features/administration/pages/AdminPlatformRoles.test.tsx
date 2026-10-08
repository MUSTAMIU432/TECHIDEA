import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { READ_ONLY_ADMIN, renderAdminPage } from '../../../test/renderAdmin'
import { AdminPlatformRolesPage } from './AdminPlatformRolesPage'

vi.mock('../api/platformRolesApi')
const api = await import('../api/platformRolesApi')

const DANA = { id: '7', email: 'dana@example.com', name: 'Dana Developer' }
const ROLES = [
  {
    key: 'developer',
    label: 'Developer',
    description: 'Can be assigned an approved idea to build.',
    holders: [DANA],
  },
  {
    key: 'delivery_manager',
    label: 'Delivery manager',
    description: 'Runs delivery.',
    holders: [],
  },
]
const OK = { success: true, message: 'Done.', field: null }

beforeEach(() => {
  vi.mocked(api.platformRolesRequest).mockResolvedValue(ROLES)
})

describe('AdminPlatformRolesPage', () => {
  it('lists each role with its holders and where its work is assigned', async () => {
    renderAdminPage(<AdminPlatformRolesPage />)

    expect(await screen.findByText('Dana Developer')).toBeInTheDocument()
    expect(screen.getByText('Nobody holds this role yet.')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Developer Queue' })).toHaveLength(2)
  })

  it('gives a role by email and shows the server’s own words on a refusal', async () => {
    vi.mocked(api.grantPlatformRoleRequest).mockResolvedValue({
      success: false,
      message: 'No account uses this email address.',
      field: 'email',
    })
    renderAdminPage(<AdminPlatformRolesPage />)

    const form = await screen.findByRole('form', { name: 'Give the Delivery manager role' })
    fireEvent.change(within(form).getByLabelText('Account email'), {
      target: { value: 'nobody@example.com' },
    })
    fireEvent.click(within(form).getByRole('button', { name: 'Add' }))

    await waitFor(() =>
      expect(api.grantPlatformRoleRequest).toHaveBeenCalledWith(
        'delivery_manager',
        'nobody@example.com',
      ),
    )
    expect(await screen.findByText('No account uses this email address.')).toBeInTheDocument()
  })

  it('removes a holder', async () => {
    vi.mocked(api.revokePlatformRoleRequest).mockResolvedValue(OK)
    renderAdminPage(<AdminPlatformRolesPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'Remove' }))

    await waitFor(() =>
      expect(api.revokePlatformRoleRequest).toHaveBeenCalledWith('developer', '7'),
    )
  })

  it('offers no forms or buttons without the permission', async () => {
    renderAdminPage(<AdminPlatformRolesPage />, { capabilities: READ_ONLY_ADMIN })

    expect(await screen.findByText('Dana Developer')).toBeInTheDocument()
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove' })).not.toBeInTheDocument()
  })
})
