/**
 * The console's icon set, in one place.
 *
 * Every glyph here is drawn on the same frame - a 24-unit box, a 1.75 stroke, round
 * caps and joins - because that is what makes a row of them read as one row. An
 * icon file per page is how a dashboard ends up with four marks that are four
 * slightly different weights, and the eye reads the inconsistency before it reads
 * any of the numbers they sit above.
 *
 * `size` is a named step rather than a class a caller appends, deliberately: two
 * Tailwind size utilities in one `class` string do not compose - whichever the
 * build emits last wins - so a caller asking for a smaller icon would get the
 * default and never know why.
 *
 * All of them are `aria-hidden`. They sit beside the word they illustrate, never
 * instead of it, so a screen reader announcing them would only add noise.
 */
import type { ReactNode } from 'react'

export type AdminIconSize = 'sm' | 'md' | 'lg'

function Icon({ size = 'md', children }: { size?: AdminIconSize; children: ReactNode }) {
  const box = size === 'lg' ? 'h-9 w-9' : size === 'sm' ? 'h-4 w-4' : 'h-5 w-5'
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${box}`}
    >
      {children}
    </svg>
  )
}

/** Accounts. Two figures, so it is not read as a single person. */
export function UsersIcon({ size }: { size?: AdminIconSize }) {
  return (
    <Icon size={size}>
      <path d="M16 20v-1.5a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4V20" />
      <circle cx="9" cy="7" r="3.25" />
      <path d="M22 20v-1.5a4 4 0 0 0-3-3.87" />
      <path d="M16 4.13a4 4 0 0 1 0 5.74" />
    </Icon>
  )
}

/** Organizations: a building, which is what a tenant is to somebody reading it. */
export function OrganizationIcon({ size }: { size?: AdminIconSize }) {
  return (
    <Icon size={size}>
      <path d="M3 21h18" />
      <path d="M5 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16" />
      <path d="M15 9h2a2 2 0 0 1 2 2v10" />
      <path d="M9 7h2M9 11h2M9 15h2" />
    </Icon>
  )
}

/** Ideas: a bulb. The one glyph on the platform that means "not built yet". */
export function IdeaIcon({ size }: { size?: AdminIconSize }) {
  return (
    <Icon size={size}>
      <path d="M9 18h6" />
      <path d="M10 21.5h4" />
      <path d="M12 2.5a6.5 6.5 0 0 0-4 11.6c.6.5 1 1.2 1 2h6c0-.8.4-1.5 1-2a6.5 6.5 0 0 0-4-11.6Z" />
    </Icon>
  )
}

/** Reviews: a clipboard with a tick - what happened to an idea, not its text. */
export function ReviewIcon({ size }: { size?: AdminIconSize }) {
  return (
    <Icon size={size}>
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <rect width="8" height="4" x="8" y="2" rx="1" />
      <path d="m9 14 2 2 4-4" />
    </Icon>
  )
}

/** Awaiting review: an inbox with something sitting in it. */
export function InboxIcon({ size }: { size?: AdminIconSize }) {
  return (
    <Icon size={size}>
      <path d="M3 13h4l1.5 3h7L17 13h4" />
      <path d="M5.5 5h13l2.5 8v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6Z" />
    </Icon>
  )
}

/** Approved: a tick in a ring. */
export function ApprovedIcon({ size }: { size?: AdminIconSize }) {
  return (
    <Icon size={size}>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.5 2.5 2.5 4.5-5" />
    </Icon>
  )
}

/** Changes requested: a pencil over a sheet - the idea goes back to be edited. */
export function ChangesIcon({ size }: { size?: AdminIconSize }) {
  return (
    <Icon size={size}>
      <path d="M12 20h8" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z" />
    </Icon>
  )
}

/** Rejected: a cross in a ring. */
export function RejectedIcon({ size }: { size?: AdminIconSize }) {
  return (
    <Icon size={size}>
      <circle cx="12" cy="12" r="9" />
      <path d="m9 9 6 6M15 9l-6 6" />
    </Icon>
  )
}
