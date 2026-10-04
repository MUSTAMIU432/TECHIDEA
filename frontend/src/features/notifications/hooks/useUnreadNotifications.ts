import { useEffect, useState } from 'react'

import { unreadNotificationsRequest } from '../api/notificationsApi'

/**
 * The unread count, for the header badge.
 *
 * On its own request rather than read out of the notifications page's state,
 * because the badge is on every authenticated page and the page is on none of
 * them most of the time. It asks the server rather than counting a list it does
 * not have, which is the only way the number stays right when the page has
 * never been opened in this session.
 *
 * The request is made **once per mount**, deliberately. This badge is in the
 * app chrome, so it renders on every navigation; re-asking on each render would
 * be a request per page the reader visits, and nothing about the count changes
 * often enough to be worth it. A write that changes it — accepting an
 * invitation, completing a review — is a navigation away, so the badge is at
 * worst one navigation stale.
 */
export function useUnreadNotifications(): number {
  const [count, setCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    unreadNotificationsRequest()
      .then((badge) => {
        if (!cancelled) setCount(badge.count)
      })
      .catch(() => {
        // A badge is not worth an error panel: the header stays usable and the
        // notifications page is where a failure is reported properly.
        if (!cancelled) setCount(0)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return count
}
