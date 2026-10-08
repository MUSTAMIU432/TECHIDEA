import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getAccessToken, setAccessToken } from '../../../graphql/tokenStore'
import { refreshTokenRequest, type AuthSession } from './authApi'
import {
  MIN_REFRESH_INTERVAL_MS,
  REFRESH_SKEW_MS,
  beginSession,
  cancelProactiveRefresh,
  ensureFreshAccessToken,
  isProactiveRefreshScheduled,
  isRefreshDue,
  refreshAccessToken,
  scheduleProactiveRefresh,
  setAuthenticationFailureHandler,
} from './tokenRefresh'
import { resetTokenRefreshState } from './tokenRefreshState'

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

/** An ISO deadline `seconds` from now - the shape the backend sends. */
function expiresIn(seconds: number): string {
  return new Date(Date.now() + seconds * 1000).toISOString()
}

function fakeSession(accessToken: string, seconds = 900): AuthSession {
  return { accessToken, accessTokenExpiresAt: expiresIn(seconds), user: USER }
}

function resolved(session: AuthSession) {
  return { success: true, message: 'Session renewed.', session }
}

type RefreshOutcome = Awaited<ReturnType<typeof refreshTokenRequest>>

/** A `refreshTokenRequest` that has not settled yet, for in-flight races. */
function pending() {
  let resolve: (value: RefreshOutcome) => void = () => {}
  const promise = new Promise<RefreshOutcome>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}

function rejected(message = 'Your session has expired. Please sign in again.') {
  return { success: false, message, session: null }
}

describe('tokenRefresh', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
    resetTokenRefreshState()
    setAccessToken(null)
    mockedRefresh.mockReset()
  })

  afterEach(() => {
    cancelProactiveRefresh()
    resetTokenRefreshState()
    setAccessToken(null)
    vi.useRealTimers()
  })

  // --- proactive refresh -----------------------------------------------------

  describe('proactive refresh', () => {
    it('schedules a refresh shortly before the token expires', () => {
      setAccessToken('current', expiresIn(900))

      scheduleProactiveRefresh(expiresIn(900))

      // 900s of lifetime, minus the 60s of headroom.
      expect(isProactiveRefreshScheduled()).toBe(true)
      expect(vi.getTimerCount()).toBe(1)
      vi.advanceTimersByTime(840_000)
      expect(isProactiveRefreshScheduled()).toBe(false)
    })

    it('does not refresh before the headroom has elapsed', () => {
      mockedRefresh.mockResolvedValue(resolved(fakeSession('next')))
      scheduleProactiveRefresh(expiresIn(900))

      vi.advanceTimersByTime(839_000)

      expect(mockedRefresh).not.toHaveBeenCalled()
    })

    it('refreshes on the timer and installs the new token', async () => {
      mockedRefresh.mockResolvedValue(resolved(fakeSession('renewed', 900)))
      setAccessToken('current', expiresIn(900))
      scheduleProactiveRefresh(expiresIn(900))

      await vi.advanceTimersByTimeAsync(840_000)

      expect(mockedRefresh).toHaveBeenCalledOnce()
      expect(getAccessToken()).toBe('renewed')
    })

    it('replaces an existing schedule rather than adding a second timer', () => {
      scheduleProactiveRefresh(expiresIn(900))
      scheduleProactiveRefresh(expiresIn(1800))

      expect(vi.getTimerCount()).toBe(1)
    })

    it('cancels a pending schedule', () => {
      scheduleProactiveRefresh(expiresIn(900))
      expect(isProactiveRefreshScheduled()).toBe(true)

      cancelProactiveRefresh()

      expect(isProactiveRefreshScheduled()).toBe(false)
      expect(vi.getTimerCount()).toBe(0)
    })

    it('does not schedule anything without an expiry', () => {
      scheduleProactiveRefresh(null)

      expect(isProactiveRefreshScheduled()).toBe(false)
    })

    it('does not schedule anything for an unparseable expiry', () => {
      scheduleProactiveRefresh('not-a-date')

      expect(isProactiveRefreshScheduled()).toBe(false)
    })

    it('schedules a far-future expiry rather than overflowing the timer', async () => {
      // `setTimeout` above 2^31-1 ms overflows and fires *immediately*. An
      // unclamped far-future deadline would therefore turn "refresh in
      // years" into a continuous refresh loop.
      setAccessToken('current', '2999-01-01T00:00:00.000Z')

      scheduleProactiveRefresh('2999-01-01T00:00:00.000Z')
      await vi.advanceTimersByTimeAsync(0)

      expect(mockedRefresh).not.toHaveBeenCalled()
      expect(isProactiveRefreshScheduled()).toBe(true)
    })

    it('never refreshes more often than the minimum interval', async () => {
      // A token whose lifetime is shorter than the refresh skew would
      // otherwise schedule "refresh in 0ms" over and over, rotating the
      // single-use cookie each time.
      mockedRefresh.mockResolvedValue(resolved(fakeSession('renewed', 5)))
      setAccessToken('current', expiresIn(5))
      scheduleProactiveRefresh(expiresIn(5))

      await vi.advanceTimersByTimeAsync(MIN_REFRESH_INTERVAL_MS * 3)

      const allowed = 1 + Math.floor((MIN_REFRESH_INTERVAL_MS * 3) / MIN_REFRESH_INTERVAL_MS)
      expect(mockedRefresh.mock.calls.length).toBeLessThanOrEqual(allowed)
    })
  })

  // --- one refresh at a time -------------------------------------------------

  describe('single-flight', () => {
    it('performs exactly one refresh for concurrent callers', async () => {
      // The refresh cookie is single-use: a second concurrent refresh would
      // present an already-revoked credential, fail, and sign the user out.
      const inFlight = pending()
      mockedRefresh.mockReturnValue(inFlight.promise)

      const first = refreshAccessToken()
      const second = refreshAccessToken()
      const third = refreshAccessToken()

      inFlight.resolve(resolved(fakeSession('shared')))
      const results = await Promise.all([first, second, third])

      expect(mockedRefresh).toHaveBeenCalledOnce()
      expect(results[0]).toBe(results[1])
      expect(results[1]).toBe(results[2])
      expect(getAccessToken()).toBe('shared')
    })

    it('shares one refresh between a request and the proactive timer', async () => {
      const inFlight = pending()
      mockedRefresh.mockReturnValue(inFlight.promise)
      scheduleProactiveRefresh(expiresIn(1))
      await vi.advanceTimersByTimeAsync(0) // fires the scheduled refresh
      const requested = refreshAccessToken() // arrives while it is in flight

      inFlight.resolve(resolved(fakeSession('shared')))
      await requested

      expect(mockedRefresh).toHaveBeenCalledOnce()
    })

    it('allows a new refresh once the previous one has settled', async () => {
      mockedRefresh.mockResolvedValue(resolved(fakeSession('one')))
      await refreshAccessToken()
      mockedRefresh.mockResolvedValue(resolved(fakeSession('two')))

      await refreshAccessToken()

      expect(mockedRefresh).toHaveBeenCalledTimes(2)
      expect(getAccessToken()).toBe('two')
    })

    it('does not treat a superseded refresh as a session-ending failure', async () => {
      // A bootstrap refresh still in flight when the user signs in must not
      // tear the newer session down when it comes back - the race is
      // ordinary, not exceptional.
      const inFlight = pending()
      mockedRefresh.mockReturnValue(inFlight.promise)
      const onFailure = vi.fn()
      setAuthenticationFailureHandler(onFailure)
      const stale = refreshAccessToken()

      setAccessToken('from-login', expiresIn(900))
      inFlight.resolve(rejected())
      await stale

      expect(onFailure).not.toHaveBeenCalled()
      expect(getAccessToken()).toBe('from-login')
    })
  })

  // --- failure is terminal, not retried --------------------------------------

  describe('failure', () => {
    it('signs the user out through the failure handler', async () => {
      const onFailure = vi.fn()
      setAuthenticationFailureHandler(onFailure)
      setAccessToken('current', expiresIn(900))
      mockedRefresh.mockResolvedValue(rejected())

      const result = await refreshAccessToken()

      expect(result).toBeNull()
      expect(onFailure).toHaveBeenCalledOnce()
      // Fail closed: the token is dropped, so this tab cannot keep making
      // requests with a credential the server will reject.
      expect(getAccessToken()).toBeNull()
    })

    it('cancels the proactive schedule when a refresh fails', async () => {
      setAccessToken('current', expiresIn(900))
      scheduleProactiveRefresh(expiresIn(900))
      mockedRefresh.mockResolvedValue(rejected())

      await refreshAccessToken()

      expect(isProactiveRefreshScheduled()).toBe(false)
    })

    it('does not retry: further calls short-circuit without a request', async () => {
      mockedRefresh.mockResolvedValue(rejected())
      await refreshAccessToken()

      await refreshAccessToken()
      await refreshAccessToken()

      expect(mockedRefresh).toHaveBeenCalledOnce()
    })

    it('does not retry even when the scheduled timer fires again', async () => {
      mockedRefresh.mockResolvedValue(rejected())
      setAccessToken('current', expiresIn(1))
      scheduleProactiveRefresh(expiresIn(1))

      await vi.advanceTimersByTimeAsync(120_000)

      // Exactly one attempt: the failure latched, the schedule was cancelled,
      // and nothing rescheduled it.
      expect(mockedRefresh).toHaveBeenCalledOnce()
      expect(isProactiveRefreshScheduled()).toBe(false)
    })

    it('treats a thrown network error the same as a rejection', async () => {
      const onFailure = vi.fn()
      setAuthenticationFailureHandler(onFailure)
      mockedRefresh.mockRejectedValue(new Error('Failed to fetch'))

      const result = await refreshAccessToken()

      expect(result).toBeNull()
      expect(onFailure).toHaveBeenCalledOnce()
      expect(getAccessToken()).toBeNull()
    })

    it('replaces the failure handler rather than stacking a second one', () => {
      const first = vi.fn()
      const second = vi.fn()

      setAuthenticationFailureHandler(first)
      setAuthenticationFailureHandler(second)
      mockedRefresh.mockResolvedValue(rejected())

      return refreshAccessToken().then(() => {
        expect(first).not.toHaveBeenCalled()
        expect(second).toHaveBeenCalledOnce()
      })
    })

    it('recovers on a real sign-in', async () => {
      mockedRefresh.mockResolvedValue(rejected())
      await refreshAccessToken()
      expect(getAccessToken()).toBeNull()

      // What `login` does: a genuine, user-driven session starts.
      beginSession()
      setAccessToken('from-login', expiresIn(900))
      mockedRefresh.mockResolvedValue(resolved(fakeSession('after-login')))

      const result = await refreshAccessToken()

      expect(result?.accessToken).toBe('after-login')
      expect(mockedRefresh).toHaveBeenCalledTimes(2)
    })
  })

  // --- on-demand refresh -----------------------------------------------------

  describe('ensureFreshAccessToken', () => {
    it('does nothing when the current token is comfortably valid', async () => {
      setAccessToken('current', expiresIn(900))

      const result = await ensureFreshAccessToken()

      expect(result).toBeNull()
      expect(mockedRefresh).not.toHaveBeenCalled()
    })

    it('refreshes when the token is inside the refresh margin', async () => {
      setAccessToken('current', expiresIn(30))
      mockedRefresh.mockResolvedValue(resolved(fakeSession('renewed')))

      const result = await ensureFreshAccessToken()

      expect(result?.accessToken).toBe('renewed')
    })

    it('refreshes when there is no token at all', async () => {
      mockedRefresh.mockResolvedValue(resolved(fakeSession('renewed')))

      const result = await ensureFreshAccessToken()

      expect(result?.accessToken).toBe('renewed')
    })

    it('refreshes when the expiry is unknown', async () => {
      // An unrecorded deadline is treated as "refresh now": refreshing early
      // is cheap, discovering the deadline after the server starts rejecting
      // requests is not.
      setAccessToken('current')
      mockedRefresh.mockResolvedValue(resolved(fakeSession('renewed')))

      await ensureFreshAccessToken()

      expect(mockedRefresh).toHaveBeenCalledOnce()
    })
  })

  describe('isRefreshDue', () => {
    it('is due with no token', () => {
      expect(isRefreshDue()).toBe(true)
    })

    it('is due with an unknown expiry', () => {
      setAccessToken('current')
      expect(isRefreshDue()).toBe(true)
    })

    it('is not due with plenty of life left', () => {
      setAccessToken('current', expiresIn(900))
      expect(isRefreshDue()).toBe(false)
    })

    it('is due once inside the skew', () => {
      setAccessToken('current', expiresIn(REFRESH_SKEW_MS / 1000 - 1))
      expect(isRefreshDue()).toBe(true)
    })

    it('is due for an already-expired token', () => {
      setAccessToken('current', expiresIn(-10))
      expect(isRefreshDue()).toBe(true)
    })
  })

  // --- the storage architecture is preserved ----------------------------------

  it('never writes a token to browser storage', async () => {
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    mockedRefresh.mockResolvedValue(resolved(fakeSession('secret-token')))
    setAccessToken('current', expiresIn(900))

    await refreshAccessToken()

    expect(getAccessToken()).toBe('secret-token')
    expect(setItemSpy).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    setItemSpy.mockRestore()
  })
})
