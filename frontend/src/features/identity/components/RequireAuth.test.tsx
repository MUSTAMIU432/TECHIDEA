import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setAccessToken } from '../../../graphql/tokenStore'
import { AuthProvider } from '../auth/AuthContext'
import { meRequest, refreshTokenRequest } from '../auth/authApi'
import { RequireAuth } from './RequireAuth'

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

const mockedRefresh = vi.mocked(refreshTokenRequest)
const mockedMe = vi.mocked(meRequest)

const USER = {
  id: '1',
  email: 'ada@example.com',
  firstName: 'Ada',
  lastName: 'Lovelace',
  phoneNumber: '+255712345678',
  isActive: true,
  isVerified: false,
}

function SignInPage() {
  const location = useLocation()
  return (
    <p>
      Sign-in page
      {location.state?.from?.pathname === '/app' ? ' from /app' : ''}
    </p>
  )
}

function renderProtectedRoute() {
  return render(
    <MemoryRouter initialEntries={['/app']}>
      <AuthProvider>
        <Routes>
          <Route path="/auth" element={<SignInPage />} />
          <Route path="/app" element={<RequireAuth />}>
            <Route index element={<p>Protected content</p>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('RequireAuth', () => {
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
    setAccessToken(null)
  })

  it('shows a non-committal loading state before the session check resolves', () => {
    mockedRefresh.mockReturnValue(new Promise(() => {})) // never resolves in this test

    renderProtectedRoute()

    expect(screen.getByText('Loading…')).toBeInTheDocument()
    expect(screen.queryByText('Protected content')).not.toBeInTheDocument()
    expect(screen.queryByText('Sign-in page')).not.toBeInTheDocument()
  })

  it('redirects to /auth when there is no authenticated session', async () => {
    mockedRefresh.mockResolvedValue({
      success: false,
      message: 'no session',
      session: null,
    })

    renderProtectedRoute()

    expect(await screen.findByText('Sign-in page from /app')).toBeInTheDocument()
    expect(screen.queryByText('Protected content')).not.toBeInTheDocument()
  })

  it('renders the protected content when authenticated', async () => {
    mockedRefresh.mockResolvedValue({
      success: true,
      message: 'ok',
      session: {
        accessToken: 'token',
        accessTokenExpiresAt: '2099-01-01',
        user: USER,
      },
    })
    mockedMe.mockResolvedValue(USER)

    renderProtectedRoute()

    expect(await screen.findByText('Protected content')).toBeInTheDocument()
    expect(screen.queryByText('Sign-in page')).not.toBeInTheDocument()
  })
})
