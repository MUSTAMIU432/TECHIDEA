import { Link, useParams } from 'react-router-dom'

import {
  adminTeamInvitationsRequest,
  adminTeamRequest,
  type AdminTeamRole,
} from '../api/administrationApi'
import {
  AdminCard,
  AdminPageHeader,
  AdminPagination,
  AdminTable,
  Badge,
  DetailList,
  EmptyState,
  ErrorState,
  IdeaStatusBadge,
  LoadingState,
  cellClasses,
} from '../components/AdminUi'
import { BackLink } from '../components/BackLink'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useBackTarget } from '../hooks/useBackTarget'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDay } from '../utils/format'

/**
 * One team: its roles, its membership and the invitations it has sent.
 *
 * **Read-only, and that is the server's position rather than an omission.** The
 * teams domain offers create, add and leave, and all three are acts of
 * *participation*: somebody joins a team by accepting an invitation, or by
 * creating it. There is no administrative operation over a team, because the
 * only two facts here - who is in it, and what it was invited to - are the facts
 * the team domain already owns, and an administrator editing either of them
 * would be writing a membership that nobody accepted.
 */
export function AdminTeamDetailPage() {
  const { teamId = '' } = useParams()
  const backTo = useBackTarget('/app/admin/teams')
  const filters = useUrlFilters()
  const { data, loading, error, reload } = useAdminQuery(
    `team:${teamId}`,
    () => adminTeamRequest(teamId),
    'We could not load this team.',
  )
  const invitations = useAdminQuery(
    `team-invitations:${teamId}:${filters.key}`,
    () => adminTeamInvitationsRequest(teamId, { offset: filters.offset }),
    'We could not load this team’s invitations.',
  )

  if (error) return <ErrorState message={error} onRetry={reload} />
  if (loading && !data) return <LoadingState label="Loading team…" />
  if (!data) return <EmptyState title="Team not found." />

  return (
    <>
      <AdminPageHeader
        title={data.name}
        description={data.description || `/${data.slug}`}
        back={<BackLink to={backTo}>All teams</BackLink>}
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <AdminCard title="Team">
          <DetailList
            items={[
              ['Slug', `/${data.slug}`],
              ['Owner', data.owner.name],
              ['Active members', String(data.memberCount)],
              [
                'Former members',
                String(data.inactiveMemberCount) +
                  (data.inactiveMemberCount === 0 ? ' (memberships are kept)' : ''),
              ],
              ['Ideas filed for it', String(data.ideaCount)],
              ['Invitations sent', String(data.invitationCount)],
              ['Created', formatDay(data.createdAt)],
            ]}
          />
        </AdminCard>

        <AdminCard title="Roles">
          {/*
            The permission codes are listed, not summarized: a team role holding
            a code outside the five `team.*` ones would be the platform's own
            boundary failing, and this is where somebody would notice.
          */}
          <ul className="space-y-3">
            {data.roles.map((role) => (
              <TeamRoleRow key={role.id} role={role} />
            ))}
          </ul>
        </AdminCard>

        <AdminCard title="Ideas by status">
          <ul className="space-y-2">
            {data.ideasByStatus
              .filter((entry) => entry.count > 0)
              .map((entry) => (
                <li key={entry.status} className="flex items-center justify-between gap-3">
                  <IdeaStatusBadge status={entry.status} />
                  <span className="text-sm font-semibold text-slate-900 tabular-nums">
                    {entry.count}
                  </span>
                </li>
              ))}
            {data.ideasByStatus.every((entry) => entry.count === 0) && (
              <li className="text-sm text-slate-500">No ideas filed for this team yet.</li>
            )}
          </ul>
        </AdminCard>
      </div>

      <div className="mt-4">
        <AdminCard title="Membership">
          {data.members.length === 0 ? (
            <p className="text-sm text-slate-500">Nobody is on this team’s roster.</p>
          ) : (
            <AdminTable label="Team members" columns={['Person', 'Roles', 'Status', 'Joined']}>
              {data.members.map((member) => (
                <tr key={member.id}>
                  <td className={cellClasses}>
                    <span className="font-semibold text-slate-900">{member.user.name}</span>
                    <p className="text-xs text-slate-500">{member.user.email}</p>
                  </td>
                  <td className={cellClasses}>
                    {member.roles.length === 0 ? (
                      <span className="text-slate-500">None</span>
                    ) : (
                      member.roles.map((role) => (
                        <Badge key={role.id} tone={role.slug === 'owner' ? 'info' : 'neutral'}>
                          {role.name}
                        </Badge>
                      ))
                    )}
                  </td>
                  <td className={cellClasses}>
                    {/*
                    Two different facts, kept apart: a membership that ended is
                    not a deactivated account, and a person who left is still on
                    the record of what the team did.
                  */}
                    {member.status === 'active' ? (
                      <Badge tone="good">Active</Badge>
                    ) : (
                      <Badge>Left</Badge>
                    )}
                    {!member.userIsActive && (
                      <span className="ml-2 text-xs text-slate-500">account deactivated</span>
                    )}
                  </td>
                  <td className={`${cellClasses} whitespace-nowrap`}>
                    {formatDay(member.joinedAt)}
                  </td>
                </tr>
              ))}
            </AdminTable>
          )}
        </AdminCard>
      </div>

      <div className="mt-4">
        <AdminCard title="Invitations">
          {invitations.error && (
            <ErrorState message={invitations.error} onRetry={invitations.reload} />
          )}
          {invitations.loading && !invitations.data && (
            <LoadingState label="Loading invitations…" />
          )}
          {invitations.data && invitations.data.items.length === 0 && (
            <p className="text-sm text-slate-500">This team has sent no invitations.</p>
          )}
          {invitations.data && invitations.data.items.length > 0 && (
            <>
              <AdminTable label="Team invitations" columns={['Email', 'Role', 'Status', 'Expires']}>
                {invitations.data.items.map((invitation) => (
                  <tr key={invitation.id}>
                    <td className={`${cellClasses} font-semibold text-slate-900`}>
                      {invitation.email}
                    </td>
                    <td className={cellClasses}>{invitation.roleSlug}</td>
                    <td className={cellClasses}>
                      {/*
                      `isOpen` rather than `status`: an invitation past its
                      expiry still reads "pending" in the database, and what a
                      reader needs to know is whether it could still be accepted.
                    */}
                      {invitation.isOpen ? (
                        <Badge tone="info">Waiting to be accepted</Badge>
                      ) : invitation.status === 'accepted' ? (
                        <Badge tone="good">Accepted</Badge>
                      ) : invitation.status === 'revoked' ? (
                        <Badge>Revoked</Badge>
                      ) : (
                        <Badge>Expired</Badge>
                      )}
                    </td>
                    <td className={`${cellClasses} whitespace-nowrap`}>
                      {formatDay(invitation.expiresAt)}
                    </td>
                  </tr>
                ))}
              </AdminTable>
              <AdminPagination
                label="Team invitations pagination"
                pageInfo={invitations.data.pageInfo}
                onOffsetChange={filters.setOffset}
              />
            </>
          )}
          <p className="mt-3 text-xs text-slate-500">
            An invitation is a request, not a membership: it creates one only when the recipient
            accepts. No token is stored in readable form, so nothing here can be replayed —{' '}
            <Link to="/app/admin/invitations" className="font-semibold text-slate-700 underline">
              Every invitation on the platform
            </Link>
            .
          </p>
        </AdminCard>
      </div>
    </>
  )
}

function TeamRoleRow({ role }: { role: AdminTeamRole }) {
  return (
    <li>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-semibold text-slate-900">{role.name}</span>
        <span className="text-xs text-slate-500 tabular-nums">
          {role.holderCount} {role.holderCount === 1 ? 'holder' : 'holders'}
        </span>
      </div>
      <p className="text-xs text-slate-500">{role.description}</p>
      <ul className="mt-1 flex flex-wrap gap-1">
        {role.permissions.map((code) => (
          <li
            key={code}
            className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-600"
          >
            {code}
          </li>
        ))}
      </ul>
    </li>
  )
}
