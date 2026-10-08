import { Link } from 'react-router-dom'

import { adminInvitationsRequest } from '../api/administrationApi'
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
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDay } from '../utils/format'

const SCOPES = [
  { value: '', label: 'All tenants' },
  { value: 'organization', label: 'Organizations' },
  { value: 'team', label: 'Teams' },
]

const STATUSES = [
  { value: '', label: 'Any status' },
  { value: 'pending', label: 'Pending' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'expired', label: 'Expired' },
  { value: 'revoked', label: 'Revoked' },
]

/**
 * Every invitation the platform has sent, for both kinds of tenant.
 *
 * **Nothing here can be replayed.** An invitation stores only the SHA-256 digest
 * of its token, so there is no token column to redact and no field an
 * administrator could copy into somebody else's browser. What the list is for is
 * the operational question - who was invited, to what, by whom, and can it still
 * be accepted - which is exactly what `isOpen` answers where `status` does not:
 * a pending invitation past its expiry is still "pending" in the database.
 *
 * The address is shown because this is the platform console and an invitation
 * row is the record of an address somebody was sent a link to. It is not shown
 * anywhere a member can see it, and no action is offered here - accepting or
 * revoking is the inviter's and the recipient's business.
 */
export function AdminInvitationsPage() {
  const filters = useUrlFilters()
  const scope = filters.get('scope')
  const status = filters.get('status')

  const { data, loading, error, reload } = useAdminQuery(
    `invitations:${filters.key}`,
    () => adminInvitationsRequest({ scope, status }, { offset: filters.offset }),
    'We could not load the invitations.',
  )

  return (
    <>
      <AdminPageHeader
        title="Invitations"
        description="Every invitation sent on the platform. An invitation is a request, not a membership: it becomes one only when the recipient accepts, which is why this console offers no way to create or accept one."
      />

      <div className="mb-4 flex flex-wrap gap-3">
        <div>
          <label className="block text-xs font-semibold text-slate-600" htmlFor="invitation-scope">
            Tenant
          </label>
          <select
            id="invitation-scope"
            className={`${controlClasses} mt-1`}
            value={scope}
            onChange={(event) => filters.set('scope', event.target.value)}
          >
            {SCOPES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs font-semibold text-slate-600" htmlFor="invitation-status">
            Status
          </label>
          <select
            id="invitation-status"
            className={`${controlClasses} mt-1`}
            value={status}
            onChange={(event) => filters.set('status', event.target.value)}
          >
            {STATUSES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading invitations…" />}
      {data && data.items.length === 0 && (
        <EmptyState title="No invitations match." description="Try a different filter." />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable
            label="Invitations"
            columns={['Invitee', 'Tenant', 'Role', 'Status', 'Invited by', 'Expires']}
          >
            {data.items.map((invitation) => (
              <tr key={invitation.id}>
                <td className={`${cellClasses} font-semibold text-slate-900`}>
                  {invitation.email}
                </td>
                <td className={cellClasses}>
                  {/*
                    A team invitation links to the team; an organization one to
                    the organization. Both are cross-tenant reads the console is
                    for, and both are already authorized by the console gate.
                  */}
                  {invitation.scope === 'team' && invitation.tenantId ? (
                    <Link
                      to={`/app/admin/teams/${invitation.tenantId}`}
                      className="font-semibold text-slate-900 hover:underline"
                    >
                      {invitation.tenantName}
                    </Link>
                  ) : (
                    <span>{invitation.tenantName}</span>
                  )}
                  <p className="text-xs text-slate-500">
                    {invitation.scope === 'team' ? 'Team' : 'Organization'}
                  </p>
                </td>
                <td className={cellClasses}>{invitation.roleSlug}</td>
                <td className={cellClasses}>
                  {invitation.isOpen ? (
                    <Badge tone="info">Waiting to be accepted</Badge>
                  ) : invitation.status === 'accepted' ? (
                    <Badge tone="good">Accepted</Badge>
                  ) : invitation.status === 'revoked' ? (
                    <Badge>Revoked</Badge>
                  ) : (
                    <Badge>Expired</Badge>
                  )}
                  {invitation.acceptedBy ? (
                    <p className="mt-1 text-xs text-slate-500">by {invitation.acceptedBy.email}</p>
                  ) : null}
                </td>
                <td className={cellClasses}>{invitation.invitedBy.email}</td>
                <td className={`${cellClasses} whitespace-nowrap`}>
                  {formatDay(invitation.expiresAt)}
                </td>
              </tr>
            ))}
          </AdminTable>
          <AdminPagination
            label="Invitations pagination"
            pageInfo={data.pageInfo}
            onOffsetChange={filters.setOffset}
          />
        </>
      )}
    </>
  )
}
