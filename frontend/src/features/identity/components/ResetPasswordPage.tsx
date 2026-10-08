import { AuthShell } from './AuthShell'
import { ResetPasswordForm } from './ResetPasswordForm'

/**
 * /reset-password — the page a user lands on from the reset-link email
 * (expected shape: /reset-password?token=<token>). Shares AuthShell with
 * /auth so it visually belongs to the same authentication system.
 */
export function ResetPasswordPage() {
  return (
    <AuthShell>
      <ResetPasswordForm />
    </AuthShell>
  )
}
