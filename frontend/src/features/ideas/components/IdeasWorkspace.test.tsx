import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeasWorkspace } from './IdeasWorkspace'
import type { IdeaMutationResult } from '../api/ideasApi'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(async () => []),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  submitIdeaRequest: vi.fn(),
  organizationIdeasRequest: vi.fn(),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))

const { createIdeaRequest, organizationIdeasRequest, submitIdeaRequest } =
  await import('../api/ideasApi')
const createMock = vi.mocked(createIdeaRequest)
const listMock = vi.mocked(organizationIdeasRequest)
const submitMock = vi.mocked(submitIdeaRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }

const DRAFT: Awaited<ReturnType<typeof organizationIdeasRequest>>[number] = {
  id: '1',
  title: 'Automate the invoice run',
  description: 'A description long enough.',
  status: 'DRAFT',
  visibility: 'PRIVATE',
  submittedAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  authorId: '7',
  organizationId: '3',
  category: null,
}

const SOMEONE_ELSES: typeof DRAFT = {
  ...DRAFT,
  id: '2',
  title: 'Their idea',
  authorId: '9',
  visibility: 'ORGANIZATION',
}

function mockContext(
  ideas: (typeof DRAFT)[] = [DRAFT],
  activeOrganization: unknown = { id: '3', name: 'Acme Labs' },
) {
  vi.mocked(useAuth).mockReturnValue({
    user: SIGNED_IN,
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization,
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(ideas)
}

describe('IdeasWorkspace', () => {
  beforeEach(() => {
    mockContext()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // --- the list -----------------------------------------------------------

  it('reads the active organization feed, not a list the client filtered', async () => {
    render(<IdeasWorkspace />)

    // The tenant and visibility rules are the server's. This component asks
    // for one organization's ideas and renders whatever it is given.
    await waitFor(() => expect(listMock).toHaveBeenCalledWith('3'))
  })

  it('shows a loading state before the ideas arrive', () => {
    render(<IdeasWorkspace />)

    expect(screen.getByText('Loading ideas…')).toBeInTheDocument()
  })

  it('renders the ideas it is given', async () => {
    mockContext([DRAFT, SOMEONE_ELSES])
    render(<IdeasWorkspace />)

    expect(await screen.findByText('Automate the invoice run')).toBeInTheDocument()
    expect(screen.getByText('Their idea')).toBeInTheDocument()
  })

  it('shows an empty state when the organization has no ideas', async () => {
    mockContext([])
    render(<IdeasWorkspace />)

    expect(await screen.findByText('No ideas here yet')).toBeInTheDocument()
  })

  it('shows an empty state, not an error, when there is no organization', async () => {
    mockContext([], null)
    render(<IdeasWorkspace />)

    expect(await screen.findByText('No organization selected')).toBeInTheDocument()
  })

  it('reports a transport failure as itself rather than as an empty list', async () => {
    // An empty list would read as "this organization has no ideas", which is a
    // different claim from "we could not ask".
    listMock.mockRejectedValue(new Error('Failed to fetch'))
    render(<IdeasWorkspace />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
  })

  it('labels a draft and a submitted idea differently', async () => {
    mockContext([
      DRAFT,
      { ...SOMEONE_ELSES, status: 'SUBMITTED', submittedAt: '2026-02-01T00:00:00.000Z' },
    ])
    render(<IdeasWorkspace />)

    expect(await screen.findByText('Draft')).toBeInTheDocument()
    expect(screen.getByText('Submitted')).toBeInTheDocument()
  })

  it('shows who each idea is shared with', async () => {
    mockContext([{ ...DRAFT, visibility: 'ORGANIZATION' }])
    render(<IdeasWorkspace />)

    expect(await screen.findByText('This organization')).toBeInTheDocument()
  })

  // --- what is offered ----------------------------------------------------

  it('offers edit and submit only on the signed-in user own draft', async () => {
    mockContext([DRAFT, SOMEONE_ELSES])
    render(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    // One of each: the other idea belongs to somebody else, and offering to
    // edit it would be offering something the server would refuse.
    expect(screen.getAllByRole('button', { name: 'Edit draft' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Submit for review' })).toHaveLength(1)
  })

  it('offers no edit or submit on somebody elses idea', async () => {
    mockContext([SOMEONE_ELSES])
    render(<IdeasWorkspace />)

    await screen.findByText('Their idea')
    expect(screen.queryByRole('button', { name: 'Edit draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Submit for review' })).not.toBeInTheDocument()
  })

  it('offers no edit or submit once an idea is submitted', async () => {
    mockContext([{ ...DRAFT, status: 'SUBMITTED', submittedAt: '2026-02-01T00:00:00.000Z' }])
    render(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: 'Edit draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Submit for review' })).not.toBeInTheDocument()
  })

  // --- creating -----------------------------------------------------------

  it('opens the form when asked to file a new idea', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: 'File a new idea' }))

    expect(await screen.findByRole('heading', { name: 'File a new idea' })).toBeInTheDocument()
  })

  it('files a draft and reloads the list', async () => {
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')
    fireEvent.click(screen.getByRole('button', { name: 'File a new idea' }))

    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'A new idea' } })
    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'A description long enough.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() => expect(createMock).toHaveBeenCalledWith('3', expect.anything()))
    expect(await screen.findByText(/Draft saved/)).toBeInTheDocument()
    // The list is re-read, because a new idea is not in the previous answer.
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThan(1))
  })

  // --- editing ------------------------------------------------------------

  it('opens the edit form with the draft in it', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))

    expect(await screen.findByRole('heading', { name: 'Edit this draft' })).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toHaveValue('Automate the invoice run')
  })

  it('saves an edit and closes the form', async () => {
    const { updateIdeaRequest } = await import('../api/ideasApi')
    vi.mocked(updateIdeaRequest).mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: { ...DRAFT, title: 'A sharper title' },
    })
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')
    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))

    fireEvent.change(await screen.findByLabelText('Title'), {
      target: { value: 'A sharper title' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(vi.mocked(updateIdeaRequest)).toHaveBeenCalledWith(
        '1',
        expect.objectContaining({ title: 'A sharper title' }),
      ),
    )
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Edit this draft' })).not.toBeInTheDocument(),
    )
  })

  // --- submitting ---------------------------------------------------------

  it('submits a draft and says it can no longer be edited', async () => {
    submitMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: { ...DRAFT, status: 'SUBMITTED', submittedAt: '2026-02-01T00:00:00.000Z' },
    })
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    expect(await screen.findByText(/Submitted for review/)).toBeInTheDocument()
    expect(submitMock).toHaveBeenCalledWith('1')
  })

  it('shows a business refusal from a submission as the backend worded it', async () => {
    submitMock.mockResolvedValue({
      success: false,
      message: 'Choose a category before submitting this idea.',
      field: null,
      idea: null,
    })
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Choose a category before submitting this idea.')
  })

  it('reports a submission transport failure without claiming it went through', async () => {
    submitMock.mockRejectedValue(new Error('Failed to fetch'))
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    expect(screen.queryByText(/Submitted for review/)).not.toBeInTheDocument()
  })

  it('spins only on the idea being submitted', async () => {
    let release: (value: IdeaMutationResult) => void = () => {}
    submitMock.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    mockContext([DRAFT, SOMEONE_ELSES])
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Submit for review' })).toBeDisabled(),
    )
    release({ success: true, message: 'ok', field: null, idea: DRAFT })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Submit for review' })).not.toBeDisabled(),
    )
  })
})
