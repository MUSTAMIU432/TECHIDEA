import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  AdminMember,
  AdminOrganization,
  AdminOrganizationDetail,
} from '../api/administrationApi'
import { pageOfItems, READ_ONLY_ADMIN, renderAdminPage } from '../../../test/renderAdmin'
import { AdminOrganizationDetailPage } from './AdminOrganizationDetailPage'
import { AdminOrganizationsPage } from './AdminOrganizationsPage'

vi.mock('../api/administrationApi', async (importOriginal) => ({
  ...(await importOriginal()),
  adminOrganizationsRequest: vi.fn(),
  adminOrganizationRequest: vi.fn(),
  adminOrganizationMembersRequest: vi.fn(),
  assignMembershipRoleRequest: vi.fn(),
  removeMembershipRoleRequest: vi.fn(),
}))

const api = await import('../api/administrationApi')
const listMock = vi.mocked(api.adminOrganizationsRequest)
const detailMock = vi.mocked(api.adminOrganizationRequest)
const membersMock = vi.mocked(api.adminOrganizationMembersRequest)
const assignMock = vi.mocked(api.assignMembershipRoleRequest)
const removeMock = vi.mocked(api.removeMembershipRoleRequest)

const ACME: AdminOrganization = {
  id: '3',
  name: 'Acme',
  slug: 'acme',
  createdAt: '2026-01-01T00:00:00Z',
  memberCount: 4,
  ideaCount: 7,
  ownerCount: 1,
  reviewerCount: 2,
}

const ACME_DETAIL: AdminOrganizationDetail = {
  ...ACME,
  roles: [
    {
      id: '1',
      name: 'Owner',
      slug: 'owner',
      description: '',
      isSystem: true,
      permissions: ['organization.view', 'idea.review'],
      holderCount: 1,
    },
    {
      id: '2',
      name: 'Reviewer',
      slug: 'reviewer',
      description: '',
      isSystem: false,
      permissions: ['organization.view', 'idea.review'],
      holderCount: 2,
    },
  ],
  ideasByStatus: [
    { status: 'APPROVED', count: 3 },
    { status: 'DRAFT', count: 0 },
  ],
}

function member(overrides: Partial<AdminMember> = {}): AdminMember {
  return {
    id: '10',
    status: 'active',
    joinedAt: '2026-02-01T00:00:00Z',
    user: { id: '5', email: 'rae@acme.example', name: 'Rae Reviewer' },
    userIsActive: true,
    roles: [{ id: '2', name: 'Reviewer', slug: 'reviewer', isSystem: false }],
    ...overrides,
  }
}

describe('AdminOrganizationsPage', () => {
  beforeEach(() => listMock.mockReset())

  it('lists organizations with members, owners, reviewers and ideas', async () => {
    listMock.mockResolvedValue(pageOfItems([ACME]))
    renderAdminPage(<AdminOrganizationsPage />)

    const row = within(await screen.findByRole('table', { name: 'Organizations' })).getAllByRole(
      'row',
    )[1]
    expect(row).toHaveTextContent('Acme')
    expect(row).toHaveTextContent('acme')
    expect(within(row).getByRole('link', { name: 'Acme' })).toHaveAttribute(
      'href',
      '/app/admin/organizations/3',
    )
  })

  it('shows an empty state', async () => {
    listMock.mockResolvedValue(pageOfItems([]))
    renderAdminPage(<AdminOrganizationsPage />)

    expect(await screen.findByText('No organizations match.')).toBeInTheDocument()
  })

  it('shows an error state', async () => {
    listMock.mockRejectedValueOnce(new Error('down'))
    renderAdminPage(<AdminOrganizationsPage />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not load the organizations.',
    )
  })
})

describe('AdminOrganizationDetailPage', () => {
  beforeEach(() => {
    detailMock.mockReset().mockResolvedValue(ACME_DETAIL)
    membersMock.mockReset().mockResolvedValue(pageOfItems([member()]))
    assignMock.mockReset()
    removeMock.mockReset()
  })

  function renderDetail(capabilities?: typeof READ_ONLY_ADMIN) {
    return renderAdminPage(<AdminOrganizationDetailPage />, {
      path: '/organizations/3',
      pattern: '/organizations/:organizationId',
      ...(capabilities ? { capabilities } : {}),
    })
  }

  it('shows roles with their holders, members, and a link to the organization’s ideas', async () => {
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Acme' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /7 — view ideas/ })).toHaveAttribute(
      'href',
      '/app/admin/ideas?organizationId=3',
    )
    const members = await screen.findByRole('table', { name: 'Members' })
    expect(members).toHaveTextContent('Rae Reviewer')
    expect(members).toHaveTextContent('Reviewer')
  })

  it('confirms before removing a role, then reloads', async () => {
    removeMock.mockResolvedValue({
      success: true,
      message: 'Role removed.',
      field: null,
      member: member({ roles: [] }),
    })
    renderDetail()

    fireEvent.click(
      await screen.findByRole('button', { name: 'Remove Reviewer from rae@acme.example' }),
    )
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('lose every permission of this role')
    expect(dialog).toHaveTextContent('recorded in the audit trail')
    expect(removeMock).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove role' }))

    await waitFor(() =>
      expect(removeMock).toHaveBeenCalledWith({ membershipId: '10', roleId: '2' }),
    )
    expect(await screen.findByText('Role removed.')).toBeInTheDocument()
    await waitFor(() => expect(membersMock).toHaveBeenCalledTimes(2))
  })

  it('confirms before assigning a role', async () => {
    assignMock.mockResolvedValue({
      success: true,
      message: 'Role assigned.',
      field: null,
      member: member(),
    })
    renderDetail()

    fireEvent.change(
      await screen.findByRole('combobox', { name: 'Assign a role to rae@acme.example' }),
      { target: { value: '1' } },
    )
    const dialog = screen.getByRole('dialog', { name: /Give rae@acme\.example the Owner role/ })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Assign role' }))

    await waitFor(() =>
      expect(assignMock).toHaveBeenCalledWith({ membershipId: '10', roleId: '1' }),
    )
  })

  it('shows the last-owner refusal from the server', async () => {
    membersMock.mockResolvedValue(
      pageOfItems([member({ roles: [{ id: '1', name: 'Owner', slug: 'owner', isSystem: true }] })]),
    )
    removeMock.mockResolvedValue({
      success: false,
      message: 'The last active holder of a system role cannot be removed.',
      field: null,
      member: null,
    })
    renderDetail()

    fireEvent.click(
      await screen.findByRole('button', { name: 'Remove Owner from rae@acme.example' }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Remove role' }))

    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent(
      'The last active holder of a system role cannot be removed.',
    )
  })

  it('offers no role controls without the organization-role permission', async () => {
    renderDetail(READ_ONLY_ADMIN)

    await screen.findByRole('table', { name: 'Members' })
    expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: /Assign a role/ })).not.toBeInTheDocument()
  })
})
