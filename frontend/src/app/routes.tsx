import { createBrowserRouter } from 'react-router-dom'

import type * as AdminPages from '../features/administration/pages'

import { ActivateAccountPage } from '../features/identity/components/ActivateAccountPage'
import { AuthPage } from '../features/identity/components/AuthPage'
import { RequireAuth } from '../features/identity/components/RequireAuth'
import { ResetPasswordPage } from '../features/identity/components/ResetPasswordPage'
import { AcceptInvitationPage } from '../features/invitations/components/AcceptInvitationPage'
import { MessageThreadPage } from '../features/messaging/components/MessageThreadPage'
import { MessagesPage } from '../features/messaging/components/MessagesPage'
import { NotificationsPage } from '../features/notifications/components/NotificationsPage'
import { IdeaReportPage } from '../features/reviews/components/IdeaReportPage'
import { AppLayout } from '../layouts/AppLayout'
import { RootLayout } from '../layouts/RootLayout'
import { DashboardPage } from '../routes/DashboardPage'
import { EditIdeaPage } from '../routes/EditIdeaPage'
import { HomePage } from '../routes/HomePage'
import { IdeasPage } from '../routes/IdeasPage'
import { NewIdeaPage } from '../routes/NewIdeaPage'
import { NotFoundPage } from '../routes/NotFoundPage'
import { ReviewsPage } from '../routes/ReviewsPage'
import { TeamDetailPage } from '../routes/TeamDetailPage'
import { TeamsPage } from '../routes/TeamsPage'

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
 * rest of the authenticated app. Every `/app` child renders inside AppLayout,
 * which owns the shared header/navigation and the single OrganizationProvider,
 * so the active organization survives navigation between app pages.
 * `/app/ideas/new` is the create-idea form; saving or cancelling it navigates
 * back to `/app/ideas`.
 *
 * `/app/admin` is the platform administration console. It sits under `/app`
 * to inherit the sign-in gate, and `AdminLayout` adds a second, presentational
 * gate (a "not authorized" panel for anyone who is not a platform
 * administrator). Neither is the security boundary: the server authorizes
 * every console query and mutation. Not `/admin`, which the backend serves
 * as Django's own admin site.
 *
 * The console is loaded on demand (`lazy`): it is for a handful of platform
 * administrators, so nobody else downloads it with the customer app.
 */
type AdminPageName = keyof typeof AdminPages

/** A console page, loaded with the rest of the console the first time it is visited. */
function adminPage(name: AdminPageName) {
  return async () => ({ Component: (await import('../features/administration/pages'))[name] })
}

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
  /*
    An invitation link, at the path the backend builds (`invitations.email
    .INVITATION_PATH`, shape `?token=`). Top-level like the two above and not a
    `/app` child: the recipient may have no account yet, and the invitation may be
    the reason they arrive at all. It is the only page outside `/app` that can act
    on a token, and it needs a session to accept — which the page itself says
    rather than failing with a redirect.
  */
  { path: '/invitations/accept', element: <AcceptInvitationPage /> },
  {
    path: '/app',
    element: <RequireAuth />,
    children: [
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: 'ideas', element: <IdeasPage /> },
          { path: 'ideas/new', element: <NewIdeaPage /> },
          { path: 'ideas/:ideaId/edit', element: <EditIdeaPage /> },
          /*
            The platform's report on an idea, and the author's own decision to
            act on it. It is a page of its own rather than a section on the
            card because the report is long and the decision it ends in deserves
            the reader's whole attention — the same reasoning as the create and
            edit forms.
          */
          { path: 'ideas/:ideaId/report', element: <IdeaReportPage /> },
          { path: 'reviews', element: <ReviewsPage /> },
          /*
            Teams and messages are `/app` children but read nothing from
            `OrganizationProvider`: a team is a collaboration boundary rather than
            a tenant, and a private message belongs to its participants, so
            neither has an organization to scope itself to. Putting them under
            `/app` is still what gives them the sign-in gate.
          */
          { path: 'teams', element: <TeamsPage /> },
          { path: 'teams/:teamId', element: <TeamDetailPage /> },
          { path: 'messages', element: <MessagesPage /> },
          { path: 'messages/:threadId', element: <MessageThreadPage /> },
          { path: 'notifications', element: <NotificationsPage /> },
          {
            path: 'admin',
            lazy: async () => ({
              Component: (await import('../features/administration/components/AdminLayout'))
                .AdminLayout,
            }),
            children: [
              { index: true, lazy: adminPage('AdminDashboardPage') },
              { path: 'users', lazy: adminPage('AdminUsersPage') },
              { path: 'users/:userId', lazy: adminPage('AdminUserDetailPage') },
              { path: 'organizations', lazy: adminPage('AdminOrganizationsPage') },
              {
                path: 'organizations/:organizationId',
                lazy: adminPage('AdminOrganizationDetailPage'),
              },
              { path: 'ideas', lazy: adminPage('AdminIdeasPage') },
              { path: 'ideas/:ideaId', lazy: adminPage('AdminIdeaDetailPage') },
              { path: 'reviews', lazy: adminPage('AdminReviewsPage') },
              { path: 'reviews/:reviewId', lazy: adminPage('AdminReviewDetailPage') },
              { path: 'approvals', lazy: adminPage('AdminApprovalsPage') },
              { path: 'categories', lazy: adminPage('AdminCategoriesPage') },
            ],
          },
        ],
      },
    ],
  },
])
