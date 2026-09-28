import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { page } from '../../../test/ideaPage'
import type { Idea } from '../api/ideasApi'
import { IdeasWorkspace } from './IdeasWorkspace'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(async () => [
    { id: '4', name: 'Finance', slug: 'finance', description: '' },
  ]),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  submitIdeaRequest: vi.fn(),
  transitionIdeaRequest: vi.fn(),
  organizationIdeasRequest: vi.fn(),
}))
vi.mock('../../reviews/api/reviewsApi', () => ({
  ideaReviewsRequest: vi.fn(),
  startReviewRequest: vi.fn(),
  completeReviewRequest: vi.fn(),
}))
vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))

const { organizationIdeasRequest, submitIdeaRequest, transitionIdeaRequest, updateIdeaRequest } =
  await import('../api/ideasApi')
const { ideaReviewsRequest, startReviewRequest } = await import('../../reviews/api/reviewsApi')
const listMock = vi.mocked(organizationIdeasRequest)
const submitMock = vi.mocked(submitIdeaRequest)
const transitionMock = vi.mocked(transitionIdeaRequest)
const updateMock = vi.mocked(updateIdeaRequest)
const historyMock = vi.mocked(ideaReviewsRequest)

const AUTHOR = { id: '7', email: 'author@acme.example' }
const REVIEWER = { id: '9', email: 'reviewer@acme.example' }
const FEEDBACK = 'Add the monthly volume.'

function idea(overrides: Partial<Idea> = {}): Idea {
  return {
    id: '1',
    title: 'Automate the invoice run',
    description: 'We key every invoice in by hand, every month.',
    status: 'CHANGES_REQUESTED',
    visibility: 'ORGANIZATION',
    submittedAt: '2026-01-02T00:00:00.000Z',
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    authorId: '7',
    organizationId: '3',
    category: { id: '4', name: 'Finance', slug: 'finance', description: '' },
    availableTransitions: ['SUBMITTED'],
    discussionOpen: true,
    voteCount: 0,
    viewerHasVoted: false,
    viewerCanStartReview: false,
    viewerActiveReviewId: null,
    ...overrides,
  }
}

function mockContext(viewer: { id: string; email: string }, subject: Idea) {
  vi.mocked(useAuth).mockReturnValue({ user: viewer } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: { id: '3', name: 'Acme' },
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(page([subject]))
}

function renderWorkspace() {
  return render(
    <MemoryRouter>
      <IdeasWorkspace />
    </MemoryRouter>,
  )
}

/**
 * Changes requested and resubmission (S3-005), through the existing
 * workspace: the card offers the author a revision and a resubmission, the
 * existing form is reused with the reviewer's feedback above it, and the
 * resubmission is the existing `submitIdea`. Nothing here creates a review.
 */
describe('IdeasWorkspace — changes requested', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    historyMock.mockResolvedValue([
      {
        id: '11',
        ideaId: '1',
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
  })

  it('offers the author a revision and a resubmission', async () => {
    mockContext(AUTHOR, idea())

    renderWorkspace()

    expect(await screen.findByRole('button', { name: 'Revise idea' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Submit again' })).toBeInTheDocument()
  })

  it('offers nobody else the author’s controls', async () => {
    mockContext(REVIEWER, idea({ availableTransitions: [] }))

    renderWorkspace()

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: 'Revise idea' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Submit again' })).not.toBeInTheDocument()
  })

  it('reuses the idea form, with the feedback above it and visibility fixed', async () => {
    mockContext(AUTHOR, idea())

    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: 'Revise idea' }))

    expect(screen.getByRole('heading', { name: 'Revise this idea' })).toBeInTheDocument()
    const feedback = screen.getByRole('region', { name: 'Reviewer feedback' })
    expect(await within(feedback).findByText(FEEDBACK)).toBeInTheDocument()
    for (const radio of screen.getAllByRole('radio')) {
      expect(radio).toBeDisabled()
    }
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument()
  })

  it('saves the revision through the existing updateIdea and refreshes', async () => {
    mockContext(AUTHOR, idea())
    updateMock.mockResolvedValue({
      success: true,
      message: 'Idea saved.',
      field: null,
      idea: idea({ description: 'We key 1,200 invoices in by hand every month.' }),
    })

    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: 'Revise idea' }))
    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'We key 1,200 invoices in by hand every month.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1))
    expect(updateMock.mock.calls[0][0]).toBe('1')
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThan(1))
  })

  it('resubmits through the existing submitIdea, refreshes, and creates no review', async () => {
    mockContext(AUTHOR, idea())
    submitMock.mockResolvedValue({
      success: true,
      message: 'Idea submitted for review.',
      field: null,
      idea: idea({ status: 'SUBMITTED', availableTransitions: [] }),
    })

    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: 'Submit again' }))

    expect(await screen.findByText('Idea submitted for review.')).toBeInTheDocument()
    expect(submitMock).toHaveBeenCalledWith('1')
    expect(transitionMock).not.toHaveBeenCalled()
    expect(startReviewRequest).not.toHaveBeenCalled()
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThan(1))
  })

  it('shows a refused resubmission as the server wrote it', async () => {
    mockContext(AUTHOR, idea())
    submitMock.mockResolvedValue({
      success: false,
      message: 'Describe the problem in at least 20 characters before submitting.',
      field: null,
      idea: null,
    })

    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: 'Submit again' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('at least 20 characters')
  })

  it('keeps the review feedback visible on the card', async () => {
    mockContext(AUTHOR, idea())

    renderWorkspace()
    fireEvent.click(await screen.findByRole('button', { name: 'Review history' }))

    expect(await screen.findByText(FEEDBACK)).toBeInTheDocument()
  })
})
