import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AdminCategory } from '../api/administrationApi'
import { READ_ONLY_ADMIN, renderAdminPage } from '../../../test/renderAdmin'
import { AdminCategoriesPage } from './AdminCategoriesPage'

vi.mock('../api/administrationApi', async (importOriginal) => ({
  ...(await importOriginal()),
  adminCategoriesRequest: vi.fn(),
  createCategoryRequest: vi.fn(),
  updateCategoryRequest: vi.fn(),
  setCategoryActiveRequest: vi.fn(),
}))

const api = await import('../api/administrationApi')
const listMock = vi.mocked(api.adminCategoriesRequest)
const createMock = vi.mocked(api.createCategoryRequest)
const updateMock = vi.mocked(api.updateCategoryRequest)
const setActiveMock = vi.mocked(api.setCategoryActiveRequest)

function category(overrides: Partial<AdminCategory> = {}): AdminCategory {
  return {
    id: '4',
    name: 'Finance',
    slug: 'finance',
    description: 'Money matters',
    isActive: true,
    ideaCount: 3,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('AdminCategoriesPage', () => {
  beforeEach(() => {
    listMock
      .mockReset()
      .mockResolvedValue([
        category(),
        category({ id: '5', name: 'Old', slug: 'old', isActive: false, ideaCount: 0 }),
      ])
    createMock.mockReset()
    updateMock.mockReset()
    setActiveMock.mockReset()
  })

  it('lists active and retired categories with their idea counts', async () => {
    renderAdminPage(<AdminCategoriesPage />)

    const rows = within(await screen.findByRole('table', { name: 'Categories' })).getAllByRole(
      'row',
    )
    expect(rows[1]).toHaveTextContent('Finance')
    expect(rows[1]).toHaveTextContent('Active')
    expect(rows[1]).toHaveTextContent('3')
    expect(rows[2]).toHaveTextContent('Retired')
    expect(screen.queryByRole('button', { name: /delete/i })).not.toBeInTheDocument()
  })

  it('creates a category and reloads the list', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'Category created.',
      field: null,
      category: category({ id: '6', name: 'Logistics' }),
    })
    renderAdminPage(<AdminCategoriesPage />)
    await screen.findByRole('table', { name: 'Categories' })

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Logistics' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create category' }))

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({ name: 'Logistics', description: '' }),
    )
    expect(await screen.findByText('Category created.')).toBeInTheDocument()
    await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))
  })

  it('shows the server’s refusal of a duplicate name', async () => {
    createMock.mockResolvedValue({
      success: false,
      message: 'A category with this name already exists.',
      field: 'name',
      category: null,
    })
    renderAdminPage(<AdminCategoriesPage />)
    await screen.findByRole('table', { name: 'Categories' })

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Finance' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create category' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'A category with this name already exists.',
    )
  })

  it('confirms before retiring, explaining existing ideas keep it', async () => {
    setActiveMock.mockResolvedValue({
      success: true,
      message: 'Category retired.',
      field: null,
      category: category({ isActive: false }),
    })
    renderAdminPage(<AdminCategoriesPage />)

    const row = within(await screen.findByRole('table', { name: 'Categories' })).getAllByRole(
      'row',
    )[1]
    fireEvent.click(within(row).getByRole('button', { name: 'Retire' }))

    const dialog = screen.getByRole('dialog', { name: 'Retire Finance?' })
    expect(dialog).toHaveTextContent('The 3 ideas already filed under it keep it')
    expect(setActiveMock).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Retire category' }))

    await waitFor(() => expect(setActiveMock).toHaveBeenCalledWith({ id: '4', isActive: false }))
    expect(await screen.findByText('Category retired.')).toBeInTheDocument()
  })

  it('edits a category in a dialog, keeping its slug', async () => {
    updateMock.mockResolvedValue({
      success: true,
      message: 'Category updated.',
      field: null,
      category: category({ name: 'Money' }),
    })
    renderAdminPage(<AdminCategoriesPage />)

    const row = within(await screen.findByRole('table', { name: 'Categories' })).getAllByRole(
      'row',
    )[1]
    fireEvent.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit Finance' })
    expect(dialog).toHaveTextContent('The slug stays finance')

    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Money' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith({
        id: '4',
        name: 'Money',
        description: 'Money matters',
      }),
    )
  })

  it('is read-only without the category permission', async () => {
    renderAdminPage(<AdminCategoriesPage />, { capabilities: READ_ONLY_ADMIN })

    await screen.findByRole('table', { name: 'Categories' })
    expect(screen.queryByRole('button', { name: 'Create category' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retire' })).not.toBeInTheDocument()
  })

  it('shows an empty state and an error state', async () => {
    listMock.mockResolvedValueOnce([])
    const { unmount } = renderAdminPage(<AdminCategoriesPage />)
    expect(await screen.findByText('No categories yet.')).toBeInTheDocument()
    unmount()

    listMock.mockRejectedValueOnce(new Error('down'))
    renderAdminPage(<AdminCategoriesPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('We could not load the categories.')
  })
})
