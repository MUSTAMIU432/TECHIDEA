import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'

/**
 * Route guard for the authenticated application area. While the initial
 * silent-refresh attempt (AuthContext) is still in flight, renders nothing
 * committal rather than bouncing straight to /auth - a real session behind
 * the refresh cookie shouldn't cause a visible flash of the sign-in page.
 */
export function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-white">
        <p className="text-sm text-gray-500">Loading…</p>
      </div>
    )
  }

  if (status === 'unauthenticated') {
    return <Navigate to="/auth" replace state={{ from: location }} />
  }

  return <Outlet />
}
