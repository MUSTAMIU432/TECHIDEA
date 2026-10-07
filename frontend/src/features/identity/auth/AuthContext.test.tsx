import { act, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getAccessToken, setAccessToken } from '../../../graphql/tokenStore'
import { AuthProvider, useAuth, type LoginOutcome } from './AuthContext'
import {
  NETWORK_ERROR_MESSAGE,
  googleLoginRequest,
  loginRequest,
  logoutRequest,
  meRequest,
  refreshTokenRequest,
} from './authApi'

import type * as AuthApiModule from './authApi'
// Only the request functions are stubbed; the module's real constants and
// types are kept. A bare factory object also replaces NETWORK_ERROR_MESSAGE
// with `undefined`, which would make a component set its error to undefined
// and render nothing - invisible to every assertion except one that happens
// to look for the message.
vi.mock('./authApi', async (importOriginal) => ({
  ...(await importOriginal<typeof AuthApiModule>()),
  loginRequest: vi.fn(),
  googleLoginRequest: vi.fn(),
  logoutRequest: vi.fn(),
  refreshTokenRequest: vi.fn(),
  meRequest: vi.fn(),
}))

const mockedLogin = vi.mocked(loginRequest)
const mockedGoogleLogin = vi.mocked(googleLoginRequest)
const mockedLogout = vi.mocked(logoutRequest)
const mockedMe = vi.mocked(meRequest)
const mockedRefresh = vi.mocked(refreshTokenRequest)

const USER = {
  id: '1',
  email: 'ada@example.com',
  firstName: 'Ada',
  lastName: 'Lovelace',
  phoneNumber: '+255712345678',
  isActive: true,
  isVerified: false,
}

/**
 * Renders one provider with a single "Go" button, and returns a function
 * that performs a chosen attempt and hands back what the context returned.
 *
 * Asserting "never rejects" has to go through a real provider and a real
 * click: the bug was never in `AuthContext`'s internals but in what a caller
 * can be handed. One render serves many attempts, so a test can act, then
 * retry on the same provider - which is the shape of the actual user
 * journey being covered here.
 */
function renderAuthDriver(): (
  attempt: (context: ReturnType<typeof useAuth>) => Promise<LoginOutcome>,
) => Promise<LoginOutcome> {
  type Attempt = (context: ReturnType<typeof useAuth>) => Promise<LoginOutcome>
  const captured: { attempt: Attempt | null; outcome: LoginOutcome | null } = {
    attempt: null,
    outcome: null,
  }

  function Consumer() {
    const context = useAuth()
    return (
      <div>
        <p data-testid="status">{context.status}</p>
        <button
          onClick={async () => {
            if (captured.attempt) captured.outcome = await captured.attempt(context)
          }}
        >
          Go
        </button>
      </div>
    )
  }

  render(
    <AuthProvider>
      <Consumer />
    </AuthProvider>,
  )

  return async (attempt) => {
    captured.attempt = attempt
    captured.outcome = null
    await act(async () => {
      screen.getByRole('button', { name: 'Go' }).click()
    })
    if (!captured.outcome) {
      throw new Error('the attempt never produced an outcome')
    }
    return captured.outcome
  }
}

function TestConsumer() {
  const { status, user, login, loginWithGoogle, logout } = useAuth()
  return (
    <div>
      <p data-testid="status">{status}</p>
      <p data-testid="email">{user?.email ?? 'none'}</p>
      <button onClick={() => login('ada@example.com', 'a-strong-unique-pass-1')}>Login</button>
      <button onClick={() => loginWithGoogle('a-google-credential')}>Google Login</button>
      <button onClick={() => logout()}>Logout</button>
    </div>
  )
}

describe('AuthContext', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    setAccessToken(null)
    mockedRefresh.mockResolvedValue({
      success: false,
      message: 'no session',
      session: null,
    })
    mockedMe.mockResolvedValue(null)
  })

  afterEach(() => {
    vi.clearAllMocks()
    setAccessToken(null)
  })

  it('starts in the loading status while the initial refresh attempt is in flight', () => {
    let resolveRefresh: (value: Awaited<ReturnType<typeof refreshTokenRequest>>) => void = () => {}
    mockedRefresh.mockReturnValue(
      new Promise((resolve) => {
        resolveRefresh = resolve
      }),
    )

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    expect(screen.getByTestId('status')).toHaveTextContent('loading')
    resolveRefresh({ success: false, message: 'no session', session: null })
  })

  it('becomes unauthenticated when there is no valid refresh session on load', async () => {
    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))
    expect(screen.getByTestId('email')).toHaveTextContent('none')
  })

  it('becomes authenticated when a valid refresh cookie exists on load', async () => {
    mockedRefresh.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'refreshed-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    mockedMe.mockResolvedValue(USER)

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))
    expect(screen.getByTestId('email')).toHaveTextContent('ada@example.com')
    expect(getAccessToken()).toBe('refreshed-token')
  })

  it('authenticates from an existing access token when me succeeds', async () => {
    setAccessToken('existing-token')
    mockedMe.mockResolvedValue(USER)

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))
    expect(screen.getByTestId('email')).toHaveTextContent('ada@example.com')
    expect(getAccessToken()).toBe('existing-token')
    expect(mockedMe).toHaveBeenCalledOnce()
    expect(mockedRefresh).not.toHaveBeenCalled()
  })

  it('refreshes an expired access token once and confirms the new token with me', async () => {
    setAccessToken('expired-token')
    mockedMe.mockResolvedValueOnce(null).mockResolvedValueOnce(USER)
    mockedRefresh.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'refreshed-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))
    expect(getAccessToken()).toBe('refreshed-token')
    expect(mockedMe).toHaveBeenCalledTimes(2)
    expect(mockedRefresh).toHaveBeenCalledOnce()
  })

  it('does not refresh again when the refreshed token cannot be confirmed', async () => {
    setAccessToken('expired-token')
    mockedRefresh.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'unusable-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))
    expect(mockedMe).toHaveBeenCalledTimes(2)
    expect(mockedRefresh).toHaveBeenCalledOnce()
    expect(getAccessToken()).toBeNull()
  })

  it('does not start duplicate bootstrap requests in StrictMode', async () => {
    mockedRefresh.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'strict-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    mockedMe.mockResolvedValue(USER)

    render(
      <StrictMode>
        <AuthProvider>
          <TestConsumer />
        </AuthProvider>
      </StrictMode>,
    )

    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))
    expect(mockedRefresh).toHaveBeenCalledOnce()
    expect(mockedMe).toHaveBeenCalledOnce()
  })

  it('does not let a completed login get overwritten by an earlier bootstrap', async () => {
    let resolveRefresh: (value: Awaited<ReturnType<typeof refreshTokenRequest>>) => void = () => {}
    mockedRefresh.mockReturnValue(
      new Promise((resolve) => {
        resolveRefresh = resolve
      }),
    )
    mockedLogin.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'login-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    expect(screen.getByTestId('status')).toHaveTextContent('loading')

    await act(async () => {
      screen.getByRole('button', { name: 'Login' }).click()
    })
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(getAccessToken()).toBe('login-token')

    resolveRefresh({ success: false, message: 'no session', session: null })
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))
    expect(getAccessToken()).toBe('login-token')
  })

  it('login success updates status, user, and the shared access token', async () => {
    mockedLogin.mockResolvedValue({
      success: true,
      message: 'Signed in successfully.',
      session: {
        accessToken: 'login-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))

    await act(async () => {
      screen.getByRole('button', { name: 'Login' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(screen.getByTestId('email')).toHaveTextContent('ada@example.com')
    expect(getAccessToken()).toBe('login-token')
  })

  it('never writes the access token (or anything else) to localStorage on login', async () => {
    mockedLogin.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'login-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))

    await act(async () => {
      screen.getByRole('button', { name: 'Login' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(setItemSpy).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)

    setItemSpy.mockRestore()
  })

  it('login failure leaves the user unauthenticated and clears the token', async () => {
    mockedLogin.mockResolvedValue({
      success: false,
      message: 'Invalid email or password.',
      session: null,
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))

    await act(async () => {
      screen.getByRole('button', { name: 'Login' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
    expect(getAccessToken()).toBeNull()
  })

  it('google login success updates status, user, and the shared access token', async () => {
    mockedGoogleLogin.mockResolvedValue({
      success: true,
      message: 'Signed in successfully.',
      session: {
        accessToken: 'google-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))

    await act(async () => {
      screen.getByRole('button', { name: 'Google Login' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(screen.getByTestId('email')).toHaveTextContent('ada@example.com')
    expect(getAccessToken()).toBe('google-token')
    expect(mockedGoogleLogin).toHaveBeenCalledWith('a-google-credential')
  })

  it('google login never writes the access token (or anything else) to localStorage', async () => {
    mockedGoogleLogin.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'google-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))

    await act(async () => {
      screen.getByRole('button', { name: 'Google Login' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')
    expect(setItemSpy).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)

    setItemSpy.mockRestore()
  })

  it('google login failure leaves the user unauthenticated and clears the token', async () => {
    mockedGoogleLogin.mockResolvedValue({
      success: false,
      message: 'Could not sign in with Google.',
      session: null,
    })

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))

    await act(async () => {
      screen.getByRole('button', { name: 'Google Login' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
    expect(getAccessToken()).toBeNull()
  })

  it('logout clears status, user, and the shared access token', async () => {
    mockedLogin.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'login-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    mockedLogout.mockResolvedValue(undefined)

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))
    await act(async () => {
      screen.getByRole('button', { name: 'Login' }).click()
    })
    expect(screen.getByTestId('status')).toHaveTextContent('authenticated')

    await act(async () => {
      screen.getByRole('button', { name: 'Logout' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
    expect(screen.getByTestId('email')).toHaveTextContent('none')
    expect(getAccessToken()).toBeNull()
    expect(mockedLogout).toHaveBeenCalledOnce()
  })

  it('clears local authentication state while logout is still pending', async () => {
    setAccessToken('token')
    mockedMe.mockResolvedValue(USER)
    let resolveLogout: () => void = () => {}
    mockedLogout.mockReturnValue(
      new Promise((resolve) => {
        resolveLogout = resolve
      }),
    )

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('authenticated'))

    act(() => {
      screen.getByRole('button', { name: 'Logout' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
    expect(getAccessToken()).toBeNull()
    expect(mockedLogout).toHaveBeenCalledOnce()

    resolveLogout()
  })

  it('logout still clears local state even if the network call fails', async () => {
    mockedLogin.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'login-token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    mockedLogout.mockImplementation(() => Promise.reject(new Error('network error')))

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    )
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated'))
    await act(async () => {
      screen.getByRole('button', { name: 'Login' }).click()
    })

    await act(async () => {
      screen.getByRole('button', { name: 'Logout' }).click()
    })

    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
    expect(getAccessToken()).toBeNull()
  })

  // --- a request that never reached the server ----------------------------
  //
  // Regression cover for a real failure: with the backend not running, the
  // Google credential arrived from Google, `loginWithGoogle` rejected with
  // `TypeError: Failed to fetch`, the rejection was uncaught, and the
  // sign-in button stayed disabled and spinning forever because the
  // component's `setStatus('idle')` was never reached. `login`/
  // `loginWithGoogle` must fold a transport failure into an ordinary
  // outcome, so no caller has to remember to handle it.

  it('login reports a transport failure instead of rejecting', async () => {
    mockedLogin.mockRejectedValue(new TypeError('Failed to fetch'))
    const go = renderAuthDriver()

    const outcome = await go((context) => context.login('ada@example.com', 'a-strong-pass-1'))

    expect(outcome).toEqual({ success: false, message: NETWORK_ERROR_MESSAGE })
  })

  it('google login reports a transport failure instead of rejecting', async () => {
    mockedGoogleLogin.mockRejectedValue(new TypeError('Failed to fetch'))
    const go = renderAuthDriver()

    const outcome = await go((context) => context.loginWithGoogle('a-google-credential'))

    expect(outcome).toEqual({ success: false, message: NETWORK_ERROR_MESSAGE })
  })

  it('a transport failure never leaves a session behind', async () => {
    // Fail closed: the attempt established nothing, so nothing may look
    // authenticated afterwards.
    mockedLogin.mockRejectedValue(new TypeError('Failed to fetch'))
    const go = renderAuthDriver()

    await go((context) => context.login('ada@example.com', 'a-strong-pass-1'))

    expect(getAccessToken()).toBeNull()
    expect(screen.getByTestId('status')).toHaveTextContent('unauthenticated')
  })

  it('a transport failure is distinguishable from a refused credential', async () => {
    // The backend's generic message reports an authentication decision. A
    // request that never reached one must not borrow it, or the user retypes
    // a correct password believing it was wrong.
    const go = renderAuthDriver()
    mockedLogin.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const transport = await go((context) => context.login('ada@example.com', 'pass'))
    mockedLogin.mockResolvedValueOnce({
      success: false,
      message: 'Invalid email or password.',
      session: null,
    })
    const refused = await go((context) => context.login('ada@example.com', 'wrong'))

    expect(transport.message).toBe(NETWORK_ERROR_MESSAGE)
    expect(transport.message).not.toBe(refused.message)
    expect(refused.message).toBe('Invalid email or password.')
  })

  it('the user can retry on the same provider after a transport failure', async () => {
    // The journey the fix is for: the button must become usable again, and
    // the retry must go through, rather than the user being left staring at a
    // permanently disabled button.
    const go = renderAuthDriver()
    mockedLogin.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const failed = await go((context) => context.login('ada@example.com', 'a-strong-pass-1'))
    mockedLogin.mockResolvedValueOnce({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'second-try',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    const retried = await go((context) => context.login('ada@example.com', 'a-strong-pass-1'))

    expect(failed.success).toBe(false)
    expect(retried.success).toBe(true)
    expect(getAccessToken()).toBe('second-try')
  })

  it('throws when useAuth is used outside an AuthProvider', () => {
    function Bare() {
      useAuth()
      return null
    }

    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => render(<Bare />)).toThrow('useAuth must be used within an AuthProvider')
    consoleError.mockRestore()
  })
})
