import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import { AcceptInvitationPage } from './AcceptInvitationPage'

vi.mock('../api/invitationsApi', () => ({
  invitationDetailsRequest: vi.fn(),
  acceptInvitationRequest: vi.fn(),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))

const { invitationDetailsRequest: detailsMock, acceptInvitationRequest: acceptMock } =
  await import('../api/invitationsApi')

const TOKEN = 'raw-token-in-the-url'
const PREVIEW = {
  scope: 'ORGANIZATION' as const,
  tenantName: 'Acme Labs',
  roleName: 'Member',
  email: 'ada@example.com',
  invitedByFirstName: 'Rae',
  isOpen: true,
  expired: false,
  accepted: false,
  revoked: false,
}

/**
 * The page at `/invitations/accept?token=…`, which is the path
 * `invitations.email.INVITATION_PATH` builds and the only page in the app that
 * acts on a token without one having been typed.
 */
function renderAt(token: string | null = TOKEN) {
  return render(
    <MemoryRouter
      initialEntries={[`/invitations/accept${token === null ? '' : `?token=${token}`}`]}
    >
      <AcceptInvitationPage />
    </MemoryRouter>,
  )
}

function signedInAs(email: string | null) {
  vi.mocked(useAuth).mockReturnValue({
    user: email === null ? null : { id: '7', email },
  } as unknown as ReturnType<typeof useAuth>)
}

describe('AcceptInvitationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    signedInAs('ada@example.com')
    vi.mocked(detailsMock).mockResolvedValue(PREVIEW)
  })

  it('says who was invited, by whom, and to what', async () => {
    renderAt()

    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'You have been invited',
    )
    expect(screen.getByText(/Rae invited/)).toBeInTheDocument()
    expect(screen.getByText('Acme Labs')).toBeInTheDocument()
    expect(screen.getByText('Member')).toBeInTheDocument()
    // The token is read once and sent nowhere but the two requests that need it.
    expect(detailsMock).toHaveBeenCalledWith(TOKEN)
  })

  it('never renders a token, and never asks for anything but the preview', async () => {
    renderAt()
    await screen.findByText('Acme Labs')

    expect(document.body.textContent).not.toContain(TOKEN)
    expect(detailsMock).toHaveBeenCalledOnce()
  })

  it('offers signing in to somebody signed out, rather than accepting', async () => {
    signedInAs(null)
    renderAt()

    expect(await screen.findByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/auth')
    expect(screen.queryByRole('button', { name: /Join Acme Labs/ })).not.toBeInTheDocument()
    expect(acceptMock).not.toHaveBeenCalled()
  })

  it('says plainly when the invitation went to somebody else, and offers nothing', async () => {
    signedInAs('someone.else@example.com')
    renderAt()

    const warning = await screen.findByRole('alert')
    expect(warning).toHaveTextContent('sent to ada@example.com')
    expect(warning).toHaveTextContent('signed in as someone.else@example.com')
    // The server binds acceptance to the account's own email, so the button is
    // disabled rather than present-and-failing: there is nothing here that could
    // switch accounts.
    expect(screen.getByRole('button', { name: /Join Acme Labs/ })).toBeDisabled()
    expect(acceptMock).not.toHaveBeenCalled()
  })

  it('accepts and says so, in the server’s wording', async () => {
    vi.mocked(acceptMock).mockResolvedValue({
      success: true,
      message: 'You have joined Acme Labs.',
      field: null,
      invitation: null,
    })
    renderAt()

    fireEvent.click(await screen.findByRole('button', { name: /Join Acme Labs/ }))

    await waitFor(() => expect(acceptMock).toHaveBeenCalledWith(TOKEN))
    expect(await screen.findByText('You have joined Acme Labs.')).toBeInTheDocument()
  })

  it('shows a refusal as the server wrote it, and stays on the page', async () => {
    vi.mocked(acceptMock).mockResolvedValue({
      success: false,
      message: 'This invitation has expired.',
      field: null,
      invitation: null,
    })
    renderAt()

    fireEvent.click(await screen.findByRole('button', { name: /Join Acme Labs/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('This invitation has expired.')
  })

  it('reports a transport failure as itself, claiming nothing changed', async () => {
    vi.mocked(acceptMock).mockRejectedValue(new Error('Failed to fetch'))
    renderAt()

    fireEvent.click(await screen.findByRole('button', { name: /Join Acme Labs/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('nothing has changed')
  })

  it('says an unknown token is not valid, without confirming anything', async () => {
    vi.mocked(detailsMock).mockResolvedValue(null)
    renderAt()

    expect(await screen.findByRole('alert')).toHaveTextContent('not valid')
    expect(screen.queryByRole('button', { name: /Join/ })).not.toBeInTheDocument()
  })

  it('treats a link with no token as not valid, without asking the server', async () => {
    renderAt(null)

    expect(await screen.findByRole('alert')).toHaveTextContent('not valid')
    expect(detailsMock).not.toHaveBeenCalled()
  })

  // Each dead state asks for a different next step, so one "invalid" message
  // would waste the reader's time on all three. They are spelled out rather than
  // parametrized because the wording *is* the assertion.
  it('distinguishes an expired invitation from an invalid one', async () => {
    vi.mocked(detailsMock).mockResolvedValue({ ...PREVIEW, expired: true })

    renderAt()

    expect(await screen.findByText(/has expired/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Join Acme Labs/ })).not.toBeInTheDocument()
  })

  it('distinguishes a withdrawn invitation from an invalid one', async () => {
    vi.mocked(detailsMock).mockResolvedValue({ ...PREVIEW, revoked: true })

    renderAt()

    expect(await screen.findByText(/was withdrawn/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Join Acme Labs/ })).not.toBeInTheDocument()
  })

  it('distinguishes an already-accepted invitation from an invalid one', async () => {
    vi.mocked(detailsMock).mockResolvedValue({ ...PREVIEW, accepted: true })

    renderAt()

    expect(await screen.findByText(/already been accepted/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Join Acme Labs/ })).not.toBeInTheDocument()
  })
})
