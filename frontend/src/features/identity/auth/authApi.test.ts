import { afterEach, describe, expect, it, vi } from 'vitest'

import { graphqlClient } from '../../../graphql/client'
import {
  activateAccountRequest,
  googleLoginRequest,
  loginRequest,
  logoutRequest,
  meRequest,
  refreshTokenRequest,
  registerRequest,
  requestPasswordResetRequest,
  resendActivationEmailRequest,
  resetPasswordRequest,
} from './authApi'

/**
 * `authApi` is the frontend's half of the Identity contract: it defines
 * every document sent and every response shape read back. A mismatch here
 * does not fail a test - it fails as a GraphQL `errors` array, or as a
 * silently `null` session, in a browser. So the documents are asserted
 * against the backend's schema rather than left to the components that use
 * them.
 *
 * The schema is defined in the Python backend, so this suite pins the
 * frontend's half and `backend/tests/test_frontend_schema_contract.py` pins
 * the backend's; a rename on either side fails one of the two.
 */
describe('authApi', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubFetch(data: unknown) {
    const fetchMock = vi.fn(async () =>
      Response.json({ data }, { headers: { 'content-type': 'application/json' } }),
    )
    vi.stubGlobal('fetch', fetchMock)
    return fetchMock
  }

  function sentRequests(fetchMock: ReturnType<typeof stubFetch>) {
    return fetchMock.mock.calls.map((call) => {
      const [, init] = call as unknown as [URL | string, RequestInit]
      return JSON.parse(String(init.body)) as {
        query: string
        operationName?: string
        variables?: Record<string, unknown>
      }
    })
  }

  // --- login ---------------------------------------------------------------

  it('sends the login mutation with the credentials as variables', async () => {
    const fetchMock = stubFetch({
      login: {
        success: true,
        message: 'Signed in successfully.',
        accessToken: 'a-token',
        accessTokenExpiresAt: '2099-01-01T00:00:00.000Z',
        user: null,
      },
    })

    await loginRequest('ada@example.com', 'a-strong-pass-1')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Login')
    expect(request.query).toContain('mutation Login($input: LoginInput!)')
    expect(request.variables).toEqual({
      input: { email: 'ada@example.com', password: 'a-strong-pass-1' },
    })
  })

  it('never expects the refresh credential in a login response', async () => {
    // The refresh credential is an HttpOnly cookie and must not be read back
    // from the body: if a payload ever carried one, this would start storing
    // it in memory, and it would be readable by any XSS payload.
    stubFetch({
      login: {
        success: true,
        message: 'ok',
        accessToken: 'a-token',
        accessTokenExpiresAt: '2099-01-01T00:00:00.000Z',
        user: {
          id: '1',
          email: 'ada@example.com',
          firstName: 'Ada',
          lastName: 'Lovelace',
          phoneNumber: '+255712345678',
          isActive: true,
          isVerified: false,
        },
      },
    })

    const result = await loginRequest('ada@example.com', 'a-strong-pass-1')

    expect(result.success).toBe(true)
    expect(Object.keys(result.session ?? {}).sort()).toEqual([
      'accessToken',
      'accessTokenExpiresAt',
      'user',
    ])
  })

  it('reports a rejected login without a session', async () => {
    stubFetch({
      login: {
        success: false,
        message: 'Invalid email or password.',
        accessToken: null,
        accessTokenExpiresAt: null,
        user: null,
      },
    })

    const result = await loginRequest('ada@example.com', 'wrong')

    expect(result).toEqual({
      success: false,
      message: 'Invalid email or password.',
      session: null,
    })
  })

  it('treats an incomplete success payload as a failure', async () => {
    // Fail closed: a payload that says `success` but carries no token is
    // treated as no session at all, never as a half-established one.
    stubFetch({
      login: {
        success: true,
        message: 'ok',
        accessToken: null,
        accessTokenExpiresAt: null,
        user: null,
      },
    })

    const result = await loginRequest('ada@example.com', 'a-strong-pass-1')

    expect(result.success).toBe(false)
    expect(result.session).toBeNull()
  })

  // --- refreshToken --------------------------------------------------------

  it('sends the refresh mutation with no arguments at all', async () => {
    const fetchMock = stubFetch({
      refreshToken: {
        success: true,
        message: 'Session renewed.',
        accessToken: 'renewed',
        accessTokenExpiresAt: '2099-01-01T00:00:00.000Z',
        user: {
          id: '1',
          email: 'ada@example.com',
          firstName: 'Ada',
          lastName: 'Lovelace',
          phoneNumber: '+255712345678',
          isActive: true,
          isVerified: false,
        },
      },
    })

    const result = await refreshTokenRequest()

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('RefreshToken')
    expect(request.query).toContain('mutation RefreshToken')
    expect(request.query).toContain('refreshToken')
    // The credential is read from the httpOnly cookie by the browser, so a
    // variable here would be a client-supplied value the backend has no
    // business accepting.
    expect(request.variables).toBeUndefined()
    expect(result.session?.accessToken).toBe('renewed')
  })

  // --- logout --------------------------------------------------------------

  it('sends the logout mutation with no arguments', async () => {
    const fetchMock = stubFetch({ logout: { success: true } })

    await logoutRequest()

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Logout')
    expect(request.variables).toBeUndefined()
  })

  // --- googleLogin ---------------------------------------------------------

  it('sends only the Google credential, never a client-asserted identity', async () => {
    const fetchMock = stubFetch({
      googleLogin: {
        success: true,
        message: 'ok',
        accessToken: 'google-token',
        accessTokenExpiresAt: '2099-01-01T00:00:00.000Z',
        user: {
          id: '1',
          email: 'grace@example.com',
          firstName: 'Grace',
          lastName: 'Hopper',
          phoneNumber: '',
          isActive: true,
          isVerified: true,
        },
      },
    })

    await googleLoginRequest('a-google-id-token')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('GoogleLogin')
    // No email, no name, no Google user id: everything the backend uses is
    // extracted from the verified credential server-side, so there is
    // nothing here for a caller to tamper with.
    expect(request.variables).toEqual({
      input: { credential: 'a-google-id-token' },
    })
  })

  it('surfaces the backend generic message for a refused Google sign-in', async () => {
    stubFetch({
      googleLogin: {
        success: false,
        message: 'Could not sign in with Google.',
        accessToken: null,
        accessTokenExpiresAt: null,
        user: null,
      },
    })

    const result = await googleLoginRequest('a-credential')

    // The same message for every cause - an invalid credential, an
    // unconfigured client id, or a refused account-link - so it cannot be
    // used to find out whether an account exists for an address.
    expect(result).toEqual({
      success: false,
      message: 'Could not sign in with Google.',
      session: null,
    })
  })

  // --- register ------------------------------------------------------------

  it('sends the register mutation with the RegisterInput fields', async () => {
    const fetchMock = stubFetch({
      register: { success: true, message: 'ok', field: null },
    })

    const result = await registerRequest({
      firstName: 'Ada',
      lastName: 'Lovelace',
      email: 'ada@example.com',
      phoneNumber: '+255712345678',
      password: 'a-strong-pass-1',
    })

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Register')
    expect(request.query).toContain('mutation Register($input: RegisterInput!)')
    expect(request.variables).toEqual({
      input: {
        firstName: 'Ada',
        lastName: 'Lovelace',
        email: 'ada@example.com',
        phoneNumber: '+255712345678',
        password: 'a-strong-pass-1',
      },
    })
    // Registration creates the account only: no session is expected back, so
    // the payload must not ask for a token.
    expect(request.query).not.toContain('accessToken')
    expect(result).toEqual({ success: true, message: 'ok', field: null })
  })

  it('returns the field a backend validation failure applies to', async () => {
    stubFetch({
      register: {
        success: false,
        message: 'An account with this email already exists.',
        field: 'email',
      },
    })

    const result = await registerRequest({
      firstName: 'Ada',
      lastName: 'Lovelace',
      email: 'ada@example.com',
      phoneNumber: '+255712345678',
      password: 'a-strong-pass-1',
    })

    expect(result.success).toBe(false)
    expect(result.field).toBe('email')
  })

  it('reports a whole-form backend failure with a null field', async () => {
    stubFetch({
      register: {
        success: false,
        message: 'We could not create the account.',
        field: null,
      },
    })

    const result = await registerRequest({
      firstName: 'Ada',
      lastName: 'Lovelace',
      email: 'ada@example.com',
      phoneNumber: '+255712345678',
      password: 'a-strong-pass-1',
    })

    expect(result.field).toBeNull()
  })

  // --- me -----------------------------------------------------------------

  it('sends the me query with no arguments', async () => {
    const fetchMock = stubFetch({ me: null })

    const result = await meRequest()

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('Me')
    expect(request.variables).toBeUndefined()
    // null, not a throw: this is the backend's answer for a missing,
    // invalid, expired or deactivated-account token, and the caller must not
    // have to distinguish it from a transport failure.
    expect(result).toBeNull()
  })

  // --- emailed links: password reset and account activation -------------------

  it('sends requestPasswordReset with the address as a variable', async () => {
    const fetchMock = stubFetch({
      requestPasswordReset: {
        success: true,
        message:
          "If an account exists for that email, we've sent instructions to reset your password.",
        field: null,
        user: null,
      },
    })

    const result = await requestPasswordResetRequest('ada@example.com')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('RequestPasswordReset')
    expect(request.variables).toEqual({ input: { email: 'ada@example.com' } })
    // The message is the backend's, passed through untouched: it is identical
    // whether or not the address has an account, and a frontend that rewrote it
    // could easily say something narrower.
    expect(result.message).toContain('If an account exists for that email')
  })

  it('sends resetPassword with the token and the new password', async () => {
    const fetchMock = stubFetch({
      resetPassword: {
        success: true,
        message: 'Your password has been reset.',
        field: null,
        user: null,
      },
    })

    await resetPasswordRequest('a-real-token', 'a-new-password')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('ResetPassword')
    expect(request.variables).toEqual({
      input: { token: 'a-real-token', newPassword: 'a-new-password' },
    })
  })

  it('sends activateAccount with the token', async () => {
    const fetchMock = stubFetch({
      activateAccount: {
        success: true,
        message: 'Confirmed.',
        field: null,
        user: null,
      },
    })

    await activateAccountRequest('a-real-token')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('ActivateAccount')
    expect(request.variables).toEqual({ input: { token: 'a-real-token' } })
  })

  it('sends resendActivationEmail with the address as a variable', async () => {
    const fetchMock = stubFetch({
      resendActivationEmail: {
        success: true,
        message: "If that email needs confirming, we've sent it a confirmation link.",
        field: null,
        user: null,
      },
    })

    await resendActivationEmailRequest('ada@example.com')

    const [request] = sentRequests(fetchMock)
    expect(request.operationName).toBe('ResendActivationEmail')
    expect(request.variables).toEqual({ input: { email: 'ada@example.com' } })
  })

  it.each([
    ['requestPasswordReset', () => requestPasswordResetRequest('ada@example.com')],
    ['resetPassword', () => resetPasswordRequest('a-real-token', 'a-new-password')],
    ['activateAccount', () => activateAccountRequest('a-real-token')],
    ['resendActivationEmail', () => resendActivationEmailRequest('ada@example.com')],
  ])('reports the backend field on a %s failure', async (rootField, call) => {
    stubFetch({
      [rootField]: {
        success: false,
        message: 'This link is invalid or has expired.',
        field: 'token',
        user: null,
      },
    })

    const result = await call()

    // The frontend places a `field` failure next to the input that caused it
    // and treats a `null` field as a whole-form problem, so the shape has to
    // survive the trip.
    expect(result).toMatchObject({ success: false, field: 'token' })
  })

  it.each([
    ['requestPasswordReset', () => requestPasswordResetRequest('ada@example.com')],
    ['resetPassword', () => resetPasswordRequest('a-real-token', 'a-new-password')],
    ['activateAccount', () => activateAccountRequest('a-real-token')],
    ['resendActivationEmail', () => resendActivationEmailRequest('ada@example.com')],
  ])('sends no access token for %s, since none establishes a session', async (rootField, call) => {
    const fetchMock = stubFetch({
      [rootField]: {
        success: true,
        message: 'Done.',
        field: null,
        user: null,
      },
    })

    await call()

    const [request] = sentRequests(fetchMock)
    // A reset or a confirmation is emphatically not a login: the backend
    // returns no access token for any of them, and asking for one here would
    // be a contract the backend does not have.
    expect(request.query).not.toContain('accessToken')
  })

  it('goes through the shared client, so the refresh cookie travels with me', async () => {
    const fetchMock = stubFetch({ me: null })

    await meRequest()

    const [, init] = fetchMock.mock.calls[0] as unknown as [URL | string, RequestInit]
    expect(init.credentials).toBe('include')
    expect(graphqlClient).toBeDefined()
  })
})
