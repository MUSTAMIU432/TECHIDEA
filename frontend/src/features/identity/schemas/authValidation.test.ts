import { describe, expect, it } from 'vitest'

import { hasErrors, validateSignIn, validateSignUp } from './authValidation'
import type { SignInFormValues, SignUpFormValues } from '../types/auth'

const validSignUp: SignUpFormValues = {
  firstName: 'Ada',
  lastName: 'Lovelace',
  email: 'ada@example.com',
  countryCode: '+255',
  phoneNumber: '712345678',
  password: 'correct-horse',
  confirmPassword: 'correct-horse',
  acceptedTerms: true,
}

describe('validateSignIn', () => {
  it('requires an email and password', () => {
    const errors = validateSignIn({ email: '', password: '' })

    expect(errors.email).toBe('Email is required.')
    expect(errors.password).toBe('Password is required.')
  })

  it('rejects a malformed email', () => {
    const values: SignInFormValues = {
      email: 'not-an-email',
      password: 'secret123',
    }

    expect(validateSignIn(values).email).toBe('Enter a valid email address.')
  })

  it('passes for valid values', () => {
    const errors = validateSignIn({
      email: 'ada@example.com',
      password: 'secret123',
    })

    expect(hasErrors(errors)).toBe(false)
  })
})

describe('validateSignUp', () => {
  it('passes for fully valid values', () => {
    expect(hasErrors(validateSignUp(validSignUp))).toBe(false)
  })

  it('requires first name, last name and terms acceptance', () => {
    const errors = validateSignUp({
      ...validSignUp,
      firstName: '',
      lastName: '',
      acceptedTerms: false,
    })

    expect(errors.firstName).toBe('First name is required.')
    expect(errors.lastName).toBe('Last name is required.')
    expect(errors.acceptedTerms).toBe('You must accept the Terms and Privacy Policy.')
  })

  it('rejects a phone number that is too short', () => {
    const errors = validateSignUp({ ...validSignUp, phoneNumber: '123' })

    expect(errors.phoneNumber).toBe('Enter a valid phone number.')
  })

  it('rejects a password below the minimum length', () => {
    const errors = validateSignUp({
      ...validSignUp,
      password: 'short',
      confirmPassword: 'short',
    })

    expect(errors.password).toBe('Password must be at least 8 characters.')
  })

  it('detects a confirm-password mismatch', () => {
    const errors = validateSignUp({
      ...validSignUp,
      confirmPassword: 'different-password',
    })

    expect(errors.confirmPassword).toBe('Passwords do not match.')
  })
})
