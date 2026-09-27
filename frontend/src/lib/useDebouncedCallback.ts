import { useCallback, useEffect, useRef } from 'react'

/**
 * `callback`, at least `delayMs` after it was last asked for.
 *
 * Exists for the search box, where the alternative is a GraphQL round trip
 * per keystroke: a reader typing "invoicing" would otherwise ask the server
 * eight questions, get eight answers, and render whichever arrived last. The
 * backend bounds the size of a page; bounding the *number of requests* is
 * this client's job.
 *
 * A callback rather than a debounced value, because what needs to happen once
 * the reader stops typing is an *event* — "narrow the list to this" — and not
 * a value for something else to watch. Returning a value instead would mean
 * pushing that event into a state update from an effect, which is a second
 * render for something the caller could have said directly.
 *
 * The timer is torn down on every call and on unmount, so a call that is
 * superseded mid-flight never lands. That matters beyond tidiness: a stale
 * search landing after a newer one would leave the list showing results for a
 * term that is no longer in the box.
 *
 * The latest callback is the one that runs, so `onChange` may be a fresh
 * closure every render without re-arming the timer on every render.
 */
export function useDebouncedCallback<A extends unknown[]>(
  callback: (...args: A) => void,
  delayMs: number,
): (...args: A) => void {
  const latest = useRef(callback)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    latest.current = callback
  }, [callback])

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current)
    },
    [],
  )

  return useCallback(
    (...args: A) => {
      if (timer.current !== null) clearTimeout(timer.current)
      timer.current = setTimeout(() => {
        timer.current = null
        latest.current(...args)
      }, delayMs)
    },
    [delayMs],
  )
}
