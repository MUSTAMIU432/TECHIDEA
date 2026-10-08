interface ImportMetaEnv {
  readonly VITE_GRAPHQL_URL: string
  // Optional - see src/lib/env.ts.
  readonly VITE_GOOGLE_OAUTH_CLIENT_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
