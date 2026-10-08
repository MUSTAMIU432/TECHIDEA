/**
 * Shared types for the combined sign in / sign up experience at `/auth`.
 * These describe UI form state only — the GraphQL `login`/`register`
 * mutation contracts will be defined in a later Identity task.
 */

export type AuthMode = 'sign-in' | 'sign-up'

/**
 * The full set of views AuthPage can show in its one card. Forgot-password
 * isn't a tab — it's a third view that temporarily replaces the tabs/form
 * area, the same way Sign In and Sign Up already swap in place.
 */
export type AuthView = AuthMode | 'forgot-password'

export interface SignInFormValues {
  email: string
  password: string
}

export interface SignUpFormValues {
  firstName: string
  lastName: string
  email: string
  countryCode: string
  phoneNumber: string
  password: string
  confirmPassword: string
  acceptedTerms: boolean
}

export interface ForgotPasswordFormValues {
  email: string
}

export interface ResetPasswordFormValues {
  password: string
  confirmPassword: string
}
