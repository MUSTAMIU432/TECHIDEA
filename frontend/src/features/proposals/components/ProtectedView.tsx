import type { ReactNode } from 'react'

import type { ViewReceipt } from '../api/proposalsApi'

/**
 * A page that is meant to be read and not taken away.
 *
 * What this does: no text selection, no copy, cut, drag or right-click, nothing at all when
 * printed, and the reader's name, email and the time repeated across the page. What it cannot
 * do, and does not claim to: a browser has no way to stop a screenshot or a photograph of the
 * screen. The watermark is the deterrent for that - a leaked image carries the name of whoever
 * it was shown to - and the page says so rather than promising more.
 */
export function ProtectedView({
  receipt,
  children,
}: {
  receipt: ViewReceipt
  children: ReactNode
}) {
  const stamp = `${receipt.viewerName} · ${receipt.viewerEmail} · ${new Date(
    receipt.viewedAt,
  ).toLocaleString()}`
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='460' height='170'>
    <text x='20' y='110' fill='rgba(15,23,42,0.11)' font-size='15' font-family='sans-serif'
      transform='rotate(-22 20 110)'>${stamp.replace(/[<>&']/g, '')}</text></svg>`
  const background = `url("data:image/svg+xml;utf8,${encodeURIComponent(svg)}")`

  const block = (event: { preventDefault: () => void }) => event.preventDefault()

  return (
    <div
      className="relative rounded-xl border border-gray-200 bg-white p-6 shadow-sm select-none print:hidden"
      onCopy={block}
      onCut={block}
      onDragStart={block}
      onContextMenu={block}
      data-protected="true"
    >
      <div
        aria-hidden="true"
        data-testid="watermark"
        className="pointer-events-none absolute inset-0 z-10"
        style={{ backgroundImage: background }}
      />
      <div className="relative">{children}</div>
    </div>
  )
}
