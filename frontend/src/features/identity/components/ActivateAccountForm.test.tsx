import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { renderRoutes } from '../../../test/renderWithRouter'
import {
  activateAccountRequest,
  resendActivationEmailRequest,
  type ActionResult,
} from '../auth/authApi'
import { ActivateAccountForm } from './ActivateAccountForm'

vi.mock('../auth/authApi', async (importOriginal) => ({
  ...(await importOriginal()),
  activateAccountRequest: vi.fn(),
  resendActivationEmailRequest: vi.fn(),
}))

const activateMock = vi.mocked(activateAccountRequest)
const resendMock = vi.mocked(resendActivationEmailRequest)

/** The backend's own generic message for the resend, identical either way. */
const GENERIC_MESSAGE = "If that email needs confirming, we've sent it a confirmation link."

function renderAt(token: string | null) {
  const path = token ? `/activate-account?token=${token}` : '/activate-account'
  return renderRoutes([{ path: '/activate-account', element: <ActivateAccountForm /> }], path)
}

function clickConfirm() {
  fireEvent.click(screen.getByRole('button', { name: /Confirm my email|Confirming/ }))
}

describe('ActivateAccountForm', () => {
  afterEach(() => {
    activateMock.mockReset()
    resendMock.mockReset()
  })

  it('shows the invalid-link state when no token is present in the URL', () => {
    renderAt(null)

    expect(screen.getByRole('heading', { name: 'Link invalid' })).toBeInTheDocument()
    expect(screen.getByText(/This confirmation link is invalid or has expired/)).toBeInTheDocument()
  })

  it('offers a resend form on the invalid-link state', () => {
    renderAt(null)

    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send a new link' })).toBeInTheDocument()
  })

  it('says signing in does not require confirming', () => {
    renderAt(null)

    // Verification is not a login requirement in the backend, so the page must
    // not read as though it were a gate.
    expect(screen.getByText(/You can sign in without confirming/)).toBeInTheDocument()
  })

  it('does not redeem the token on its own, only on a deliberate press', () => {
    renderAt('a-real-token')

    // Mail clients and link scanners fetch URLs in a message before a person
    // sees them. Confirming on mount would spend a single-use token on a
    // prefetch and leave the real click on an "invalid link" page.
    expect(activateMock).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Confirm my email' })).toBeInTheDocument()
  })

  it('redeems the token from the URL when confirmed', async () => {
    activateMock.mockResolvedValue({
      success: true,
      message: 'Your email address has been confirmed.',
      field: null,
      user: null,
    })
    renderAt('a-real-token')

    clickConfirm()

    await waitFor(() => expect(activateMock).toHaveBeenCalledWith('a-real-token'))
  })

  it('shows a loading state while confirming', async () => {
    let release: (value: ActionResult) => void = () => {}
    activateMock.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    renderAt('a-real-token')

    clickConfirm()

    expect(screen.getByRole('button', { name: 'Confirming…' })).toBeDisabled()
    release({ success: true, message: 'Confirmed.', field: null, user: null })
    await screen.findByRole('heading', { name: 'Email confirmed' })
  })

  it('confirms the address on success', async () => {
    activateMock.mockResolvedValue({
      success: true,
      message: 'Your email address has been confirmed.',
      field: null,
      user: null,
    })
    renderAt('a-real-token')

    clickConfirm()

    expect(await screen.findByRole('heading', { name: 'Email confirmed' })).toBeInTheDocument()
    expect(screen.getByText('Your email address has been confirmed.')).toBeInTheDocument()
  })

  it('says explicitly that confirming did not sign anybody in', async () => {
    activateMock.mockResolvedValue({
      success: true,
      message: 'Your email address has been confirmed.',
      field: null,
      user: null,
    })
    renderAt('a-real-token')

    clickConfirm()

    // A confirmation link must never be a way to sign in, and the user should
    // not have to guess whether it signed them in.
    expect(await screen.findByText(/not signed in by this/i)).toBeInTheDocument()
  })

  it('offers no access token and no session after confirming', async () => {
    activateMock.mockResolvedValue({
      success: true,
      message: 'Your email address has been confirmed.',
      field: null,
      user: null,
    })
    renderAt('a-real-token')

    clickConfirm()
    await screen.findByRole('heading', { name: 'Email confirmed' })

    // The only way onward is the ordinary sign-in page: this component has no
    // session state of its own to inherit.
    expect(screen.getByRole('link', { name: 'Sign In' })).toHaveAttribute('href', '/auth')
  })

  it('falls back to the resend form when the backend refuses the token', async () => {
    activateMock.mockResolvedValue({
      success: false,
      message: 'This confirmation link is invalid or has expired.',
      field: 'token',
      user: null,
    })
    renderAt('a-stale-token')

    clickConfirm()

    // Deliberately not distinguishing *why*: the backend refuses to say, and
    // repeating that here keeps a forwarded link from learning anything.
    expect(await screen.findByRole('heading', { name: 'Link invalid' })).toBeInTheDocument()
    // One message covering every reason, matching the backend's own wording
    // rather than narrowing it. What must *not* appear is any copy that names
    // a cause — "this link was already used" would tell somebody holding a
    // forwarded link exactly how far it got.
    expect(
      screen.getByText(/This confirmation link is invalid or has expired\./),
    ).toBeInTheDocument()
    expect(screen.queryByText(/already been used/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/no longer valid/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/does not exist/i)).not.toBeInTheDocument()
  })

  it('does not claim the link is bad when the server could not be reached', async () => {
    activateMock.mockRejectedValue(new Error('Failed to fetch'))
    renderAt('a-real-token')

    clickConfirm()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    // The request never reached a decision about the token.
    expect(screen.queryByRole('heading', { name: 'Link invalid' })).not.toBeInTheDocument()
  })

  it('sends a new link for the submitted address', async () => {
    resendMock.mockResolvedValue({
      success: true,
      message: GENERIC_MESSAGE,
      field: null,
      user: null,
    })
    renderAt(null)

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send a new link' }))

    await waitFor(() => expect(resendMock).toHaveBeenCalledWith('ada@example.com'))
  })

  it("shows the backend's generic resend message verbatim", async () => {
    resendMock.mockResolvedValue({
      success: true,
      message: GENERIC_MESSAGE,
      field: null,
      user: null,
    })
    renderAt(null)

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'nobody@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send a new link' }))

    // Identical for an address that needs confirming and one that does not, so
    // this page cannot be used to find out whether somebody is registered.
    expect(await screen.findByRole('heading', { name: 'Check your email' })).toBeInTheDocument()
    expect(screen.getByText(GENERIC_MESSAGE)).toBeInTheDocument()
  })

  it('validates the resend address before calling the backend', () => {
    renderAt(null)

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'not-an-email' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send a new link' }))

    expect(screen.getByText('Enter a valid email address.')).toBeInTheDocument()
    expect(resendMock).not.toHaveBeenCalled()
  })

  it('reports a resend refusal about the address', async () => {
    resendMock.mockResolvedValue({
      success: false,
      message: 'Enter a valid email address.',
      field: 'email',
      user: null,
    })
    renderAt(null)

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send a new link' }))

    expect(await screen.findByText('Enter a valid email address.')).toBeInTheDocument()
  })
})
