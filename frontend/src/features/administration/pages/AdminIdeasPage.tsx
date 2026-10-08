import { Link } from 'react-router-dom'

import type { IdeaStatus, IdeaVisibility } from '../../ideas/api/ideasApi'
import { STATUS_LABELS, VISIBILITY_LABELS } from '../../ideas/utils/lifecycle'
import { adminCategoriesRequest, adminIdeasRequest } from '../api/administrationApi'
import {
  AdminPageHeader,
  AdminPagination,
  AdminTable,
  EmptyState,
  ErrorState,
  IdeaStatusBadge,
  LoadingState,
  Restricted,
  cellClasses,
  controlClasses,
  primaryButtonClasses,
} from '../components/AdminUi'
import { SearchBox } from '../components/SearchBox'
import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDay, ideaHomeLabel, RESTRICTED_TITLE } from '../utils/format'
import { ASSIGN_REVIEW_TEAM_ANCHOR, awaitsReviewTeam } from '../utils/reviewRouting'

const STATUSES = Object.keys(STATUS_LABELS) as IdeaStatus[]
const VISIBILITIES = Object.keys(VISIBILITY_LABELS) as IdeaVisibility[]

function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <button
      type="button"
      onClick={onClear}
      className="rounded-full bg-slate-200 px-3 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-300"
    >
      {label} ✕
    </button>
  )
}

/**
 * Every idea on the platform, filtered and paged by the server. An idea whose
 * content this administrator may not read is listed by its metadata with its
 * title withheld - and search never matches a withheld title.
 */
export function AdminIdeasPage() {
  const filters = useUrlFilters()
  // Display only: the button is offered to whoever can route work, and the server decides.
  const canAssign = useAdminCapabilities().capabilities.canAssignPlatformReviewers
  const values = {
    search: filters.get('search'),
    status: filters.get('status') as IdeaStatus | '',
    visibility: filters.get('visibility') as IdeaVisibility | '',
    categoryId: filters.get('categoryId'),
    organizationId: filters.get('organizationId'),
    authorId: filters.get('authorId'),
    createdFrom: filters.get('createdFrom'),
    createdTo: filters.get('createdTo'),
  }

  const categories = useAdminQuery('categories', adminCategoriesRequest)
  const { data, loading, error, reload } = useAdminQuery(
    `ideas:${filters.key}`,
    () =>
      adminIdeasRequest(
        {
          search: values.search,
          status: values.status || null,
          visibility: values.visibility || null,
          categoryId: values.categoryId || null,
          organizationId: values.organizationId || null,
          authorId: values.authorId || null,
          createdFrom: values.createdFrom || null,
          createdTo: values.createdTo || null,
        },
        { offset: filters.offset },
      ),
    'We could not load the ideas.',
  )

  return (
    <>
      <AdminPageHeader
        title="Ideas"
        description="Problem submissions across every organization, in every lifecycle state."
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchBox
          label="Search ideas"
          placeholder="Title, author email or organization"
          initialValue={values.search}
          onSearch={(value) => filters.set('search', value)}
        />
        <select
          aria-label="Status"
          value={values.status}
          onChange={(event) => filters.set('status', event.target.value)}
          className={controlClasses}
        >
          <option value="">Any status</option>
          {STATUSES.map((status) => (
            <option key={status} value={status}>
              {STATUS_LABELS[status]}
            </option>
          ))}
        </select>
        <select
          aria-label="Visibility"
          value={values.visibility}
          onChange={(event) => filters.set('visibility', event.target.value)}
          className={controlClasses}
        >
          <option value="">Any visibility</option>
          {VISIBILITIES.map((visibility) => (
            <option key={visibility} value={visibility}>
              {VISIBILITY_LABELS[visibility]}
            </option>
          ))}
        </select>
        <select
          aria-label="Category"
          value={values.categoryId}
          onChange={(event) => filters.set('categoryId', event.target.value)}
          className={controlClasses}
        >
          <option value="">Any category</option>
          {(categories.data ?? []).map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
              {category.isActive ? '' : ' (retired)'}
            </option>
          ))}
        </select>
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          From
          <input
            type="date"
            aria-label="Created from"
            value={values.createdFrom}
            onChange={(event) => filters.set('createdFrom', event.target.value)}
            className={controlClasses}
          />
        </label>
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          To
          <input
            type="date"
            aria-label="Created to"
            value={values.createdTo}
            onChange={(event) => filters.set('createdTo', event.target.value)}
            className={controlClasses}
          />
        </label>
        {values.organizationId && (
          <FilterChip label="One organization" onClear={() => filters.set('organizationId', '')} />
        )}
        {values.authorId && (
          <FilterChip label="One author" onClear={() => filters.set('authorId', '')} />
        )}
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading ideas…" />}
      {data && data.items.length === 0 && (
        <EmptyState title="No ideas match." description="Try a different search or filter." />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable
            label="Ideas"
            columns={[
              'Idea',
              'Belongs to',
              'Author',
              'Category',
              'Status',
              'Review team',
              'Visibility',
              'Created',
              'Updated',
            ]}
          >
            {data.items.map((idea) => (
              <tr key={idea.id}>
                <td className={cellClasses}>
                  <Link
                    to={`/app/admin/ideas/${idea.id}`}
                    state={filters.here}
                    className="font-semibold text-slate-900 hover:underline"
                  >
                    {idea.title ?? <Restricted>{RESTRICTED_TITLE}</Restricted>}
                  </Link>
                  <p className="text-xs text-slate-500">
                    {idea.voteCount} votes · {idea.commentCount} comments · {idea.attachmentCount}{' '}
                    files
                  </p>
                </td>
                <td className={cellClasses}>
                  {idea.organization ? (
                    <Link
                      to={`/app/admin/organizations/${idea.organization.id}`}
                      className="hover:underline"
                    >
                      {idea.organization.name}
                    </Link>
                  ) : (
                    ideaHomeLabel(idea)
                  )}
                </td>
                <td className={cellClasses}>
                  <Link to={`/app/admin/users/${idea.author.id}`} className="hover:underline">
                    {idea.author.name}
                  </Link>
                </td>
                <td className={cellClasses}>{idea.categoryName ?? '—'}</td>
                <td className={cellClasses}>
                  <IdeaStatusBadge status={idea.status} />
                </td>
                <td className={`${cellClasses} whitespace-nowrap`}>
                  {idea.reviewTeam ? (
                    idea.reviewTeam.name
                  ) : canAssign && awaitsReviewTeam(idea.status, idea.reviewTeam) ? (
                    <Link
                      to={`/app/admin/ideas/${idea.id}#${ASSIGN_REVIEW_TEAM_ANCHOR}`}
                      state={filters.here}
                      className={primaryButtonClasses}
                    >
                      Assign to review team
                    </Link>
                  ) : (
                    '—'
                  )}
                </td>
                <td className={`${cellClasses} whitespace-nowrap`}>
                  {VISIBILITY_LABELS[idea.visibility]}
                </td>
                <td className={`${cellClasses} whitespace-nowrap`}>{formatDay(idea.createdAt)}</td>
                <td className={`${cellClasses} whitespace-nowrap`}>{formatDay(idea.updatedAt)}</td>
              </tr>
            ))}
          </AdminTable>
          <AdminPagination
            label="Ideas pagination"
            pageInfo={data.pageInfo}
            onOffsetChange={filters.setOffset}
          />
        </>
      )}
    </>
  )
}
