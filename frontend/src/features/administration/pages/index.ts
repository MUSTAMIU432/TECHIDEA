/**
 * Every console page, as one module, so the router can load the whole
 * console as a single on-demand chunk the first time an administrator opens
 * it (see `src/app/routes.tsx`).
 */
export { AdminCategoriesPage } from './AdminCategoriesPage'
export { AdminDashboardPage } from './AdminDashboardPage'
export { AdminIdeaDetailPage } from './AdminIdeaDetailPage'
export { AdminIdeasPage } from './AdminIdeasPage'
export { AdminOrganizationDetailPage } from './AdminOrganizationDetailPage'
export { AdminOrganizationsPage } from './AdminOrganizationsPage'
export { AdminReviewDetailPage } from './AdminReviewDetailPage'
export { AdminApprovalsPage, AdminReviewsPage } from './AdminReviewsPage'
export { AdminUserDetailPage } from './AdminUserDetailPage'
export { AdminUsersPage } from './AdminUsersPage'
