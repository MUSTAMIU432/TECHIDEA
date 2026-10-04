import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { AuthShell } from '../../identity/components/AuthShell'
import { SpinnerIcon } from '../../identity/components/icons'
import { useAuth } from '../../identity/auth/AuthContext'
import {
  acceptInvitationRequest,
  invitationDetailsRequest,
  type InvitationPreview,
} from '../api/invitationsApi'

type Load =
  | { state: 'waiting' }
  | { state: 'preview'; preview: InvitationPreview }
  | { state: 'unknown' }
  | { state: 'error' }

type Outcome = { kind: 'none' } | { kind: 'accepted' } | { kind: 'refused'; message: string }

/**
 * `/invitations/accept?token=…` — what an emailed invitation link points at.
 *
 * **A top-level route rather than a `/app` child**, for the same reason
 * `/reset-password` is: the recipient may have no account yet, and an
 * invitation is the message that may be the reason they arrive. It shares
 * `AuthShell` so it reads as part of the same sign-in system.
 *
 * The path is one contract with the backend, which builds the URL from
 * `FRONTEND_URL` and `invitations.email.INVITATION_PATH`; the shape
 * (`?token=`) is the one `identity.email.build_action_url` writes, so this page
 * and the links in every email cannot drift apart.
 *
 * Two rules about what this page does, and both are the server's:
 *
 * - **The token is read once and never stored.** It is not put in component
 *   state that outlives the render, never logged, and sent nowhere but the two
 *   requests that need it. Everything on screen comes from `invitationDetails`,
 *   which is deliberately limited to the tenant's *name* and the role: an
 *   inbox is readable by more than its owner, so a leaked link must not be a
 *   link that reveals anything about the tenant.
 * - **Accepting is bound to the signed-in account's email.** So this page says
 *   plainly when the invitation went to somebody else's address, and it offers
 *   no "switch account" — there is nothing here that could.
 */
export function AcceptInvitationPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const { user } = useAuth()
  /*
    "Waiting" is derived from having no answer *for this token*, rather than
    written as a state transition: an effect that sets it synchronously would be
    a second render to express something knowable already, and a missing token is
    a fact about the URL that render can read directly.
  */
  const [answer, setAnswer] = useState<{ token: string; load: Load } | null>(null)
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'none' })
  const [accepting, setAccepting] = useState(false)

  useEffect(() => {
    // No token is not a request that can be made; it is a link that was copied
    // incompletely, and it renders as the same honest "not valid".
    if (!token) return
    let cancelled = false
    invitationDetailsRequest(token)
      .then((preview) => {
        if (cancelled) return
        setAnswer({
          token,
          load: preview === null ? { state: 'unknown' } : { state: 'preview', preview },
        })
      })
      .catch(() => {
        if (!cancelled) setAnswer({ token, load: { state: 'error' } })
      })
    return () => {
      cancelled = true
    }
  }, [token])

  const load: Load =
    token === null
      ? { state: 'unknown' }
      : answer !== null && answer.token === token
        ? answer.load
        : { state: 'waiting' }

  async function handleAccept() {
    if (!token) return
    setAccepting(true)
    setOutcome({ kind: 'none' })
    try {
      const result = await acceptInvitationRequest(token)
      setOutcome(
        result.success ? { kind: 'accepted' } : { kind: 'refused', message: result.message },
      )
    } catch {
      setOutcome({
        kind: 'refused',
        message: 'We could not reach the server, so nothing has changed. Please try again.',
      })
    } finally {
      setAccepting(false)
    }
  }

  return (
    <AuthShell>
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">You have been invited</h1>

        {load.state === 'waiting' && (
          <p className="mt-4 text-sm text-gray-600">Checking this invitation…</p>
        )}

        {load.state === 'unknown' && (
          <div className="mt-4">
            <p role="alert" className="text-sm text-red-700">
              This invitation link is not valid. It may have already been used, or it may have been
              copied incompletely.
            </p>
            <Link
              to="/auth"
              className="mt-4 inline-block text-sm font-semibold text-brand-700 hover:text-brand-800"
            >
              Go to sign in
            </Link>
          </div>
        )}

        {load.state === 'error' && (
          <p role="alert" className="mt-4 text-sm text-red-700">
            We could not check this invitation. Please refresh the page.
          </p>
        )}

        {load.state === 'preview' && (
          <AcceptBody
            preview={load.preview}
            signedInEmail={user?.email ?? null}
            outcome={outcome}
            accepting={accepting}
            onAccept={() => void handleAccept()}
          />
        )}
      </div>
    </AuthShell>
  )
}

function AcceptBody({
  preview,
  signedInEmail,
  outcome,
  accepting,
  onAccept,
}: {
  preview: InvitationPreview
  signedInEmail: string | null
  outcome: Outcome
  accepting: boolean
  onAccept: () => void
}) {
  /*
    The three dead states are kept apart because the reader can act on each
    differently: an expired one asks the inviter for a new link, a revoked one
    says somebody withdrew it, and an accepted one is already done. One
    "invalid invitation" would waste their time on all three.
  */
  if (outcome.kind === 'accepted') {
    return (
      <div className="mt-4">
        <output className="block text-sm font-semibold text-green-700">
          You have joined {preview.tenantName}.
        </output>
        <Link
          to="/app"
          className="mt-4 inline-block text-sm font-semibold text-brand-700 hover:text-brand-800"
        >
          Go to your workspace
        </Link>
      </div>
    )
  }

  if (preview.accepted) {
    return (
      <output className="mt-4 block text-sm text-gray-600">
        This invitation has already been accepted. If that was not you, tell whoever invited you.
      </output>
    )
  }

  if (preview.revoked) {
    return (
      <output className="mt-4 block text-sm text-gray-600">
        This invitation was withdrawn. Ask {preview.invitedByFirstName} to send another one.
      </output>
    )
  }

  if (preview.expired) {
    return (
      <output className="mt-4 block text-sm text-gray-600">
        This invitation has expired. Ask {preview.invitedByFirstName} to send another one.
      </output>
    )
  }

  /*
    Acceptance is bound to the signed-in account's own email, so this is worth
    saying before anybody presses anything — and it is the one case where being
    signed in *is* the problem rather than the solution. There is nothing here
    that could switch accounts, and offering to would be a false promise.
  */
  const wrongAccount =
    signedInEmail !== null && signedInEmail.toLowerCase() !== preview.email.toLowerCase()

  return (
    <div className="mt-4">
      <p className="text-sm leading-6 text-gray-600">
        {preview.invitedByFirstName} invited{' '}
        <span className="font-semibold text-gray-900">{preview.email}</span> to join{' '}
        <span className="font-semibold text-gray-900">{preview.tenantName}</span> as{' '}
        <span className="font-semibold text-gray-900">{preview.roleName}</span>.
      </p>

      {wrongAccount ? (
        <p role="alert" className="mt-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-900">
          This invitation was sent to {preview.email}, and you are signed in as {signedInEmail}.
          Sign out and sign in with that address to accept it.
        </p>
      ) : outcome.kind === 'refused' ? (
        <p role="alert" className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {outcome.message}
        </p>
      ) : null}

      {signedInEmail === null ? (
        <div className="mt-6">
          <p className="text-sm text-gray-600">
            Sign in as {preview.email} to accept this invitation.
          </p>
          <Link
            to="/auth"
            className="mt-4 inline-flex h-11 items-center justify-center rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            Sign in
          </Link>
        </div>
      ) : (
        <button
          type="button"
          onClick={onAccept}
          disabled={accepting || wrongAccount}
          className="mt-6 inline-flex h-11 items-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {accepting && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
          {accepting ? 'Joining…' : `Join ${preview.tenantName}`}
        </button>
      )}
    </div>
  )
}
