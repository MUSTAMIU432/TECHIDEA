import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

import { resetTokenRefreshState } from '../features/identity/auth/tokenRefreshState'
import { setAccessToken } from '../graphql/tokenStore'

// Vitest globals are off, so Testing Library can't register its own cleanup.
afterEach(() => {
  cleanup()
  // The authentication singletons - the in-memory token store and the
  // single-flight refresh (its in-flight promise, its schedule, its failure
  // latch) - are module-level state *by design*: the refresh cookie is
  // single-use, so two providers on one page must share one refresh. That
  // makes them leak between tests unless reset here, and the leaks are
  // silent and misleading: an abandoned in-flight promise makes the next
  // test's bootstrap hang, and a latch left set makes its refresh short-
  // circuit. Both surface as "expected authenticated, got loading", pointing
  // nowhere near the real cause.
  //
  // Imported from `tokenRefreshState` rather than `tokenRefresh` on purpose:
  // the latter pulls in `authApi` -> `graphql/client` -> `lib/env`, and a
  // static import from a setup file would evaluate that chain before each
  // test's own `vi.mock` is registered - so every test that mocks `authApi`
  // would silently be talking to the real one.
  resetTokenRefreshState()
  setAccessToken(null)
})
