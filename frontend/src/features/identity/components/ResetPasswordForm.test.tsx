import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderRoutes } from '../../../test/renderWithRouter'
import { ResetPasswordForm, ResetPasswordSuccessView } from './ResetPasswordForm'
import { resetPasswordRequest } from '../auth/authApi'

vi.mock('../auth/authApi', async (importOriginal) => ({
  ...(await importOriginal()),
  resetPasswordRequest: vi.fn(),
}))

const resetPasswordRequestMock = vi.mocked(resetPasswordRequest)

function renderWithToken(token: string | null) {
  const path = token ? `/reset-password?token=${token}` : '/reset-password'
  return renderRoutes([{ path: '/reset-password', element: <ResetPasswordForm /> }], path)
}

function fillValidForm() {
  fireEvent.change(screen.getByLabelText('New password'), {
    target: { value: 'correct-horse' },
  })
  fireEvent.change(screen.getByLabelText('Confirm new password'), {
    target: { value: 'correct-horse' },
  })
}

describe('ResetPasswordForm', () => {
  afterEach(() => {
    resetPasswordRequestMock.mockReset()
  })

  it('shows the invalid-link state when no token is present in the URL', () => {
    renderWithToken(null)

    expect(screen.getByRole('heading', { name: 'Link invalid' })).toBeInTheDocument()
    expect(
      screen.getByText('This password reset link is invalid or has expired.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Request a new reset link' })).toHaveAttribute(
      'href',
      '/auth',
    )
  })

  it('renders the reset password fields when a token is present', () => {
    renderWithToken('sample-token')

    expect(screen.getByRole('heading', { name: 'Reset your password' })).toBeInTheDocument()
    expect(screen.getByLabelText('New password')).toBeInTheDocument()
    expect(screen.getByLabelText('Confirm new password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset Password' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Back to Sign In/ })).toHaveAttribute('href', '/auth')
  })

  it('requires both password fields', () => {
    renderWithToken('sample-token')

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    expect(screen.getByText('Password is required.')).toBeInTheDocument()
    expect(screen.getByText('Please confirm your password.')).toBeInTheDocument()
  })

  it('requires a minimum password length', () => {
    renderWithToken('sample-token')

    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'short' } })
    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    expect(screen.getByText('Password must be at least 8 characters.')).toBeInTheDocument()
  })

  it('detects a confirm-password mismatch', () => {
    renderWithToken('sample-token')

    fireEvent.change(screen.getByLabelText('New password'), {
      target: { value: 'correct-horse' },
    })
    fireEvent.change(screen.getByLabelText('Confirm new password'), {
      target: { value: 'different-password' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument()
  })

  it('toggles password visibility independently for both fields', () => {
    renderWithToken('sample-token')

    const newPassword = screen.getByLabelText('New password') as HTMLInputElement
    const confirmPassword = screen.getByLabelText('Confirm new password') as HTMLInputElement

    fireEvent.change(newPassword, { target: { value: 'correct-horse' } })
    expect(newPassword.type).toBe('password')

    fireEvent.click(screen.getByRole('button', { name: 'Show new password' }))

    expect(newPassword.type).toBe('text')
    expect(confirmPassword.type).toBe('password')
  })

  it('does not call the backend when the form is invalid', () => {
    renderWithToken('sample-token')

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    expect(resetPasswordRequestMock).not.toHaveBeenCalled()
  })

  it('redeems the token from the URL with the new password', async () => {
    resetPasswordRequestMock.mockResolvedValue({
      success: true,
      message: 'Your password has been reset.',
      field: null,
      user: null,
    })
    renderWithToken('a-real-token')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    await waitFor(() =>
      expect(resetPasswordRequestMock).toHaveBeenCalledWith('a-real-token', 'correct-horse'),
    )
  })

  it('shows the success state once the reset is accepted', async () => {
    resetPasswordRequestMock.mockResolvedValue({
      success: true,
      message: 'Your password has been reset.',
      field: null,
      user: null,
    })
    renderWithToken('a-real-token')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    expect(await screen.findByRole('heading', { name: 'Password reset' })).toBeInTheDocument()
    expect(screen.getByText('Your password has been reset successfully.')).toBeInTheDocument()
  })

  it('tells the user their other sessions were signed out', async () => {
    resetPasswordRequestMock.mockResolvedValue({
      success: true,
      message: 'Your password has been reset.',
      field: null,
      user: null,
    })
    renderWithToken('a-real-token')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    // The reset revoked every session on the account, so signing in again is
    // required rather than optional. Saying so is the difference between a
    // user understanding what happened and filing a bug.
    expect(
      await screen.findByText(/every session on this account was signed out/i),
    ).toBeInTheDocument()
  })

  it('shows the invalid-link state when the backend refuses the token', async () => {
    resetPasswordRequestMock.mockResolvedValue({
      success: false,
      message: 'This password reset link is invalid or has expired.',
      field: 'token',
      user: null,
    })
    renderWithToken('a-stale-token')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    expect(await screen.findByRole('heading', { name: 'Link invalid' })).toBeInTheDocument()
    // The form is gone: re-asking for a new password with a link the backend
    // has already refused would be pointless.
    expect(screen.queryByLabelText('New password')).not.toBeInTheDocument()
  })

  it('puts a rejected password next to the input and keeps the link usable', async () => {
    resetPasswordRequestMock.mockResolvedValue({
      success: false,
      message: 'This password is too short. It must contain at least 8 characters.',
      field: 'password',
      user: null,
    })
    renderWithToken('a-real-token')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    expect(
      await screen.findByText('This password is too short. It must contain at least 8 characters.'),
    ).toBeInTheDocument()
    // The link was not spent, so the form stays and the user can try again.
    expect(screen.getByLabelText('New password')).toBeInTheDocument()
  })

  it('shows a whole-form error for a failure with no field, such as a throttle', async () => {
    resetPasswordRequestMock.mockResolvedValue({
      success: false,
      message: 'Too many attempts. Please try again later.',
      field: null,
      user: null,
    })
    renderWithToken('a-real-token')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Too many attempts. Please try again later.')
    expect(screen.getByLabelText('New password')).toBeInTheDocument()
  })

  it('reports an unreachable server without claiming the link is bad', async () => {
    resetPasswordRequestMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithToken('a-real-token')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    // Crucially not the invalid-link view: the request never reached a
    // decision about the token, so claiming the link is dead would send the
    // user off to request a new one for no reason.
    expect(screen.queryByRole('heading', { name: 'Link invalid' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('New password')).toBeInTheDocument()
  })

  it('keeps what the user typed when the request fails', async () => {
    resetPasswordRequestMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithToken('a-real-token')
    fillValidForm()

    fireEvent.click(screen.getByRole('button', { name: 'Reset Password' }))

    await screen.findByRole('alert')
    expect(screen.getByLabelText('New password')).toHaveValue('correct-horse')
  })
})

describe('ResetPasswordSuccessView', () => {
  it('renders the success copy and a link back to sign in', () => {
    renderRoutes(
      [{ path: '/reset-password', element: <ResetPasswordSuccessView /> }],
      '/reset-password',
    )

    expect(screen.getByRole('heading', { name: 'Password reset' })).toBeInTheDocument()
    expect(screen.getByText('Your password has been reset successfully.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sign In' })).toHaveAttribute('href', '/auth')
  })
})
