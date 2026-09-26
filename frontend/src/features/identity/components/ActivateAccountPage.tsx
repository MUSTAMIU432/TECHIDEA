import { ActivateAccountForm } from './ActivateAccountForm'
import { AuthShell } from './AuthShell'

/**
 * /activate-account — the page a user lands on from the confirmation-link
 * email (expected shape: /activate-account?token=<token>). Shares AuthShell
 * with /auth and /reset-password so all three read as one authentication
 * system rather than three separate pages.
 */
export function ActivateAccountPage() {
  return (
    <AuthShell>
      <ActivateAccountForm />
    </AuthShell>
  )
}
