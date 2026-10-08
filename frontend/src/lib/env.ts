/**
 * Centralized access to Vite-exposed environment variables.
 * Import from here instead of reading `import.meta.env` in components.
 */

function requireEnv(key: keyof ImportMetaEnv): string {
  const value = import.meta.env[key]
  if (!value) {
    throw new Error(`Missing required environment variable: ${key}`)
  }
  return value
}

const graphqlUrl = requireEnv('VITE_GRAPHQL_URL')

export const env = {
  graphqlUrl,
  // Derived rather than a second required variable: the backend serves
  // `/graphql/` and Ideas' attachment HTTP endpoints (S2-007) from the same
  // origin, so there is nothing for a separate `VITE_API_BASE_URL` to say
  // that `VITE_GRAPHQL_URL` does not already say. `attachmentsApi.ts` (via
  // `ideasApi.ts`) uses this to build an upload/download URL from the
  // relative path GraphQL's `AttachmentType.downloadUrl` returns.
  apiBaseUrl: graphqlUrl.replace(/\/graphql\/?$/, ''),
  // Optional, unlike graphqlUrl: Google sign-in is disabled (the
  // "Continue with Google" button does nothing) rather than the app
  // failing to start, when it isn't configured - see
  // src/features/identity/auth/googleIdentityServices.ts.
  googleClientId: import.meta.env.VITE_GOOGLE_OAUTH_CLIENT_ID || '',
}
