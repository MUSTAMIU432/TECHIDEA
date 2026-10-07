/**
 * In-app notifications: what the platform decided, and what is waiting.
 *
 * Two separate concerns that must not be confused:
 *
 * - `ideaId` / `reportId` are **links into the app**, requiring authentication.
 *   The notification says what happened and what to do next; the report itself
 *   stays behind the sign-in, and the email carries the same summary and the
 *   same link rather than the content.
 * - `kind` is presentation only. The kinds that carry a report link are the ones
 *   about a *decision*; a queue nudge never does, because "somebody submitted
 *   something" pointing at an approval report would be nonsense. Which kinds are
 *   which is decided by the server's `DECISION_KINDS` and re-asserted in
 *   `notificationsApi`, so this module cannot start offering a report link on a
 *   nudge.
 */

import { graphqlClient } from '../../../graphql/client'

export type NotificationKind =
  // A decision about an idea.
  | 'idea.organization_changes_requested'
  | 'idea.organization_confirmed'
  | 'idea.submitted_to_platform'
  | 'idea.platform_changes_requested'
  | 'idea.platform_rejected'
  | 'idea.platform_approved'
  | 'idea.owner_go_ahead'
  // A queue waiting for somebody.
  | 'review.assigned'
  | 'review.organization_queue'
  | 'review.platform_queue'
  // Invitations and messages.
  | 'invitation.received'
  | 'invitation.accepted'
  | 'invitation.declined'
  | 'message.received'

/**
 * The kinds about a decision rather than a queue, and so the only ones that may
 * link to a report. Mirrors the backend's `notifications.models.DECISION_KINDS`;
 * `hasReportLink` below uses it rather than a string test, because "starts with
 * `idea.`" would include a queue nudge the moment one was added.
 */
const DECISION_KINDS: ReadonlySet<NotificationKind> = new Set<NotificationKind>([
  'idea.organization_changes_requested',
  'idea.organization_confirmed',
  'idea.submitted_to_platform',
  'idea.platform_changes_requested',
  'idea.platform_rejected',
  'idea.platform_approved',
  'idea.owner_go_ahead',
])

/** Whether this notification may offer a link to a review report. */
export function hasReportLink(kind: NotificationKind): boolean {
  return DECISION_KINDS.has(kind)
}

export interface Notification {
  id: string
  kind: NotificationKind
  /** The kind in the server's own words, so a client never re-spells it. */
  label: string
  title: string
  body: string
  ideaId: string | null
  reportId: string | null
  isRead: boolean
  /**
   * Where this takes you, decided by the server, or `null` when there is
   * nowhere specific to go.
   *
   * **The server's answer, not a guess.** This client used to build its own
   * destination out of `ideaId` and the kind, which is how three links ended up
   * pointing at a route that did not exist and an invitation notification ended
   * up with no way to act at all. One mapping - `Notification.action_path` -
   * serves the in-app link here and any payload built from the same row, so the
   * two cannot disagree and a rename is one edit on the server.
   *
   * `null` is a real answer, not a gap: an invitation cannot carry an accept
   * link, because acceptance needs a token the server stores only as a digest.
   */
  actionPath: string | null
  createdAt: string
}

/**
 * A page of notifications.
 *
 * `totalCount` rather than the full `PageInfo`: this list is capped and read
 * end to end, like a conversation, and nothing in it needs "showing 1-20 of
 * 137" - so the page's own shape is kept rather than growing the same four
 * fields the discovery lists need.
 */
export interface NotificationPage {
  items: Notification[]
  totalCount: number
}

export interface NotificationPayload {
  success: boolean
  message: string
  notification: Notification | null
}

export interface NotificationPageRequest {
  offset?: number
  limit?: number
  unreadOnly?: boolean
}

const NOTIFICATION_FIELDS = `
  id
  kind
  label
  title
  body
  ideaId
  reportId
  isRead
  actionPath
  createdAt
`

/** The notification list is not paged by the server, so it is capped here. */
export const NOTIFICATIONS_LIMIT = 50

const NOTIFICATIONS_QUERY = `
  query Notifications($offset: Int, $limit: Int, $unreadOnly: Boolean) {
    notifications(offset: $offset, limit: $limit, unreadOnly: $unreadOnly) {
      ${NOTIFICATION_FIELDS}
    }
  }
`

const UNREAD_COUNT_QUERY = `
  query UnreadNotificationCount {
    unreadNotificationCount
    hasUnreadNotifications
  }
`

const MARK_READ_MUTATION = `
  mutation MarkNotificationRead($id: ID!) {
    markNotificationRead(id: $id) {
      success
      message
      notification { ${NOTIFICATION_FIELDS} }
    }
  }
`

const MARK_ALL_READ_MUTATION = `
  mutation MarkAllNotificationsRead {
    markAllNotificationsRead { success message }
  }
`

/**
 * The caller's own notifications, newest first, and how many there are.
 *
 * The count is the list's own length rather than a second request, because this
 * screen is not paginated - it asks for everything up to a cap - so a total
 * would be a second round trip for a number the response already implies.
 */
export async function notificationsRequest(
  page: NotificationPageRequest = {},
): Promise<NotificationPage> {
  const data = await graphqlClient.request<{ notifications: Notification[] }>(NOTIFICATIONS_QUERY, {
    offset: page.offset ?? null,
    limit: page.limit ?? NOTIFICATIONS_LIMIT,
    unreadOnly: page.unreadOnly ?? false,
  })
  return { items: data.notifications, totalCount: data.notifications.length }
}

/**
 * The unread badge, as one request.
 *
 * Both numbers come back because the header needs the count and the list page
 * needs the boolean, and a second request for either would be a request per
 * render of a badge that is on every authenticated page.
 */
export async function unreadNotificationsRequest(): Promise<{
  count: number
  hasUnread: boolean
}> {
  const data = await graphqlClient.request<{
    unreadNotificationCount: number
    hasUnreadNotifications: boolean
  }>(UNREAD_COUNT_QUERY)
  return {
    count: data.unreadNotificationCount,
    hasUnread: data.hasUnreadNotifications,
  }
}

/** Mark one read. Idempotent on the server, and scoped to the caller's own rows. */
export async function markNotificationReadRequest(id: string): Promise<NotificationPayload> {
  const data = await graphqlClient.request<{ markNotificationRead: NotificationPayload }>(
    MARK_READ_MUTATION,
    { id },
  )
  return data.markNotificationRead
}

/** Mark every one of the caller's notifications read, in one statement. */
export async function markAllNotificationsReadRequest(): Promise<NotificationPayload> {
  const data = await graphqlClient.request<{ markAllNotificationsRead: NotificationPayload }>(
    MARK_ALL_READ_MUTATION,
  )
  return data.markAllNotificationsRead
}
