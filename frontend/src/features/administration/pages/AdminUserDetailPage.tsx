import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { useAuth } from '../../identity/auth/AuthContext'
import {
  adminUserRequest,
  setUserActiveRequest,
  type AdminUserDetail,
} from '../api/administrationApi'
import {
  AdminCard,
  AdminPageHeader,
  Badge,
  DetailList,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  dangerButtonClasses,
  primaryButtonClasses,
} from '../components/AdminUi'
import { BackLink } from '../components/BackLink'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useBackTarget } from '../hooks/useBackTarget'
import { auditActionLabel, formatDateTime, formatDay, fullName } from '../utils/format'
import { UserStatusBadges } from './AdminUsersPage'

const SIGN_IN_LABELS: Record<string, string> = { password: 'Email and password', google: 'Google' }

/**
 * One account: profile, memberships and roles, activity counts, the
 * administrative actions taken on it, and - for an administrator allowed to
 * manage accounts - activation. The action is offered, never decided, here:
 * the server re-checks the permission and its rules (not your own account,
 * not a superuser unless you are one).
 */
export function AdminUserDetailPage() {
  const { userId = '' } = useParams()
  const {
    data: user,
    loading,
    error,
    reload,
  } = useAdminQuery(
    `user:${userId}`,
    () => adminUserRequest(userId),
    'We could not load this account.',
  )

  const backTo = useBackTarget('/app/admin/users')
  if (error) return <ErrorState message={error} onRetry={reload} />
  if (loading && !user) return <LoadingState label="Loading account…" />
  if (!user) {
    return <EmptyState title="Account not found." description="It may have been removed." />
  }

  return (
    <>
      <AdminPageHeader
        title={fullName(user)}
        description={user.email}
        back={<BackLink to={backTo}>All users</BackLink>}
      />
      <div className="space-y-6">
        <AdminCard title="Profile">
          <DetailList
            items={[
              ['Name', fullName(user)],
              ['Email', user.email],
              ['Phone', user.phoneNumber || '—'],
              ['Status', <UserStatusBadges key="status" user={user} />],
              [
                'Signs in with',
                user.signInMethods.map((method) => SIGN_IN_LABELS[method] ?? method).join(', ') ||
                  '—',
              ],
              ['Joined', formatDay(user.createdAt)],
              ['Last active', formatDateTime(user.lastActiveAt)],
            ]}
          />
        </AdminCard>

        <AccountActions user={user} onChanged={reload} />

        <AdminCard title="Organizations">
          {user.memberships.length === 0 ? (
            <p className="text-sm text-slate-500">Not a member of any organization.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {user.memberships.map((membership) => (
                <li key={membership.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <Link
                    to={`/app/admin/organizations/${membership.organization.id}`}
                    className="font-semibold text-slate-900 hover:underline"
                  >
                    {membership.organization.name}
                  </Link>
                  {membership.status !== 'active' && <Badge tone="warn">Inactive membership</Badge>}
                  {membership.roles.length === 0 ? (
                    <span className="text-slate-500">Member</span>
                  ) : (
                    membership.roles.map((role) => (
                      <Badge key={role.id} tone={role.isSystem ? 'info' : 'neutral'}>
                        {role.name}
                      </Badge>
                    ))
                  )}
                </li>
              ))}
            </ul>
          )}
        </AdminCard>

        <AdminCard title="Activity">
          <DetailList
            items={[
              [
                'Ideas written',
                <Link
                  key="ideas"
                  to={`/app/admin/ideas?authorId=${user.id}`}
                  className="font-semibold hover:underline"
                >
                  {user.ideaCount}
                </Link>,
              ],
              [
                'Review rounds',
                <Link
                  key="reviews"
                  to={`/app/admin/reviews?reviewerId=${user.id}`}
                  className="font-semibold hover:underline"
                >
                  {user.reviewCount}
                </Link>,
              ],
              ['Comments', user.commentCount],
            ]}
          />
        </AdminCard>

        <AdminCard title="Administrative history">
          {user.auditEntries.length === 0 ? (
            <p className="text-sm text-slate-500">No administrative actions on this account.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {user.auditEntries.map((entry) => (
                <li key={entry.id} className="py-2 text-sm">
                  <span className="font-semibold">{auditActionLabel(entry.action)}</span>{' '}
                  {entry.result === 'REFUSED' && <Badge tone="bad">Refused</Badge>}
                  <p className="text-xs text-slate-500">
                    {entry.actor ? entry.actor.name : 'Command line'} ·{' '}
                    {formatDateTime(entry.createdAt)}
                    {entry.message && ` · ${entry.message}`}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </AdminCard>
      </div>
    </>
  )
}

function AccountActions({ user, onChanged }: { user: AdminUserDetail; onChanged: () => void }) {
  const { capabilities } = useAdminCapabilities()
  const { user: viewer } = useAuth()
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Offered only where the server could allow it - never on your own account.
  // The server decides anyway, including that only a superuser can change a
  // superuser's account, and its refusal is shown in the dialog.
  if (!capabilities.canManageUserAccounts || viewer?.id === user.id) return null

  const deactivating = user.isActive

  async function confirm(reason: string) {
    setBusy(true)
    setError(null)
    try {
      const result = await setUserActiveRequest({
        userId: user.id,
        isActive: !deactivating,
        ...(reason ? { reason } : {}),
      })
      if (!result.success) {
        setError(result.message)
        return
      }
      setConfirming(false)
      setNotice(result.message)
      onChanged()
    } catch {
      setError('We could not reach the server. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AdminCard title="Account actions">
      <div className="space-y-3">
        {notice && <Notice tone="success">{notice}</Notice>}
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => {
              setError(null)
              setNotice(null)
              setConfirming(true)
            }}
            className={deactivating ? dangerButtonClasses : primaryButtonClasses}
          >
            {deactivating ? 'Deactivate account' : 'Reactivate account'}
          </button>
          <p className="text-xs text-slate-500">
            {deactivating
              ? 'Stops the account from signing in. Nothing is deleted.'
              : 'Lets the account sign in again.'}
          </p>
        </div>
      </div>
      {confirming && (
        <ConfirmDialog
          title={deactivating ? `Deactivate ${user.email}?` : `Reactivate ${user.email}?`}
          confirmLabel={deactivating ? 'Deactivate' : 'Reactivate'}
          danger={deactivating}
          withReason
          busy={busy}
          error={error}
          onCancel={() => setConfirming(false)}
          onConfirm={(reason) => void confirm(reason)}
        >
          {deactivating ? (
            <>
              <p>
                They are signed out everywhere immediately and cannot sign in again until the
                account is reactivated.
              </p>
              <p>
                Their ideas, comments and reviews stay. A review they have in progress becomes
                stalled, and another reviewer in that organization can take it over.
              </p>
            </>
          ) : (
            <p>They will be able to sign in again with their existing credentials.</p>
          )}
        </ConfirmDialog>
      )}
    </AdminCard>
  )
}
