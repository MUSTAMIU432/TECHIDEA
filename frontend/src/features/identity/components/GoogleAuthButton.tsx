import { GoogleIcon } from './icons'

interface GoogleAuthButtonProps {
  label?: string
  disabled?: boolean
  onClick?: () => void
}

/**
 * Custom-styled "Continue with Google" trigger. It has no OAuth logic of
 * its own - a parent wires `onClick` to `useGoogleSignIn`'s `trigger()`
 * (see `SignInForm`/`SignUpForm`), which forwards the click to Google
 * Identity Services' own hidden button. `disabled` covers both form
 * submission in progress and Google sign-in not being configured
 * (`useGoogleSignIn`'s `isConfigured`).
 */
export function GoogleAuthButton({
  label = 'Continue with Google',
  disabled,
  onClick,
}: GoogleAuthButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="flex h-11 w-full items-center justify-center gap-3 rounded-lg border border-gray-300 bg-white px-4 text-sm font-semibold text-gray-700 shadow-sm motion-safe:transition-colors motion-safe:duration-150 hover:border-gray-400 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-200 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:border-gray-300 disabled:hover:bg-white"
    >
      <GoogleIcon className="h-5 w-5" />
      <span>{label}</span>
    </button>
  )
}
