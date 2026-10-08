import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setAccessToken } from '../../../graphql/tokenStore'
import { renderWithProviders } from '../../../test/renderWithRouter'
import {
  NETWORK_ERROR_MESSAGE,
  googleLoginRequest,
  loginRequest,
  meRequest,
  refreshTokenRequest,
} from '../auth/authApi'
import { useGoogleSignIn } from '../auth/useGoogleSignIn'
import { SignInForm } from './SignInForm'

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
  refreshTokenRequest: vi.fn(),
  meRequest: vi.fn(),
}))

// SignInForm's own responsibility ends at wiring GoogleAuthButton to
// useGoogleSignIn and reacting to the credential it produces - the GIS
// script/DOM integration itself is covered by useGoogleSignIn.test.tsx.
vi.mock('../auth/useGoogleSignIn', () => ({
  useGoogleSignIn: vi.fn(),
}))

const mockedLogin = vi.mocked(loginRequest)
const mockedGoogleLogin = vi.mocked(googleLoginRequest)
const mockedRefresh = vi.mocked(refreshTokenRequest)
const mockedMe = vi.mocked(meRequest)
const mockedUseGoogleSignIn = vi.mocked(useGoogleSignIn)

/** Captures the `onCredential` callback SignInForm passes to the hook, so
 * a test can simulate Google Identity Services returning a credential. */
function stubGoogleSignIn({ isConfigured = true } = {}) {
  let capturedOnCredential: (credential: string) => void = () => {}
  mockedUseGoogleSignIn.mockImplementation((onCredential) => {
    capturedOnCredential = onCredential
    return {
      hiddenButtonContainerId: 'google-button-container',
      trigger: vi.fn(),
      isConfigured,
    }
  })
  return {
    emitCredential: (credential: string) => act(() => capturedOnCredential(credential)),
  }
}

describe('SignInForm', () => {
  beforeEach(() => {
    setAccessToken(null)
    // No pre-existing session: every test starts from the sign-in form,
    // not redirected/pre-authenticated by the mount-time silent refresh.
    mockedRefresh.mockResolvedValue({
      success: false,
      message: 'no session',
      session: null,
    })
    mockedMe.mockResolvedValue(null)
    stubGoogleSignIn()
  })

  afterEach(() => {
    // Isolation: a previous test's session must not leak into the next
    // one's mount-time bootstrap via the shared in-memory token store.
    setAccessToken(null)
  })

  it('shows required-field errors when submitted empty', async () => {
    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    expect(screen.getByText('Email is required.')).toBeInTheDocument()
    expect(screen.getByText('Password is required.')).toBeInTheDocument()
    expect(mockedLogin).not.toHaveBeenCalled()
  })

  it('validates email format', async () => {
    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'not-an-email' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    expect(screen.getByText('Enter a valid email address.')).toBeInTheDocument()
  })

  it('calls onSwitchToSignUp when "Create account" is clicked', async () => {
    const onSwitchToSignUp = vi.fn()
    renderWithProviders(
      <SignInForm onSwitchToSignUp={onSwitchToSignUp} onForgotPassword={() => {}} />,
    )
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: 'Create account' }))

    expect(onSwitchToSignUp).toHaveBeenCalledOnce()
  })

  it('shows a "Forgot password?" link that calls onForgotPassword when clicked', async () => {
    const onForgotPassword = vi.fn()
    renderWithProviders(
      <SignInForm onSwitchToSignUp={() => {}} onForgotPassword={onForgotPassword} />,
    )
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    const link = screen.getByRole('button', { name: 'Forgot password?' })
    expect(link).toBeInTheDocument()

    fireEvent.click(link)

    expect(onForgotPassword).toHaveBeenCalledOnce()
  })

  it('submits valid credentials and shows a loading state', async () => {
    let resolveLogin: (value: Awaited<ReturnType<typeof loginRequest>>) => void = () => {}
    mockedLogin.mockReturnValue(
      new Promise((resolve) => {
        resolveLogin = resolve
      }),
    )

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'ada@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'a-strong-unique-pass-1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    expect(await screen.findByRole('button', { name: 'Signing in…' })).toBeDisabled()
    expect(mockedLogin).toHaveBeenCalledWith('ada@example.com', 'a-strong-unique-pass-1')

    resolveLogin({
      success: false,
      message: 'Invalid email or password.',
      session: null,
    })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sign In' })).not.toBeDisabled())
  })

  it('shows the generic auth error message on invalid credentials, without navigating', async () => {
    mockedLogin.mockResolvedValue({
      success: false,
      message: 'Invalid email or password.',
      session: null,
    })

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'ada@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'wrong-password' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.')
    // Still on the sign-in form - a failed login must not be treated as one.
    expect(screen.getByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
  })

  it('clears the auth error once the user edits a field again', async () => {
    mockedLogin.mockResolvedValue({
      success: false,
      message: 'Invalid email or password.',
      session: null,
    })

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'ada@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'wrong-password' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'trying-again-1' },
    })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('the Google button is disabled when Google sign-in is not configured', async () => {
    stubGoogleSignIn({ isConfigured: false })

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeDisabled()
  })

  it('navigates to /app on a successful Google credential', async () => {
    const { emitCredential } = stubGoogleSignIn()
    mockedGoogleLogin.mockResolvedValue({
      success: true,
      message: 'Signed in successfully.',
      session: {
        accessToken: 'google-token',
        accessTokenExpiresAt: '2099-01-01',
        user: {
          id: '1',
          email: 'ada@example.com',
          firstName: 'Ada',
          lastName: 'Lovelace',
          phoneNumber: '',
          isActive: true,
          isVerified: true,
        },
      },
    })

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    await emitCredential('a-google-id-token')

    expect(mockedGoogleLogin).toHaveBeenCalledWith('a-google-id-token')
    // The success path never sets the form-level error, unlike failure.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows the generic auth error message when Google sign-in fails, without navigating', async () => {
    const { emitCredential } = stubGoogleSignIn()
    mockedGoogleLogin.mockResolvedValue({
      success: false,
      message: 'Could not sign in with Google.',
      session: null,
    })

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    await emitCredential('a-google-id-token')

    expect(await screen.findByRole('alert')).toHaveTextContent('Could not sign in with Google.')
    expect(screen.getByRole('heading', { name: 'Welcome back' })).toBeInTheDocument()
  })
  // --- the backend is unreachable ------------------------------------------
  //
  // Regression cover for a real failure: with the Django server not running,
  // Google delivered a perfectly valid credential, `googleLogin` rejected
  // with `TypeError: Failed to fetch`, the rejection was uncaught, and the
  // form was left stuck on its spinner with the button disabled - no error,
  // no way to retry. `AuthContext` now folds a transport failure into an
  // ordinary outcome, so both symptoms are the form's to show or not show.

  it('re-enables the form and reports a network error when the backend is down', async () => {
    const { emitCredential } = stubGoogleSignIn()
    mockedGoogleLogin.mockRejectedValue(new TypeError('Failed to fetch'))

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    await emitCredential('a-google-id-token')

    // Back to its normal label and clickable: the spinner the user was
    // stuck on is gone and the form can be used again.
    expect(await screen.findByRole('alert')).toHaveTextContent(NETWORK_ERROR_MESSAGE)
    expect(screen.getByRole('button', { name: 'Sign In' })).toBeEnabled()
  })

  it('re-enables the form and reports a network error when a password submit fails', async () => {
    mockedLogin.mockRejectedValue(new TypeError('Failed to fetch'))

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())
    fireEvent.change(screen.getByLabelText('Email'), {
      target: { value: 'ada@example.com' },
    })
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'a-strong-pass-1' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))

    // Back to its normal label and clickable: the spinner the user was
    // stuck on is gone and the form can be used again.
    expect(await screen.findByRole('alert')).toHaveTextContent(NETWORK_ERROR_MESSAGE)
    expect(screen.getByRole('button', { name: 'Sign In' })).toBeEnabled()
  })

  it('does not show the generic invalid-credentials message for a network error', async () => {
    const { emitCredential } = stubGoogleSignIn()
    mockedGoogleLogin.mockRejectedValue(new TypeError('Failed to fetch'))

    renderWithProviders(<SignInForm onSwitchToSignUp={() => {}} onForgotPassword={() => {}} />)
    await waitFor(() => expect(mockedRefresh).toHaveBeenCalled())

    await emitCredential('a-google-id-token')
    const alert = await screen.findByRole('alert')

    // "Could not sign in with Google." reports an authentication decision the
    // request never reached, and would leave the user retyping a correct
    // password.
    expect(alert).not.toHaveTextContent('Could not sign in with Google.')
    expect(alert).not.toHaveTextContent('Invalid email or password.')
  })
})
