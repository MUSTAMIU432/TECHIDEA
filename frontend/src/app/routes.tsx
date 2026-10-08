import { createBrowserRouter, Navigate } from 'react-router-dom'
import { AccountSettingsPage } from '../features/identity/components/AccountSettingsPage'

import type * as AdminPages from '../features/administration/pages'

import { ActivateAccountPage } from '../features/identity/components/ActivateAccountPage'
import { AuthPage } from '../features/identity/components/AuthPage'
import { RequireAuth } from '../features/identity/components/RequireAuth'
import { ResetPasswordPage } from '../features/identity/components/ResetPasswordPage'
import { AcceptInvitationPage } from '../features/invitations/components/AcceptInvitationPage'
import { MessageThreadPage } from '../features/messaging/components/MessageThreadPage'
import { MessagesPage } from '../features/messaging/components/MessagesPage'
import { NotificationsPage } from '../features/notifications/components/NotificationsPage'
import { IdeaDetailPage } from '../features/ideas/components/IdeaDetailPage'
import { IdeaReportPage } from '../features/reviews/components/IdeaReportPage'
import { ProposalWorkspacePage } from '../features/proposals/components/ProposalWorkspacePage'
import { AppLayout } from '../layouts/AppLayout'
import { RootLayout } from '../layouts/RootLayout'
import { DashboardPage } from '../routes/DashboardPage'
import { EditIdeaPage } from '../routes/EditIdeaPage'
import { HomePage } from '../routes/HomePage'
import { IdeasPage } from '../routes/IdeasPage'
import { NewIdeaPage } from '../routes/NewIdeaPage'
import { NotFoundPage } from '../routes/NotFoundPage'
import { ReviewsPage } from '../routes/ReviewsPage'
import { OrganizationDetailPage } from '../features/organizations/components/OrganizationDetailPage'
import { TeamDetailPage } from '../routes/TeamDetailPage'
import { ProposalReadPage } from '../features/proposals/components/ProposalReadPage'
import { AutomationLayout } from '../features/automation/components/AutomationLayout'
import { DeveloperQueuePage } from '../features/automation/components/DeveloperQueuePage'
import { OpportunitiesPage } from '../features/automation/components/OpportunitiesPage'
import { OpportunityPage } from '../features/automation/components/OpportunityPage'
import { ProjectPage } from '../features/automation/components/ProjectPage'
import { ProjectsPage } from '../features/automation/components/ProjectsPage'
import { OrganizationsPage } from '../routes/OrganizationsPage'
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
  return async () => ({
    Component: (await import('../features/administration/pages'))[name],
  })
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
          /*
            One idea. Three places already linked here and all three were a 404,
            because ideas existed only as rows in a list - so this is the fix as
            much as the feature. Its query parameters carry the ownership the
            reader chose: `?context=team&team=9` opens the form already decided,
            which is what makes a Team page's "Create Idea" a link rather than a
            second copy of the ownership question.
          */
          { path: 'ideas/:ideaId', element: <IdeaDetailPage /> },
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
          { path: 'ideas/:ideaId/proposal', element: <ProposalReadPage /> },
          { path: 'reviews', element: <ReviewsPage /> },
          { path: 'reviews/proposals/:ideaId', element: <ProposalWorkspacePage /> },
          /*
            Teams and messages are `/app` children but read nothing from
            `OrganizationProvider`: a team is a collaboration boundary rather than
            a tenant, and a private message belongs to its participants, so
            neither has an organization to scope itself to. Putting them under
            `/app` is still what gives them the sign-in gate.
          */
          { path: 'organizations', element: <OrganizationsPage /> },
          {
            path: 'automation',
            element: <AutomationLayout />,
            children: [
              { index: true, element: <Navigate to="opportunities" replace /> },
              { path: 'opportunities', element: <OpportunitiesPage /> },
              { path: 'opportunities/:opportunityId', element: <OpportunityPage /> },
              { path: 'queue', element: <DeveloperQueuePage /> },
              { path: 'projects', element: <ProjectsPage /> },
              { path: 'projects/:projectId', element: <ProjectPage /> },
            ],
          },
          { path: 'teams', element: <TeamsPage /> },
          { path: 'teams/:teamId', element: <TeamDetailPage /> },
          {
            path: 'organizations/:organizationId',
            element: <OrganizationDetailPage />,
          },
          { path: 'messages', element: <MessagesPage /> },
          { path: 'messages/:threadId', element: <MessageThreadPage /> },
          { path: 'notifications', element: <NotificationsPage /> },
          {
            path: 'settings',
            element: <Navigate to="/app/settings/profile" replace />,
          },
          { path: 'settings/:section', element: <AccountSettingsPage /> },
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
              {
                path: 'organizations',
                lazy: adminPage('AdminOrganizationsPage'),
              },
              {
                path: 'organizations/:organizationId',
                lazy: adminPage('AdminOrganizationDetailPage'),
              },
              { path: 'ideas', lazy: adminPage('AdminIdeasPage') },
              { path: 'ideas/:ideaId', lazy: adminPage('AdminIdeaDetailPage') },
              { path: 'reviews', lazy: adminPage('AdminReviewsPage') },
              {
                path: 'reviews/:reviewId',
                lazy: adminPage('AdminReviewDetailPage'),
              },
              { path: 'approvals', lazy: adminPage('AdminApprovalsPage') },
              { path: 'reviewers', lazy: adminPage('AdminReviewersPage') },
              { path: 'roles', lazy: adminPage('AdminPlatformRolesPage') },
              { path: 'decisions', lazy: adminPage('AdminDecisionsPage') },
              { path: 'proposals', lazy: adminPage('AdminProposalsPage') },
              { path: 'automation', lazy: adminPage('AdminAutomationPage') },
              /*
                The collaboration set: teams, and the three record sets that
                answer how people got into them, how they talk, and what the
                platform has told them. All read-only, and Teams is a sibling of
                Organizations rather than a child of it - a team is a
                collaboration boundary, not a tenant.
              */
              { path: 'teams', lazy: adminPage('AdminTeamsPage') },
              { path: 'teams/:teamId', lazy: adminPage('AdminTeamDetailPage') },
              { path: 'invitations', lazy: adminPage('AdminInvitationsPage') },
              { path: 'messages', lazy: adminPage('AdminMessagesPage') },
              {
                path: 'messages/:threadId',
                lazy: adminPage('AdminMessageDetailPage'),
              },
              {
                path: 'notifications',
                lazy: adminPage('AdminNotificationsPage'),
              },
              { path: 'categories', lazy: adminPage('AdminCategoriesPage') },
            ],
          },
        ],
      },
    ],
  },
])
