import { useLocation } from 'react-router-dom'

/**
 * Where a detail page should send the reader who presses "back".
 *
 * **The view a person came from, not that view's front door.** Every console list
 * is filtered and paged through the URL, so an administrator who narrowed to one
 * organization, turned to page three and opened a row has done four things worth
 * keeping - and a link to `/app/admin/ideas` throws all four away, which is the
 * complaint this exists to fix. The list passes its own address as link state
 * (`useUrlFilters().here`) and this reads it back.
 *
 * Two guards, because "where did I come from" is attacker-shaped data:
 *
 * - **It must be a path inside the section being left.** Otherwise a crafted
 *   `from` could bounce an administrator out of the console and back in through a
 *   redirect - or off the platform entirely - on a click that looked local. It
 *   also keeps the link's own wording honest: a control that says "All ideas" and
 *   lands on the accounts list is worse than no cleverness at all. The cost is
 *   that an idea opened from the dashboard's recent activity goes back to the
 *   ideas list rather than to the dashboard, because the dashboard is not a
 *   filtered view of the ideas list and pretending otherwise would be the
 *   misleading part.
 * - **It is only ever a fallback for the section root, never a replacement for
 *   it.** A page opened cold, pasted or bookmarked has no state, and the reader
 *   still gets a working way out.
 *
 * A `Link` rather than a `navigate(-1)` button, deliberately: a real link can be
 * middle-clicked, opened in a new tab and read by assistive technology as a
 * destination, and it never leaves the console history growing behind the
 * reader's back.
 */
export function useBackTarget(sectionRoot: string): string {
  const location = useLocation()
  const from = (location.state as { from?: unknown } | null | undefined)?.from
  if (typeof from !== 'string') return sectionRoot
  // The root itself and everything nested under it, and nothing else: a prefix
  // match on "/app/admin/ideas" would also accept "/app/admin/ideas-and-more".
  if (from !== sectionRoot && !from.startsWith(`${sectionRoot}?`)) return sectionRoot
  return from
}
