import { GraphQLClient } from 'graphql-request'

import { env } from '../lib/env'
import { getAccessToken } from './tokenStore'

/**
 * Single shared GraphQL client for the app. Domain features import this
 * instead of constructing their own client or reading env vars directly,
 * so the endpoint and request config stay in one place.
 *
 * Authenticated requests go through this same client, not a second one:
 * `headers` is a function (graphql-request calls it fresh per request)
 * that reads the current access token from tokenStore and attaches it as
 * `Authorization: Bearer <token>` - there is deliberately no separate
 * "authenticated client". `credentials: 'include'` makes the backend's
 * HttpOnly refresh-token cookie flow with every request even though the
 * frontend and backend are different origins (see backend
 * config/settings/base.py's CORS_ALLOW_CREDENTIALS for the server side of
 * this).
 */
export const graphqlClient = new GraphQLClient(env.graphqlUrl, {
  credentials: 'include',
  headers: (): Record<string, string> => {
    const token = getAccessToken()
    const headers: Record<string, string> = {}
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }
    return headers
  },
})
