import type { IdeaPageInfo } from '../api/ideasApi'

/**
 * "Showing 1-20 of 137", and the two buttons that move a page.
 *
 * Rendered from the server's `pageInfo` and never from the array it returned.
 * That distinction is the whole point: a client that derived "you are on the
 * last page" from `items.length < limit` would say so while a filter was
 * still loading, and a client that derived the total from a client-side count
 * would have to have every row to do it — which is the thing paging exists to
 * avoid.
 *
 * The buttons move by the *applied* `limit` rather than by a page number, so
 * "next" cannot be off by one when the server clamped the page size.
 */
export function IdeaPagination({
  pageInfo,
  onOffsetChange,
}: {
  pageInfo: IdeaPageInfo
  onOffsetChange: (offset: number) => void
}) {
  // One past the end, clamped to the total: "20-20 of 20" on the last page
  // rather than "20-40 of 20", which is what a naive `offset + items.length`
  // would print whenever the database returned fewer rows than asked for.
  const first = pageInfo.totalCount === 0 ? 0 : pageInfo.offset + 1
  const last = Math.min(pageInfo.offset + pageInfo.limit, pageInfo.totalCount)
  const atStart = !pageInfo.hasPreviousPage
  const atEnd = !pageInfo.hasNextPage

  // Nothing at all, or nothing to go to. A disabled pair of buttons and a
  // "showing 1-1 of 1" is a control the reader cannot use, reporting nothing
  // they cannot already see from the list itself.
  if (pageInfo.totalCount === 0 || (!pageInfo.hasNextPage && !pageInfo.hasPreviousPage)) {
    return null
  }

  return (
    <nav
      aria-label="Ideas pagination"
      className="mt-4 flex items-center justify-between gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm"
    >
      <p className="text-xs text-gray-600">
        Showing <span className="font-semibold text-gray-900">{first}</span>-
        <span className="font-semibold text-gray-900">{last}</span> of{' '}
        <span className="font-semibold text-gray-900">{pageInfo.totalCount}</span>
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={atStart}
          onClick={() => onOffsetChange(Math.max(0, pageInfo.offset - pageInfo.limit))}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Previous
        </button>
        <button
          type="button"
          disabled={atEnd}
          onClick={() => onOffsetChange(pageInfo.offset + pageInfo.limit)}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Next
        </button>
      </div>
    </nav>
  )
}
