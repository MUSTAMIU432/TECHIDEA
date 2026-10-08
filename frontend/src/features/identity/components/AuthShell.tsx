import type { ReactNode } from 'react'

import { AuthBrandPanel, BrandMark } from './AuthBrandPanel'

interface AuthShellProps {
  children: ReactNode
}

/**
 * Shared split-screen shell for every page in the auth system (/auth,
 * /reset-password, ...): the dark brand panel on desktop, a condensed
 * brand header on mobile, and a light card surface for the form. Pulled out
 * of AuthPage so /reset-password can visually belong to the same system
 * without duplicating this markup.
 */
export function AuthShell({ children }: AuthShellProps) {
  return (
    <div className="grid min-h-screen bg-white lg:grid-cols-2">
      <AuthBrandPanel />

      <div className="bg-gradient-to-br from-brand-950 via-brand-900 to-brand-950 px-6 py-10 text-center lg:hidden">
        <div className="flex justify-center">
          <BrandMark />
        </div>
        <p className="mx-auto mt-3 max-w-sm text-sm text-brand-100/70">
          Turn problems into automation opportunities.
        </p>
      </div>

      {/*
        min-w-0: without it, a grid item's automatic minimum width is its
        content's intrinsic size — and a plain <input> carries its own
        content-based minimum (from the default 20-character sizing hint)
        that flex-basis/min-w-0 on the input itself doesn't fully cancel out
        for the purposes of an ancestor's auto grid track. That let the phone
        number field force this whole column (and the page) wider than the
        viewport on narrow screens. min-w-0 here caps the track at the
        available width instead.
      */}
      <div className="flex min-w-0 items-center justify-center px-6 py-10 sm:px-10 lg:bg-gray-50 lg:px-16 lg:py-16">
        <div className="w-full max-w-lg lg:max-w-xl lg:rounded-2xl lg:border lg:border-gray-100 lg:bg-white lg:p-12 lg:shadow-[0_1px_2px_rgba(16,24,40,0.04),0_2px_8px_rgba(16,24,40,0.05)]">
          {children}
        </div>
      </div>
    </div>
  )
}
