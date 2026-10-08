import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import type { RouteObject } from 'react-router-dom'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom'

import { AuthProvider } from '../features/identity/auth/AuthContext'

/**
 * Render a route tree at `path` using an in-memory router (no real browser
 * history). Wrapped in AuthProvider, mirroring App.tsx's real composition -
 * routes reachable from the real router (e.g. /auth, /app) need `useAuth()`
 * to be available. A test exercising a route that calls the auth API
 * (directly or via a rendered child like SignInForm) must mock
 * `features/identity/auth/authApi` itself, the same as renderWithProviders.
 */
export function renderRoutes(routes: RouteObject[], path = '/') {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  return render(
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>,
  )
}

/** Render a single component that uses router primitives (e.g. `<Link>`) outside a route tree. */
export function renderWithRouter(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

/**
 * Render a component that needs both router primitives and `useAuth()`
 * (e.g. SignInForm, AuthPage). The caller must mock
 * `features/identity/auth/authApi` itself (`vi.mock` is file-scoped/hoisted,
 * so a shared helper can't do it) - otherwise AuthProvider's mount-time
 * silent-refresh attempt hits the real GraphQL client.
 */
export function renderWithProviders(ui: ReactElement) {
  return render(
    <MemoryRouter>
      <AuthProvider>{ui}</AuthProvider>
    </MemoryRouter>,
  )
}
