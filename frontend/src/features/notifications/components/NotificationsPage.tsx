import { useState } from 'react'
import { Link } from 'react-router-dom'

import { hasReportLink, type Notification, type NotificationKind } from '../api/notificationsApi'
import { useNotifications } from '../hooks/useNotifications'
import { formatDate } from '../../reviews/utils/reviewLabels'

/**
 * What the platform has told you, at `/app/notifications`.
 *
 * Two kinds of notification, kept apart because they answer to different rules
 * on the server and this page must not blur them:
 *
 * - a **decision** about an idea (approved, changes asked for, rejected, and so
 *   on) may link to the idea and, for the platform-approved case, to the review
 *   report;
 * - a **nudge** — "somebody submitted something" — links to nothing beyond the
 *   idea it concerns, because pointing a queue notification at an approval report
 *   would be nonsense.
 *
 * Which is which is `hasReportLink`'s question, and that function reads the
 * server's own list of decision kinds rather than testing for a prefix — so
 * adding a queue kind later cannot accidentally make it offer a report.
 *
 * **Nothing here renders content.** A notification says what happened and what to
 * do next; the idea, the report and the feedback all stay behind the sign-in.
 */
export function NotificationsPage() {
  const { notifications, loading, error, unreadCount, reload, markRead, markAllRead } =
    useNotifications()
  // New notifications are the list; once read, one clears into "Earlier", folded.
  const [earlierOpen, setEarlierOpen] = useState(false)
  const fresh = notifications.filter((notification) => !notification.isRead)
  const earlier = notifications.filter((notification) => notification.isRead)

  return (
    <section aria-labelledby="notifications-heading" className="mt-8">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">
            Notifications
          </p>
          <h1
            id="notifications-heading"
            className="mt-1 text-3xl font-bold tracking-tight text-gray-900"
          >
            What has happened.
          </h1>
          <p className="mt-3 max-w-2xl text-base leading-7 text-gray-600">
            Decisions about your ideas, and invitations waiting for you. The ideas themselves stay
            behind your sign-in.
          </p>
        </div>
        {unreadCount > 0 && (
          <button
            type="button"
            onClick={() => void markAllRead()}
            className="self-start rounded-lg border border-gray-300 px-3 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            Mark all as read
          </button>
        )}
      </div>

      <div className="mt-6">
        {loading && notifications.length === 0 ? (
          <p className="text-sm text-gray-600">Loading your notifications…</p>
        ) : error ? (
          <div className="rounded-xl border border-red-200 bg-red-50 p-5" role="alert">
            <p className="text-sm font-semibold text-red-800">Notifications unavailable</p>
            <p className="mt-1 text-sm text-red-700">{error}</p>
            <button
              type="button"
              onClick={reload}
              className="mt-4 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-800 hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
            >
              Try again
            </button>
          </div>
        ) : notifications.length === 0 ? (
          <p className="text-sm text-gray-600">Nothing to report yet.</p>
        ) : (
          <>
            <h2 className="text-sm font-semibold text-gray-900">New</h2>
            {fresh.length === 0 ? (
              <p className="mt-2 rounded-lg border border-dashed border-gray-300 bg-white px-4 py-4 text-sm text-gray-600">
                You are all caught up.
              </p>
            ) : (
              <ul
                aria-label="New notifications"
                className="mt-2 divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white"
              >
                {fresh.map((notification) => (
                  <NotificationRow
                    key={notification.id}
                    notification={notification}
                    onRead={() => void markRead(notification.id)}
                  />
                ))}
              </ul>
            )}
            {earlier.length > 0 && (
              <div className="mt-6">
                <button
                  type="button"
                  aria-expanded={earlierOpen}
                  aria-controls="earlier-notifications"
                  onClick={() => setEarlierOpen((open) => !open)}
                  className="text-sm font-semibold text-gray-600 hover:text-gray-900"
                >
                  {earlierOpen ? 'Hide earlier' : `Earlier (${earlier.length})`}
                </button>
                {earlierOpen && (
                  <ul
                    id="earlier-notifications"
                    aria-label="Earlier notifications"
                    className="mt-2 divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white opacity-80"
                  >
                    {earlier.map((notification) => (
                      <NotificationRow
                        key={notification.id}
                        notification={notification}
                        onRead={() => void markRead(notification.id)}
                      />
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  )
}

/**
 * One notification: what it says, and the one thing to do about it.
 *
 * Unread rows are marked by a tinted surface and a dot rather than by bolding
 * the text, because the words are the server's and this client should not be
 * restyling them to carry a state the server already reports as `isRead`.
 */
function NotificationRow({
  notification,
  onRead,
}: {
  notification: Notification
  onRead: () => void
}) {
  return (
    <li className={`px-4 py-3 ${notification.isRead ? '' : 'bg-brand-50/60'}`}>
      <div className="flex items-start gap-3">
        {!notification.isRead && (
          <span aria-hidden="true" className="mt-2 h-2 w-2 shrink-0 rounded-full bg-brand-600" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-gray-900">{notification.title}</p>
          {notification.body ? (
            <p className="mt-1 text-sm leading-6 text-gray-600">{notification.body}</p>
          ) : null}
          <p className="mt-1 text-xs text-gray-500">
            {notificationLabel(notification.kind)} · {formatDate(notification.createdAt)}
          </p>
          <div className="mt-2 flex flex-wrap gap-3">
            {/*
              **Where this goes is the server's answer.** `actionPath` comes from
              one mapping on the server that also builds any payload for a future
              push channel, so an in-app link and a notification tapped on a
              phone cannot land on different screens. This client used to compose
              the destination itself out of `ideaId` and the kind, which is how
              three links came to point at a route that did not exist.

              A report link is offered separately and only for a decision about
              an idea, because the report is the thing worth reading in that case
              and the idea page is not.
            */}
            {notification.actionPath && (
              <Link
                to={notification.actionPath}
                // Opening it is reading it: it clears from the new ones.
                onClick={notification.isRead ? undefined : onRead}
                className="text-sm font-semibold text-brand-700 hover:text-brand-800"
              >
                {actionLinkLabel(notification)}
              </Link>
            )}
            {notification.reportId && hasReportLink(notification.kind) && (
              <Link
                to={`/app/ideas/${notification.ideaId}/report`}
                className="text-sm font-semibold text-brand-700 hover:text-brand-800"
              >
                Read the review report
              </Link>
            )}
            {!notification.isRead && (
              <button
                type="button"
                onClick={onRead}
                className="text-sm font-semibold text-gray-600 hover:text-gray-800"
              >
                Mark as read
              </button>
            )}
          </div>
        </div>
      </div>
    </li>
  )
}

/**
 * A plain description of what kind of thing this is, for the row's meta line.
 *
 * The server sends a title and a body; it does not send a human name for the
 * kind, and inventing one here would be a second vocabulary to keep in step.
 * So this reads the kind itself, which is a stable identifier rather than
 * display text.
 */
/**
 * What the link says, from where it points.
 *
 * The destination is the server's; the wording is this client's, and it names
 * the thing rather than the route - "Open the idea", "Read the report", "Open
 * your conversations" - so a reader is not shown a path.
 */
function actionLinkLabel(notification: Notification): string {
  const path = notification.actionPath ?? ''
  if (path.endsWith('#respond')) return 'Respond to the review'
  if (path.endsWith('#decision-letter')) return 'Read your letter'
  if (path.endsWith('#proposal')) return 'See the proposal'
  if (path.includes('?tab=uat')) return 'Open acceptance testing'
  if (path.includes('?tab=testing')) return 'Open testing'
  if (path.includes('?tab=deployment')) return 'Open the deployment'
  if (path.includes('?tab=impact')) return 'Open the impact'
  if (path.includes('?tab=work')) return 'Open tasks and milestones'
  if (path.startsWith('/app/automation/projects/')) return 'Open the project'
  if (path.startsWith('/app/automation/opportunities/')) return 'Open the opportunity'
  if (path.startsWith('/app/automation/queue')) return 'Open the developer queue'
  if (path.endsWith('/report')) return 'Read the review report'
  if (path.startsWith('/app/messages')) return 'Open your conversations'
  if (path.startsWith('/app/reviews/proposals/')) return 'Open the proposal'
  if (path.startsWith('/app/reviews')) return 'Open the review'
  if (path.startsWith('/app/admin/decisions')) return 'Open Decisions'
  if (path.startsWith('/app/admin/proposals')) return 'Open Proposals'
  if (path.endsWith('/proposal')) return 'Read your proposal'
  if (path.startsWith('/app/ideas')) return 'Open the idea'
  return 'Open'
}

function notificationLabel(kind: NotificationKind): string {
  const [, what] = kind.split('.')
  return what.replace(/_/g, ' ').replace(/^\w/, (letter) => letter.toUpperCase())
}
