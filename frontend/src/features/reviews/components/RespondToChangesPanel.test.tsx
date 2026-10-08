import { makeIdea } from '../../../test/idea'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Review } from '../api/reviewsApi'
import { RespondToChangesPanel } from './RespondToChangesPanel'

vi.mock('../api/reviewsApi', () => ({ ideaReviewsRequest: vi.fn() }))
vi.mock('../api/changeResponsesApi', () => ({ respondToChangesRequest: vi.fn() }))
const { ideaReviewsRequest } = await import('../api/reviewsApi')
const { respondToChangesRequest } = await import('../api/changeResponsesApi')

const ASKED: Review = {
  id: '9',
  ideaId: '4',
  scope: 'PLATFORM',
  round: 1,
  reviewerId: '8',
  decision: 'CHANGES_REQUESTED',
  feedback: 'Please attach the collection forms.',
  assessments: [
    { criterion: 'EVIDENCE', rating: 'DOES_NOT_MEET', note: 'No forms attached' },
    { criterion: 'FEASIBILITY', rating: 'MEETS', note: '' },
  ],
  createdAt: '2026-10-08T08:00:00Z',
  completedAt: '2026-10-08T09:00:00Z',
  submissionSnapshot: null,
}

function renderPanel(overrides = {}, onResponded = vi.fn()) {
  render(
    <MemoryRouter>
      <RespondToChangesPanel
        idea={makeIdea({ id: '4', authorId: '7', status: 'CHANGES_REQUESTED', ...overrides })}
        viewerId="7"
        onResponded={onResponded}
      />
    </MemoryRouter>,
  )
  return onResponded
}

beforeEach(() => {
  vi.mocked(ideaReviewsRequest).mockResolvedValue([ASKED])
  vi.mocked(respondToChangesRequest).mockReset()
})

describe('RespondToChangesPanel', () => {
  it('shows what was asked, and only the criteria that fell short', async () => {
    renderPanel()

    expect(await screen.findByText('Please attach the collection forms.')).toBeInTheDocument()
    const criteria = screen.getByRole('list', { name: 'Criteria to improve' })
    expect(criteria).toHaveTextContent('Evidence: Does not meet - No forms attached')
    expect(criteria).not.toHaveTextContent('Feasibility')
  })

  it('sends the response and resubmits in one step', async () => {
    vi.mocked(respondToChangesRequest).mockResolvedValue({
      success: true,
      message: 'Your response was sent and the idea is back with the reviewers.',
      field: null,
      ideaStatus: 'SUBMITTED',
    })
    const onResponded = renderPanel()

    const send = await screen.findByRole('button', { name: 'Send response and resubmit' })
    expect(send).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Tell the reviewers what you changed/), {
      target: { value: 'Attached the March forms.' },
    })
    fireEvent.click(send)

    await waitFor(() =>
      expect(respondToChangesRequest).toHaveBeenCalledWith('4', 'Attached the March forms.'),
    )
    await waitFor(() =>
      expect(onResponded).toHaveBeenCalledWith(
        'Your response was sent and the idea is back with the reviewers.',
      ),
    )
  })

  it('keeps the text and shows the refusal in the server’s words', async () => {
    vi.mocked(respondToChangesRequest).mockResolvedValue({
      success: false,
      message: 'Add a category before you submit.',
      field: null,
      ideaStatus: null,
    })
    renderPanel()

    fireEvent.change(await screen.findByLabelText(/Tell the reviewers what you changed/), {
      target: { value: 'Done.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send response and resubmit' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Add a category before you submit.')
    expect(screen.getByLabelText(/Tell the reviewers what you changed/)).toHaveValue('Done.')
  })

  it('is drawn only for the author, and only while changes are requested', () => {
    const { container } = render(
      <MemoryRouter>
        <RespondToChangesPanel
          idea={makeIdea({ id: '4', authorId: '7', status: 'UNDER_REVIEW' })}
          viewerId="7"
          onResponded={vi.fn()}
        />
        <RespondToChangesPanel
          idea={makeIdea({ id: '4', authorId: '7', status: 'CHANGES_REQUESTED' })}
          viewerId="8"
          onResponded={vi.fn()}
        />
      </MemoryRouter>,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
