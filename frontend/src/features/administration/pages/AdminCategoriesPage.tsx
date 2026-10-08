import { useId, useState, type FormEvent } from 'react'

import {
  adminCategoriesRequest,
  createCategoryRequest,
  setCategoryActiveRequest,
  updateCategoryRequest,
  type AdminCategory,
} from '../api/administrationApi'
import {
  AdminCard,
  AdminPageHeader,
  AdminTable,
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  cellClasses,
  controlClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
} from '../components/AdminUi'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { useAdminQuery } from '../hooks/useAdminQuery'

/**
 * The platform-wide category list the idea form offers - the same backend
 * `Category` rows, not a copy. Categories are retired, never deleted: every
 * idea filed under one keeps it, and a retired category just stops being
 * offered for new ideas.
 */
export function AdminCategoriesPage() {
  const { capabilities } = useAdminCapabilities()
  const { data, loading, error, reload } = useAdminQuery(
    'admin-categories',
    adminCategoriesRequest,
    'We could not load the categories.',
  )
  const [notice, setNotice] = useState<string | null>(null)
  const [editing, setEditing] = useState<AdminCategory | null>(null)
  const [toggling, setToggling] = useState<AdminCategory | null>(null)
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const canManage = capabilities.canManageCategories

  function changed(message: string) {
    setNotice(message)
    reload()
  }

  async function confirmToggle() {
    if (!toggling) return
    setBusy(true)
    setDialogError(null)
    try {
      const result = await setCategoryActiveRequest({
        id: toggling.id,
        isActive: !toggling.isActive,
      })
      if (!result.success) {
        setDialogError(result.message)
        return
      }
      setToggling(null)
      changed(result.message)
    } catch {
      setDialogError('We could not reach the server. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <AdminPageHeader
        title="Categories"
        description="The classifications authors choose from when they file an idea. Shared by every organization."
      />
      <div className="space-y-6">
        {notice && <Notice tone="success">{notice}</Notice>}
        {canManage && (
          <AdminCard title="New category">
            <CategoryForm
              submitLabel="Create category"
              onSubmit={async (values) => {
                const result = await createCategoryRequest(values)
                if (result.success) changed(result.message)
                return result
              }}
            />
          </AdminCard>
        )}

        {error && <ErrorState message={error} onRetry={reload} />}
        {loading && !data && <LoadingState label="Loading categories…" />}
        {data && data.length === 0 && (
          <EmptyState
            title="No categories yet."
            description="Authors need at least one category to submit an idea."
          />
        )}
        {data && data.length > 0 && (
          <AdminTable
            label="Categories"
            columns={['Category', 'Status', 'Ideas', ...(canManage ? ['Actions'] : [])]}
          >
            {data.map((category) => (
              <tr key={category.id}>
                <td className={cellClasses}>
                  <p className="font-semibold text-slate-900">{category.name}</p>
                  <p className="text-xs text-slate-500">
                    {category.slug}
                    {category.description && ` · ${category.description}`}
                  </p>
                </td>
                <td className={cellClasses}>
                  {category.isActive ? (
                    <Badge tone="good">Active</Badge>
                  ) : (
                    <Badge tone="neutral">Retired</Badge>
                  )}
                </td>
                <td className={`${cellClasses} tabular-nums`}>{category.ideaCount}</td>
                {canManage && (
                  <td className={`${cellClasses} whitespace-nowrap`}>
                    <span className="flex gap-2" aria-label={`Actions for ${category.name}`}>
                      <button
                        type="button"
                        onClick={() => setEditing(category)}
                        className={secondaryButtonClasses}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setDialogError(null)
                          setToggling(category)
                        }}
                        className={secondaryButtonClasses}
                      >
                        {category.isActive ? 'Retire' : 'Restore'}
                      </button>
                    </span>
                  </td>
                )}
              </tr>
            ))}
          </AdminTable>
        )}
      </div>

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <dialog
            open
            aria-modal="true"
            aria-label={`Edit ${editing.name}`}
            className="relative m-0 w-full max-w-md rounded-xl bg-white p-5 text-left shadow-2xl"
          >
            <h2 className="mb-3 text-base font-bold text-slate-900">Edit {editing.name}</h2>
            <CategoryForm
              initial={editing}
              submitLabel="Save changes"
              onCancel={() => setEditing(null)}
              onSubmit={async (values) => {
                const result = await updateCategoryRequest({ id: editing.id, ...values })
                if (result.success) {
                  setEditing(null)
                  changed(result.message)
                }
                return result
              }}
            />
            <p className="mt-3 text-xs text-slate-500">
              The slug stays <code>{editing.slug}</code>. Ideas filed under this category keep it.
            </p>
          </dialog>
        </div>
      )}

      {toggling && (
        <ConfirmDialog
          title={toggling.isActive ? `Retire ${toggling.name}?` : `Restore ${toggling.name}?`}
          confirmLabel={toggling.isActive ? 'Retire category' : 'Restore category'}
          danger={toggling.isActive}
          busy={busy}
          error={dialogError}
          onCancel={() => setToggling(null)}
          onConfirm={() => void confirmToggle()}
        >
          {toggling.isActive ? (
            <>
              <p>Authors will no longer be able to choose it for new ideas.</p>
              <p>
                The {toggling.ideaCount} {toggling.ideaCount === 1 ? 'idea' : 'ideas'} already filed
                under it keep it. Nothing is deleted, and you can restore it later.
              </p>
            </>
          ) : (
            <p>Authors will be able to choose it for new ideas again.</p>
          )}
        </ConfirmDialog>
      )}
    </>
  )
}

interface CategoryFormResult {
  success: boolean
  message: string
  field: string | null
}

function CategoryForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: AdminCategory
  submitLabel: string
  onSubmit: (values: { name: string; description: string }) => Promise<CategoryFormResult>
  onCancel?: () => void
}) {
  const nameId = useId()
  const descriptionId = useId()
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim()) {
      setError('Enter a category name.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await onSubmit({ name: name.trim(), description: description.trim() })
      if (!result.success) {
        setError(result.message)
      } else if (!initial) {
        setName('')
        setDescription('')
      }
    } catch {
      setError('We could not reach the server. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-3" noValidate>
      <div className="flex flex-wrap gap-3">
        <div className="min-w-48 flex-1">
          <label htmlFor={nameId} className="block text-xs font-semibold text-slate-700">
            Name
          </label>
          <input
            id={nameId}
            value={name}
            maxLength={120}
            onChange={(event) => setName(event.target.value)}
            className={`${controlClasses} mt-1 w-full`}
          />
        </div>
        <div className="min-w-48 flex-[2]">
          <label htmlFor={descriptionId} className="block text-xs font-semibold text-slate-700">
            Description
          </label>
          <input
            id={descriptionId}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            className={`${controlClasses} mt-1 w-full`}
          />
        </div>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-2">
        <button type="submit" disabled={busy} className={primaryButtonClasses}>
          {busy ? 'Saving…' : submitLabel}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className={secondaryButtonClasses}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
