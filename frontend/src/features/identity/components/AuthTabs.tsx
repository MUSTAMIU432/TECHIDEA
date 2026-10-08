import type { AuthMode } from '../types/auth'

interface AuthTabsProps {
  mode: AuthMode
  onChange: (mode: AuthMode) => void
}

const TABS: Array<{ mode: AuthMode; label: string }> = [
  { mode: 'sign-in', label: 'Sign In' },
  { mode: 'sign-up', label: 'Sign Up' },
]

/**
 * Underline-style tab switch between the sign in and sign up forms on the
 * same page — reads as part of the auth surface rather than a separate
 * segmented-button control floating above it.
 */
export function AuthTabs({ mode, onChange }: AuthTabsProps) {
  return (
    <div
      role="tablist"
      aria-label="Authentication mode"
      className="flex gap-12 border-b border-gray-200"
    >
      {TABS.map((tab) => {
        const isActive = tab.mode === mode
        return (
          <button
            key={tab.mode}
            type="button"
            role="tab"
            id={`auth-tab-${tab.mode}`}
            aria-selected={isActive}
            aria-controls={`auth-panel-${tab.mode}`}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onChange(tab.mode)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                event.preventDefault()
                onChange(tab.mode === 'sign-in' ? 'sign-up' : 'sign-in')
              }
            }}
            className={[
              // h-11 keeps the tap target comfortable on touch screens even
              // though the visible underline sits tight beneath the label.
              '-mb-px flex h-11 items-center border-b-2 text-sm font-semibold motion-safe:transition-colors motion-safe:duration-150',
              'focus:outline-none focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2',
              isActive
                ? 'border-brand-600 text-gray-900'
                : 'border-transparent text-gray-400 hover:border-gray-300 hover:text-gray-600',
            ].join(' ')}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}
