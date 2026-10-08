/**
 * Wires the existing, custom-styled `GoogleAuthButton` to Google Identity
 * Services (GIS) - loaded dynamically, never bundled, since it's Google's
 * script, not this app's code.
 *
 * Google's Sign In With Google flow only issues a credential from a real
 * user gesture (a click) on a button GIS itself rendered - a click handler
 * cannot just call some "start sign-in" function directly. To keep our own
 * button's design (rather than duplicating it or replacing it with
 * Google's default-styled one), this renders GIS's own button into a
 * visually hidden container and forwards a click on `GoogleAuthButton` to
 * it, which is Google's own documented pattern for a custom button design:
 * https://developers.google.com/identity/gsi/web/guides/personalized-button
 *
 * The credential this produces is a Google ID token (a JWT) - opaque to
 * this hook and never trusted client-side; only the backend's verified
 * decode of it (identity/google_oauth.py) is ever treated as identity.
 */

import { useCallback, useEffect, useId, useRef } from 'react'

import { env } from '../../../lib/env'

interface GoogleCredentialResponse {
  credential: string
}

interface GoogleIdConfiguration {
  client_id: string
  callback: (response: GoogleCredentialResponse) => void
}

interface GoogleAccountsId {
  initialize: (config: GoogleIdConfiguration) => void
  renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void
}

declare global {
  interface Window {
    google?: { accounts: { id: GoogleAccountsId } }
  }
}

const GIS_SCRIPT_SRC = 'https://accounts.google.com/gsi/client'

let gisScriptPromise: Promise<void> | null = null

function loadGoogleIdentityServices(): Promise<void> {
  if (window.google?.accounts?.id) return Promise.resolve()
  gisScriptPromise ??= new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = GIS_SCRIPT_SRC
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('Failed to load Google Identity Services.'))
    document.head.appendChild(script)
  })
  return gisScriptPromise
}

export interface UseGoogleSignInResult {
  /** Attach to the hidden container that receives Google's own button. */
  hiddenButtonContainerId: string
  /** Attach as `GoogleAuthButton`'s `onClick`. No-op if not configured. */
  trigger: () => void
  /** False when `VITE_GOOGLE_OAUTH_CLIENT_ID` is unset - the button should
   * still render, but do nothing, rather than the app failing to start. */
  isConfigured: boolean
}

/**
 * @param onCredential Called with the verified-by-Google (not yet
 * verified-by-us) ID token once the user completes the Google flow.
 */
export function useGoogleSignIn(onCredential: (credential: string) => void): UseGoogleSignInResult {
  const hiddenButtonContainerId = useId()
  const isConfigured = Boolean(env.googleClientId)
  // Avoids re-initializing GIS (and re-rendering its button) on every
  // render just because the caller passed a fresh function identity - kept
  // current via its own effect (assigning a ref during render itself is
  // not allowed) rather than being a dependency of the effect below.
  const onCredentialRef = useRef(onCredential)
  useEffect(() => {
    onCredentialRef.current = onCredential
  })

  useEffect(() => {
    if (!isConfigured) return
    const container = document.getElementById(hiddenButtonContainerId)
    if (!container) return
    let cancelled = false

    loadGoogleIdentityServices()
      .then(() => {
        if (cancelled || !window.google) return
        window.google.accounts.id.initialize({
          client_id: env.googleClientId,
          callback: (response) => onCredentialRef.current(response.credential),
        })
        window.google.accounts.id.renderButton(container, { type: 'standard' })
      })
      .catch(() => {
        // Google's script failed to load (offline, blocked, ...) - the
        // button below simply has nothing to forward a click to.
      })

    return () => {
      cancelled = true
    }
  }, [isConfigured, hiddenButtonContainerId])

  const trigger = useCallback(() => {
    document
      .getElementById(hiddenButtonContainerId)
      ?.querySelector<HTMLElement>('div[role="button"]')
      ?.click()
  }, [hiddenButtonContainerId])

  return { hiddenButtonContainerId, trigger, isConfigured }
}
