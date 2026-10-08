import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import {
  grantPlatformRoleRequest,
  platformRolesRequest,
  revokePlatformRoleRequest,
  type Outcome,
  type PlatformRole,
} from '../api/platformRolesApi'
import {
  AdminCard,
  AdminPageHeader,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  controlClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
} from '../components/AdminUi'
import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { useAdminQuery } from '../hooks/useAdminQuery'

/** Runs one action and keeps the server's own words for the result. */
function useAction() {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  async function run(action: () => Promise<Outcome>, onDone: () => void) {
    setBusy(true)
    setNotice(null)
    try {
      const result = await action()
      setNotice({ tone: result.success ? 'success' : 'error', text: result.message })
      if (result.success) onDone()
    } catch {
      setNotice({ tone: 'error', text: 'We could not reach the server. Please try again.' })
    } finally {
      setBusy(false)
    }
  }
  return { busy, notice, run }
}

/** Where the work itself is handed out, once somebody holds the role for it. */
const WHERE_WORK_IS_ASSIGNED: Record<string, { text: string; to: string; link: string }> = {
  developer: {
    text: 'A delivery manager gives a developer a specific opportunity from the',
    to: '/app/automation/queue',
    link: 'Developer Queue',
  },
  delivery_manager: {
    text: 'They assign each approved idea to a developer from the',
    to: '/app/automation/queue',
    link: 'Developer Queue',
  },
  intake: {
    text: 'They route each submitted idea to a review team from its page under',
    to: '/app/admin/ideas',
    link: 'Ideas',
  },
  proposal_approver: {
    text: 'They send each approval or rejection to its owner, and decide on proposals, from',
    to: '/app/admin/decisions',
    link: 'Decisions',
  },
}

function RoleCard({
  role,
  canManage,
  onChanged,
}: {
  role: PlatformRole
  canManage: boolean
  onChanged: () => void
}) {
  const grant = useAction()
  const revoke = useAction()
  const [email, setEmail] = useState('')
  const where = WHERE_WORK_IS_ASSIGNED[role.key]

  return (
    <AdminCard title={role.label}>
      <p className="text-sm text-slate-600">{role.description}</p>
      {where && (
        <p className="mt-1 mb-3 text-xs text-slate-500">
          {where.text}{' '}
          <Link to={where.to} className="font-semibold text-slate-700 underline">
            {where.link}
          </Link>
          .
        </p>
      )}
      {canManage && (
        <form
          aria-label={`Give the ${role.label} role`}
          className="mb-4 flex flex-wrap items-end gap-2"
          onSubmit={(event: FormEvent) => {
            event.preventDefault()
            void grant.run(
              () => grantPlatformRoleRequest(role.key, email),
              () => {
                setEmail('')
                onChanged()
              },
            )
          }}
        >
          <label className="text-sm font-semibold text-slate-700">
            Account email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className={`${controlClasses} mt-1 block w-72`}
              placeholder="person@example.com"
            />
          </label>
          <button
            type="submit"
            disabled={grant.busy || email.trim() === ''}
            className={primaryButtonClasses}
          >
            Add
          </button>
        </form>
      )}
      {grant.notice && <Notice tone={grant.notice.tone}>{grant.notice.text}</Notice>}
      {revoke.notice && <Notice tone={revoke.notice.tone}>{revoke.notice.text}</Notice>}
      {role.holders.length === 0 ? (
        <EmptyState
          title="Nobody holds this role yet."
          description="Add an existing account by its email."
        />
      ) : (
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {role.holders.map((holder) => (
            <li
              key={holder.id}
              className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
            >
              <span>
                <span className="block text-sm font-semibold text-slate-900">{holder.name}</span>
                <span className="block text-xs text-slate-500">{holder.email}</span>
              </span>
              {canManage && (
                <button
                  type="button"
                  disabled={revoke.busy}
                  className={secondaryButtonClasses}
                  onClick={() =>
                    void revoke.run(() => revokePlatformRoleRequest(role.key, holder.id), onChanged)
                  }
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </AdminCard>
  )
}

/**
 * Who does which job on the platform: developers, delivery managers, intake and
 * proposal approvers, each made from an existing account and each change audited.
 *
 * A role is eligibility, not work - the work is handed out where it happens (a review
 * team per idea, a developer per opportunity), and the conflicts are refused there.
 * Reviewers live on their own page because they also belong to review teams.
 */
export function AdminPlatformRolesPage() {
  const { capabilities } = useAdminCapabilities()
  const canManage = capabilities.canManagePlatformRoles
  const roles = useAdminQuery(
    'admin-platform-roles',
    platformRolesRequest,
    'We could not load the platform roles.',
  )

  return (
    <>
      <AdminPageHeader
        title="Platform roles"
        description="Give existing accounts the platform's jobs. A role makes someone eligible; the work itself is assigned per idea or per opportunity."
      />
      <p className="mb-4 text-sm text-slate-600">
        Reviewers are made, and formed into review teams, on the{' '}
        <Link to="/app/admin/reviewers" className="font-semibold text-slate-700 underline">
          Reviewers
        </Link>{' '}
        page.
      </p>
      {roles.error && <ErrorState message={roles.error} onRetry={roles.reload} />}
      {roles.loading && !roles.data && <LoadingState label="Loading platform roles…" />}
      {roles.data && roles.data.length === 0 && (
        <EmptyState
          title="Nothing to show."
          description="Managing platform roles needs the platform-roles permission."
        />
      )}
      {roles.data && roles.data.length > 0 && (
        <div className="space-y-6">
          {roles.data.map((role) => (
            <RoleCard key={role.key} role={role} canManage={canManage} onChanged={roles.reload} />
          ))}
        </div>
      )}
    </>
  )
}
