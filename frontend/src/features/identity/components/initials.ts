import type { AuthUser } from '../auth/authApi'

/** The initials shown when there is no photo. */
export function initialsFor(user: AuthUser | null): string {
  if (!user) return '?'
  const fromName = `${user.firstName.trim().charAt(0)}${user.lastName.trim().charAt(0)}`
  return (fromName || user.email.charAt(0)).toUpperCase()
}
