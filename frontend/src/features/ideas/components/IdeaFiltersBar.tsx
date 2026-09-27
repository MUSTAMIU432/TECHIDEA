import { useEffect, useState } from 'react'

import type { IdeaCategory, IdeaFilters, IdeaStatus } from '../api/ideasApi'

/**
 * The controls that narrow a discovery query: a search box, a category, a
 * status.
 *
 * Fully controlled, with one piece of local state for the text in the box.
 * That state is the *request* the reader is making, not the request the server
 * has answered: it is what they typed, which is not the same thing as the
 * `search` filter in effect, and conflating them is how a search box ends up
 * showing a term that was never searched for.
 *
 * Nothing here decides what a filter may do. Every control can only narrow,
 * because the vocabulary of narrowings is the backend's `IdeaFiltersInput`:
 * there is no visibility control and no author control, and a control that
 * filtered for one would be a control the server has to defend against. The
 * statuses are all seven rather than the two S2-002 can produce, for the same
 * reason `IdeaStatus` lists all seven — the union is the contract.
 */

/** How long the box waits after the last keystroke before it is applied. */
export const SEARCH_DEBOUNCE_MS = 300

const ALL_STATUSES: ReadonlyArray<{ value: IdeaStatus; label: string }> = [
  { value: 'DRAFT', label: 'Draft' },
  { value: 'SUBMITTED', label: 'Submitted' },
  { value: 'UNDER_REVIEW', label: 'Under review' },
  { value: 'CHANGES_REQUESTED', label: 'Changes requested' },
  { value: 'REJECTED', label: 'Rejected' },
  { value: 'APPROVED', label: 'Approved' },
  { value: 'AUTOMATION_PROPOSAL', label: 'Automation proposal' },
]

export function IdeaFiltersBar({
  filters,
  categories,
  onSearchChange,
  onCategoryChange,
  onStatusChange,
  onClear,
}: {
  filters: IdeaFilters
  categories: IdeaCategory[]
  /**
   * The reader typed. Applied as a filter after the debounce in the parent,
   * which is where the timer lives — this component reports keystrokes and
   * nothing else.
   */
  onSearchChange: (search: string) => void
  onCategoryChange: (categoryId: string | null) => void
  onStatusChange: (status: IdeaStatus | null) => void
  onClear: () => void
}) {
  const appliedSearch = filters.search ?? ''
  // Keyed by the applied value rather than synced from it: the applied value
  // changes a debounce's worth *after* the text does, and an effect that
  // pushed the applied value back into the box would overwrite what somebody
  // is still typing. The key is what makes the one case that must sync — the
  // "Clear" button — land, by remounting the input at the empty value.
  const [text, setText] = useState({ value: appliedSearch, appliedSearch })

  useEffect(() => {
    if (text.appliedSearch !== appliedSearch) setText({ value: appliedSearch, appliedSearch })
    // `text.appliedSearch` is read, not depended on: this effect exists to
    // notice that the *applied* value moved on, and depending on the old one
    // would make it re-run for its own state write.
    // oxlint-disable-next-line react/exhaustive-deps
  }, [appliedSearch])

  const hasFilters = Boolean(appliedSearch || filters.categoryId || filters.status)

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <label htmlFor="idea-search" className="block text-xs font-semibold text-gray-700">
            Search
          </label>
          <input
            id="idea-search"
            type="search"
            value={text.value}
            placeholder="Search titles and descriptions"
            onChange={(event) => {
              setText((previous) => ({ ...previous, value: event.target.value }))
              onSearchChange(event.target.value)
            }}
            className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
          />
        </div>

        <div>
          <label htmlFor="idea-category" className="block text-xs font-semibold text-gray-700">
            Category
          </label>
          <select
            id="idea-category"
            value={filters.categoryId ?? ''}
            onChange={(event) => onCategoryChange(event.target.value || null)}
            className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
          >
            <option value="">All categories</option>
            {categories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label htmlFor="idea-status" className="block text-xs font-semibold text-gray-700">
            Status
          </label>
          <select
            id="idea-status"
            value={filters.status ?? ''}
            onChange={(event) => onStatusChange((event.target.value || null) as IdeaStatus | null)}
            className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200"
          >
            <option value="">Any status</option>
            {ALL_STATUSES.map((status) => (
              <option key={status.value} value={status.value}>
                {status.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {hasFilters && (
        <button
          type="button"
          onClick={onClear}
          className="mt-3 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          Clear filters
        </button>
      )}
    </div>
  )
}
