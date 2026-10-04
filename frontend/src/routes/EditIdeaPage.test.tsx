import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../features/identity/auth/AuthContext'
import { CLASSIFY_STEP, goToStep, titleField } from '../test/ideaForm'
import { makeIdea } from '../test/idea'
import { EditIdeaPage } from './EditIdeaPage'

vi.mock('../features/ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  ideaRequest: vi.fn(),
  categoriesRequest: vi.fn(async () => [
    { id: '4', name: 'Finance', slug: 'finance', description: '' },
  ]),
  updateIdeaRequest: vi.fn(),
  createIdeaRequest: vi.fn(),
}))
vi.mock('../features/reviews/api/reviewsApi', () => ({ ideaReviewsRequest: vi.fn() }))
vi.mock('../features/identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))

const { ideaRequest, updateIdeaRequest, createIdeaRequest } =
  await import('../features/ideas/api/ideasApi')
const { ideaReviewsRequest } = await import('../features/reviews/api/reviewsApi')
const loadMock = vi.mocked(ideaRequest)
const updateMock = vi.mocked(updateIdeaRequest)

const FEEDBACK = 'Add the monthly volume.'

/**
 * The draft this page edits: an organization idea with one answered story
 * question, so a test can tell the form's own values from the empty answers
 * `EMPTY_PROBLEM_STORY` would leave everywhere else.
 */
function loadedIdea(overrides: Parameters<typeof makeIdea>[0] = {}) {
  return makeIdea({
    currentProcess: 'Invoices arrive by email.',
    // Organization-visible, so the confirmation this page builds has something
    // to say about who can read it.
    visibility: 'ORGANIZATION',
    ...overrides,
  })
}

function IdeasStub() {
  const state = useLocation().state as { notice?: string } | null
  return <p>Ideas list: {state?.notice ?? 'no notice'}</p>
}

function renderAt(path = '/app/ideas/1/edit') {
  const router = createMemoryRouter(
    [
      { path: '/app/ideas', element: <IdeasStub /> },
      { path: '/app/ideas/:ideaId/edit', element: <EditIdeaPage /> },
    ],
    { initialEntries: ['/app/ideas', path], initialIndex: 1 },
  )
  render(<RouterProvider router={router} />)
  return router
}

describe('EditIdeaPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useAuth).mockReturnValue({ user: { id: '7' } } as unknown as ReturnType<
      typeof useAuth
    >)
  })

  it('loads the idea by its id and opens it in the guided form', async () => {
    loadMock.mockResolvedValue(loadedIdea())

    renderAt()

    expect(await screen.findByRole('heading', { name: 'Edit this draft' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Edit your draft.' })).toBeInTheDocument()
    expect(loadMock).toHaveBeenCalledWith('1')
    expect(titleField()).toHaveValue('Automate the invoice run')
    expect(screen.getByText('Step 1 of 8')).toBeInTheDocument()
    goToStep('How does this happen today?')
    expect(screen.getByLabelText('How do you currently handle this?')).toHaveValue(
      'Invoices arrive by email.',
    )
  })

  it('saves through updateIdea and returns to the list with a confirmation', async () => {
    loadMock.mockResolvedValue(loadedIdea())
    // The confirmation is built from the idea the *server* returned, so the
    // saved idea is what decides whether the page says who can read it.
    updateMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: loadedIdea({ title: 'A sharper title' }),
    })
    const router = renderAt()

    fireEvent.change(await screen.findByRole('textbox', { name: /What problem/ }), {
      target: { value: 'A sharper title' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith(
        '1',
        expect.objectContaining({
          title: 'A sharper title',
          currentProcess: 'Invoices arrive by email.',
        }),
      ),
    )
    expect(
      await screen.findByText('Ideas list: Draft saved. Members of this organization can read it.'),
    ).toBeInTheDocument()
    expect(router.state.historyAction).toBe('REPLACE')
    expect(vi.mocked(createIdeaRequest)).not.toHaveBeenCalled()
  })

  it('revises a sent-back idea with the feedback above it and visibility fixed', async () => {
    loadMock.mockResolvedValue(
      makeIdea({ status: 'CHANGES_REQUESTED', submittedAt: '2026-01-02T00:00:00.000Z' }),
    )
    vi.mocked(ideaReviewsRequest).mockResolvedValue([
      {
        id: '11',
        ideaId: '1',
        scope: 'PLATFORM',
        round: 1,
        reviewerId: '9',
        decision: 'CHANGES_REQUESTED',
        feedback: FEEDBACK,
        assessments: [],
        createdAt: '2026-01-03T00:00:00.000Z',
        completedAt: '2026-01-04T00:00:00.000Z',
        submissionSnapshot: null,
      },
    ])
    updateMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: makeIdea() })
    renderAt()

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Revise your idea.' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Revise this idea' })).toBeInTheDocument()
    const feedback = screen.getByRole('region', { name: 'Reviewer feedback' })
    expect(await within(feedback).findByText(FEEDBACK)).toBeInTheDocument()
    goToStep(CLASSIFY_STEP)
    const visibility = screen.getByRole('group', { name: 'Who should be able to see this?' })
    for (const radio of within(visibility).getAllByRole('radio')) {
      expect(radio).toBeDisabled()
    }
    // Only who can see it is fixed: the story itself is still the author's to revise.
    goToStep('Tell us about the impact')
    expect(screen.getByRole('radio', { name: 'Daily' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1))
    expect(updateMock.mock.calls[0][0]).toBe('1')
  })

  it.each([
    ['somebody else’s idea', makeIdea({ authorId: '99' })],
    ['a submitted idea', makeIdea({ status: 'SUBMITTED', submittedAt: '2026-01-02T00:00:00Z' })],
  ])('offers no form for %s', async (_, subject) => {
    loadMock.mockResolvedValue(subject)

    renderAt()

    expect(
      await screen.findByText(/Only your own draft, or an idea a reviewer sent back/),
    ).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /What problem/ })).toBeNull()
    expect(screen.getByRole('link', { name: 'Back to your ideas' })).toHaveAttribute(
      'href',
      '/app/ideas',
    )
  })

  it('says so when the idea cannot be read', async () => {
    loadMock.mockResolvedValue(null)

    renderAt('/app/ideas/404/edit')

    expect(await screen.findByText('This idea is not available.')).toBeInTheDocument()
    expect(loadMock).toHaveBeenCalledWith('404')
  })

  it('reports a failed load as a failure', async () => {
    loadMock.mockRejectedValue(new Error('offline'))

    renderAt()

    expect(await screen.findByRole('alert')).toHaveTextContent('We could not load this idea.')
  })

  it('returns to the list without saving on cancel', async () => {
    loadMock.mockResolvedValue(makeIdea())
    const router = renderAt()

    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))

    expect(await screen.findByText('Ideas list: no notice')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/app/ideas')
    expect(updateMock).not.toHaveBeenCalled()
  })
})
