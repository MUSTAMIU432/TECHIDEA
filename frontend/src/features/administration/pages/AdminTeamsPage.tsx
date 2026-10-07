import { Link } from 'react-router-dom'

import { adminTeamsRequest } from '../api/administrationApi'
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

/**
 * Every team on the platform.
 *
 * A team is a collaboration boundary rather than a tenant, so this list is
 * about **who is writing ideas with whom** - not about a second kind of
 * organization. It sits beside Organizations rather than inside it because a
 * team belongs to no organization and validating one is not something this
 * console can do.
 *
 * `memberCount` and `inactiveMemberCount` are both shown because the inactive
 * rows are real: a team keeps the membership of somebody who left so it does
 * not re-invite them, and an administrator investigating "why does this team
 * have two people and three former ones" needs the second number to answer it.
 */
export function AdminTeamsPage() {
  const filters = useUrlFilters()
  const search = filters.get('search')

  const { data, loading, error, reload } = useAdminQuery(
    `teams:${filters.key}`,
    () => adminTeamsRequest(search, { offset: filters.offset }),
    'We could not load the teams.',
  )

  return (
    <>
      <AdminPageHeader
        title="Teams"
        description="Every team on the platform: a group of people who put an idea forward together. A team is not an organization — it has no reviewer, validates nothing and approves nothing."
      />
      <div className="mb-4">
        <SearchBox
          label="Search teams"
          placeholder="Name, slug or owner"
          initialValue={search}
          onSearch={(value) => filters.set('search', value)}
        />
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading teams…" />}
      {data && data.items.length === 0 && (
        <EmptyState
          title="No teams match."
          description="Try a different search. A reader with no organization can still create one."
        />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable
            label="Teams"
            columns={['Team', 'Members', 'Former members', 'Ideas', 'Invitations', 'Created']}
          >
            {data.items.map((team) => (
              <tr key={team.id}>
                <td className={cellClasses}>
                  <Link
                    to={`/app/admin/teams/${team.id}`}
                    state={filters.here}
                    className="font-semibold text-slate-900 hover:underline"
                  >
                    {team.name}
                  </Link>
                  <p className="text-xs text-slate-500">
                    /{team.slug} · owned by {team.owner.email}
                  </p>
                </td>
                <td className={`${cellClasses} tabular-nums`}>{team.memberCount}</td>
                <td className={`${cellClasses} tabular-nums`}>{team.inactiveMemberCount}</td>
                <td className={`${cellClasses} tabular-nums`}>{team.ideaCount}</td>
                <td className={`${cellClasses} tabular-nums`}>{team.invitationCount}</td>
                <td className={`${cellClasses} whitespace-nowrap`}>{formatDay(team.createdAt)}</td>
              </tr>
            ))}
          </AdminTable>
          <AdminPagination
            label="Teams pagination"
            pageInfo={data.pageInfo}
            onOffsetChange={filters.setOffset}
          />
        </>
      )}
    </>
  )
}
