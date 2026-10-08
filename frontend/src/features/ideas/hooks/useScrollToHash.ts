import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

const HIGHLIGHT = ['ring-4', 'ring-amber-300', 'ring-offset-2', 'transition-shadow']

/**
 * Bring the section named by the URL's `#hash` into view, and mark it for a moment.
 *
 * Where a notification points (`/app/ideas/4#respond`). The sections load on their own -
 * the letter and the proposal each ask the server - so this waits for the element rather
 * than looking once, and gives up quietly after a few seconds if it never appears.
 */
export function useScrollToHash(ready: boolean) {
  const { hash } = useLocation()

  useEffect(() => {
    if (!ready || hash.length < 2) return
    const id = decodeURIComponent(hash.slice(1))
    let tries = 0
    let unmark: number | undefined
    const timer = window.setInterval(() => {
      const target = document.getElementById(id)
      tries += 1
      if (target === null) {
        if (tries > 40) window.clearInterval(timer)
        return
      }
      window.clearInterval(timer)
      target.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
      target.classList.add(...HIGHLIGHT)
      unmark = window.setTimeout(() => target.classList.remove(...HIGHLIGHT), 2500)
    }, 100)
    return () => {
      window.clearInterval(timer)
      if (unmark !== undefined) window.clearTimeout(unmark)
    }
  }, [ready, hash])
}
