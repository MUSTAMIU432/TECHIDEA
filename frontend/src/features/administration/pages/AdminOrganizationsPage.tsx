import { Link } from 'react-router-dom'

import { adminOrganizationsRequest } from '../api/administrationApi'
import {
  AdminPageHeader,
  AdminPagination,
  AdminTable,
  EmptyState,
  ErrorState,
  LoadingState,
  cellClasses,
} from '../components/AdminUi'
import { SearchBox } from '../components/SearchBox'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDay } from '../utils/format'

/** Every organization on the platform, with its size and activity. */
export function AdminOrganizationsPage() {
  const filters = useUrlFilters()
  const search = filters.get('search')

  const { data, loading, error, reload } = useAdminQuery(
    `organizations:${filters.key}`,
    () => adminOrganizationsRequest(search, { offset: filters.offset }),
    'We could not load the organizations.',
  )

  return (
    <>
      <AdminPageHeader
        title="Organizations"
        description="Every organization on the platform. Visible here across tenants because this is the platform console."
      />
      <div className="mb-4">
        <SearchBox
          label="Search organizations"
          placeholder="Name or slug"
          initialValue={search}
          onSearch={(value) => filters.set('search', value)}
        />
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading organizations…" />}
      {data && data.items.length === 0 && (
        <EmptyState title="No organizations match." description="Try a different search." />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable
            label="Organizations"
            columns={['Organization', 'Members', 'Owners', 'Reviewers', 'Ideas', 'Created']}
          >
            {data.items.map((organization) => (
              <tr key={organization.id}>
                <td className={cellClasses}>
                  <Link
                    to={`/app/admin/organizations/${organization.id}`}
                    state={filters.here}
                    className="font-semibold text-slate-900 hover:underline"
                  >
                    {organization.name}
                  </Link>
                  <p className="text-xs text-slate-500">{organization.slug}</p>
                </td>
                <td className={`${cellClasses} tabular-nums`}>{organization.memberCount}</td>
                <td className={`${cellClasses} tabular-nums`}>{organization.ownerCount}</td>
                <td className={`${cellClasses} tabular-nums`}>{organization.reviewerCount}</td>
                <td className={`${cellClasses} tabular-nums`}>{organization.ideaCount}</td>
                <td className={`${cellClasses} whitespace-nowrap`}>
                  {formatDay(organization.createdAt)}
                </td>
              </tr>
            ))}
          </AdminTable>
          <AdminPagination
            label="Organizations pagination"
            pageInfo={data.pageInfo}
            onOffsetChange={filters.setOffset}
          />
        </>
      )}
    </>
  )
}
