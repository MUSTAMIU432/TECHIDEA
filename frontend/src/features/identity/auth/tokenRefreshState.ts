/**
 * The mutable state behind `tokenRefresh.ts`, in a module of its own.
 *
 * Two reasons it is not simply declared inside `tokenRefresh.ts`:
 *
 * 1. **It is deliberately shared.** The refresh cookie is single-use, so two
 *    providers on one page (and a remount, and React StrictMode's
 *    double-invoked effects) must share *one* refresh. Module scope is the
 *    only place that guarantee can live, and saying so explicitly - here,
 *    where a reader can see the whole state at once - is clearer than a
 *    `let` buried in the middle of the implementation.
 *
 * 2. **It can be reset from a test setup file without dragging in the
 *    network layer.** `tokenRefresh.ts` imports `authApi` and therefore
 *    `graphql/client` and `lib/env`. A test setup file that statically
 *    imported `tokenRefresh` would evaluate that whole chain *before* each
 *    test file's own `vi.mock` calls were registered, and every test that
 *    mocks `authApi` would silently end up talking to the real one. This
 *    module depends on nothing, so importing it from setup is safe.
 */

export interface TokenRefreshState {
  /** The single in-flight refresh, shared by every concurrent caller. */
  inFlight: Promise<unknown> | null
  /**
   * Latched by a failed refresh and cleared only by `beginSession()`. While
   * set, any refresh call resolves to `null` without a network request.
   */
  failed: boolean
  scheduledTimer: ReturnType<typeof setTimeout> | null
  lastRefreshStartedAt: number
  onAuthenticationFailure: (() => void) | null
}

export const tokenRefreshState: TokenRefreshState = {
  inFlight: null,
  failed: false,
  scheduledTimer: null,
  lastRefreshStartedAt: 0,
  onAuthenticationFailure: null,
}

/**
 * Returns every piece of state to its initial value, including the pending
 * schedule and the failure handler.
 *
 * Test-only. Production has no "reset": a real session's state is replaced
 * by signing in again, which is what `beginSession` is for. It exists
 * because module state that is shared *by design* also leaks *between
 * tests*, and the leaks are silent - an abandoned in-flight promise makes
 * the next test's bootstrap hang, and a latch left set makes its refresh
 * short-circuit.
 */
export function resetTokenRefreshState(): void {
  if (tokenRefreshState.scheduledTimer !== null) {
    clearTimeout(tokenRefreshState.scheduledTimer)
  }
  tokenRefreshState.scheduledTimer = null
  tokenRefreshState.inFlight = null
  tokenRefreshState.failed = false
  tokenRefreshState.lastRefreshStartedAt = 0
  tokenRefreshState.onAuthenticationFailure = null
}
