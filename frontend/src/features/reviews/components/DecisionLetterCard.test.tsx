import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { DecisionLetterCard } from './DecisionLetterCard'

vi.mock('../api/decisionLettersApi', () => ({ ideaDecisionLetterRequest: vi.fn() }))
const { ideaDecisionLetterRequest } = await import('../api/decisionLettersApi')
const letterMock = vi.mocked(ideaDecisionLetterRequest)

describe('DecisionLetterCard', () => {
  it('celebrates an approval, with the letter as sent and what happens next', async () => {
    letterMock.mockResolvedValue({
      decision: 'approved',
      message: 'Dear Amina,\n\nCongratulations!',
      sentAt: '2026-10-08T09:00:00Z',
    })
    render(<DecisionLetterCard ideaId="4" />)

    expect(
      await screen.findByRole('heading', { name: 'Congratulations! Your idea has been approved' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/Dear Amina/)).toBeInTheDocument()
    expect(screen.getByText(/What happens next/)).toBeInTheDocument()
  })

  it('delivers a rejection as a letter, without the celebration', async () => {
    letterMock.mockResolvedValue({
      decision: 'rejected',
      message: 'Dear Amina,\n\nThank you for submitting your idea.',
      sentAt: '2026-10-08T09:00:00Z',
    })
    render(<DecisionLetterCard ideaId="4" />)

    expect(
      await screen.findByRole('heading', { name: 'A letter about your idea' }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/What happens next/)).toBeNull()
  })

  it('draws nothing when no letter has been sent', async () => {
    letterMock.mockResolvedValue(null)
    const { container } = render(<DecisionLetterCard ideaId="4" />)

    await vi.waitFor(() => expect(letterMock).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
