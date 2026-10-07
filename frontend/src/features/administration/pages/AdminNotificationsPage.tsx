import { Link } from 'react-router-dom'

import { adminNotificationsRequest } from '../api/administrationApi'
import {
  AdminPageHeader,
  AdminPagination,
  AdminTable,
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  Restricted,
  cellClasses,
  controlClasses,
} from '../components/AdminUi'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDay } from '../utils/format'

/**
 * The kinds the platform sends, grouped the way `notifications.models` groups
 * them. Kept here as the filter's vocabulary rather than derived from a prefix:
 * `DECISION_KINDS` on the server is the set that may link a review report, and a
 * client that guessed at kinds by string would eventually offer a report link
 * for a queue nudge.
 */
const KINDS: Array<{ value: string; label: string; group: string }> = [
  { value: '', label: 'Everything', group: '' },
  {
    value: 'idea.organization_changes_requested',
    label: 'Changes requested by the organization',
    group: 'The idea journey',
  },
  {
    value: 'idea.organization_confirmed',
    label: 'The organization confirmed the idea',
    group: 'The idea journey',
  },
  {
    value: 'idea.submitted_to_platform',
    label: 'Submitted to the platform',
    group: 'The idea journey',
  },
  {
    value: 'idea.platform_changes_requested',
    label: 'The platform asked for changes',
    group: 'The idea journey',
  },
  {
    value: 'idea.platform_rejected',
    label: 'The platform did not approve it',
    group: 'The idea journey',
  },
  {
    value: 'idea.platform_approved',
    label: 'Platform review completed',
    group: 'The idea journey',
  },
  { value: 'idea.owner_go_ahead', label: 'Ready for implementation', group: 'The idea journey' },
  { value: 'review.assigned', label: 'An idea was assigned to you', group: 'The review queues' },
  {
    value: 'review.organization_queue',
    label: 'Waiting for organization review',
    group: 'The review queues',
  },
  {
    value: 'review.platform_queue',
    label: 'Waiting for platform review',
    group: 'The review queues',
  },
  {
    value: 'invitation.received',
    label: 'You have been invited',
    group: 'Invitations and messages',
  },
  {
    value: 'invitation.accepted',
    label: 'Your invitation was accepted',
    group: 'Invitations and messages',
  },
  { value: 'message.received', label: 'You have a new message', group: 'Invitations and messages' },
]

/**
 * Every notification the platform has sent, to everybody.
 *
 * **These are the platform's own words.** A notification row only ever holds
 * something a *domain* decided — "your idea was approved", "a reviewer asked for
 * changes" — and its body is length-bounded precisely so it never carries a
 * review report. So the text here is metadata under the console gate, unlike a
 * message body, which is content.
 *
 * The idea a notification points at is still gated: a title is only shown to an
 * administrator who may read that idea. Read state is per reader and is shown
 * as somebody else's here, which is the point — an administrator investigating
 * "was this person told?" needs to know whether they opened it.
 */
export function AdminNotificationsPage() {
  const filters = useUrlFilters()
  const kind = filters.get('kind')
  const unreadOnly = filters.get('unreadOnly') === 'true'

  const { data, loading, error, reload } = useAdminQuery(
    `notifications:${filters.key}`,
    () =>
      adminNotificationsRequest(
        { kind, ...(unreadOnly ? { unreadOnly: true } : {}) },
        { offset: filters.offset },
      ),
    'We could not load the notifications.',
  )

  return (
    <>
      <AdminPageHeader
        title="Notifications"
        description="Everything the platform has told anybody. Each row is one delivery of one business fact to one person — never anything a user wrote, and never the contents of a review report."
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-xs font-semibold text-slate-600" htmlFor="notification-kind">
            Kind
          </label>
          <select
            id="notification-kind"
            className={`${controlClasses} mt-1`}
            value={kind}
            onChange={(event) => filters.set('kind', event.target.value)}
          >
            {Object.entries(
              KINDS.reduce<Record<string, Array<{ value: string; label: string }>>>(
                (groups, entry) => {
                  if (entry.group === '') groups.all = [...(groups.all ?? []), entry]
                  else groups[entry.group] = [...(groups[entry.group] ?? []), entry]
                  return groups
                },
                {},
              ),
            ).map(([group, options]) => (
              <optgroup key={group} label={group === 'all' ? 'Everything' : group}>
                {options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm font-semibold text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-300"
            checked={unreadOnly}
            onChange={(event) => filters.set('unreadOnly', event.target.checked ? 'true' : '')}
          />
          Unread only
        </label>
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading notifications…" />}
      {data && data.items.length === 0 && (
        <EmptyState title="No notifications match." description="Try a different filter." />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable
            label="Notifications"
            columns={['Recipient', 'Kind', 'What it said', 'About', 'Read', 'Sent']}
          >
            {data.items.map((notification) => (
              <tr key={notification.id}>
                <td className={cellClasses}>
                  <span className="font-semibold text-slate-900">
                    {notification.recipient.name}
                  </span>
                  <p className="text-xs text-slate-500">{notification.recipient.email}</p>
                </td>
                <td className={cellClasses}>
                  <Badge>
                    {KINDS.find((entry) => entry.value === notification.kind)?.label ??
                      notification.kind}
                  </Badge>
                </td>
                <td className={cellClasses}>
                  <span className="font-medium text-slate-900">{notification.title}</span>
                  <p className="mt-0.5 text-xs text-slate-600">{notification.body}</p>
                </td>
                <td className={cellClasses}>
                  {notification.ideaId ? (
                    notification.ideaTitle ? (
                      <Link
                        to={`/app/admin/ideas/${notification.ideaId}`}
                        className="font-semibold text-slate-900 hover:underline"
                      >
                        {notification.ideaTitle}
                      </Link>
                    ) : (
                      <Restricted>An idea you may not read</Restricted>
                    )
                  ) : (
                    <span className="text-slate-500">Nothing</span>
                  )}
                </td>
                <td className={cellClasses}>
                  {notification.isRead ? (
                    <Badge tone="good">Read</Badge>
                  ) : (
                    <Badge tone="warn">Unread</Badge>
                  )}
                </td>
                <td className={`${cellClasses} whitespace-nowrap`}>
                  {formatDay(notification.createdAt)}
                </td>
              </tr>
            ))}
          </AdminTable>
          <AdminPagination
            label="Notifications pagination"
            pageInfo={data.pageInfo}
            onOffsetChange={filters.setOffset}
          />
        </>
      )}
    </>
  )
}
