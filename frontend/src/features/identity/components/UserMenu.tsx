import { useEffect, useId, useRef, useState } from 'react'

import type { AuthUser } from '../auth/authApi'

interface UserMenuProps {
  user: AuthUser | null
  onSignOut: () => void
}

function initialsFor(user: AuthUser | null): string {
  if (!user) return '?'
  const fromName = `${user.firstName.trim().charAt(0)}${user.lastName.trim().charAt(0)}`
  return (fromName || user.email.charAt(0)).toUpperCase()
}

function displayName(user: AuthUser | null): string {
  if (!user) return 'Account'
  return `${user.firstName} ${user.lastName}`.trim() || user.email
}

/**
 * Profile avatar that opens the account menu (who is signed in, and Sign out).
 * Closes on Escape, on a click outside it, and after choosing an item; Escape
 * returns focus to the avatar so keyboard users are not dropped at the top of
 * the page.
 */
export function UserMenu({ user, onSignOut }: UserMenuProps) {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const firstItemRef = useRef<HTMLButtonElement>(null)
  const menuId = useId()

  useEffect(() => {
    if (!isOpen) return
    firstItemRef.current?.focus()

    function handlePointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setIsOpen(false)
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsOpen(false)
        triggerRef.current?.focus()
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen])

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-label="Account menu"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-controls={isOpen ? menuId : undefined}
        onClick={() => setIsOpen((open) => !open)}
        className="flex h-10 w-10 items-center justify-center rounded-full bg-white text-sm font-bold text-brand-800 shadow-sm ring-2 ring-white/40 transition hover:ring-white/80 focus:outline-none focus-visible:ring-white"
      >
        {initialsFor(user)}
      </button>

      {isOpen && (
        <div
          id={menuId}
          role="menu"
          aria-label="Account"
          className="absolute right-0 z-40 mt-2 w-64 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl shadow-gray-900/10"
        >
          <div className="flex items-center gap-3 border-b border-gray-100 px-4 py-3">
            <span
              aria-hidden="true"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-600 text-sm font-bold text-white"
            >
              {initialsFor(user)}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm font-bold text-gray-900">
                {displayName(user)}
              </span>
              {user && <span className="block truncate text-xs text-gray-500">{user.email}</span>}
            </span>
          </div>
          <div className="p-1.5">
            <button
              ref={firstItemRef}
              type="button"
              role="menuitem"
              onClick={() => {
                setIsOpen(false)
                onSignOut()
              }}
              className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm font-semibold text-gray-700 hover:bg-red-50 hover:text-red-700 focus:bg-red-50 focus:text-red-700 focus:outline-none"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M8 17H4.5A1.5 1.5 0 0 1 3 15.5v-11A1.5 1.5 0 0 1 4.5 3H8M13 14l4-4-4-4M17 10H8" />
              </svg>
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
