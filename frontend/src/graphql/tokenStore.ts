/**
 * Holds the current short-lived access token in memory only - never in
 * localStorage/sessionStorage (a stolen token there survives an XSS
 * payload's next page load; an in-memory value doesn't survive any reload
 * at all, which is exactly the point - the httpOnly refresh cookie is what
 * re-establishes a session after a reload, not this).
 *
 * `graphql/client.ts` reads this to attach `Authorization: Bearer <token>`
 * to every request; `features/identity/auth/AuthContext.tsx` and
 * `tokenRefresh.ts` are the only writers, updating it on
 * login/refresh/logout. Nothing else should import the setter.
 *
 * The token's expiry is stored here, alongside it rather than in a second
 * place, for one reason: a client can only refresh *proactively* - before a
 * request fails - if it knows when the current token stops working, and
 * having the two in one place makes it impossible to hold a token without
 * also holding its deadline. `clearAccessToken()` resets both together for
 * the same reason: a stale deadline next to a cleared token would schedule a
 * refresh for a session that no longer exists.
 */

let accessToken: string | null = null
let accessTokenExpiresAt: number | null = null

export function getAccessToken(): string | null {
  return accessToken
}

/**
 * The current access token's expiry as a Unix epoch in milliseconds, or
 * `null` when there is no token (or its expiry was never recorded).
 *
 * Parsed once, on the way in, so every reader compares numbers rather than
 * re-parsing an ISO string - and so an unparseable value becomes `null`
 * ("unknown", which refreshes sooner) rather than `NaN`, which would make
 * every comparison false and never refresh at all.
 */
export function getAccessTokenExpiresAt(): number | null {
  return accessTokenExpiresAt
}

/**
 * The same expiry as an ISO 8601 string - the shape the backend sends and
 * the shape `tokenRefresh` schedules from - or `null` when there is none.
 * Exposed so a consumer that needs to re-arm the refresh schedule (a
 * provider remounting mid-session, say) can do so from the token already in
 * memory without having to invent its own deadline.
 */
export function getAccessTokenExpiresAtIso(): string | null {
  return accessTokenExpiresAt === null ? null : new Date(accessTokenExpiresAt).toISOString()
}

export function setAccessToken(token: string | null, expiresAt?: string | null): void {
  accessToken = token
  accessTokenExpiresAt = token && expiresAt ? Date.parse(expiresAt) : null
  if (accessTokenExpiresAt !== null && Number.isNaN(accessTokenExpiresAt)) {
    accessTokenExpiresAt = null
  }
}

export function clearAccessToken(): void {
  setAccessToken(null)
}
