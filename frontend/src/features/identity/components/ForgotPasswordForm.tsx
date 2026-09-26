import { useState, type FormEvent } from 'react'

import { NETWORK_ERROR_MESSAGE, requestPasswordResetRequest } from '../auth/authApi'
import { validateEmail } from '../schemas/authValidation'
import { SpinnerIcon } from './icons'
import { TextField } from './TextField'

interface ForgotPasswordFormProps {
  onBackToSignIn: () => void
}

type Status = 'idle' | 'submitting' | 'success'

const PRIMARY_BUTTON_CLASSES =
  'flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-brand-600 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-brand-300 disabled:shadow-none'

/**
 * "Forgot password?" entry point's view. Renders in place of the Sign
 * In/Sign Up tabs and form — the same in-card swap pattern AuthTabs already
 * uses — rather than a modal, so the whole auth system reads as one
 * continuous form instead of a popup interrupting it.
 *
 * What this view does *not* do is claim anything about the address. The
 * backend answers this mutation identically whether the address has an
 * account, is deactivated, signed up through Google, or does not exist at
 * all, so that the endpoint cannot be used to find out whether somebody is
 * registered here. The success copy is therefore the backend's own message,
 * shown verbatim, and this component must never add a "we found your
 * account" flourish or a different tone that would give the game away.
 */
export function ForgotPasswordForm({ onBackToSignIn }: ForgotPasswordFormProps) {
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | undefined>()
  const [attempted, setAttempted] = useState(false)
  const [status, setStatus] = useState<Status>('idle')
  const [notice, setNotice] = useState<string | undefined>()
  const isSubmitting = status === 'submitting'

  function handleEmailChange(value: string) {
    setEmail(value)
    if (attempted) {
      setError(validateEmail(value))
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)
    setNotice(undefined)

    const emailError = validateEmail(email)
    setError(emailError)
    if (emailError) return

    setStatus('submitting')
    try {
      const result = await requestPasswordResetRequest(email)
      if (result.success) {
        // The backend's wording, not ours: it is identical whether or not
        // anything was sent, and this component must not narrow it.
        setNotice(result.message)
        setStatus('success')
        return
      }
      if (result.field === 'email') {
        // The one failure genuinely about this input — and the backend reports
        // it identically for a registered and an unregistered address, so
        // showing it narrows nothing.
        setError(result.message)
        setStatus('idle')
        return
      }
      // Anything else — a throttle, most of all — did *not* send a message, so
      // the confirmation view would be a lie. Reported as a form-level error
      // instead, which is also why the notice is cleared on the next attempt.
      setNotice(result.message)
      setStatus('idle')
    } catch {
      // A transport failure is a different fact from a refusal: the request
      // never reached a decision about the account, so it is reported as its
      // own error rather than as the generic "check your email" - which would
      // tell somebody to wait for a message that was never sent.
      setNotice(NETWORK_ERROR_MESSAGE)
      setStatus('idle')
    }
  }

  if (status === 'success') {
    return (
      <div>
        <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Check your email</h1>
        <p className="mt-2 text-sm text-gray-500">{notice}</p>
        <p className="mt-2 text-sm text-gray-500">
          The link can only be used once, and it stops working after a short while.
        </p>
        <button type="button" onClick={onBackToSignIn} className={`mt-7 ${PRIMARY_BUTTON_CLASSES}`}>
          Back to Sign In
        </button>
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Reset your password</h1>
      <p className="mt-2 text-sm text-gray-500">
        Enter your email and we&apos;ll send you a secure password reset link.
      </p>

      {notice && (
        <p role="alert" className="mt-5 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {notice}
        </p>
      )}

      <form noValidate onSubmit={handleSubmit} aria-busy={isSubmitting} className="mt-7 space-y-5">
        <TextField
          id="forgot-password-email"
          label="Email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={handleEmailChange}
          error={error}
          placeholder="you@company.com"
          disabled={isSubmitting}
        />

        <button type="submit" disabled={isSubmitting} className={PRIMARY_BUTTON_CLASSES}>
          {isSubmitting && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
          <span>{isSubmitting ? 'Sending…' : 'Send reset link'}</span>
        </button>
      </form>

      <p className="mt-6 text-center text-sm text-gray-500">
        <button
          type="button"
          onClick={onBackToSignIn}
          className="inline-flex items-center gap-1.5 font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:underline"
        >
          <span aria-hidden="true">←</span> Back to Sign In
        </button>
      </p>
    </div>
  )
}
