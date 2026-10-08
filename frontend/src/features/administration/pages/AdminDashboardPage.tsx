import { Link } from 'react-router-dom'

import { statusLabel } from '../../ideas/utils/lifecycle'
import { adminOverviewRequest, type AdminStatusCount } from '../api/administrationApi'
import {
  AdminCard,
  AdminPageHeader,
  Badge,
  EmptyState,
  ErrorState,
  IdeaStatusBadge,
  LoadingState,
  MetricCard,
  Restricted,
} from '../components/AdminUi'
import {
  ApprovedIcon,
  ChangesIcon,
  IdeaIcon,
  InboxIcon,
  OrganizationIcon,
  RejectedIcon,
  ReviewIcon,
  UsersIcon,
} from '../components/AdminIcons'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { auditActionLabel, formatDateTime, ideaHomeLabel } from '../utils/format'

function countOf(counts: AdminStatusCount[], status: AdminStatusCount['status']): number {
  return counts.find((item) => item.status === status)?.count ?? 0
}

/**
 * What is happening on the platform right now, from live data: how many
 * accounts, organizations and ideas there are, where ideas stand in the
 * review lifecycle, and the most recent lifecycle moves and administrative
 * actions. Every number is the server's.
 */
export function AdminDashboardPage() {
  const { data, loading, error, reload } = useAdminQuery(
    'overview',
    adminOverviewRequest,
    'We could not load the platform overview.',
  )

  return (
    <>
      <AdminPageHeader
        title="Overview"
        description="The platform at a glance. Counts are computed from live data on every visit."
      />
      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState />}
      {!loading && !error && data === null && <EmptyState title="No overview available." />}
      {data && (
        <div className="space-y-6">
          <section aria-label="Key figures" className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                label="Users"
                value={data.userCount}
                hint={`${data.activeUserCount} active`}
                icon={<UsersIcon size="lg" />}
                tone="brand"
              />
              <MetricCard
                label="Organizations"
                value={data.organizationCount}
                icon={<OrganizationIcon size="lg" />}
                tone="cyan"
              />
              <MetricCard
                label="Ideas"
                value={data.ideaCount}
                icon={<IdeaIcon size="lg" />}
                tone="violet"
              />
              <MetricCard
                label="Reviews in progress"
                value={data.openReviewCount}
                hint={`${data.completedReviewCount} completed`}
                icon={<ReviewIcon size="lg" />}
                tone="amber"
              />
            </div>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                label="Awaiting review"
                value={countOf(data.ideasByStatus, 'SUBMITTED')}
                hint="Submitted, not yet picked up"
                icon={<InboxIcon size="lg" />}
                tone="sky"
              />
              <MetricCard
                label="Approved"
                value={countOf(data.ideasByStatus, 'APPROVED')}
                icon={<ApprovedIcon size="lg" />}
                tone="brand"
              />
              <MetricCard
                label="Changes requested"
                value={countOf(data.ideasByStatus, 'CHANGES_REQUESTED')}
                icon={<ChangesIcon size="lg" />}
                tone="amber"
              />
              <MetricCard
                label="Rejected"
                value={countOf(data.ideasByStatus, 'REJECTED')}
                icon={<RejectedIcon size="lg" />}
                tone="red"
              />
            </div>
          </section>

          <AdminCard title="Ideas by status">
            <ul aria-label="Ideas by status" className="flex flex-wrap gap-2">
              {data.ideasByStatus.map((item) => (
                <li key={item.status}>
                  <Link
                    to={`/app/admin/ideas?status=${item.status}`}
                    className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-1.5 text-sm hover:bg-slate-50"
                  >
                    <IdeaStatusBadge status={item.status} />
                    <span className="font-semibold text-slate-900 tabular-nums">{item.count}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </AdminCard>

          <div className="grid gap-6 xl:grid-cols-2">
            <AdminCard title="Recent lifecycle activity">
              {data.recentActivity.length === 0 ? (
                <p className="text-sm text-slate-500">No lifecycle activity yet.</p>
              ) : (
                <ul className="divide-y divide-slate-100">
                  {data.recentActivity.map((activity) => (
                    <li key={activity.id} className="py-2.5 text-sm">
                      <Link
                        to={`/app/admin/ideas/${activity.ideaId}`}
                        className="font-semibold text-slate-900 hover:underline"
                      >
                        {activity.ideaTitle ?? <Restricted>Restricted idea</Restricted>}
                      </Link>{' '}
                      <span className="text-slate-600">
                        {statusLabel(activity.fromStatus)} → {statusLabel(activity.toStatus)}
                      </span>
                      <p className="text-xs text-slate-500">
                        {activity.actor.name} · {ideaHomeLabel(activity)} ·{' '}
                        {formatDateTime(activity.createdAt)}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </AdminCard>

            <AdminCard title="Recent administrative actions">
              {data.recentAdminActions.length === 0 ? (
                <p className="text-sm text-slate-500">No administrative actions yet.</p>
              ) : (
                <ul className="divide-y divide-slate-100">
                  {data.recentAdminActions.map((entry) => (
                    <li key={entry.id} className="py-2.5 text-sm">
                      <span className="font-semibold text-slate-900">
                        {auditActionLabel(entry.action)}
                      </span>{' '}
                      {entry.result === 'REFUSED' && <Badge tone="bad">Refused</Badge>}
                      <span className="text-slate-600"> · {entry.targetLabel}</span>
                      <p className="text-xs text-slate-500">
                        {entry.actor ? entry.actor.name : 'Command line'} ·{' '}
                        {formatDateTime(entry.createdAt)}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </AdminCard>
          </div>
        </div>
      )}
    </>
  )
}
