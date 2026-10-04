import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useOrganization } from '../features/organizations/context/useOrganization'
import { NewIdeaPage } from './NewIdeaPage'
import {
  CLASSIFY_STEP,
  EVIDENCE_STEP,
  contextChoice,
  fillProblem,
  goToStep,
} from '../test/ideaForm'
import { makeIdea } from '../test/idea'

vi.mock('../features/ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(async () => []),
  createIdeaRequest: vi.fn(),
  submitIdeaRequest: vi.fn(),
  uploadAttachmentRequest: vi.fn(),
}))
vi.mock('../features/organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))

const { categoriesRequest, createIdeaRequest, submitIdeaRequest, uploadAttachmentRequest } =
  await import('../features/ideas/api/ideasApi')
const createMock = vi.mocked(createIdeaRequest)
const submitMock = vi.mocked(submitIdeaRequest)

const CREATED = makeIdea({
  id: '1',
  title: 'A new idea',
  description: 'A description long enough.',
  availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
})

/** Stands in for the ideas list: shows what the redirect handed it. */
function IdeasStub() {
  const state = useLocation().state as { notice?: string; tone?: string; ideaId?: string } | null
  return (
    <>
      <p>Ideas list: {state?.notice ?? 'no notice'}</p>
      {state?.tone && <p>tone: {state.tone}</p>}
      {state?.ideaId && <p>idea: {state.ideaId}</p>}
    </>
  )
}

function renderAt(path = '/app/ideas/new') {
  const router = createMemoryRouter(
    [
      { path: '/app/ideas', element: <IdeasStub /> },
      { path: '/app/ideas/new', element: <NewIdeaPage /> },
      { path: '/app', element: <p>Workspace</p> },
    ],
    { initialEntries: ['/app/ideas', path], initialIndex: 1 },
  )
  render(<RouterProvider router={router} />)
  return router
}

function mockOrganization(activeOrganization: unknown, status = 'ready') {
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization,
    status,
  } as unknown as ReturnType<typeof useOrganization>)
}

describe('NewIdeaPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockOrganization({ id: '3', name: 'Acme Labs' })
  })

  it('shows the create form for the active organization', async () => {
    renderAt()

    expect(
      screen.getByRole('heading', { level: 1, name: 'Tell us about a problem.' }),
    ).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Share a problem' })).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 8')).toBeInTheDocument()
  })

  it('redirects to the ideas list with a confirmation once saved', async () => {
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: CREATED })
    const router = renderAt()

    fillProblem()
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({ context: 'INDIVIDUAL' }, expect.anything()),
    )
    expect(
      await screen.findByText('Ideas list: Draft saved. Only you can see it.'),
    ).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/app/ideas')
    // Replaced, not pushed: Back must not reopen the form for a saved idea.
    expect(router.state.historyAction).toBe('REPLACE')
  })

  it('submits the idea and redirects to the list with a success alert', async () => {
    vi.mocked(categoriesRequest).mockResolvedValue([
      { id: '10', name: 'Finance', slug: 'finance', description: '' },
    ])
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: { ...CREATED, visibility: 'ORGANIZATION' },
    })
    submitMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: { ...CREATED, visibility: 'ORGANIZATION', status: 'SUBMITTED' },
    })
    const router = renderAt()

    fillProblem()
    goToStep(CLASSIFY_STEP)
    await screen.findByRole('option', { name: 'Finance' })
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: '10' } })
    fireEvent.click(contextChoice('My organization'))
    fireEvent.click(screen.getByRole('radio', { name: /My organization.*People in your/ }))
    goToStep(EVIDENCE_STEP)
    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    expect(
      await screen.findByText(
        'Ideas list: Your idea "A new idea" was created and submitted for review.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('tone: success')).toBeInTheDocument()
    expect(screen.getByText('idea: 1')).toBeInTheDocument()
    expect(submitMock).toHaveBeenCalledWith('1')
    expect(router.state.location.pathname).toBe('/app/ideas')
    expect(router.state.historyAction).toBe('REPLACE')
  })

  it('warns, naming the file, when a supporting document could not be attached', async () => {
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: CREATED })
    vi.mocked(uploadAttachmentRequest).mockResolvedValue({
      success: false,
      message: 'That file type is not supported.',
      field: 'file',
      attachment: null,
    })
    renderAt()

    fillProblem()
    goToStep(EVIDENCE_STEP)
    fireEvent.change(screen.getByLabelText('Choose files'), {
      target: { files: [new File(['%PDF'], 'form.pdf', { type: 'application/pdf' })] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    expect(
      await screen.findByText(
        "Ideas list: Draft saved. Only you can see it. This file could not be attached: form.pdf. You can add it from the idea's supporting evidence.",
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('tone: warning')).toBeInTheDocument()
  })

  it('stays on the page when the server refuses the idea', async () => {
    createMock.mockResolvedValue({
      success: false,
      message: 'Give the idea a title.',
      field: 'title',
      idea: null,
    })
    const router = renderAt()

    fillProblem()
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    expect(await screen.findByText('Give the idea a title.')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/app/ideas/new')
  })

  it('returns to the ideas list without saving on cancel', async () => {
    const router = renderAt()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(await screen.findByText('Ideas list: no notice')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/app/ideas')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('still offers the form with no organization, because an idea need not belong to one', () => {
    mockOrganization(null)
    renderAt()

    // An individual filing names no tenant at all, so "you need an organization"
    // would be false. What changes is the organization context, which is drawn
    // closed with its reason rather than omitted.
    expect(screen.getByRole('heading', { name: 'Share a problem' })).toBeInTheDocument()
    goToStep(CLASSIFY_STEP)
    expect(screen.getByText('You are not in an organization yet.')).toBeInTheDocument()
    expect(contextChoice('Just me')).toBeChecked()
  })
})
