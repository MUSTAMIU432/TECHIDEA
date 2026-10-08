/**
 * GraphQL operations for authentication (login, refresh, logout), used
 * only by AuthContext - components call `useAuth()`, not these directly.
 * All requests go through the single shared `graphqlClient`
 * (frontend/src/graphql/client.ts); this module defines documents and
 * response shapes only, no client of its own.
 */

import { graphqlClient } from '../../../graphql/client'
import { getAccessToken } from '../../../graphql/tokenStore'
import { env } from '../../../lib/env'

export interface AuthUser {
  id: string
  email: string
  firstName: string
  lastName: string
  phoneNumber: string
  isActive: boolean
  isVerified: boolean
  /** Where the profile photo is served from (needs the bearer token), or null. */
  avatarUrl?: string | null
}

export interface AuthSession {
  accessToken: string
  accessTokenExpiresAt: string
  user: AuthUser
}

export interface AuthResult {
  success: boolean
  message: string
  session: AuthSession | null
}

/**
 * Shown when the request could not be made at all — the server is down, DNS
 * failed, CORS or a preflight blocked it — rather than when it was made and
 * refused.
 *
 * Deliberately different from every authentication-failure message, and not
 * a weakening of it. The backend collapses unknown email, wrong password,
 * inactive account, refused Google link and throttling into one generic
 * answer so nothing about the account can be inferred; that reasoning is
 * untouched. A transport failure is a different fact: the request never
 * reached an authentication decision, so answering "Invalid email or
 * password" would be reporting a verdict that was never given, and would
 * send the user off to re-type a password that was correct all along. It
 * also says nothing about the account either way.
 */
export const NETWORK_ERROR_MESSAGE = 'We could not reach the server. Please try again.'

interface RawAuthPayload {
  success: boolean
  message: string
  accessToken: string | null
  accessTokenExpiresAt: string | null
  user: AuthUser | null
}

const USER_FIELDS = `
  id
  email
  firstName
  lastName
  phoneNumber
  isActive
  isVerified
  avatarUrl
`

const LOGIN_MUTATION = `
  mutation Login($input: LoginInput!) {
    login(input: $input) {
      success
      message
      accessToken
      accessTokenExpiresAt
      user { ${USER_FIELDS} }
    }
  }
`

const REFRESH_TOKEN_MUTATION = `
  mutation RefreshToken {
    refreshToken {
      success
      message
      accessToken
      accessTokenExpiresAt
      user { ${USER_FIELDS} }
    }
  }
`

const LOGOUT_MUTATION = `
  mutation Logout {
    logout {
      success
    }
  }
`

const GOOGLE_LOGIN_MUTATION = `
  mutation GoogleLogin($input: GoogleLoginInput!) {
    googleLogin(input: $input) {
      success
      message
      accessToken
      accessTokenExpiresAt
      user { ${USER_FIELDS} }
    }
  }
`

const ME_QUERY = `
  query Me {
    me { ${USER_FIELDS} }
  }
`

function toAuthResult(payload: RawAuthPayload): AuthResult {
  if (payload.success && payload.accessToken && payload.accessTokenExpiresAt && payload.user) {
    return {
      success: true,
      message: payload.message,
      session: {
        accessToken: payload.accessToken,
        accessTokenExpiresAt: payload.accessTokenExpiresAt,
        user: payload.user,
      },
    }
  }
  return { success: false, message: payload.message, session: null }
}

export async function loginRequest(email: string, password: string): Promise<AuthResult> {
  const data = await graphqlClient.request<{ login: RawAuthPayload }>(LOGIN_MUTATION, {
    input: { email, password },
  })
  return toAuthResult(data.login)
}

/**
 * Exchanges the browser's httpOnly refresh cookie (if any) for a fresh
 * access token, rotating the refresh credential. Called on app start to
 * silently re-establish a session after a reload - the access token itself
 * never survives a reload (it's memory-only), only the cookie does.
 */
export async function refreshTokenRequest(): Promise<AuthResult> {
  const data = await graphqlClient.request<{ refreshToken: RawAuthPayload }>(REFRESH_TOKEN_MUTATION)
  return toAuthResult(data.refreshToken)
}

export async function logoutRequest(): Promise<void> {
  await graphqlClient.request<{ logout: { success: boolean } }>(LOGOUT_MUTATION)
}

/**
 * Exchanges a Google ID token (from Google Identity Services, obtained by
 * `useGoogleSignIn`) for a platform session. The credential is opaque to
 * this module and to the backend's caller - only `identity.google_oauth`
 * verifies it; nothing here trusts anything about the signed-in Google
 * account beyond what that verification returns.
 */
export async function googleLoginRequest(credential: string): Promise<AuthResult> {
  const data = await graphqlClient.request<{ googleLogin: RawAuthPayload }>(GOOGLE_LOGIN_MUTATION, {
    input: { credential },
  })
  return toAuthResult(data.googleLogin)
}

/**
 * The one query AuthContext uses to confirm "who is the currently
 * authenticated user" for whatever access token is currently in
 * `tokenStore` - the canonical source of truth for bootstrapping a session
 * (see AuthContext's mount-time logic), not the `user` object embedded in
 * a login/refresh mutation's own response (which is a same-request
 * convenience, not re-checked independently). Returns `null` for the same
 * reason the backend's `me` resolver does - a missing, invalid, expired,
 * wrong-type, or inactive-user token - never throws for that; a thrown
 * error here means something else went wrong (network, transport).
 */
export async function meRequest(): Promise<AuthUser | null> {
  const data = await graphqlClient.request<{ me: AuthUser | null }>(ME_QUERY)
  return data.me
}

/**
 * The fields the backend's `RegisterInput` requires. */
export interface RegisterInput {
  firstName: string
  lastName: string
  email: string
  phoneNumber: string
  password: string
}

export interface RegisterResult {
  success: boolean
  message: string
  /**
   * The `RegisterInput` field a failure applies to, in the same camelCase
   * naming the schema uses, or `null` for a whole-form failure. Lets the
   * form show a backend validation error next to the field that caused it,
   * exactly where it shows its own client-side ones.
   */
  field: string | null
}

const REGISTER_MUTATION = `
  mutation Register($input: RegisterInput!) {
    register(input: $input) {
      success
      message
      field
    }
  }
`

/**
 * Creates an account. The backend's `register` mutation creates the User
 * record and emails it an activation link - it does not authenticate, so
 * this returns no session and the caller still has to sign in (see
 * `SignUpForm`).
 *
 * A GraphQL-level `errors` array is *not* a registration failure: the
 * backend reports every input problem as `success: false` with a `field`,
 * so it is returned like any other outcome. A thrown error here therefore
 * means something the mutation never got to - a network failure, an
 * unreachable server - and callers must treat it as a distinct, whole-form
 * error rather than a validation result.
 */
export async function registerRequest(input: RegisterInput): Promise<RegisterResult> {
  const data = await graphqlClient.request<{ register: RegisterResult }>(REGISTER_MUTATION, {
    input,
  })
  return data.register
}

// --- emailed links: password reset and account activation ---------------------------
//
// The three operations below share a payload shape (`ActionPayload`) and one
// rule that matters more than their differences: the backend answers the
// *request* side generically — the same message and the same outcome whether
// or not the address has an account — because those mutations take a
// caller-supplied address and would otherwise be an account-existence oracle.
// So `requestPasswordResetRequest` and `resendActivationEmailRequest` return
// a message that says nothing, and the UI must not narrow it. What they
// return is deliberately not a boolean "an account exists".

/**
 * The backend's `ActionPayload`: an outcome that establishes no session.
 *
 * `field` names the input a failure applies to (`'email'`, `'password'` or
 * `'token'`) in the same camelCase the schema uses, or is `null` for a
 * whole-form failure. The frontend uses `token` to know that re-showing the
 * form would be pointless, and `password` to put a rejected password next to
 * the input rather than at the top of the form.
 */
export interface ActionResult {
  success: boolean
  message: string
  field: string | null
  user: AuthUser | null
}

const REQUEST_PASSWORD_RESET_MUTATION = `
  mutation RequestPasswordReset($input: RequestPasswordResetInput!) {
    requestPasswordReset(input: $input) {
      success
      message
      field
      user { ${USER_FIELDS} }
    }
  }
`

const RESET_PASSWORD_MUTATION = `
  mutation ResetPassword($input: ResetPasswordInput!) {
    resetPassword(input: $input) {
      success
      message
      field
      user { ${USER_FIELDS} }
    }
  }
`

const ACTIVATE_ACCOUNT_MUTATION = `
  mutation ActivateAccount($input: ActivateAccountInput!) {
    activateAccount(input: $input) {
      success
      message
      field
      user { ${USER_FIELDS} }
    }
  }
`

const RESEND_ACTIVATION_EMAIL_MUTATION = `
  mutation ResendActivationEmail($input: ResendActivationEmailInput!) {
    resendActivationEmail(input: $input) {
      success
      message
      field
      user { ${USER_FIELDS} }
    }
  }
`

/**
 * Asks the backend to email a password-reset link.
 *
 * The success message is the backend's, verbatim and unedited: it is written
 * to be identical whether or not the address has an account, and the UI
 * showing its own wording instead would risk saying something narrower than
 * the API did. So this returns the message to display rather than a boolean.
 */
export async function requestPasswordResetRequest(email: string): Promise<ActionResult> {
  const data = await graphqlClient.request<{
    requestPasswordReset: ActionResult
  }>(REQUEST_PASSWORD_RESET_MUTATION, { input: { email } })
  return data.requestPasswordReset
}

/**
 * Sets a new password using the token from a reset link.
 *
 * A success here signs the user *out* everywhere: the backend revokes every
 * session for the account (including the one this browser was holding) and
 * clears its cookie, so the only correct next step is signing in with the
 * new password. This establishes no session itself and returns no access
 * token.
 */
export async function resetPasswordRequest(
  token: string,
  newPassword: string,
): Promise<ActionResult> {
  const data = await graphqlClient.request<{ resetPassword: ActionResult }>(
    RESET_PASSWORD_MUTATION,
    {
      input: { token, newPassword },
    },
  )
  return data.resetPassword
}

/**
 * Confirms an email address using the token from an activation link.
 *
 * Establishes no session: a confirmation link must never be a way to sign
 * somebody in, so there is no access token here even on success. The
 * account's `isVerified` flag is the only thing that changes.
 */
export async function activateAccountRequest(token: string): Promise<ActionResult> {
  const data = await graphqlClient.request<{ activateAccount: ActionResult }>(
    ACTIVATE_ACCOUNT_MUTATION,
    { input: { token } },
  )
  return data.activateAccount
}

/**
 * Asks for the activation email to be sent again, for an account whose first
 * message was lost. Answers exactly like `requestPasswordResetRequest` — the
 * same generic message for every outcome — for the same reason.
 */
export async function resendActivationEmailRequest(email: string): Promise<ActionResult> {
  const data = await graphqlClient.request<{
    resendActivationEmail: ActionResult
  }>(RESEND_ACTIVATION_EMAIL_MUTATION, { input: { email } })
  return data.resendActivationEmail
}

const UPDATE_PROFILE_MUTATION = `
  mutation UpdateProfile($input: UpdateProfileInput!) {
    updateProfile(input: $input) {
      success
      message
      field
      user { ${USER_FIELDS} }
    }
  }
`

const CHANGE_PASSWORD_MUTATION = `
  mutation ChangePassword($input: ChangePasswordInput!) {
    changePassword(input: $input) {
      success
      message
      field
      user { ${USER_FIELDS} }
    }
  }
`

/** Saves the signed-in user's name and phone number. Email is not editable. */
export async function updateProfileRequest(input: {
  firstName: string
  lastName: string
  phoneNumber: string
}): Promise<ActionResult> {
  const data = await graphqlClient.request<{ updateProfile: ActionResult }>(
    UPDATE_PROFILE_MUTATION,
    { input },
  )
  return data.updateProfile
}

/**
 * Changes the signed-in user's password. The server signs every *other* device
 * out; this browser's session is untouched.
 */
export async function changePasswordRequest(
  currentPassword: string,
  newPassword: string,
): Promise<ActionResult> {
  const data = await graphqlClient.request<{ changePassword: ActionResult }>(
    CHANGE_PASSWORD_MUTATION,
    { input: { currentPassword, newPassword } },
  )
  return data.changePassword
}

export const AVATAR_MAX_BYTES = 100 * 1024 * 1024
export const AVATAR_TYPES = ['image/jpeg', 'image/png', 'image/webp']

interface AvatarResult {
  success: boolean
  message: string
  avatarUrl: string | null
}

async function avatarRequest(method: 'POST' | 'DELETE', body?: FormData): Promise<AvatarResult> {
  const token = getAccessToken()
  const response = await fetch(`${env.apiBaseUrl}/account/avatar/`, {
    method,
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body,
  })
  // Not every failure is our JSON (a proxy's 502, say), so a bad body is
  // reported, not thrown: the server answered, it just was not us.
  let payload: {
    success?: boolean
    message?: string
    user?: { avatarUrl?: string | null }
  } = {}
  try {
    payload = await response.json()
  } catch {
    /* handled below */
  }
  return {
    success: response.ok && payload.success === true,
    message: payload.message ?? 'We could not update your photo. Please try again.',
    avatarUrl: payload.user?.avatarUrl ?? null,
  }
}

/** Uploads a new profile photo (JPEG, PNG or WebP, up to 100 MB). */
export function uploadAvatarRequest(file: File): Promise<AvatarResult> {
  const body = new FormData()
  body.append('file', file)
  return avatarRequest('POST', body)
}

/** Removes the profile photo. */
export function removeAvatarRequest(): Promise<AvatarResult> {
  return avatarRequest('DELETE')
}

/** Fetches a photo with the bearer token, since an `<img>` cannot send one. */
export async function fetchAvatarBlob(avatarUrl: string): Promise<Blob> {
  const token = getAccessToken()
  const response = await fetch(`${env.apiBaseUrl}${avatarUrl}`, {
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) throw new Error('No photo')
  return response.blob()
}
