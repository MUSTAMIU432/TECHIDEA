import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import {
  getAccessToken,
  getAccessTokenExpiresAtIso,
  setAccessToken,
} from '../../../graphql/tokenStore'
import {
  NETWORK_ERROR_MESSAGE,
  googleLoginRequest,
  loginRequest,
  logoutRequest,
  meRequest,
  type AuthResult,
  type AuthSession,
  type AuthUser,
} from './authApi'
import {
  beginSession,
  cancelProactiveRefresh,
  refreshAccessToken,
  scheduleProactiveRefresh,
  setAuthenticationFailureHandler,
} from './tokenRefresh'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

export interface LoginOutcome {
  success: boolean
  message: string
}

interface AuthContextValue {
  status: AuthStatus
  user: AuthUser | null
  login: (email: string, password: string) => Promise<LoginOutcome>
  loginWithGoogle: (credential: string) => Promise<LoginOutcome>
  logout: () => Promise<void>
  /** Replaces the signed-in user after the server confirmed a profile change. */
  updateUser: (user: AuthUser) => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

interface AuthProviderProps {
  children: ReactNode
}

interface ResolvedSession {
  accessToken: string
  accessTokenExpiresAt: string | null
  user: AuthUser
}

/**
 * Establishes the current session the way `me` makes authoritative: an
 * in-memory access token (if any) is confirmed via `me` first, rather than
 * assumed valid; if that doesn't resolve to a user (no token yet, or an
 * expired/invalid one), the existing refresh mechanism is tried exactly
 * once, and `me` is checked again with whatever token that produces. Never
 * loops beyond that single refresh attempt - a failed refresh (or a `me`
 * that still fails afterwards) resolves to `null`, not another retry.
 *
 * Uses `refreshAccessToken` rather than `refreshTokenRequest` so this
 * mount-time attempt shares its one-in-flight promise with any proactive
 * refresh already running or about to: the refresh cookie is single-use, so
 * two overlapping refreshes would make the second one fail on an
 * already-revoked credential and sign the user out for no reason.
 *
 * Used for the mount-time bootstrap below. `login`/`loginWithGoogle`
 * deliberately don't route through this - their own mutation's returned
 * `user` is already a fresh, server-validated result from the same
 * request, and re-querying `me` immediately afterwards would just be an
 * extra round trip proving what the mutation already established.
 */
async function resolveSession(isCurrent: () => boolean): Promise<ResolvedSession | null> {
  // Mounting the app *is* a fresh sign-in attempt, so any failure latch
  // left by a previous session is lifted here - the only place that may do
  // so. Inside a session, a failed refresh stays failed; this is not a
  // retry loop, it is the first attempt.
  beginSession()

  const existingToken = getAccessToken()
  if (existingToken && isCurrent()) {
    const user = await meRequest().catch(() => null)
    if (!isCurrent()) return null
    if (user) {
      return {
        accessToken: existingToken,
        accessTokenExpiresAt: getAccessTokenExpiresAtIso(),
        user,
      }
    }
  }

  if (!isCurrent()) return null
  const session = await refreshAccessToken()
  if (!isCurrent() || !session) return null

  const user = await meRequest().catch(() => null)
  if (!isCurrent() || !user) return null

  return {
    accessToken: session.accessToken,
    accessTokenExpiresAt: session.accessTokenExpiresAt,
    user,
  }
}

/**
 * Owns the app's authentication state, so it isn't scattered across
 * components - everything else reads it through `useAuth()`.
 *
 * The access token itself is never exposed here (or anywhere else in
 * component state): it's held only in the shared GraphQL client's
 * in-memory token store (graphql/tokenStore.ts) and attached automatically
 * to outgoing requests. This context only ever hands components `status`
 * and `user`.
 *
 * A session established here also starts the proactive refresh cycle
 * (see `tokenRefresh.ts`), and one that ends cancels it - so a signed-out
 * app has no timer running against it.
 */
export function AuthProvider({ children }: AuthProviderProps) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [user, setUser] = useState<AuthUser | null>(null)
  const authVersionRef = useRef(0)
  const bootstrapPromiseRef = useRef<Promise<ResolvedSession | null> | null>(null)

  const applySession = useCallback((session: ResolvedSession | null) => {
    authVersionRef.current += 1
    if (session) {
      setAccessToken(session.accessToken, session.accessTokenExpiresAt)
      setUser(session.user)
      setStatus('authenticated')
      // Keep the session alive before it lapses. `tokenRefresh` replaces any
      // pending schedule, so re-establishing a session cannot leave two
      // timers running (and cannot double-rotate the single-use cookie).
      scheduleProactiveRefresh(session.accessTokenExpiresAt)
    } else {
      setAccessToken(null)
      setUser(null)
      setStatus('unauthenticated')
      cancelProactiveRefresh()
    }
  }, [])

  useEffect(() => {
    // On first load (including a hard refresh) there's no access token in
    // memory yet - only the httpOnly refresh cookie the browser already
    // holds, if any - so resolveSession()'s own token check is normally a
    // no-op here and it goes straight to refresh; the check still exists
    // because this effect is the same bootstrap path a provider remount
    // within an already-running tab would take, and that case *can* have
    // a token already in memory.
    let cancelled = false
    const authVersion = authVersionRef.current
    const isCurrent = () => authVersionRef.current === authVersion
    const bootstrapPromise = bootstrapPromiseRef.current ?? resolveSession(isCurrent)
    bootstrapPromiseRef.current = bootstrapPromise

    bootstrapPromise
      .then((session) => {
        if (!cancelled && isCurrent()) applySession(session)
      })
      .catch(() => {
        if (!cancelled && isCurrent()) applySession(null)
      })

    return () => {
      cancelled = true
    }
  }, [applySession])

  /**
   * Runs one authentication attempt and folds a transport failure into a
   * normal outcome, so `login`/`loginWithGoogle` never reject.
   *
   * The context is the right place for this rather than each form: it is the
   * component that owns the session lifecycle, so it is the one that must
   * guarantee a consistent state afterwards. Letting the request reject
   * meant that a backend that was simply not running left the sign-in button
   * spinning forever (the caller's `setStatus('idle')` was never reached),
   * logged an unhandled rejection, and - for a Google sign-in that had
   * already delivered a valid credential - left a half-finished attempt
   * with no error shown at all. A caller that can trust `success: false` is
   * also a caller that cannot forget to handle it.
   *
   * `applySession(null)` runs either way, including on a transport failure:
   * no session was established, and failing closed is the same posture the
   * context already takes for a refused attempt.
   */
  const runAuthAttempt = useCallback(
    async (attempt: () => Promise<AuthResult>): Promise<LoginOutcome> => {
      let outcome: LoginOutcome
      let session: AuthSession | null
      try {
        const result = await attempt()
        outcome = { success: result.success, message: result.message }
        session = result.success ? result.session : null
      } catch {
        outcome = { success: false, message: NETWORK_ERROR_MESSAGE }
        session = null
      }
      applySession(session)
      return outcome
    },
    [applySession],
  )

  const login = useCallback(
    (email: string, password: string): Promise<LoginOutcome> =>
      runAuthAttempt(() => loginRequest(email, password)),
    [runAuthAttempt],
  )

  const loginWithGoogle = useCallback(
    (credential: string): Promise<LoginOutcome> =>
      runAuthAttempt(() => googleLoginRequest(credential)),
    [runAuthAttempt],
  )

  const logout = useCallback(async () => {
    // Local state is cleared even if the network call fails - the user
    // asked to log out, and the access token being dropped from memory
    // here means this tab can't make authenticated requests regardless of
    // whether the server-side session was also revoked. The network error
    // (if any) is deliberately swallowed, not just ignored via `finally`:
    // callers can treat `logout()` as always succeeding locally, the same
    // way the backend's own logout mutation is designed to never fail.
    const request = logoutRequest()
    applySession(null)
    try {
      await request
    } catch {
      // Best-effort: the server-side session may not have been revoked,
      // but there is nothing actionable for the caller to do about it.
    }
    // react-hooks/exhaustive-deps requires applySession here; oxlint's own
    // react/memo-dependencies flags the same dependency as unnecessary
    // (it's a stable, empty-deps callback) - the two rules disagree on
    // this exact case, so the latter is suppressed rather than dropping a
    // dependency exhaustive-deps correctly requires.
    // oxlint-disable-next-line react/memo-dependencies
  }, [applySession])

  // A refresh that cannot be renewed ends the session. This is the
  // fail-closed posture: the alternative - keeping the user "signed in"
  // with a token the server will reject - would show them a dashboard full
  // of errors instead of a sign-in prompt, and would leave a broken client
  // retrying a refresh that can only fail.
  //
  // Registered as an effect rather than inline so a remount replaces the
  // handler instead of stacking another one behind it, and torn down on
  // unmount so a provider that is gone is not signed out by a timer that
  // outlives it.
  useEffect(() => {
    setAuthenticationFailureHandler(() => {
      // Deliberately not `logout()`: there is no server-side session left
      // to revoke (the refresh credential was refused or already gone), so
      // this is a purely local teardown.
      applySession(null)
    })
    return () => {
      setAuthenticationFailureHandler(null)
      cancelProactiveRefresh()
    }
  }, [applySession])

  // A real sign-in lifts the refresh failure latch, so a user who signs in
  // again after being signed out is not left with a permanently disabled
  // refresh.
  useEffect(() => {
    if (status === 'authenticated') {
      beginSession()
    }
  }, [status])

  const value = useMemo(
    () => ({
      status,
      user,
      login,
      loginWithGoogle,
      logout,
      updateUser: setUser,
    }),
    [status, user, login, loginWithGoogle, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// The Provider and its matching hook belong in one small, cohesive module;
// splitting them into two files purely for a Fast-Refresh optimization
// isn't worth the fragmentation for a file this size.
// oxlint-disable-next-line react/only-export-components
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
