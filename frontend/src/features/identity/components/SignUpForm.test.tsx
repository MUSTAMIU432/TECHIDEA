import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { registerRequest, meRequest, refreshTokenRequest } from '../auth/authApi'
import { renderWithProviders } from '../../../test/renderWithRouter'
import { SignUpForm } from './SignUpForm'

import type * as AuthApiModule from '../auth/authApi'
// Only the request functions are stubbed; the module's real constants and
// types are kept. A bare factory object also replaces NETWORK_ERROR_MESSAGE
// with `undefined`, which would make a component set its error to undefined
// and render nothing - invisible to every assertion except one that happens
// to look for the message.
vi.mock('../auth/authApi', async (importOriginal) => ({
  ...(await importOriginal<typeof AuthApiModule>()),
  loginRequest: vi.fn(),
  googleLoginRequest: vi.fn(),
  logoutRequest: vi.fn(),
  registerRequest: vi.fn(),
  refreshTokenRequest: vi.fn(),
  meRequest: vi.fn(),
}))

const mockedRegister = vi.mocked(registerRequest)
const mockedRefresh = vi.mocked(refreshTokenRequest)
const mockedMe = vi.mocked(meRequest)

const TERMS_LABEL = 'I agree to the Terms of Service and Privacy Policy.'

function fillMinimumValidFields() {
  fireEvent.change(screen.getByLabelText('First name'), { target: { value: 'Ada' } })
  fireEvent.change(screen.getByLabelText('Last name'), { target: { value: 'Lovelace' } })
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.com' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Phone number' }), {
    target: { value: '712345678' },
  })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct-horse' } })
  fireEvent.change(screen.getByLabelText('Confirm password'), {
    target: { value: 'correct-horse' },
  })
  fireEvent.click(screen.getByLabelText(TERMS_LABEL))
}

async function renderAndFill() {
  const onSwitchToSignIn = vi.fn()
  renderWithProviders(<SignUpForm onSwitchToSignIn={onSwitchToSignIn} />)
  await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())
  fillMinimumValidFields()
  return { onSwitchToSignIn }
}

function submit() {
  fireEvent.click(screen.getByRole('button', { name: 'Create Account' }))
}

describe('SignUpForm', () => {
  beforeEach(() => {
    mockedRegister.mockReset()
    // No pre-existing session: every test starts unauthenticated, not
    // redirected/pre-authenticated by the mount-time silent refresh.
    mockedRefresh.mockResolvedValue({ success: false, message: 'no session', session: null })
    mockedMe.mockResolvedValue(null)
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // --- client-side validation still comes first -----------------------------

  it('shows required-field errors when submitted empty', async () => {
    renderWithProviders(<SignUpForm onSwitchToSignIn={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    submit()

    expect(screen.getByText('First name is required.')).toBeInTheDocument()
    expect(screen.getByText('Last name is required.')).toBeInTheDocument()
    expect(screen.getByText('Email is required.')).toBeInTheDocument()
    expect(screen.getByText('Phone number is required.')).toBeInTheDocument()
    expect(screen.getByText('Password is required.')).toBeInTheDocument()
    expect(screen.getByText('Please confirm your password.')).toBeInTheDocument()
    expect(screen.getByText('You must accept the Terms and Privacy Policy.')).toBeInTheDocument()
  })

  it('detects a confirm-password mismatch', async () => {
    renderWithProviders(<SignUpForm onSwitchToSignIn={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fillMinimumValidFields()
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'different-password' },
    })
    submit()

    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument()
  })

  it('requires the terms checkbox to be accepted', async () => {
    renderWithProviders(<SignUpForm onSwitchToSignIn={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fillMinimumValidFields()
    fireEvent.click(screen.getByLabelText(TERMS_LABEL))
    submit()

    expect(screen.getByText('You must accept the Terms and Privacy Policy.')).toBeInTheDocument()
  })

  it('does not call the backend when client-side validation fails', async () => {
    renderWithProviders(<SignUpForm onSwitchToSignIn={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    submit()

    expect(mockedRegister).not.toHaveBeenCalled()
  })

  it('links to the Terms of Service and Privacy Policy routes', async () => {
    renderWithProviders(<SignUpForm onSwitchToSignIn={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    expect(screen.getByRole('link', { name: 'Terms of Service' })).toHaveAttribute(
      'href',
      '/legal/terms',
    )
    expect(screen.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute(
      'href',
      '/legal/privacy',
    )
  })

  it('calls onSwitchToSignIn when "Sign in" is clicked', async () => {
    const onSwitchToSignIn = vi.fn()
    renderWithProviders(<SignUpForm onSwitchToSignIn={onSwitchToSignIn} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(onSwitchToSignIn).toHaveBeenCalledOnce()
  })

  // --- the real register mutation -------------------------------------------

  it('sends a real RegisterInput to the register mutation', async () => {
    mockedRegister.mockResolvedValue({ success: true, message: 'ok', field: null })
    await renderAndFill()

    submit()

    await waitFor(() => expect(mockedRegister).toHaveBeenCalledOnce())
    expect(mockedRegister).toHaveBeenCalledWith({
      firstName: 'Ada',
      lastName: 'Lovelace',
      email: 'ada@example.com',
      // The form's country code and national number joined into the single
      // E.164-style string the backend stores.
      phoneNumber: '+255712345678',
      password: 'correct-horse',
    })
  })

  it('trims surrounding whitespace before sending', async () => {
    mockedRegister.mockResolvedValue({ success: true, message: 'ok', field: null })
    renderWithProviders(<SignUpForm onSwitchToSignIn={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('First name'), { target: { value: '  Ada  ' } })
    fireEvent.change(screen.getByLabelText('Last name'), { target: { value: ' Lovelace ' } })
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: ' ada@example.com ' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Phone number' }), {
      target: { value: '712 345 678' },
    })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct-horse' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), {
      target: { value: 'correct-horse' },
    })
    fireEvent.click(screen.getByLabelText(TERMS_LABEL))
    submit()

    await waitFor(() => expect(mockedRegister).toHaveBeenCalledOnce())
    expect(mockedRegister).toHaveBeenCalledWith({
      firstName: 'Ada',
      lastName: 'Lovelace',
      email: 'ada@example.com',
      // Punctuation in the national number is digits-only by the time it is
      // sent; the backend validates the same digits the user typed.
      phoneNumber: '+255712345678',
      password: 'correct-horse',
    })
  })

  it('never sends the confirm-password or terms values, which are not inputs', async () => {
    mockedRegister.mockResolvedValue({ success: true, message: 'ok', field: null })
    await renderAndFill()

    submit()

    await waitFor(() => expect(mockedRegister).toHaveBeenCalledOnce())
    const input = mockedRegister.mock.calls[0][0]
    expect(Object.keys(input).sort()).toEqual([
      'email',
      'firstName',
      'lastName',
      'password',
      'phoneNumber',
    ])
  })

  it('shows a success state once the account exists', async () => {
    mockedRegister.mockResolvedValue({ success: true, message: 'ok', field: null })
    await renderAndFill()

    submit()

    expect(await screen.findByText('Account created')).toBeInTheDocument()
    expect(screen.getByText('Your account is ready. Sign in to continue.')).toBeInTheDocument()
    // The form is gone: a second submission is not possible by accident.
    expect(screen.queryByRole('button', { name: 'Create Account' })).not.toBeInTheDocument()
  })

  it('mentions the confirmation email, and that confirming is optional', async () => {
    mockedRegister.mockResolvedValue({ success: true, message: 'ok', field: null })
    await renderAndFill()

    submit()

    // Registration emails a confirmation link. The copy has to say both that
    // it exists and that it is not required: the backend's `login` does not
    // check `isVerified`, so an unconfirmed account can sign in and work
    // normally. Reading this as a gate would strand anyone who lost the first
    // message, which is exactly who this page is read by.
    expect(
      await screen.findByText(/emailed you a link to confirm your address/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/it isn't required/i)).toBeInTheDocument()
  })

  it('leads to sign-in from the success state, since registration does not authenticate', async () => {
    mockedRegister.mockResolvedValue({ success: true, message: 'ok', field: null })
    const { onSwitchToSignIn } = await renderAndFill()

    submit()
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to sign in' }))

    expect(onSwitchToSignIn).toHaveBeenCalledOnce()
  })

  it('shows a loading state while the mutation is in flight', async () => {
    let resolveRegister: (value: Awaited<ReturnType<typeof registerRequest>>) => void = () => {}
    mockedRegister.mockReturnValue(
      new Promise((resolve) => {
        resolveRegister = resolve
      }),
    )
    await renderAndFill()

    submit()

    expect(await screen.findByRole('button', { name: 'Creating account…' })).toBeDisabled()
    resolveRegister({ success: true, message: 'ok', field: null })
  })

  // --- backend field errors --------------------------------------------------

  it('shows a duplicate-email rejection on the email field', async () => {
    mockedRegister.mockResolvedValue({
      success: false,
      message: 'An account with this email already exists.',
      field: 'email',
    })
    await renderAndFill()

    submit()

    expect(
      await screen.findByText('An account with this email already exists.'),
    ).toBeInTheDocument()
    // Attached to the field, not hoisted into a whole-form banner - the same
    // place its own client-side errors appear, and wired to the input with
    // aria-describedby so a screen reader announces it there.
    const field = screen.getByLabelText('Email')
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(field).toHaveAttribute('aria-describedby', 'signup-email-error')
    expect(screen.getByText('An account with this email already exists.')).toHaveAttribute(
      'id',
      'signup-email-error',
    )
  })

  it.each([
    ['firstName', 'First name'],
    ['lastName', 'Last name'],
    ['email', 'Email'],
  ])('shows a backend %s validation error next to that field', async (field, label) => {
    mockedRegister.mockResolvedValue({ success: false, message: 'Rejected by the server.', field })
    await renderAndFill()

    submit()

    await screen.findByText('Rejected by the server.')
    expect(screen.getByLabelText(label)).toHaveAttribute('aria-invalid', 'true')
  })

  it('shows a backend password validation error on the password field', async () => {
    // The form's own 8-character minimum is the same threshold the backend
    // enforces, so this is the path a password the client accepted but the
    // server rejects takes.
    mockedRegister.mockResolvedValue({
      success: false,
      message: 'This password is too common.',
      field: 'password',
    })
    await renderAndFill()

    submit()

    await screen.findByText('This password is too common.')
    expect(screen.getAllByLabelText('Password')[0]).toHaveAttribute('aria-invalid', 'true')
  })

  it('shows a whole-form backend error when no field is named', async () => {
    mockedRegister.mockResolvedValue({
      success: false,
      message: 'We could not create the account. Please try again.',
      field: null,
    })
    await renderAndFill()

    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not create the account. Please try again.',
    )
  })

  it('does not attach an unknown backend field name to the form', async () => {
    // `field` is a string from the server. Writing an unrecognised value
    // into the error map unchecked would make it part of the form's state.
    mockedRegister.mockResolvedValue({
      success: false,
      message: 'Something unexpected went wrong.',
      field: 'notARealField',
    })
    await renderAndFill()

    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent('Something unexpected went wrong.')
  })

  // --- server errors --------------------------------------------------------

  it('reports an unreachable server as a whole-form error and re-enables the form', async () => {
    mockedRegister.mockRejectedValue(new Error('Failed to fetch'))
    await renderAndFill()

    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not reach the server. Please try again.',
    )
    // The user can try again - a transport failure is not a verdict on
    // anything they typed.
    expect(screen.getByRole('button', { name: 'Create Account' })).not.toBeDisabled()
  })

  it('does not show a raw transport error to the user', async () => {
    mockedRegister.mockRejectedValue(new Error('ECONNREFUSED 127.0.0.1:8000'))
    await renderAndFill()

    submit()

    await screen.findByRole('alert')
    expect(screen.queryByText(/ECONNREFUSED/)).not.toBeInTheDocument()
  })

  it('keeps what the user typed after a failure, so it can be resubmitted', async () => {
    mockedRegister.mockResolvedValue({
      success: false,
      message: 'An account with this email already exists.',
      field: 'email',
    })
    await renderAndFill()

    submit()
    await screen.findByText('An account with this email already exists.')

    expect(screen.getByLabelText('First name')).toHaveValue('Ada')
    expect(screen.getByLabelText('Email')).toHaveValue('ada@example.com')
  })

  it('replaces a field error once the user edits that field again', async () => {
    mockedRegister.mockResolvedValue({
      success: false,
      message: 'An account with this email already exists.',
      field: 'email',
    })
    await renderAndFill()
    submit()
    await screen.findByText('An account with this email already exists.')

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'new@example.com' } })

    // The client-side validation pass supersedes the server's message for
    // that field, so the two never contradict each other.
    expect(screen.queryByText('An account with this email already exists.')).not.toBeInTheDocument()
  })
})
