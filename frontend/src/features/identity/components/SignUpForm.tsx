import { useCallback, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../auth/AuthContext'
import { NETWORK_ERROR_MESSAGE, registerRequest } from '../auth/authApi'
import { useGoogleSignIn } from '../auth/useGoogleSignIn'
import { validateSignUp, hasErrors, type FieldErrors } from '../schemas/authValidation'
import type { SignUpFormValues } from '../types/auth'
import { GoogleAuthButton } from './GoogleAuthButton'
import { fieldErrorClasses } from './fieldStyles'
import { SpinnerIcon } from './icons'
import { PasswordField } from './PasswordField'
import { PhoneField } from './PhoneField'
import { TextField } from './TextField'

interface SignUpFormProps {
  onSwitchToSignIn: () => void
  returnTo?: string
}

const INITIAL_VALUES: SignUpFormValues = {
  firstName: '',
  lastName: '',
  email: '',
  countryCode: '+255',
  phoneNumber: '',
  password: '',
  confirmPassword: '',
  acceptedTerms: false,
}

const LEGAL_LINK_CLASSES =
  'font-medium text-brand-700 underline decoration-brand-300 underline-offset-2 hover:text-brand-800'

/**
 * The `RegisterInput` fields a backend validation error can be attached to.
 * The schema names them in camelCase (Strawberry's own conversion of the
 * Python field names), which is exactly how this form's state is keyed, so
 * a backend message drops straight into the matching field error.
 *
 * Restricted to this list rather than trusted blindly: `field` is a string
 * from the server, and writing it into the error map unchecked would let a
 * value outside the form's own fields become state.
 */
const REGISTRABLE_FIELDS = [
  'firstName',
  'lastName',
  'email',
  'phoneNumber',
  'password',
] as const satisfies readonly (keyof SignUpFormValues)[]

type RegistrableField = (typeof REGISTRABLE_FIELDS)[number]

function isRegistrableField(field: string | null): field is RegistrableField {
  return field !== null && (REGISTRABLE_FIELDS as readonly string[]).includes(field)
}

/**
 * Sign up form. Deliberately minimal: it creates the account only. Profile
 * details (photo, country/city, bio, job title, organization, skills, etc.)
 * are completed later, from the dashboard, after authentication.
 */
export function SignUpForm({ onSwitchToSignIn, returnTo = '/app' }: SignUpFormProps) {
  const navigate = useNavigate()
  const { loginWithGoogle } = useAuth()
  const [values, setValues] = useState<SignUpFormValues>(INITIAL_VALUES)
  const [errors, setErrors] = useState<FieldErrors<SignUpFormValues>>({})
  const [attempted, setAttempted] = useState(false)
  const [status, setStatus] = useState<'idle' | 'submitting'>('idle')
  const [googleError, setGoogleError] = useState<string | null>(null)
  // A failure the backend did not attach to any one field (a whole-form
  // problem, or a transport failure), shown once above the form rather than
  // next to an arbitrary field.
  const [formError, setFormError] = useState<string | null>(null)
  // The account exists. Registration does not authenticate - the backend's
  // `register` mutation creates the User record only - so this is a
  // confirmation that leads to sign-in, not a redirect into the app.
  const [created, setCreated] = useState(false)
  const isSubmitting = status === 'submitting'

  // Google sign-in doesn't distinguish "sign up" from "sign in" - the
  // googleLogin mutation creates the account on first use and
  // authenticates it on every return visit - so this button performs
  // exactly the same operation as the one on SignInForm.
  const handleGoogleCredential = useCallback(
    async (credential: string) => {
      setGoogleError(null)
      setStatus('submitting')
      const result = await loginWithGoogle(credential)

      if (result.success) {
        navigate(returnTo, { replace: true })
        return
      }

      setGoogleError(result.message)
      setStatus('idle')
    },
    [loginWithGoogle, navigate, returnTo],
  )
  const googleSignIn = useGoogleSignIn(handleGoogleCredential)

  function updateField<K extends keyof SignUpFormValues>(field: K, value: SignUpFormValues[K]) {
    const next = { ...values, [field]: value }
    setValues(next)
    if (attempted) {
      setErrors(validateSignUp(next))
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)

    const nextErrors = validateSignUp(values)
    setErrors(nextErrors)
    if (hasErrors(nextErrors)) return

    setFormError(null)
    setStatus('submitting')
    try {
      const result = await registerRequest({
        firstName: values.firstName.trim(),
        lastName: values.lastName.trim(),
        email: values.email.trim(),
        // The backend stores one E.164-style string; the form collects the
        // country code separately only to make the picker convenient.
        phoneNumber: `${values.countryCode}${values.phoneNumber.replace(/\D/g, '')}`,
        password: values.password,
      })

      if (result.success) {
        setCreated(true)
        return
      }

      // A field-level failure (a duplicate email, a password the backend's
      // own validator rejects, a malformed phone number) replaces that
      // field's error so it is shown in the same place as a client-side one.
      if (isRegistrableField(result.field)) {
        setErrors({ [result.field]: result.message })
      } else {
        setFormError(result.message)
      }
    } catch {
      // The mutation never ran, or its response never arrived. There is
      // nothing to attach to a field, and guessing one would be a lie the
      // user could act on. The message is shared with the sign-in forms so
      // "the server is unreachable" reads the same everywhere - and is
      // deliberately not one of the backend's generic authentication
      // messages, which report a decision this request never reached.
      setFormError(NETWORK_ERROR_MESSAGE)
    } finally {
      setStatus('idle')
    }
  }

  if (created) {
    return (
      <div>
        <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Account created</h1>
        <output className="mt-2 block text-sm text-gray-500">
          Your account is ready. Sign in to continue.
        </output>
        {/* Registration emails a confirmation link. Worth saying out loud, and
            worth saying it is optional: the backend's `login` does not check
            `isVerified`, so an unconfirmed account can sign in and work
            normally. Without that sentence this reads as "you must confirm
            before you can continue", which is not true and would strand
            anyone who lost the first message. */}
        <p className="mt-3 text-sm text-gray-500">
          We&apos;ve emailed you a link to confirm your address. You can sign in now and confirm
          later — it isn&apos;t required.
        </p>
        <button
          type="button"
          onClick={onSwitchToSignIn}
          className="mt-7 flex h-11 w-full items-center justify-center rounded-lg bg-brand-600 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2"
        >
          <span>Continue to sign in</span>
        </button>
      </div>
    )
  }

  return (
    <div>
      <h1 className="text-[26px] font-bold tracking-tight text-gray-900">Create your account</h1>
      <p className="mt-2 text-sm text-gray-500">Start turning problems into automation.</p>

      <div className="mt-7">
        <GoogleAuthButton
          disabled={isSubmitting || !googleSignIn.isConfigured}
          onClick={googleSignIn.trigger}
        />
        {/* Google Identity Services renders its own real button here,
            visually hidden - see SignInForm's matching comment. */}
        <div id={googleSignIn.hiddenButtonContainerId} className="sr-only" />
        {googleError && (
          <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
            {googleError}
          </p>
        )}
      </div>

      <div className="my-6 flex items-center gap-3">
        <div className="h-px flex-1 bg-gray-200" />
        <span className="text-xs font-medium uppercase tracking-wide text-gray-400">Or</span>
        <div className="h-px flex-1 bg-gray-200" />
      </div>

      <form noValidate onSubmit={handleSubmit} aria-busy={isSubmitting} className="space-y-5">
        {formError && (
          <p role="alert" className="rounded-lg bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
            {formError}
          </p>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            id="signup-first-name"
            label="First name"
            autoComplete="given-name"
            value={values.firstName}
            onChange={(value) => updateField('firstName', value)}
            error={errors.firstName}
            disabled={isSubmitting}
          />
          <TextField
            id="signup-last-name"
            label="Last name"
            autoComplete="family-name"
            value={values.lastName}
            onChange={(value) => updateField('lastName', value)}
            error={errors.lastName}
            disabled={isSubmitting}
          />
        </div>

        <TextField
          id="signup-email"
          label="Email"
          type="email"
          autoComplete="email"
          value={values.email}
          onChange={(value) => updateField('email', value)}
          error={errors.email}
          placeholder="you@company.com"
          disabled={isSubmitting}
        />

        <PhoneField
          countryCode={values.countryCode}
          phoneNumber={values.phoneNumber}
          onCountryCodeChange={(value) => updateField('countryCode', value)}
          onPhoneNumberChange={(value) => updateField('phoneNumber', value)}
          error={errors.phoneNumber}
          disabled={isSubmitting}
        />

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <PasswordField
            id="signup-password"
            label="Password"
            autoComplete="new-password"
            value={values.password}
            onChange={(value) => updateField('password', value)}
            error={errors.password}
            disabled={isSubmitting}
          />

          <PasswordField
            id="signup-confirm-password"
            label="Confirm password"
            autoComplete="new-password"
            value={values.confirmPassword}
            onChange={(value) => updateField('confirmPassword', value)}
            error={errors.confirmPassword}
            disabled={isSubmitting}
          />
        </div>

        <div>
          <div className="flex items-start gap-2.5">
            <input
              id="signup-terms"
              name="signup-terms"
              type="checkbox"
              checked={values.acceptedTerms}
              onChange={(event) => updateField('acceptedTerms', event.target.checked)}
              disabled={isSubmitting}
              aria-invalid={Boolean(errors.acceptedTerms)}
              aria-describedby={errors.acceptedTerms ? 'signup-terms-error' : undefined}
              className="mt-0.5 h-4 w-4 shrink-0 rounded border-gray-300 text-brand-600 focus:ring-2 focus:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
            />
            <label htmlFor="signup-terms" className="text-sm text-gray-600">
              I agree to the{' '}
              <Link to="/legal/terms" className={LEGAL_LINK_CLASSES}>
                Terms of Service
              </Link>{' '}
              and{' '}
              <Link to="/legal/privacy" className={LEGAL_LINK_CLASSES}>
                Privacy Policy
              </Link>
              .
            </label>
          </div>
          {errors.acceptedTerms && (
            <p id="signup-terms-error" role="alert" className={fieldErrorClasses}>
              {errors.acceptedTerms}
            </p>
          )}
        </div>

        <button
          type="submit"
          disabled={isSubmitting}
          className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-brand-600 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-brand-300 disabled:shadow-none"
        >
          {isSubmitting && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
          <span>{isSubmitting ? 'Creating account…' : 'Create Account'}</span>
        </button>
      </form>

      <p className="mt-6 text-center text-sm text-gray-500">
        Already have an account?{' '}
        <button
          type="button"
          onClick={onSwitchToSignIn}
          className="-my-3 inline-block py-3 font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:underline"
        >
          Sign in
        </button>
      </p>
    </div>
  )
}
