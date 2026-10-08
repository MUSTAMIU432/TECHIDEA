import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { NETWORK_ERROR_MESSAGE, resetPasswordRequest } from '../auth/authApi'
import { validateResetPassword, hasErrors, type FieldErrors } from '../schemas/authValidation'
import type { ResetPasswordFormValues } from '../types/auth'
import { PasswordField } from './PasswordField'
import { SpinnerIcon } from './icons'

const INITIAL_VALUES: ResetPasswordFormValues = {
  password: '',
  confirmPassword: '',
}

/**
 * `invalid-token` and `error` are separate states on purpose: the first is
 * the backend's decision about the link (re-asking for the form would be
 * pointless), the second is a request that never got that far. Rendering them
 * with the same view would either tell somebody their link is broken when the
 * server was merely down, or hide a dead link behind "try again".
 */
type Status = 'idle' | 'submitting' | 'success' | 'invalid-token' | 'error'

const PRIMARY_BUTTON_CLASSES =
  'flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-brand-600 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-brand-300 disabled:shadow-none'

/**
 * Shown once the real `resetPassword` mutation resolves successfully.
 *
 * Unlike the forgot-password flow, this is a definite claim about a
 * security-sensitive action, and it is one the backend backs: the token was
 * valid and unspent. It also says what else happened, because that is the
 * part people are surprised by - the reset ended every session on the
 * account, this browser's included, so signing in again is required rather
 * than optional.
 */
export function ResetPasswordSuccessView() {
  return (
    <div>
      <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Password reset</h1>
      <p className="mt-2 text-sm text-gray-500">Your password has been reset successfully.</p>
      <p className="mt-2 text-sm text-gray-500">
        For safety, every session on this account was signed out, including this one. Sign in again
        with your new password.
      </p>
      <Link to="/auth" className={`mt-6 ${PRIMARY_BUTTON_CLASSES}`}>
        Sign In
      </Link>
    </div>
  )
}

/**
 * Shown when there is no usable link: either the URL carries no `token` at
 * all, or the backend refused the one it did.
 *
 * The backend answers every unusable token with one message — never real,
 * expired, already used, or belonging to the other kind of link — so this
 * view deliberately does not say which. "Ask for a new link" is the right
 * next step in all four cases, and distinguishing them here would hand
 * somebody holding a stolen link a free read on how far it got.
 */
export function ResetPasswordInvalidTokenView() {
  return (
    <div>
      <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Link invalid</h1>
      <p className="mt-2 text-sm text-gray-500">
        This password reset link is invalid or has expired.
      </p>
      <Link to="/auth" className={`mt-6 ${PRIMARY_BUTTON_CLASSES}`}>
        Request a new reset link
      </Link>
    </div>
  )
}

/**
 * The /reset-password form: the page a reset link lands on.
 *
 * The token is read from the URL and handed to the backend to redeem, which
 * is the only place its validity can be decided — nothing here tries to
 * inspect it. Two outcomes are kept apart on purpose:
 *
 * - the backend refuses the token (`field === 'token'`): the link is no good
 *   for any reason it declines to say, and re-showing the form would be
 *   pointless, so this shows the invalid-link view;
 * - the backend refuses the *password* (`field === 'password'`): the link is
 *   fine and still live, so the message goes next to the input and the user
 *   tries again with the same link.
 */
export function ResetPasswordForm() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')

  const [values, setValues] = useState<ResetPasswordFormValues>(INITIAL_VALUES)
  const [errors, setErrors] = useState<FieldErrors<ResetPasswordFormValues>>({})
  const [attempted, setAttempted] = useState(false)
  const [status, setStatus] = useState<Status>('idle')
  const [formError, setFormError] = useState<string | undefined>()
  const isSubmitting = status === 'submitting'

  function updateField<K extends keyof ResetPasswordFormValues>(
    field: K,
    value: ResetPasswordFormValues[K],
  ) {
    const next = { ...values, [field]: value }
    setValues(next)
    if (attempted) {
      setErrors(validateResetPassword(next))
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)
    setFormError(undefined)

    const nextErrors = validateResetPassword(values)
    setErrors(nextErrors)
    if (hasErrors(nextErrors)) return

    setStatus('submitting')
    try {
      const result = await resetPasswordRequest(token as string, values.password)

      if (result.success) {
        setStatus('success')
        return
      }

      if (result.field === 'token') {
        // The link itself is no good; showing the form again would be a lie.
        setStatus('invalid-token')
        return
      }

      if (result.field === 'password') {
        // The link is still live — the user just has to choose a better
        // password, so the message belongs next to the input.
        setErrors({ password: result.message })
        setStatus('idle')
        return
      }

      // A throttle, or anything else the backend declined to attribute to a
      // field: a whole-form problem, so it goes at the top.
      setFormError(result.message)
      setStatus('idle')
    } catch {
      // The request never reached a decision about the link.
      setFormError(NETWORK_ERROR_MESSAGE)
      setStatus('idle')
    }
  }

  if (!token) {
    return <ResetPasswordInvalidTokenView />
  }

  if (status === 'success') {
    return <ResetPasswordSuccessView />
  }

  if (status === 'invalid-token') {
    return <ResetPasswordInvalidTokenView />
  }

  return (
    <div>
      <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Reset your password</h1>
      <p className="mt-2 text-sm text-gray-500">Choose a new password for your account.</p>

      {formError && (
        <p role="alert" className="mt-5 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {formError}
        </p>
      )}

      <form noValidate onSubmit={handleSubmit} aria-busy={isSubmitting} className="mt-7 space-y-5">
        <PasswordField
          id="reset-password-new"
          label="New password"
          autoComplete="new-password"
          value={values.password}
          onChange={(value) => updateField('password', value)}
          error={errors.password}
          disabled={isSubmitting}
        />

        <PasswordField
          id="reset-password-confirm"
          label="Confirm new password"
          autoComplete="new-password"
          value={values.confirmPassword}
          onChange={(value) => updateField('confirmPassword', value)}
          error={errors.confirmPassword}
          disabled={isSubmitting}
        />

        <button type="submit" disabled={isSubmitting} className={PRIMARY_BUTTON_CLASSES}>
          {isSubmitting && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
          <span>{isSubmitting ? 'Resetting…' : 'Reset Password'}</span>
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
