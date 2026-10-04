import { useCallback, useEffect, useRef, useState } from 'react'

import {
  markAllNotificationsReadRequest,
  markNotificationReadRequest,
  notificationsRequest,
  unreadNotificationsRequest,
  type Notification,
} from '../api/notificationsApi'

/**
 * The caller's notifications, and the unread count for the header.
 *
 * Two consumers, one source of truth per concern, because they are read at
 * different times and must not be made to agree by a client-side derivation:
 *
 * - the **badge** asks only for the count, because it is on every authenticated
 *   page and a list to count them would be a request per render of a header;
 * - the **page** asks for the list, which is capped and read end to end.
 *
 * The count is therefore not "how many of the rows I have are unread": it is the
 * server's number, which stays right when the page has never been opened.
 *
 * `loading` is derived by comparing the query during render rather than written
 * inside the effect — the same discipline `useMyTeams` uses, and for the same
 * reason: a state write there would be a second render to express something
 * already knowable.
 */
export function useNotifications() {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    token: number
    items: Notification[]
    unread: number
  } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  const key = `notifications:${token}`

  useEffect(() => {
    let cancelled = false
    // Two requests rather than one derived count, and both are needed: the list
    // for this page and the count for the header, which may be on screen while
    // this page is not mounted at all.
    Promise.all([notificationsRequest(), unreadNotificationsRequest()])
      .then(([page, badge]) => {
        if (cancelled) return
        setAnswer({ token, items: page.items, unread: badge.count })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setErrorKey(key)
      })
    return () => {
      cancelled = true
    }
    // `key` carries the token, so it *is* the query; `token` is named too
    // because the answer records which token produced it.
  }, [key, token])

  const reload = useCallback(() => setToken((value) => value + 1), [])

  /*
    The rows, kept in a ref so an event handler can read them synchronously.

    Needed for exactly one question: "was this one already read?" The server's
    answer comes back with the row already read, so it cannot tell a first press
    from a second one, and counting the same notification down twice would drift
    the badge below the truth. State cannot be read from a click handler, which
    is what the ref is for.

    Written in an effect rather than during render, because a ref written during
    render is a value the current render has not accounted for.
  */
  const rows = useRef<Notification[]>([])
  useEffect(() => {
    rows.current = answer?.items ?? []
  }, [answer])

  /**
   * Marking one read is a local splice rather than a refetch: the server
   * answered with the row as it now stands, and re-reading a whole capped list
   * to change one flag would be a request whose effect the reader cannot see.
   */
  const markRead = useCallback(async (id: string) => {
    const wasUnread = rows.current.some((item) => item.id === id && !item.isRead)

    const result = await markNotificationReadRequest(id)
    const updated = result.notification
    if (!result.success || updated === null) return
    setAnswer((current) =>
      current === null
        ? current
        : {
            ...current,
            items: current.items.map((item) => (item.id === id ? updated : item)),
            // Clamped at zero: the badge and the list are two facts, and a race
            // between them would otherwise render "-1 unread".
            unread: wasUnread ? Math.max(0, current.unread - 1) : current.unread,
          },
    )
  }, [])

  const markAllRead = useCallback(async () => {
    const result = await markAllNotificationsReadRequest()
    if (!result.success) return
    setAnswer((current) =>
      current === null
        ? current
        : {
            ...current,
            items: current.items.map((item) => ({ ...item, isRead: true })),
            unread: 0,
          },
    )
  }, [])

  const fresh = answer !== null && answer.token === token
  const failed = errorKey === key

  return {
    notifications: answer?.items ?? [],
    loading: !fresh && !failed,
    error: failed ? 'We could not load your notifications. Please try again.' : null,
    unreadCount: fresh ? (answer?.unread ?? 0) : 0,
    reload,
    markRead,
    markAllRead,
  }
}
