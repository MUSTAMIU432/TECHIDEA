import { afterEach, describe, expect, it } from 'vitest'

import {
  clearAccessToken,
  getAccessToken,
  getAccessTokenExpiresAt,
  setAccessToken,
} from './tokenStore'

// A deadline in the near future, expressed the way the backend sends it
// (ISO 8601, produced by `datetime.isoformat()`).
const IN_TWO_MINUTES = new Date(Date.now() + 120_000).toISOString()

describe('tokenStore', () => {
  afterEach(() => {
    setAccessToken(null)
  })

  it('starts with no access token', () => {
    expect(getAccessToken()).toBeNull()
  })

  it('returns the token that was set', () => {
    setAccessToken('a-token')

    expect(getAccessToken()).toBe('a-token')
  })

  it('can be cleared back to null', () => {
    setAccessToken('a-token')
    setAccessToken(null)

    expect(getAccessToken()).toBeNull()
  })

  it('never touches browser storage', () => {
    setAccessToken('a-token', IN_TWO_MINUTES)
    clearAccessToken()

    // The whole point of the httpOnly refresh cookie: nothing here survives
    // a reload, and nothing here is readable by an XSS payload.
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  describe('expiry', () => {
    it('has no expiry when there is no token', () => {
      expect(getAccessTokenExpiresAt()).toBeNull()
    })

    it('parses the expiry the backend sent into an epoch', () => {
      setAccessToken('a-token', IN_TWO_MINUTES)

      expect(getAccessTokenExpiresAt()).toBe(Date.parse(IN_TWO_MINUTES))
    })

    it('drops a stale expiry when the token is cleared', () => {
      setAccessToken('a-token', IN_TWO_MINUTES)
      clearAccessToken()

      // A deadline left behind next to no token would schedule a refresh
      // for a session that does not exist.
      expect(getAccessTokenExpiresAt()).toBeNull()
    })

    it('replaces the expiry when a new session is set', () => {
      const later = new Date(Date.now() + 600_000).toISOString()
      setAccessToken('first', IN_TWO_MINUTES)
      setAccessToken('second', later)

      expect(getAccessTokenExpiresAt()).toBe(Date.parse(later))
    })

    it('has no expiry when a token is stored without one', () => {
      setAccessToken('a-token')

      expect(getAccessTokenExpiresAt()).toBeNull()
    })

    it('treats an unparseable expiry as unknown rather than as NaN', () => {
      setAccessToken('a-token', 'not-a-date')

      // NaN would make every comparison false, so a token with an
      // unparseable deadline would never be refreshed and would only fail
      // once the server started rejecting it.
      expect(getAccessTokenExpiresAt()).toBeNull()
    })

    it('treats an empty expiry as unknown', () => {
      setAccessToken('a-token', '')

      expect(getAccessTokenExpiresAt()).toBeNull()
    })
  })
})
