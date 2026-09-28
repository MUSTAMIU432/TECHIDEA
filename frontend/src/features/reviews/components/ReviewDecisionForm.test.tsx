import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReviewMutationResult } from '../api/reviewsApi'
import { ReviewDecisionForm } from './ReviewDecisionForm'

vi.mock('../api/reviewsApi', () => ({ completeReviewRequest: vi.fn() }))

const { completeReviewRequest } = await import('../api/reviewsApi')
const completeMock = vi.mocked(completeReviewRequest)

const CRITERIA = [
  'Problem clarity',
  'Automation suitability',
  'Feasibility',
  'Expected benefit',
  'Evidence',
]

function renderForm() {
  const onCompleted = vi.fn()
  render(<ReviewDecisionForm ideaId="1" reviewId="12" onCompleted={onCompleted} />)
  return onCompleted
}

function rateAll(rating = 'MEETS') {
  for (const label of CRITERIA) {
    fireEvent.change(screen.getByLabelText(label), { target: { value: rating } })
  }
}

const decide = (label: string) => fireEvent.click(screen.getByRole('radio', { name: label }))
const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Record decision' }))

const SUCCESS: ReviewMutationResult = {
  success: true,
  message: 'Review completed: Approved.',
  field: null,
  review: null,
  idea: null,
}

/**
 * The decision form (S3-004). Its checks mirror the server's so a reviewer is
 * told what is missing before a round trip; the server's own refusal is shown
 * as written, and a success is handed up unchanged.
 */
describe('ReviewDecisionForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    completeMock.mockResolvedValue(SUCCESS)
  })

  it('renders all five criteria, each with a rating and a note', () => {
    renderForm()

    for (const label of CRITERIA) {
      expect(screen.getByLabelText(label)).toBeInTheDocument()
      expect(screen.getByLabelText(`Note on ${label.toLowerCase()} (optional)`)).toBeInTheDocument()
    }
    expect(screen.getAllByRole('option', { name: 'Not applicable' })).toHaveLength(5)
  })

  it('requires every criterion to be rated', () => {
    renderForm()
    decide('Approved')

    submit()

    expect(screen.getByRole('alert')).toHaveTextContent('Rate every criterion')
    expect(completeMock).not.toHaveBeenCalled()
  })

  it('requires a decision', () => {
    renderForm()
    rateAll()

    submit()

    expect(screen.getByRole('alert')).toHaveTextContent('Choose a decision')
  })

  it.each(['Changes requested', 'Rejected'])('requires feedback for %s', (label) => {
    renderForm()
    rateAll()
    decide(label)

    submit()

    expect(screen.getByRole('alert')).toHaveTextContent('Explain this decision')
    expect(completeMock).not.toHaveBeenCalled()
  })

  it('sends every criterion, its note, the feedback and the decision', async () => {
    const onCompleted = renderForm()
    rateAll('PARTIALLY_MEETS')
    fireEvent.change(screen.getByLabelText('Note on evidence (optional)'), {
      target: { value: ' Needs volumes. ' },
    })
    fireEvent.change(screen.getByLabelText('Feedback to the author'), {
      target: { value: 'Add the monthly volume.' },
    })
    decide('Changes requested')

    submit()

    await waitFor(() => expect(onCompleted).toHaveBeenCalledWith(SUCCESS))
    const sent = completeMock.mock.calls[0][0]
    expect(sent).toMatchObject({
      ideaId: '1',
      reviewId: '12',
      decision: 'CHANGES_REQUESTED',
      feedback: 'Add the monthly volume.',
    })
    expect(sent.assessments).toHaveLength(5)
    expect(sent.assessments.find((a) => a.criterion === 'EVIDENCE')).toEqual({
      criterion: 'EVIDENCE',
      rating: 'PARTIALLY_MEETS',
      note: 'Needs volumes.',
    })
  })

  it('lets an approval go without feedback', async () => {
    const onCompleted = renderForm()
    rateAll('NOT_APPLICABLE')
    decide('Approved')

    submit()

    await waitFor(() => expect(onCompleted).toHaveBeenCalled())
  })

  it('shows the server’s refusal as written and keeps the form', async () => {
    completeMock.mockResolvedValue({
      success: false,
      message: 'This review has already been completed.',
      field: null,
      review: null,
      idea: null,
    })
    const onCompleted = renderForm()
    rateAll()
    decide('Approved')

    submit()

    expect(await screen.findByRole('alert')).toHaveTextContent('already been completed')
    expect(onCompleted).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Record decision' })).toBeEnabled()
  })

  it('shows a loading state while recording, then a transport failure as such', async () => {
    let reject: (error: Error) => void = () => {}
    completeMock.mockReturnValue(new Promise((_, r) => (reject = r)))
    renderForm()
    rateAll()
    decide('Approved')

    submit()

    expect(screen.getByRole('button', { name: 'Recording decision…' })).toBeDisabled()
    reject(new Error('offline'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Your review was not recorded')
  })
})
