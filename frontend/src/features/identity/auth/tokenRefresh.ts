/**
 * Keeping the access token alive (S1-009).
 *
 * The architecture is unchanged and deliberately so: the refresh
 * credential is an httpOnly cookie the browser attaches on its own, the
 * access token is a short-lived value held only in `graphql/tokenStore.ts`
 * (never in localStorage/sessionStorage), and `refreshToken` rotates the
 * credential server-side every time it is used. This module adds the
 * scheduling and the concurrency control that architecture was missing, and
 * nothing else - it introduces no new storage, no new credential, and no
 * second source of truth. The shared state lives in `tokenRefreshState.ts`;
 * this file is the policy that acts on it.
 *
 * Three problems it solves:
 *
 * 1. **Proactive refresh.** `refreshToken` is called shortly *before* the
 *    current access token expires, using the `accessTokenExpiresAt` the
 *    backend already returns, instead of waiting for a request to come back
 *    unauthenticated. A user mid-click never sees the gap.
 *
 * 2. **One refresh at a time.** The refresh cookie is single-use: every
 *    `refreshToken` rotates it, and the old one is revoked immediately. So
 *    two overlapping refreshes are not merely wasteful - the second one
 *    presents an already-revoked credential and *fails*, logging the user
 *    out. Concurrent callers therefore share a single in-flight promise,
 *    and a scheduled refresh that lands while a request-triggered one is in
 *    flight waits for that one instead of starting a second.
 *
 * 3. **No retry loops.** A failed refresh is terminal for the session, not
 *    an event to retry: the failure handler signs the user out, the
 *    schedule is cancelled, and a `failed` latch short-circuits any further
 *    refresh call - including one from a scheduled timer that was already
 *    queued - until a real sign-in lifts it. There is no code path from
 *    "refresh failed" back to "refresh again".
 *
 * The minimum-interval floor is the other half of that guarantee, and it
 * protects the *success* path: a backend (or a test clock) handing out a
 * token whose lifetime is shorter than the refresh skew would otherwise
 * have this module refresh continuously. The floor makes the fastest
 * possible refresh cadence one per `MIN_REFRESH_INTERVAL_MS`, whatever the
 * token lifetime says.
 */

import {
  clearAccessToken,
  getAccessToken,
  getAccessTokenExpiresAt,
  setAccessToken,
} from '../../../graphql/tokenStore'
import { refreshTokenRequest, type AuthSession } from './authApi'
import { resetTokenRefreshState, tokenRefreshState as state } from './tokenRefreshState'

/**
 * How long before expiry a refresh is scheduled.
 *
 * A minute of headroom is enough to cover a slow network round trip and the
 * request that was already on the wire when the timer fired, without
 * meaningfully shortening the session.
 */
export const REFRESH_SKEW_MS = 60_000

/**
 * The fastest this module will ever refresh, whatever the token's lifetime
 * claims. Bounds the success path against a pathological (or mocked)
 * lifetime; see the module docstring.
 */
export const MIN_REFRESH_INTERVAL_MS = 30_000

/**
 * The largest delay a browser `setTimeout` can actually represent.
 *
 * Anything above this overflows the 32-bit signed millisecond counter and
 * fires *immediately* instead - which, for a schedule derived from a long
 * token lifetime, turns "refresh once in a very long while" into "refresh
 * continuously". Clamping here is what makes a far-future expiry (a test
 * fixture, a clock skew, a genuinely long-lived token) schedule correctly
 * rather than becoming a busy loop.
 */
const MAX_TIMEOUT_MS = 2_147_483_647

/**
 * Registers what should happen when a refresh fails. Called by AuthContext;
 * replacing rather than adding to, so a remount cannot leave two handlers
 * behind and sign the user out twice.
 */
export function setAuthenticationFailureHandler(handler: (() => void) | null): void {
  state.onAuthenticationFailure = handler
}

/**
 * Marks the start of a real, user-driven session and clears any leftover
 * failure state.
 *
 * Called after `login`/`loginWithGoogle` succeed, and at the start of the
 * mount-time bootstrap - which is itself a first sign-in attempt, and the
 * only thing that should be able to clear a latch left behind by a previous
 * session. Deliberately not called from anywhere else: within a session, a
 * failed refresh must stay failed.
 */
export function beginSession(): void {
  state.failed = false
}

/**
 * Refreshes the access token, at most once at a time.
 *
 * Every concurrent caller - the proactive timer, a request that needs a
 * guaranteed-fresh token, a component reacting to expiry - receives the same
 * promise and therefore the same single network round trip and the same
 * single cookie rotation. Resolves with the new session, or `null` when the
 * session could not be renewed.
 */
export function refreshAccessToken(): Promise<AuthSession | null> {
  if (state.failed) {
    return Promise.resolve(null)
  }
  if (state.inFlight) {
    return state.inFlight as Promise<AuthSession | null>
  }

  // The session this refresh belongs to. Refreshes are not instant, and one
  // can easily be overtaken while it waits: the app loads, the bootstrap
  // refresh goes out, and the user signs in before it comes back. A refresh
  // that has been superseded must not overwrite - or tear down - the session
  // that replaced it, so both outcomes below are checked against this.
  const tokenAtStart = getAccessToken()
  const isSuperseded = () => getAccessToken() !== tokenAtStart

  const attempt = (async (): Promise<AuthSession | null> => {
    try {
      const result = await refreshTokenRequest()
      if (!result.success || !result.session) {
        throw new Error('refresh rejected')
      }
      if (isSuperseded()) {
        // Someone signed in or out while this was in flight. The browser
        // cookie has already rotated either way, so there is nothing useful
        // to install - and installing it would sign the newer session out.
        return null
      }
      setAccessToken(result.session.accessToken, result.session.accessTokenExpiresAt)
      beginSession()
      scheduleProactiveRefresh(result.session.accessTokenExpiresAt)
      return result.session
    } catch {
      if (isSuperseded()) {
        return null
      }
      // Terminal, and deliberately not retried. `beginSession` is the only
      // way back, and only a real sign-in calls it.
      state.failed = true
      clearAccessToken()
      cancelProactiveRefresh()
      state.onAuthenticationFailure?.()
      return null
    } finally {
      state.inFlight = null
    }
  })()

  state.lastRefreshStartedAt = Date.now()
  state.inFlight = attempt
  return attempt
}

/**
 * Refreshes only if the current token is at (or past) its expiry margin,
 * otherwise resolves with `null` without a request. For callers that need a
 * token guaranteed valid for the request they are about to make.
 */
export async function ensureFreshAccessToken(): Promise<AuthSession | null> {
  if (!isRefreshDue()) {
    return null
  }
  return refreshAccessToken()
}

/**
 * Whether the current token is gone, unmeasured, or within the refresh
 * margin. An unknown expiry counts as due: refreshing early is cheap,
 * discovering the deadline after the server starts rejecting requests is
 * not.
 */
export function isRefreshDue(now = Date.now()): boolean {
  const expiresAt = getAccessTokenExpiresAt()
  if (expiresAt === null) return true
  return expiresAt - now <= REFRESH_SKEW_MS
}

/**
 * Schedules the next proactive refresh for `accessTokenExpiresAt`.
 *
 * Replaces any pending schedule rather than adding to it, so a session
 * established twice (StrictMode, a fast re-login) leaves exactly one timer.
 */
export function scheduleProactiveRefresh(accessTokenExpiresAt: string | null): void {
  cancelProactiveRefresh()
  if (!accessTokenExpiresAt) return

  const expiresAt = Date.parse(accessTokenExpiresAt)
  if (Number.isNaN(expiresAt)) return

  const untilRefresh = expiresAt - Date.now() - REFRESH_SKEW_MS
  const sinceLastRefresh = state.lastRefreshStartedAt
    ? state.lastRefreshStartedAt + MIN_REFRESH_INTERVAL_MS - Date.now()
    : 0
  const delay = Math.min(Math.max(untilRefresh, sinceLastRefresh, 0), MAX_TIMEOUT_MS)

  state.scheduledTimer = setTimeout(() => {
    state.scheduledTimer = null
    // Resolves either way: a failure signs the user out via the handler, and
    // the `failed` latch means a second fire is a no-op.
    void refreshAccessToken()
  }, delay)
}

/** Cancels any pending proactive refresh. */
export function cancelProactiveRefresh(): void {
  if (state.scheduledTimer !== null) {
    clearTimeout(state.scheduledTimer)
    state.scheduledTimer = null
  }
}

/** Whether a proactive refresh is currently scheduled. */
export function isProactiveRefreshScheduled(): boolean {
  return state.scheduledTimer !== null
}

export { resetTokenRefreshState }
