import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  adminOrganizationMembersRequest,
  adminOrganizationRequest,
  assignMembershipRoleRequest,
  removeMembershipRoleRequest,
  type AdminMember,
  type AdminRole,
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
  Notice,
  cellClasses,
  controlClasses,
} from '../components/AdminUi'
import { BackLink } from '../components/BackLink'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { SearchBox } from '../components/SearchBox'
import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useBackTarget } from '../hooks/useBackTarget'
import { formatDay } from '../utils/format'

/**
 * One organization: its roles and who holds them, where its ideas stand, and
 * its members - with role changes for an administrator allowed to make them.
 * Role changes go through the organization's own rules on the server (the last
 * Owner keeps the Owner role, roles belong to their organization).
 */
export function AdminOrganizationDetailPage() {
  const { organizationId = '' } = useParams()
  const { data, loading, error, reload } = useAdminQuery(
    `organization:${organizationId}`,
    () => adminOrganizationRequest(organizationId),
    'We could not load this organization.',
  )

  const backTo = useBackTarget('/app/admin/organizations')
  if (error) return <ErrorState message={error} onRetry={reload} />
  if (loading && !data) return <LoadingState label="Loading organization…" />
  if (!data) return <EmptyState title="Organization not found." />

  return (
    <>
      <AdminPageHeader
        title={data.name}
        description={`/${data.slug}`}
        back={<BackLink to={backTo}>All organizations</BackLink>}
      />
      <div className="space-y-6">
        <AdminCard title="Details">
          <DetailList
            items={[
              ['Created', formatDay(data.createdAt)],
              ['Active members', data.memberCount],
              ['Owners', data.ownerCount],
              ['Reviewers', data.reviewerCount],
              [
                'Ideas',
                <Link
                  key="ideas"
                  to={`/app/admin/ideas?organizationId=${data.id}`}
                  className="font-semibold hover:underline"
                >
                  {data.ideaCount} — view ideas
                </Link>,
              ],
            ]}
          />
        </AdminCard>

        <AdminCard title="Ideas by status">
          <ul className="flex flex-wrap gap-2">
            {data.ideasByStatus
              .filter((item) => item.count > 0)
              .map((item) => (
                <li key={item.status} className="inline-flex items-center gap-2 text-sm">
                  <IdeaStatusBadge status={item.status} />
                  <span className="font-semibold tabular-nums">{item.count}</span>
                </li>
              ))}
            {data.ideasByStatus.every((item) => item.count === 0) && (
              <li className="text-sm text-slate-500">No ideas yet.</li>
            )}
          </ul>
        </AdminCard>

        <AdminCard title="Roles">
          <ul className="divide-y divide-slate-100">
            {data.roles.map((role) => (
              <li key={role.id} className="py-2 text-sm">
                <span className="font-semibold text-slate-900">{role.name}</span>{' '}
                {role.isSystem && <Badge tone="info">System</Badge>}{' '}
                <span className="text-slate-500">
                  · {role.holderCount} {role.holderCount === 1 ? 'holder' : 'holders'}
                </span>
                <p className="text-xs text-slate-500">{role.permissions.join(', ')}</p>
              </li>
            ))}
          </ul>
        </AdminCard>

        <MembersCard organizationId={data.id} roles={data.roles} onRolesChanged={reload} />
      </div>
    </>
  )
}

interface PendingChange {
  member: AdminMember
  role: { id: string; name: string }
  assign: boolean
}

function MembersCard({
  organizationId,
  roles,
  onRolesChanged,
}: {
  organizationId: string
  roles: AdminRole[]
  onRolesChanged: () => void
}) {
  const { capabilities } = useAdminCapabilities()
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [pending, setPending] = useState<PendingChange | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const members = useAdminQuery(
    `members:${organizationId}:${search}:${offset}`,
    () => adminOrganizationMembersRequest(organizationId, search, { offset }),
    'We could not load the members.',
  )
  const canManage = capabilities.canManageOrganizationRoles

  async function confirm(reason: string) {
    if (!pending) return
    setBusy(true)
    setError(null)
    const input = {
      membershipId: pending.member.id,
      roleId: pending.role.id,
      ...(reason ? { reason } : {}),
    }
    try {
      const result = pending.assign
        ? await assignMembershipRoleRequest(input)
        : await removeMembershipRoleRequest(input)
      if (!result.success) {
        setError(result.message)
        return
      }
      setPending(null)
      setNotice(result.message)
      members.reload()
      onRolesChanged()
    } catch {
      setError('We could not reach the server. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  function ask(change: PendingChange) {
    setError(null)
    setNotice(null)
    setPending(change)
  }

  return (
    <AdminCard title="Members">
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <SearchBox
          label="Search members"
          placeholder="Name or email"
          initialValue=""
          onSearch={(value) => {
            setSearch(value)
            setOffset(0)
          }}
        />
      </div>
      {notice && (
        <div className="mb-3">
          <Notice tone="success">{notice}</Notice>
        </div>
      )}
      {members.error && <ErrorState message={members.error} onRetry={members.reload} />}
      {members.loading && !members.data && <LoadingState label="Loading members…" />}
      {members.data && members.data.items.length === 0 && <EmptyState title="No members match." />}
      {members.data && members.data.items.length > 0 && (
        <>
          <AdminTable
            label="Members"
            columns={['Member', 'Membership', 'Roles', 'Joined', ...(canManage ? ['Actions'] : [])]}
          >
            {members.data.items.map((member) => {
              const held = new Set(member.roles.map((role) => role.id))
              const assignable = roles.filter((role) => !held.has(role.id))
              return (
                <tr key={member.id}>
                  <td className={cellClasses}>
                    <Link
                      to={`/app/admin/users/${member.user.id}`}
                      className="font-semibold text-slate-900 hover:underline"
                    >
                      {member.user.name}
                    </Link>
                    <p className="text-xs text-slate-500">{member.user.email}</p>
                    {!member.userIsActive && <Badge tone="bad">Account deactivated</Badge>}
                  </td>
                  <td className={cellClasses}>
                    {member.status === 'active' ? (
                      <Badge tone="good">Active</Badge>
                    ) : (
                      <Badge tone="warn">Inactive</Badge>
                    )}
                  </td>
                  <td className={cellClasses}>
                    <span className="flex flex-wrap gap-1">
                      {member.roles.length === 0 && <span className="text-slate-400">Member</span>}
                      {member.roles.map((role) => (
                        <span key={role.id} className="inline-flex items-center gap-1">
                          <Badge tone={role.isSystem ? 'info' : 'neutral'}>{role.name}</Badge>
                          {canManage && (
                            <button
                              type="button"
                              aria-label={`Remove ${role.name} from ${member.user.email}`}
                              onClick={() => ask({ member, role, assign: false })}
                              className="rounded px-1 text-xs font-bold text-red-700 hover:bg-red-50"
                            >
                              ✕
                            </button>
                          )}
                        </span>
                      ))}
                    </span>
                  </td>
                  <td className={`${cellClasses} whitespace-nowrap`}>
                    {formatDay(member.joinedAt)}
                  </td>
                  {canManage && (
                    <td className={cellClasses}>
                      {member.status === 'active' && assignable.length > 0 && (
                        <select
                          aria-label={`Assign a role to ${member.user.email}`}
                          value=""
                          onChange={(event) => {
                            const role = roles.find((item) => item.id === event.target.value)
                            if (role) ask({ member, role, assign: true })
                          }}
                          className={controlClasses}
                        >
                          <option value="">Assign role…</option>
                          {assignable.map((role) => (
                            <option key={role.id} value={role.id}>
                              {role.name}
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                  )}
                </tr>
              )
            })}
          </AdminTable>
          <AdminPagination
            label="Members pagination"
            pageInfo={members.data.pageInfo}
            onOffsetChange={setOffset}
          />
        </>
      )}
      {pending && (
        <ConfirmDialog
          title={
            pending.assign
              ? `Give ${pending.member.user.email} the ${pending.role.name} role?`
              : `Remove the ${pending.role.name} role from ${pending.member.user.email}?`
          }
          confirmLabel={pending.assign ? 'Assign role' : 'Remove role'}
          danger={!pending.assign}
          withReason
          busy={busy}
          error={error}
          onCancel={() => setPending(null)}
          onConfirm={(reason) => void confirm(reason)}
        >
          <p>
            {pending.assign
              ? 'They gain every permission of this role in this organization immediately.'
              : 'They lose every permission of this role in this organization immediately.'}
          </p>
          <p>
            This is an administrative change to another organization&apos;s access, and it is
            recorded in the audit trail.
          </p>
        </ConfirmDialog>
      )}
      {!canManage && (
        <p className="mt-3 text-xs text-slate-500">
          Changing organization roles needs the organization-role permission.
        </p>
      )}
    </AdminCard>
  )
}
