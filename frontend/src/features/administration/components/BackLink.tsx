import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

/**
 * The way back, drawn once so all four detail pages are the same shape.
 *
 * A pill above the title rather than a small link in the far corner, for three
 * reasons that are one reason: it is where a reader looks for "where am I", it
 * cannot drift away from the heading it belongs to on a narrow screen, and it is
 * big enough to hit without aiming.
 *
 * The arrow is an inline chevron rather than a `←` character, because a glyph is
 * drawn by whatever font the reader happens to have - it lands at a different
 * weight and baseline per platform, and at some sizes it is not an arrow at all.
 * It also nudges left on hover, which is the cheapest possible confirmation that
 * the control goes backwards.
 */
export function BackLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link
      to={to}
      className="group inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-semibold text-slate-700 shadow-sm transition-colors hover:border-brand-300 hover:bg-brand-50 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 20 20"
        className="h-4 w-4 transition-transform group-hover:-translate-x-0.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12.5 15 7.5 10l5-5" />
      </svg>
      {children}
    </Link>
  )
}
