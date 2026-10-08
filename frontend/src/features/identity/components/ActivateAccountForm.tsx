import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { activateAccountRequest, resendActivationEmailRequest } from '../auth/authApi'
import { validateEmail } from '../schemas/authValidation'
import { SpinnerIcon } from './icons'
import { TextField } from './TextField'

const PRIMARY_BUTTON_CLASSES =
  'flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-brand-600 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-brand-300 disabled:shadow-none'

/**
 * `idle` is the armed state - a usable link, waiting for a deliberate press.
 * It is a distinct state from `confirming` on purpose: folding the two
 * together would render the button disabled and spinning from the moment the
 * page loads, which reads as "working on it" while actually leaving the user
 * with nothing to click and no way to ever confirm.
 */
type Status = 'idle' | 'confirming' | 'confirmed' | 'resending' | 'resent' | 'invalid-token'

/**
 * The page an activation link lands on (`/activate-account?token=…`).
 *
 * Registration emails a confirmation link, so this is a page the product
 * genuinely sends people to; it exists in the same shape as the reset flow's
 * because it *is* the same machinery with a different outcome - one
 * single-use token, spent on redemption.
 *
 * Three things this view deliberately does not do:
 *
 * - **It does not sign anybody in.** Confirming an address proves control of
 *   a mailbox; it is not a session. The backend returns no access token for
 *   this mutation, and there is no code path here that would treat success as
 *   a login.
 * - **It does not say why a link failed.** The backend answers an unusable
 *   token with one message whatever was wrong with it — never real, expired,
 *   already used, or a reset token presented here — and repeating that
 *   discipline here keeps a forwarded or stolen link from learning anything.
 * - **It does not require the confirmation.** An unverified account can sign
 *   in exactly as before (the backend's `login` does not check
 *   `isVerified`), so this page never blocks anybody; it explains what
 *   confirming does and offers the resend for a lost message.
 *
 * Following the link is an explicit button press rather than a request on
 * mount, and that is not a UX accident. The token is single-use, and mail
 * clients and security scanners routinely *fetch* links in a message before
 * a person sees them; confirming on mount would spend the token on a
 * prefetch and leave the real click landing on an "invalid link" page. A
 * deliberate press means only a human redeems the token.
 */
export function ActivateAccountForm() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')

  const [status, setStatus] = useState<Status>(token ? 'idle' : 'invalid-token')
  const [email, setEmail] = useState('')
  const [emailError, setEmailError] = useState<string | undefined>()
  const [notice, setNotice] = useState<string | undefined>()

  async function confirm() {
    setStatus('confirming')
    try {
      const result = await activateAccountRequest(token as string)
      if (result.success) {
        setStatus('confirmed')
        return
      }
      // `field === 'token'`, or anything else the backend declined: the link
      // did not work, and it declines to say more than that.
      setStatus('invalid-token')
    } catch {
      // The request never reached a decision. Offer the resend rather than
      // telling somebody their link is bad when we do not know.
      setNotice('We could not reach the server. Please try again.')
    }
  }

  async function resend(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const error = validateEmail(email)
    setEmailError(error)
    if (error) return

    setNotice(undefined)
    setStatus('resending')
    try {
      const result = await resendActivationEmailRequest(email)
      // The backend's own message, verbatim: it is identical whether or not
      // the address needs confirming, and this view must not narrow it. A
      // refusal is shown the same way, because the one refusal there is (the
      // address is not an address at all) is reported identically for a
      // registered and an unregistered one.
      setNotice(result.message)
      if (result.success) {
        setStatus('resent')
      } else {
        setEmailError(result.field === 'email' ? result.message : undefined)
        if (result.field !== 'email') {
          setNotice(result.message)
        }
        setStatus('invalid-token')
      }
    } catch {
      setNotice('We could not reach the server. Please try again.')
      setStatus('invalid-token')
    }
  }

  if (status === 'confirmed') {
    return (
      <div>
        <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Email confirmed</h1>
        <p className="mt-2 text-sm text-gray-500">Your email address has been confirmed.</p>
        <p className="mt-2 text-sm text-gray-500">
          You were not signed in by this — confirming an address is not a login. Sign in as usual.
        </p>
        <Link to="/auth" className={`mt-6 ${PRIMARY_BUTTON_CLASSES}`}>
          Sign In
        </Link>
      </div>
    )
  }

  // One branch for "the link did not work, so ask for another": the resend
  // form is reachable from the dead-link state and stays on screen while it is
  // in flight, which is also what keeps `status === 'resending'` meaningful
  // here rather than a state the type checker can prove is impossible.
  const showResend = status === 'invalid-token' || status === 'resending' || status === 'resent'

  if (showResend) {
    return (
      <div>
        <h1 className="text-[26px] font-bold tracking-tight text-gray-900">
          {status === 'resent' ? 'Check your email' : 'Link invalid'}
        </h1>
        <p className="mt-2 text-sm text-gray-500">
          {status === 'resent'
            ? notice
            : 'This confirmation link is invalid or has expired. It can only be used once.'}
        </p>

        {status === 'invalid-token' && (
          <p className="mt-4 text-sm text-gray-500">
            You can sign in without confirming — confirming only records that you own this inbox.
          </p>
        )}

        <form noValidate onSubmit={resend} className="mt-6 space-y-4">
          <TextField
            id="resend-activation-email"
            label="Email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={setEmail}
            error={emailError}
            placeholder="you@company.com"
            disabled={status === 'resending'}
          />
          <button
            type="submit"
            disabled={status === 'resending'}
            className={PRIMARY_BUTTON_CLASSES}
          >
            {status === 'resending' && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
            <span>{status === 'resending' ? 'Sending…' : 'Send a new link'}</span>
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-gray-500">
          <Link
            to="/auth"
            className="font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:underline"
          >
            ← Back to Sign In
          </Link>
        </p>
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Confirm your email</h1>
      <p className="mt-2 text-sm text-gray-500">
        One click to confirm this address for your account.
      </p>

      {notice && (
        <p role="alert" className="mt-5 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {notice}
        </p>
      )}

      <button
        type="button"
        onClick={confirm}
        disabled={status === 'confirming'}
        className={`mt-7 ${PRIMARY_BUTTON_CLASSES}`}
      >
        {status === 'confirming' && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
        <span>{status === 'confirming' ? 'Confirming…' : 'Confirm my email'}</span>
      </button>

      <p className="mt-6 text-center text-sm text-gray-500">
        <Link
          to="/auth"
          className="font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:underline"
        >
          ← Back to Sign In
        </Link>
      </p>
    </div>
  )
}
