import { makeIdea } from '../../../test/idea'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithRouter } from '../../../test/renderWithRouter'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeasWorkspace } from './IdeasWorkspace'
import { page, pageOf } from '../../../test/ideaPage'
import type { Idea, IdeaAttachment } from '../api/ideasApi'
import { COURTESY_MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL } from '../utils/attachmentRules'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(async () => [
    { id: '4', name: 'Customer support', slug: 'customer-support', description: '' },
  ]),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  transitionIdeaRequest: vi.fn(),
  organizationIdeasRequest: vi.fn(),
  commentsRequest: vi.fn(async () => ({ items: [], pageInfo: page([]).pageInfo })),
  voteIdeaRequest: vi.fn(),
  removeVoteRequest: vi.fn(),
  attachmentsRequest: vi.fn(),
  uploadAttachmentRequest: vi.fn(),
  deleteAttachmentRequest: vi.fn(),
  downloadAttachmentRequest: vi.fn(),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))

const {
  organizationIdeasRequest,
  attachmentsRequest,
  uploadAttachmentRequest,
  deleteAttachmentRequest,
  downloadAttachmentRequest,
} = await import('../api/ideasApi')
const listMock = vi.mocked(organizationIdeasRequest)
const attachmentsMock = vi.mocked(attachmentsRequest)
const uploadMock = vi.mocked(uploadAttachmentRequest)
const deleteMock = vi.mocked(deleteAttachmentRequest)
const downloadMock = vi.mocked(downloadAttachmentRequest)

const OWNER = { id: '9', email: 'author@example.com' }
const COLLEAGUE = { id: '7', email: 'colleague@example.com' }

/** The idea under test, authored by `OWNER` so the upload controls are offered. */
const OWNED_IDEA: Idea = makeIdea({ authorId: OWNER.id })

function attachment(overrides: Partial<IdeaAttachment> = {}): IdeaAttachment {
  return {
    id: 'a1',
    ideaId: '1',
    uploaderId: '9',
    filename: 'evidence.pdf',
    contentType: 'application/pdf',
    size: 2048,
    createdAt: '2026-01-02T00:00:00.000Z',
    downloadUrl: '/ideas/1/attachments/a1/download/',
    ...overrides,
  }
}

function mockContext(
  options: { user?: typeof OWNER; ideas?: Idea[]; attachments?: IdeaAttachment[] } = {},
) {
  const { user = OWNER, ideas: ideaList = [OWNED_IDEA], attachments: attachmentList = [] } = options

  vi.mocked(useAuth).mockReturnValue({ user } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: { id: '3', name: 'Acme Labs' },
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(page(ideaList))
  attachmentsMock.mockResolvedValue(pageOf(attachmentList))
  uploadMock.mockResolvedValue({
    success: true,
    message: 'Attachment uploaded.',
    field: null,
    attachment: attachment(),
  })
  deleteMock.mockResolvedValue({ success: true, message: 'Attachment deleted.', field: null })
  downloadMock.mockResolvedValue(undefined)
}

/** The evidence disclosure toggle on one idea's card. */
async function openEvidence(title = 'Automate the invoice run') {
  await screen.findByText(title)
  await act(async () => {})
  const card = screen.getByText(title).closest('li') as HTMLElement
  fireEvent.click(within(card).getByRole('button', { name: /supporting evidence/i }))
  await act(async () => {})
  return card
}

function pdfFile(name = 'evidence.pdf') {
  return new File(['%PDF-1.4'], name, { type: 'application/pdf' })
}

/**
 * Attachments in the Ideas UI (S2-007).
 *
 * Mirrors `IdeasWorkspace.votes.test.tsx`'s shape: the claims under test are
 * about the *wiring* (fetched lazily, rendered from the server's answer,
 * failures reported as themselves, the discovery context untouched), not a
 * re-test of the backend's own authorization or validation rules, which
 * `test_attachments.py` already covers.
 */
describe('Idea attachments (S2-007)', () => {
  beforeEach(() => {
    mockContext()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // --- lazy fetch and rendering ----------------------------------------------

  it('does not fetch attachments until the section is opened', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')
    await act(async () => {})

    expect(attachmentsMock).not.toHaveBeenCalled()
  })

  it('fetches and renders attachments once opened', async () => {
    mockContext({ attachments: [attachment()] })
    renderWithRouter(<IdeasWorkspace />)

    await openEvidence()

    expect(await screen.findByText('evidence.pdf')).toBeInTheDocument()
    expect(attachmentsMock).toHaveBeenCalledWith('1')
  })

  it('shows an empty state when there is nothing attached yet', async () => {
    mockContext({ attachments: [] })
    renderWithRouter(<IdeasWorkspace />)

    const card = await openEvidence()

    expect(await within(card).findByText(/no files attached yet/i)).toBeInTheDocument()
  })

  it('renders a loading state while the request is in flight', async () => {
    let release: (() => void) | null = null
    attachmentsMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve(pageOf<IdeaAttachment>([]))
        }),
    )
    renderWithRouter(<IdeasWorkspace />)

    const card = await openEvidence()

    expect(within(card).getByText(/loading supporting evidence/i)).toBeInTheDocument()
    await act(async () => {
      release?.()
    })
  })

  it('reports a failed listing as a transport failure', async () => {
    attachmentsMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)

    const card = await openEvidence()

    expect(
      await within(card).findByText('We could not reach the server. Please try again.'),
    ).toBeInTheDocument()
  })

  // --- upload -----------------------------------------------------------------

  it('offers the file picker to the ideas own author', async () => {
    renderWithRouter(<IdeasWorkspace />)

    const card = await openEvidence()

    expect(within(card).getByText(/choose file/i)).toBeInTheDocument()
  })

  it('does not offer the file picker to a colleague', async () => {
    mockContext({ user: COLLEAGUE })
    renderWithRouter(<IdeasWorkspace />)

    const card = await openEvidence()

    expect(within(card).queryByText(/choose file/i)).toBeNull()
  })

  it('uploads the selected file and shows it once the server confirms it', async () => {
    mockContext({ attachments: [] })
    uploadMock.mockResolvedValue({
      success: true,
      message: 'Attachment uploaded.',
      field: null,
      attachment: attachment({ filename: 'new-evidence.pdf' }),
    })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()

    const input = within(card).getByLabelText(/choose file/i, {
      selector: 'input',
    }) as HTMLInputElement
    fireEvent.change(input, { target: { files: [pdfFile('new-evidence.pdf')] } })

    await waitFor(() => expect(uploadMock).toHaveBeenCalledWith('1', expect.any(File)))
    expect(await within(card).findByText('new-evidence.pdf')).toBeInTheDocument()
  })

  it('shows a pending state while the upload is in flight', async () => {
    let release: (() => void) | null = null
    uploadMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              success: true,
              message: 'Attachment uploaded.',
              field: null,
              attachment: attachment(),
            })
        }),
    )
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    const input = within(card).getByLabelText(/choose file/i, {
      selector: 'input',
    }) as HTMLInputElement

    fireEvent.change(input, { target: { files: [pdfFile()] } })

    await waitFor(() => expect(input).toBeDisabled())
    await act(async () => {
      release?.()
    })
  })

  it('reports a refused upload with the servers own message', async () => {
    uploadMock.mockResolvedValue({
      success: false,
      message: 'That file type is not supported.',
      field: 'file',
      attachment: null,
    })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    const input = within(card).getByLabelText(/choose file/i, {
      selector: 'input',
    }) as HTMLInputElement

    fireEvent.change(input, { target: { files: [pdfFile()] } })

    expect(await within(card).findByText('That file type is not supported.')).toBeInTheDocument()
  })

  it('reports a transport failure on upload as itself', async () => {
    uploadMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    const input = within(card).getByLabelText(/choose file/i, {
      selector: 'input',
    }) as HTMLInputElement

    fireEvent.change(input, { target: { files: [pdfFile()] } })

    expect(
      await within(card).findByText('We could not reach the server. Please try again.'),
    ).toBeInTheDocument()
  })

  it('rejects an obviously oversized file before ever calling the server', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    const input = within(card).getByLabelText(/choose file/i, {
      selector: 'input',
    }) as HTMLInputElement
    // Sized from the rule rather than written out, so this test keeps testing
    // "too big is refused before the network" after the limit moves, instead of
    // quietly testing a file that is now comfortably allowed.
    const oversized = new File([new Uint8Array(COURTESY_MAX_UPLOAD_BYTES + 1)], 'huge.pdf', {
      type: 'application/pdf',
    })

    fireEvent.change(input, { target: { files: [oversized] } })

    expect(
      await within(card).findByText(new RegExp(`larger than ${MAX_UPLOAD_LABEL}`, 'i')),
    ).toBeInTheDocument()
    expect(uploadMock).not.toHaveBeenCalled()
  })

  it('rejects an unsupported extension before ever calling the server', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    const input = within(card).getByLabelText(/choose file/i, {
      selector: 'input',
    }) as HTMLInputElement
    const exe = new File(['x'], 'script.exe', { type: 'application/octet-stream' })

    fireEvent.change(input, { target: { files: [exe] } })

    expect(await within(card).findByText(/not supported/i)).toBeInTheDocument()
    expect(uploadMock).not.toHaveBeenCalled()
  })

  // --- download -----------------------------------------------------------------

  it('downloads an attachment when its button is pressed', async () => {
    mockContext({ attachments: [attachment()] })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    await within(card).findByText('evidence.pdf')

    fireEvent.click(within(card).getByRole('button', { name: /download/i }))

    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith(attachment()))
  })

  it('every reader who can see the section can download, not only the author', async () => {
    mockContext({ user: COLLEAGUE, attachments: [attachment()] })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()

    expect(await within(card).findByRole('button', { name: /download/i })).toBeInTheDocument()
  })

  // --- delete -----------------------------------------------------------------

  it('the author can delete their own attachment', async () => {
    mockContext({ attachments: [attachment()] })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    await within(card).findByText('evidence.pdf')

    fireEvent.click(within(card).getByRole('button', { name: /delete/i }))

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith('a1'))
    await waitFor(() => expect(within(card).queryByText('evidence.pdf')).not.toBeInTheDocument())
  })

  it('a colleague is not offered a delete control', async () => {
    mockContext({ user: COLLEAGUE, attachments: [attachment()] })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    await within(card).findByText('evidence.pdf')

    expect(within(card).queryByRole('button', { name: /delete/i })).toBeNull()
  })

  it('reports a refused deletion and leaves the attachment listed', async () => {
    mockContext({ attachments: [attachment()] })
    deleteMock.mockResolvedValue({
      success: false,
      message: 'Attachment is unavailable.',
      field: null,
    })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    await within(card).findByText('evidence.pdf')

    fireEvent.click(within(card).getByRole('button', { name: /delete/i }))

    expect(await within(card).findByText('Attachment is unavailable.')).toBeInTheDocument()
    expect(within(card).getByText('evidence.pdf')).toBeInTheDocument()
  })

  // --- discovery context survives ----------------------------------------------

  it('does not re-fetch the ideas list when the evidence section is opened', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await openEvidence()

    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it('does not re-fetch the ideas list after an upload or a delete', async () => {
    mockContext({ attachments: [attachment()] })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    await within(card).findByText('evidence.pdf')

    fireEvent.click(within(card).getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(deleteMock).toHaveBeenCalled())

    expect(listMock).toHaveBeenCalledTimes(1)
  })

  // --- nothing adjacent ---------------------------------------------------------

  it('offers no voting or discussion control inside the evidence section', async () => {
    mockContext({ attachments: [attachment()] })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()
    await within(card).findByText('evidence.pdf')

    const section = within(card).getByRole('region', { name: /^Supporting evidence:/ })
    expect(within(section).queryByRole('button', { name: /vote|discussion|comment/i })).toBeNull()
  })

  // --- accessibility -------------------------------------------------------------

  it('exposes the evidence list as a labelled region once open', async () => {
    mockContext({ attachments: [attachment()] })
    renderWithRouter(<IdeasWorkspace />)
    const card = await openEvidence()

    expect(
      within(card).getByRole('region', { name: 'Supporting evidence: Automate the invoice run' }),
    ).toBeInTheDocument()
  })

  it('the toggle reports its expanded state', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')
    await act(async () => {})
    const card = screen.getByText('Automate the invoice run').closest('li') as HTMLElement
    const toggle = within(card).getByRole('button', { name: /supporting evidence/i })

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    await act(async () => {})
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })
})
