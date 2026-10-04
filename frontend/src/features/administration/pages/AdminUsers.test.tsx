import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import type { AdminUser, AdminUserDetail } from '../api/administrationApi'
import { pageOfItems, READ_ONLY_ADMIN, renderAdminPage } from '../../../test/renderAdmin'
import { AdminUserDetailPage } from './AdminUserDetailPage'
import { AdminUsersPage } from './AdminUsersPage'

vi.mock('../api/administrationApi', async (importOriginal) => ({
  ...(await importOriginal()),
  adminUsersRequest: vi.fn(),
  adminUserRequest: vi.fn(),
  setUserActiveRequest: vi.fn(),
}))
vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))

const { adminUsersRequest, adminUserRequest, setUserActiveRequest } =
  await import('../api/administrationApi')
const usersMock = vi.mocked(adminUsersRequest)
const userMock = vi.mocked(adminUserRequest)
const setActiveMock = vi.mocked(setUserActiveRequest)

function user(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    id: '5',
    email: 'ada@acme.example',
    firstName: 'Ada',
    lastName: 'Author',
    isActive: true,
    isVerified: true,
    isPlatformAdmin: false,
    isSuperuser: false,
    createdAt: '2026-01-01T00:00:00Z',
    lastActiveAt: '2026-09-01T00:00:00Z',
    memberships: [
      {
        id: '8',
        status: 'active',
        organization: { id: '3', name: 'Acme' },
        roles: [{ id: '2', name: 'Reviewer', slug: 'reviewer', isSystem: false }],
      },
    ],
    ...overrides,
  }
}

function detail(overrides: Partial<AdminUserDetail> = {}): AdminUserDetail {
  return {
    ...user(),
    phoneNumber: '+255712345678',
    signInMethods: ['password', 'google'],
    ideaCount: 4,
    reviewCount: 2,
    commentCount: 1,
    auditEntries: [],
    ...overrides,
  }
}

describe('AdminUsersPage', () => {
  beforeEach(() => {
    vi.useRealTimers()
    usersMock.mockReset()
  })

  it('lists accounts with their status, organizations and roles', async () => {
    usersMock.mockResolvedValue(
      pageOfItems([user(), user({ id: '6', email: 'pat@x.example', isPlatformAdmin: true })]),
    )
    renderAdminPage(<AdminUsersPage />)

    const table = await screen.findByRole('table', { name: 'Users' })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(3)
    expect(rows[1]).toHaveTextContent('Ada Author')
    expect(rows[1]).toHaveTextContent('Acme · Reviewer')
    expect(rows[1]).toHaveTextContent('Active')
    expect(rows[2]).toHaveTextContent('Platform admin')
    expect(within(rows[1]).getByRole('link', { name: 'Ada Author' })).toHaveAttribute(
      'href',
      '/app/admin/users/5',
    )
  })

  it('never shows a password or token column', async () => {
    usersMock.mockResolvedValue(pageOfItems([user()]))
    renderAdminPage(<AdminUsersPage />)

    const table = await screen.findByRole('table', { name: 'Users' })
    expect(table).not.toHaveTextContent(/password|token/i)
  })

  it('searches on the server, once typing stops', async () => {
    vi.useFakeTimers()
    usersMock.mockResolvedValue(pageOfItems([user()]))
    renderAdminPage(<AdminUsersPage />)
    await act(async () => {})

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search users' }), {
      target: { value: 'ada' },
    })
    await act(async () => {
      vi.advanceTimersByTime(350)
    })

    expect(usersMock).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'ada' }), {
      offset: 0,
    })
    vi.useRealTimers()
  })

  it('filters by status on the server', async () => {
    usersMock.mockResolvedValue(pageOfItems([user()]))
    renderAdminPage(<AdminUsersPage />)
    await screen.findByRole('table', { name: 'Users' })

    fireEvent.change(screen.getByRole('combobox', { name: 'Account status' }), {
      target: { value: 'deactivated' },
    })

    await waitFor(() =>
      expect(usersMock).toHaveBeenLastCalledWith(expect.objectContaining({ isActive: false }), {
        offset: 0,
      }),
    )
  })

  it('shows an empty state when nothing matches', async () => {
    usersMock.mockResolvedValue(pageOfItems([]))
    renderAdminPage(<AdminUsersPage />)

    expect(await screen.findByText('No accounts match.')).toBeInTheDocument()
  })

  it('shows loading, then an error with a retry', async () => {
    let reject: (reason: unknown) => void = () => {}
    usersMock.mockReturnValueOnce(new Promise((_, r) => (reject = r)))
    renderAdminPage(<AdminUsersPage />)
    expect(screen.getByText('Loading accounts…')).toBeInTheDocument()

    await act(async () => reject(new Error('down')))
    expect(screen.getByRole('alert')).toHaveTextContent('We could not load the accounts.')
  })

  it('pages through the server’s results', async () => {
    usersMock.mockResolvedValue(
      pageOfItems([user()], { totalCount: 45, limit: 20, hasNextPage: true }),
    )
    renderAdminPage(<AdminUsersPage />)
    await screen.findByRole('table', { name: 'Users' })

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    await waitFor(() =>
      expect(usersMock).toHaveBeenLastCalledWith(expect.anything(), { offset: 20 }),
    )
  })
})

describe('AdminUserDetailPage', () => {
  beforeEach(() => {
    userMock.mockReset()
    setActiveMock.mockReset()
    vi.mocked(useAuth).mockReturnValue({
      user: { id: '1', email: 'admin@platform.example' },
    } as unknown as ReturnType<typeof useAuth>)
  })

  function renderDetail(capabilities = undefined as undefined | typeof READ_ONLY_ADMIN) {
    return renderAdminPage(<AdminUserDetailPage />, {
      path: '/users/5',
      pattern: '/users/:userId',
      ...(capabilities ? { capabilities } : {}),
    })
  }

  it('shows the profile, memberships and activity - never a credential', async () => {
    userMock.mockResolvedValue(detail())
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Ada Author' })).toBeInTheDocument()
    expect(screen.getByText('Email and password, Google')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Acme' })).toHaveAttribute(
      'href',
      '/app/admin/organizations/3',
    )
    expect(screen.getByRole('link', { name: '4' })).toHaveAttribute(
      'href',
      '/app/admin/ideas?authorId=5',
    )
    expect(userMock).toHaveBeenCalledWith('5')
  })

  it('asks for confirmation, explaining the consequences, before deactivating', async () => {
    userMock.mockResolvedValue(detail())
    setActiveMock.mockResolvedValue({
      success: true,
      message: 'Account deactivated.',
      field: null,
      user: user({ isActive: false }),
    })
    renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: 'Deactivate account' }))

    const dialog = screen.getByRole('dialog', { name: 'Deactivate ada@acme.example?' })
    expect(dialog).toHaveTextContent('signed out everywhere immediately')
    expect(dialog).toHaveTextContent('Their ideas, comments and reviews stay')
    expect(setActiveMock).not.toHaveBeenCalled()

    fireEvent.change(within(dialog).getByLabelText(/Reason/), {
      target: { value: 'Left the company' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Deactivate' }))

    await waitFor(() =>
      expect(setActiveMock).toHaveBeenCalledWith({
        userId: '5',
        isActive: false,
        reason: 'Left the company',
      }),
    )
    expect(await screen.findByText('Account deactivated.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('cancelling the dialog changes nothing', async () => {
    userMock.mockResolvedValue(detail())
    renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: 'Deactivate account' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(setActiveMock).not.toHaveBeenCalled()
  })

  it('shows the server’s refusal inside the dialog', async () => {
    userMock.mockResolvedValue(detail())
    setActiveMock.mockResolvedValue({
      success: false,
      message: "Only a superuser can change a superuser's account.",
      field: null,
      user: null,
    })
    renderDetail()

    fireEvent.click(await screen.findByRole('button', { name: 'Deactivate account' }))
    fireEvent.click(screen.getByRole('button', { name: 'Deactivate' }))

    const dialog = screen.getByRole('dialog')
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      "Only a superuser can change a superuser's account.",
    )
  })

  it('offers no account action to an administrator without that permission', async () => {
    userMock.mockResolvedValue(detail())
    renderDetail(READ_ONLY_ADMIN)

    await screen.findByRole('heading', { name: 'Ada Author' })
    expect(screen.queryByRole('button', { name: /activate account/i })).not.toBeInTheDocument()
  })

  it('offers no account action on your own account', async () => {
    userMock.mockResolvedValue(detail({ id: '1', email: 'admin@platform.example' }))
    renderDetail()

    await screen.findByRole('heading', { name: 'Ada Author' })
    expect(screen.queryByRole('button', { name: /activate account/i })).not.toBeInTheDocument()
  })

  it('says so when the account does not exist', async () => {
    userMock.mockResolvedValue(null)
    renderDetail()

    expect(await screen.findByText('Account not found.')).toBeInTheDocument()
  })
})
