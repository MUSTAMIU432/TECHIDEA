import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { env } from '../../../lib/env'
import { useGoogleSignIn } from './useGoogleSignIn'

vi.mock('../../../lib/env', () => ({
  env: { graphqlUrl: 'http://localhost:8000/graphql/', googleClientId: '' },
}))

function TestConsumer({ onCredential }: { onCredential: (credential: string) => void }) {
  const { hiddenButtonContainerId, trigger, isConfigured } = useGoogleSignIn(onCredential)
  return (
    <div>
      <p data-testid="configured">{String(isConfigured)}</p>
      <button onClick={trigger}>Continue with Google</button>
      <div id={hiddenButtonContainerId} />
    </div>
  )
}

describe('useGoogleSignIn', () => {
  beforeEach(() => {
    env.googleClientId = ''
    delete window.google
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('reports not configured, and does not touch window.google, when no client id is set', () => {
    render(<TestConsumer onCredential={() => {}} />)

    expect(screen.getByTestId('configured')).toHaveTextContent('false')
    expect(window.google).toBeUndefined()
  })

  it('initializes Google Identity Services and renders its button when configured', async () => {
    env.googleClientId = 'test-client-id'
    const initialize = vi.fn()
    const renderButton = vi.fn()
    window.google = { accounts: { id: { initialize, renderButton } } }

    render(<TestConsumer onCredential={() => {}} />)

    expect(screen.getByTestId('configured')).toHaveTextContent('true')
    await waitFor(() =>
      expect(initialize).toHaveBeenCalledWith(
        expect.objectContaining({ client_id: 'test-client-id' }),
      ),
    )
    expect(renderButton).toHaveBeenCalledOnce()
  })

  it('forwards the credential from Google Identity Services to onCredential', async () => {
    env.googleClientId = 'test-client-id'
    let capturedCallback: ((response: { credential: string }) => void) | undefined
    window.google = {
      accounts: {
        id: {
          initialize: vi.fn((config) => {
            capturedCallback = config.callback
          }),
          renderButton: vi.fn(),
        },
      },
    }
    const onCredential = vi.fn()

    render(<TestConsumer onCredential={onCredential} />)
    await waitFor(() => expect(capturedCallback).toBeDefined())

    capturedCallback?.({ credential: 'a-google-id-token' })

    expect(onCredential).toHaveBeenCalledWith('a-google-id-token')
  })

  it('trigger() clicks the real Google button rendered into the hidden container', async () => {
    env.googleClientId = 'test-client-id'
    window.google = {
      accounts: {
        id: {
          initialize: vi.fn(),
          renderButton: vi.fn((container: HTMLElement) => {
            const realButton = document.createElement('div')
            realButton.setAttribute('role', 'button')
            realButton.addEventListener('click', () =>
              realButton.setAttribute('data-clicked', 'true'),
            )
            container.appendChild(realButton)
          }),
        },
      },
    }

    render(<TestConsumer onCredential={() => {}} />)
    await waitFor(() => expect(window.google?.accounts.id.renderButton).toHaveBeenCalled())

    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))

    expect(document.querySelector('div[role="button"]')).toHaveAttribute('data-clicked', 'true')
  })

  it('trigger() does nothing when no real Google button has been rendered', () => {
    render(<TestConsumer onCredential={() => {}} />)

    // Not configured, so no hidden button exists - trigger() must not throw.
    expect(() =>
      fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' })),
    ).not.toThrow()
  })
})
