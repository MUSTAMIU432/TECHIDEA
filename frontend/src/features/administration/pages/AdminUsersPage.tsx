import { Link } from 'react-router-dom'

import { adminUsersRequest, type AdminUser } from '../api/administrationApi'
import {
  AdminPageHeader,
  AdminPagination,
  AdminTable,
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  cellClasses,
  controlClasses,
} from '../components/AdminUi'
import { SearchBox } from '../components/SearchBox'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDay, formatDateTime, fullName } from '../utils/format'

export function UserStatusBadges({ user }: { user: AdminUser }) {
  return (
    <span className="flex flex-wrap gap-1">
      {user.isActive ? <Badge tone="good">Active</Badge> : <Badge tone="bad">Deactivated</Badge>}
      {!user.isVerified && <Badge tone="warn">Unverified email</Badge>}
      {user.isPlatformAdmin && <Badge tone="admin">Platform admin</Badge>}
      {user.isSuperuser && <Badge tone="admin">Superuser</Badge>}
    </span>
  )
}

/** Every platform account, searched, filtered and paged by the server. */
export function AdminUsersPage() {
  const filters = useUrlFilters()
  const search = filters.get('search')
  const status = filters.get('status')
  const adminsOnly = filters.get('admins') === '1'
  const organizationId = filters.get('organizationId')

  const { data, loading, error, reload } = useAdminQuery(
    `users:${filters.key}`,
    () =>
      adminUsersRequest(
        {
          search,
          isActive: status === 'active' ? true : status === 'deactivated' ? false : null,
          platformAdminsOnly: adminsOnly,
          organizationId: organizationId || null,
        },
        { offset: filters.offset },
      ),
    'We could not load the accounts.',
  )

  return (
    <>
      <AdminPageHeader
        title="Users"
        description="Every platform account. Passwords, tokens and sessions are never shown here."
      />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchBox
          label="Search users"
          placeholder="Name or email"
          initialValue={search}
          onSearch={(value) => filters.set('search', value)}
        />
        <select
          aria-label="Account status"
          value={status}
          onChange={(event) => filters.set('status', event.target.value)}
          className={controlClasses}
        >
          <option value="">All accounts</option>
          <option value="active">Active</option>
          <option value="deactivated">Deactivated</option>
        </select>
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={adminsOnly}
            onChange={(event) => filters.set('admins', event.target.checked ? '1' : '')}
          />
          Platform administrators only
        </label>
        {organizationId && (
          <button
            type="button"
            onClick={() => filters.set('organizationId', '')}
            className="rounded-full bg-slate-200 px-3 py-1 text-xs font-semibold text-slate-700"
          >
            One organization only ✕
          </button>
        )}
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading accounts…" />}
      {data && data.items.length === 0 && (
        <EmptyState title="No accounts match." description="Try a different search or filter." />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable
            label="Users"
            columns={['Account', 'Status', 'Organizations and roles', 'Joined', 'Last active']}
          >
            {data.items.map((user) => (
              <tr key={user.id}>
                <td className={cellClasses}>
                  <Link
                    to={`/app/admin/users/${user.id}`}
                    state={filters.here}
                    className="font-semibold text-slate-900 hover:underline"
                  >
                    {fullName(user)}
                  </Link>
                  <p className="text-xs text-slate-500">{user.email}</p>
                </td>
                <td className={cellClasses}>
                  <UserStatusBadges user={user} />
                </td>
                <td className={cellClasses}>
                  {user.memberships.length === 0 ? (
                    <span className="text-slate-400">None</span>
                  ) : (
                    <ul className="space-y-0.5">
                      {user.memberships.map((membership) => (
                        <li key={membership.id} className="text-xs">
                          <span className="font-semibold">{membership.organization.name}</span>
                          {membership.roles.length > 0 &&
                            ` · ${membership.roles.map((role) => role.name).join(', ')}`}
                          {membership.status !== 'active' && ' (inactive)'}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className={`${cellClasses} whitespace-nowrap`}>{formatDay(user.createdAt)}</td>
                <td className={`${cellClasses} whitespace-nowrap`}>
                  {formatDateTime(user.lastActiveAt)}
                </td>
              </tr>
            ))}
          </AdminTable>
          <AdminPagination
            label="Users pagination"
            pageInfo={data.pageInfo}
            onOffsetChange={filters.setOffset}
          />
        </>
      )}
    </>
  )
}
