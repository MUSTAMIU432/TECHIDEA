import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { requestPasswordResetRequest, type ActionResult } from '../auth/authApi'
import { ForgotPasswordForm } from './ForgotPasswordForm'

vi.mock('../auth/authApi', async (importOriginal) => ({
  ...(await importOriginal()),
  requestPasswordResetRequest: vi.fn(),
}))

const requestMock = vi.mocked(requestPasswordResetRequest)

/**
 * The backend's own generic message. Pinned as a literal here on purpose:
 * this component must display whatever the backend says rather than its own
 * copy, because that message is identical for an address with an account and
 * one without. A local string could drift into saying something narrower
 * ("we found your account") and quietly turn this page into an existence
 * oracle — which is exactly the property the backend works to preserve.
 */
const GENERIC_MESSAGE =
  "If an account exists for that email, we've sent instructions to reset your password."

function renderForm(onBackToSignIn = () => {}) {
  return render(<ForgotPasswordForm onBackToSignIn={onBackToSignIn} />)
}

function submit(email: string) {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: email } })
  fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))
}

describe('ForgotPasswordForm', () => {
  afterEach(() => {
    requestMock.mockReset()
  })

  it('renders the email field and submit button', () => {
    renderForm()

    expect(
      screen.getByRole('heading', { level: 1, name: 'Reset your password' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send reset link' })).toBeInTheDocument()
  })

  it('requires an email', () => {
    renderForm()

    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))

    expect(screen.getByText('Email is required.')).toBeInTheDocument()
    expect(requestMock).not.toHaveBeenCalled()
  })

  it('validates the email format', () => {
    renderForm()

    submit('not-an-email')

    expect(screen.getByText('Enter a valid email address.')).toBeInTheDocument()
    expect(requestMock).not.toHaveBeenCalled()
  })

  it('asks the backend to send a link for the submitted address', async () => {
    requestMock.mockResolvedValue({
      success: true,
      message: GENERIC_MESSAGE,
      field: null,
      user: null,
    })
    renderForm()

    submit('ada@example.com')

    await waitFor(() => expect(requestMock).toHaveBeenCalledWith('ada@example.com'))
  })

  it('shows a loading state while the request is in flight', async () => {
    let release: (value: ActionResult) => void = () => {}
    requestMock.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    renderForm()

    submit('ada@example.com')

    expect(screen.getByRole('button', { name: 'Sending…' })).toBeDisabled()
    release({ success: true, message: GENERIC_MESSAGE, field: null, user: null })
    await screen.findByRole('heading', { name: 'Check your email' })
  })

  it("shows the backend's generic message verbatim on success", async () => {
    requestMock.mockResolvedValue({
      success: true,
      message: GENERIC_MESSAGE,
      field: null,
      user: null,
    })
    renderForm()

    submit('ada@example.com')

    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    expect(screen.getByText(GENERIC_MESSAGE)).toBeInTheDocument()
  })

  it('shows the same confirmation for an address that has no account', async () => {
    // The two are indistinguishable by construction, and this asserts the UI
    // half of that: the backend's answer for a stranger is the same message
    // and the same view, so the page cannot be used to probe for an account.
    requestMock.mockResolvedValue({
      success: true,
      message: GENERIC_MESSAGE,
      field: null,
      user: null,
    })
    renderForm()

    submit('nobody-at-all@example.com')

    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    expect(screen.getByText(GENERIC_MESSAGE)).toBeInTheDocument()
  })

  it('mentions that the link is single-use and short-lived', async () => {
    requestMock.mockResolvedValue({
      success: true,
      message: GENERIC_MESSAGE,
      field: null,
      user: null,
    })
    renderForm()

    submit('ada@example.com')

    // Worth stating: it is the difference between a user who waits and a user
    // who clicks the same link three times wondering why it stopped working.
    expect(await screen.findByText(/can only be used once/i)).toBeInTheDocument()
  })

  it('shows a backend refusal about the address next to the field', async () => {
    requestMock.mockResolvedValue({
      success: false,
      message: 'Enter a valid email address.',
      field: 'email',
      user: null,
    })
    renderForm()

    submit('ada@example.com')

    // `field: 'email'` is the one refusal that is genuinely about this input,
    // and the backend reports it identically for a registered and an
    // unregistered address, so showing it narrows nothing.
    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Check your email' })).not.toBeInTheDocument()
  })

  it('shows a throttle as a form-level error, not as a sent link', async () => {
    requestMock.mockResolvedValue({
      success: false,
      message: 'Too many attempts. Please try again later.',
      field: null,
      user: null,
    })
    renderForm()

    submit('ada@example.com')

    // A throttle has no field, and it certainly did not send an email, so
    // the confirmation view would be a lie here.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Too many attempts. Please try again later.')
    expect(screen.queryByRole('heading', { name: 'Check your email' })).not.toBeInTheDocument()
  })

  it('reports an unreachable server as its own error, not as "check your email"', async () => {
    requestMock.mockRejectedValue(new Error('Failed to fetch'))
    renderForm()

    submit('ada@example.com')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    // The distinction that matters: the request never reached a decision, so
    // telling the user to wait for a message that was never sent would be
    // worse than useless.
    expect(screen.queryByRole('heading', { name: 'Check your email' })).not.toBeInTheDocument()
  })

  it('lets the user try again after a transport failure', async () => {
    requestMock.mockRejectedValueOnce(new Error('Failed to fetch'))
    renderForm()

    submit('ada@example.com')
    await screen.findByRole('alert')

    requestMock.mockResolvedValue({
      success: true,
      message: GENERIC_MESSAGE,
      field: null,
      user: null,
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send reset link' }))

    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
  })

  it('calls onBackToSignIn from the form view', () => {
    const onBackToSignIn = vi.fn()
    renderForm(onBackToSignIn)

    fireEvent.click(screen.getByRole('button', { name: /Back to Sign In/ }))

    expect(onBackToSignIn).toHaveBeenCalledOnce()
  })

  it('calls onBackToSignIn from the success view', async () => {
    const onBackToSignIn = vi.fn()
    requestMock.mockResolvedValue({
      success: true,
      message: GENERIC_MESSAGE,
      field: null,
      user: null,
    })
    renderForm(onBackToSignIn)

    submit('ada@example.com')
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Check your email' })).toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Back to Sign In' }))

    expect(onBackToSignIn).toHaveBeenCalledOnce()
  })
})
