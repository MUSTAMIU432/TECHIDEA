import type { AdminAuditAction, AdminIdeaHome } from '../api/administrationApi'

/** Presentation only: labels for what the server returned. */

export function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDay(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** Whose idea it is: its organization, its team, or "Individual". */
export function ideaHomeLabel(idea: AdminIdeaHome): string {
  if (idea.submissionContext === 'ORGANIZATION' && idea.organization) return idea.organization.name
  if (idea.submissionContext === 'TEAM' && idea.teamName) return `Team: ${idea.teamName}`
  if (idea.submissionContext === 'TEAM') return 'Team'
  return 'Individual'
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export function fullName(user: { firstName: string; lastName: string; email: string }): string {
  return `${user.firstName} ${user.lastName}`.trim() || user.email
}

export const AUDIT_ACTION_LABELS: Record<AdminAuditAction, string> = {
  USER_ACTIVATED: 'Account activated',
  USER_DEACTIVATED: 'Account deactivated',
  MEMBERSHIP_ROLE_ASSIGNED: 'Organization role assigned',
  MEMBERSHIP_ROLE_REMOVED: 'Organization role removed',
  CATEGORY_CREATED: 'Category created',
  CATEGORY_UPDATED: 'Category updated',
  CATEGORY_ACTIVATED: 'Category reactivated',
  CATEGORY_DEACTIVATED: 'Category retired',
  ATTACHMENT_DOWNLOADED: 'Evidence downloaded',
  PLATFORM_ADMIN_GRANTED: 'Platform administration granted',
  PLATFORM_ADMIN_REVOKED: 'Platform administration revoked',
}

export function auditActionLabel(action: AdminAuditAction): string {
  return AUDIT_ACTION_LABELS[action] ?? action
}

/** Shown wherever the server withheld content the administrator may not read. */
export const RESTRICTED_TITLE = 'Restricted idea'
