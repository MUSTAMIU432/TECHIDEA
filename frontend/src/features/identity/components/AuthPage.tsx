import { useState } from 'react'
import { useLocation } from 'react-router-dom'

import type { AuthView } from '../types/auth'
import { AuthShell } from './AuthShell'
import { AuthTabs } from './AuthTabs'
import { ForgotPasswordForm } from './ForgotPasswordForm'
import { SignInForm } from './SignInForm'
import { SignUpForm } from './SignUpForm'

function getReturnTo(state: unknown): string {
  if (typeof state !== 'object' || state === null || !('from' in state)) return '/app'

  const from = state.from
  if (typeof from !== 'object' || from === null || !('pathname' in from)) return '/app'

  const pathname = from.pathname
  if (typeof pathname !== 'string' || (pathname !== '/app' && !pathname.startsWith('/app/'))) {
    return '/app'
  }

  const search = 'search' in from && typeof from.search === 'string' ? from.search : ''
  const hash = 'hash' in from && typeof from.hash === 'string' ? from.hash : ''
  return `${pathname}${search}${hash}`
}

/**
 * Combined sign in / sign up / forgot-password experience at /auth. All
 * three are views of the same card, not separate routes — switching between
 * them never navigates away from /auth.
 */
export function AuthPage() {
  const location = useLocation()
  const [view, setView] = useState<AuthView>('sign-in')
  const returnTo = getReturnTo(location.state)

  if (view === 'forgot-password') {
    return (
      <AuthShell>
        <ForgotPasswordForm onBackToSignIn={() => setView('sign-in')} />
      </AuthShell>
    )
  }

  return (
    <AuthShell>
      <AuthTabs mode={view} onChange={setView} />

      <div
        role="tabpanel"
        id={`auth-panel-${view}`}
        aria-labelledby={`auth-tab-${view}`}
        tabIndex={-1}
        className="mt-8 motion-safe:animate-[fade-in_150ms_ease-out]"
      >
        {view === 'sign-in' ? (
          <SignInForm
            returnTo={returnTo}
            onSwitchToSignUp={() => setView('sign-up')}
            onForgotPassword={() => setView('forgot-password')}
          />
        ) : (
          <SignUpForm returnTo={returnTo} onSwitchToSignIn={() => setView('sign-in')} />
        )}
      </div>
    </AuthShell>
  )
}
