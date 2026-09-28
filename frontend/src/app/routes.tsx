import { createBrowserRouter } from 'react-router-dom'

import { ActivateAccountPage } from '../features/identity/components/ActivateAccountPage'
import { AuthPage } from '../features/identity/components/AuthPage'
import { RequireAuth } from '../features/identity/components/RequireAuth'
import { ResetPasswordPage } from '../features/identity/components/ResetPasswordPage'
import { OrganizationProvider } from '../features/organizations/context/OrganizationProvider'
import { RootLayout } from '../layouts/RootLayout'
import { DashboardPage } from '../routes/DashboardPage'
import { IdeasPage } from '../routes/IdeasPage'
import { HomePage } from '../routes/HomePage'
import { NotFoundPage } from '../routes/NotFoundPage'
import { ReviewsPage } from '../routes/ReviewsPage'

/**
 * Route tree for the app. Future business domains (identity, organizations,
 * ideas, ...) each get their own route module under src/features/<domain>
 * and are wired in here.
 *
 * /auth, /reset-password and /activate-account are top-level routes rather
 * than RootLayout children: the auth system owns the full viewport
 * (split-screen brand + form panel) instead of sitting inside the shared app
 * shell/header. The latter two are the pages the emailed links point at -
 * the backend builds those URLs from FRONTEND_URL (see
 * backend/identity/email.py), so the paths here and the ones it uses are one
 * contract: `identity.email.ACTIVATION_PATH` / `PASSWORD_RESET_PATH`, pinned
 * from both sides by the backend suite and by
 * `ActivateAccountForm.test.tsx` / `ResetPasswordForm.test.tsx`.
 *
 * /app is the authenticated application area, gated by RequireAuth - it
 * redirects to /auth when there's no authenticated session. `/app/ideas` is a
 * child rather than a top-level route for exactly that reason: it inherits the
 * gate and the OrganizationProvider, so the ideas screen cannot be reached
 * without a session and takes its organization from the same switcher as the
 * rest of the authenticated app.
 */
export const router = createBrowserRouter([
  {
    path: '/',
    element: <RootLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
  { path: '/auth', element: <AuthPage /> },
  { path: '/reset-password', element: <ResetPasswordPage /> },
  { path: '/activate-account', element: <ActivateAccountPage /> },
  {
    path: '/app',
    element: <RequireAuth />,
    children: [
      {
        index: true,
        element: (
          <OrganizationProvider>
            <DashboardPage />
          </OrganizationProvider>
        ),
      },
      {
        path: 'ideas',
        element: (
          <OrganizationProvider>
            <IdeasPage />
          </OrganizationProvider>
        ),
      },
      {
        path: 'reviews',
        element: (
          <OrganizationProvider>
            <ReviewsPage />
          </OrganizationProvider>
        ),
      },
    ],
  },
])
