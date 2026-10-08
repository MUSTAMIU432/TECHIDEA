import type {
  ForgotPasswordFormValues,
  ResetPasswordFormValues,
  SignInFormValues,
  SignUpFormValues,
} from '../types/auth'

/**
 * Manual, dependency-free validation for the auth forms. The project has no
 * form/schema library installed yet (no React Hook Form or Zod); introducing
 * one for two forms isn't justified, so validation is plain functions that a
 * future GraphQL-backed implementation can reuse or replace.
 */

export type FieldErrors<T> = Partial<Record<keyof T, string>>

export const MIN_PASSWORD_LENGTH = 8

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// Accepts the digits of a national number once the country code is
// separated out. 6-14 digits comfortably covers real-world numbering plans
// (E.164 allows up to 15 digits including the country code) without being
// tied to one country's format.
const PHONE_DIGITS_PATTERN = /^\d{6,14}$/

export function validateEmail(email: string): string | undefined {
  const trimmed = email.trim()
  if (!trimmed) return 'Email is required.'
  if (!EMAIL_PATTERN.test(trimmed)) return 'Enter a valid email address.'
  return undefined
}

export function validatePhoneNumber(phoneNumber: string): string | undefined {
  const digits = phoneNumber.replace(/\D/g, '')
  if (!digits) return 'Phone number is required.'
  if (!PHONE_DIGITS_PATTERN.test(digits)) return 'Enter a valid phone number.'
  return undefined
}

export function validateSignIn(values: SignInFormValues): FieldErrors<SignInFormValues> {
  const errors: FieldErrors<SignInFormValues> = {}

  const emailError = validateEmail(values.email)
  if (emailError) errors.email = emailError

  if (!values.password) errors.password = 'Password is required.'

  return errors
}

export function validateSignUp(values: SignUpFormValues): FieldErrors<SignUpFormValues> {
  const errors: FieldErrors<SignUpFormValues> = {}

  if (!values.firstName.trim()) errors.firstName = 'First name is required.'
  if (!values.lastName.trim()) errors.lastName = 'Last name is required.'

  const emailError = validateEmail(values.email)
  if (emailError) errors.email = emailError

  const phoneError = validatePhoneNumber(values.phoneNumber)
  if (phoneError) errors.phoneNumber = phoneError

  if (!values.password) {
    errors.password = 'Password is required.'
  } else if (values.password.length < MIN_PASSWORD_LENGTH) {
    errors.password = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`
  }

  if (!values.confirmPassword) {
    errors.confirmPassword = 'Please confirm your password.'
  } else if (values.confirmPassword !== values.password) {
    errors.confirmPassword = 'Passwords do not match.'
  }

  if (!values.acceptedTerms) {
    errors.acceptedTerms = 'You must accept the Terms and Privacy Policy.'
  }

  return errors
}

export function validateForgotPassword(
  values: ForgotPasswordFormValues,
): FieldErrors<ForgotPasswordFormValues> {
  const errors: FieldErrors<ForgotPasswordFormValues> = {}

  const emailError = validateEmail(values.email)
  if (emailError) errors.email = emailError

  return errors
}

export function validateResetPassword(
  values: ResetPasswordFormValues,
): FieldErrors<ResetPasswordFormValues> {
  const errors: FieldErrors<ResetPasswordFormValues> = {}

  if (!values.password) {
    errors.password = 'Password is required.'
  } else if (values.password.length < MIN_PASSWORD_LENGTH) {
    errors.password = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`
  }

  if (!values.confirmPassword) {
    errors.confirmPassword = 'Please confirm your password.'
  } else if (values.confirmPassword !== values.password) {
    errors.confirmPassword = 'Passwords do not match.'
  }

  return errors
}

export function hasErrors(errors: Record<string, string | undefined>): boolean {
  return Object.values(errors).some(Boolean)
}
