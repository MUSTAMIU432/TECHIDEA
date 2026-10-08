import { useCallback, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { useGoogleSignIn } from '../auth/useGoogleSignIn'
import { validateSignIn, hasErrors, type FieldErrors } from '../schemas/authValidation'
import type { SignInFormValues } from '../types/auth'
import { GoogleAuthButton } from './GoogleAuthButton'
import { PasswordField } from './PasswordField'
import { SpinnerIcon } from './icons'
import { TextField } from './TextField'

interface SignInFormProps {
  onSwitchToSignUp: () => void
  onForgotPassword: () => void
  returnTo?: string
}

const INITIAL_VALUES: SignInFormValues = { email: '', password: '' }

/** Sign in form: email/password, Google button, and a switch into sign up. */
export function SignInForm({
  onSwitchToSignUp,
  onForgotPassword,
  returnTo = '/app',
}: SignInFormProps) {
  const navigate = useNavigate()
  const { login, loginWithGoogle } = useAuth()
  const [values, setValues] = useState<SignInFormValues>(INITIAL_VALUES)
  const [errors, setErrors] = useState<FieldErrors<SignInFormValues>>({})
  const [attempted, setAttempted] = useState(false)
  const [status, setStatus] = useState<'idle' | 'submitting'>('idle')
  const [authError, setAuthError] = useState<string | null>(null)
  const isSubmitting = status === 'submitting'

  const handleGoogleCredential = useCallback(
    async (credential: string) => {
      setAuthError(null)
      setStatus('submitting')
      const result = await loginWithGoogle(credential)

      if (result.success) {
        navigate(returnTo, { replace: true })
        return
      }

      setAuthError(result.message)
      setStatus('idle')
    },
    [loginWithGoogle, navigate, returnTo],
  )
  const googleSignIn = useGoogleSignIn(handleGoogleCredential)

  function updateField<K extends keyof SignInFormValues>(field: K, value: SignInFormValues[K]) {
    const next = { ...values, [field]: value }
    setValues(next)
    if (attempted) {
      setErrors(validateSignIn(next))
    }
    if (authError) {
      setAuthError(null)
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)
    setAuthError(null)

    const nextErrors = validateSignIn(values)
    setErrors(nextErrors)
    if (hasErrors(nextErrors)) return

    setStatus('submitting')
    const result = await login(values.email, values.password)

    if (result.success) {
      navigate(returnTo, { replace: true })
      return
    }

    // The backend deliberately returns one generic message for every
    // failure (unknown email, wrong password, inactive account) - shown
    // as a single form-level error, not attached to a field, since there
    // is nothing field-specific to say without undermining that.
    setAuthError(result.message)
    setStatus('idle')
  }

  return (
    <div>
      <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Welcome back</h1>
      <p className="mt-2 text-sm text-gray-500">Continue to your Automation Platform account.</p>

      <div className="mt-7">
        <GoogleAuthButton
          disabled={isSubmitting || !googleSignIn.isConfigured}
          onClick={googleSignIn.trigger}
        />
        {/* Google Identity Services renders its own real button here,
            visually hidden (not `display: none` - some embedded widgets,
            this one included, behave unreliably when their container has
            no layout box) - GoogleAuthButton's onClick forwards to it. */}
        <div id={googleSignIn.hiddenButtonContainerId} className="sr-only" />
      </div>

      <div className="my-6 flex items-center gap-3">
        <div className="h-px flex-1 bg-gray-200" />
        <span className="text-xs font-medium uppercase tracking-wide text-gray-400">Or</span>
        <div className="h-px flex-1 bg-gray-200" />
      </div>

      <form noValidate onSubmit={handleSubmit} aria-busy={isSubmitting} className="space-y-5">
        <TextField
          id="signin-email"
          label="Email"
          type="email"
          autoComplete="email"
          value={values.email}
          onChange={(value) => updateField('email', value)}
          error={errors.email}
          placeholder="you@company.com"
          disabled={isSubmitting}
        />

        <div>
          <PasswordField
            id="signin-password"
            label="Password"
            autoComplete="current-password"
            value={values.password}
            onChange={(value) => updateField('password', value)}
            error={errors.password}
            disabled={isSubmitting}
          />
          <div className="mt-2 text-right">
            <button
              type="button"
              className="-my-3 py-3 text-sm font-medium text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:underline"
              onClick={onForgotPassword}
            >
              Forgot password?
            </button>
          </div>
        </div>

        {authError && (
          <p role="alert" className="rounded-lg bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
            {authError}
          </p>
        )}

        <button
          type="submit"
          disabled={isSubmitting}
          className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-brand-600 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-brand-300 disabled:shadow-none"
        >
          {isSubmitting && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
          <span>{isSubmitting ? 'Signing in…' : 'Sign In'}</span>
        </button>
      </form>

      <p className="mt-6 text-center text-sm text-gray-500">
        Don&apos;t have an account?{' '}
        <button
          type="button"
          onClick={onSwitchToSignUp}
          className="-my-3 inline-block py-3 font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:underline"
        >
          Create account
        </button>
      </p>
    </div>
  )
}
