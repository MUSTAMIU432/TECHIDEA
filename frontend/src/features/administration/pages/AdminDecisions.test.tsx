import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { READ_ONLY_ADMIN, renderAdminPage } from '../../../test/renderAdmin'
import type { DecisionLetter } from '../../reviews/api/decisionLettersApi'
import { AdminDecisionsPage } from './AdminDecisionsPage'

vi.mock('../../reviews/api/decisionLettersApi')
const api = await import('../../reviews/api/decisionLettersApi')

function letter(overrides: Partial<DecisionLetter> = {}): DecisionLetter {
  return {
    id: '1',
    decision: 'approved',
    status: 'pending',
    message: 'Dear Amina,\n\nCongratulations!',
    createdAt: '2026-10-08T09:00:00Z',
    sentAt: null,
    sentByName: null,
    ideaId: '4',
    ideaTitle: 'Tax collection',
    ownerName: 'Amina Owner',
    ownerEmail: 'amina@example.com',
    reviewerName: 'Helper Test',
    reviewRound: 2,
    reviewFeedback: 'Clear problem, strong evidence.',
    ...overrides,
  }
}

beforeEach(() => {
  vi.mocked(api.decisionLettersRequest).mockResolvedValue([
    letter(),
    letter({
      id: '2',
      decision: 'rejected',
      status: 'sent',
      ideaTitle: 'Hostel payments',
      sentAt: '2026-10-07T09:00:00Z',
      sentByName: 'Admin Person',
    }),
  ])
})

describe('AdminDecisionsPage', () => {
  it('shows what is waiting, with the team’s feedback and the drafted letter', async () => {
    renderAdminPage(<AdminDecisionsPage />)

    expect(await screen.findByText('Waiting to be sent (1)')).toBeInTheDocument()
    expect(screen.getByText('Clear problem, strong evidence.')).toBeInTheDocument()
    expect(screen.getByLabelText(/Letter to the owner/)).toHaveValue(
      'Dear Amina,\n\nCongratulations!',
    )
    expect(screen.getByText('Sent (1)')).toBeInTheDocument()
    expect(screen.getByText(/Sent to Amina Owner/)).toHaveTextContent('by Admin Person')
  })

  it('sends the letter as edited', async () => {
    vi.mocked(api.sendDecisionLetterRequest).mockResolvedValue({
      success: true,
      message: 'The letter was sent to the owner.',
      field: null,
    })
    renderAdminPage(<AdminDecisionsPage />)

    const box = await screen.findByLabelText(/Letter to the owner/)
    fireEvent.change(box, { target: { value: 'Dear Amina,\n\nWell done!' } })
    expect(screen.getByRole('button', { name: 'Restore the template' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Send to owner and open the proposal' }))

    await waitFor(() =>
      expect(api.sendDecisionLetterRequest).toHaveBeenCalledWith('1', 'Dear Amina,\n\nWell done!'),
    )
    expect(await screen.findByText('The letter was sent to the owner.')).toBeInTheDocument()
  })

  it('shows a refusal in the server’s own words', async () => {
    vi.mocked(api.sendDecisionLetterRequest).mockResolvedValue({
      success: false,
      message: 'You decided this review, so somebody else has to send its letter.',
      field: null,
    })
    renderAdminPage(<AdminDecisionsPage />)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Send to owner and open the proposal' }),
    )

    expect(
      await screen.findByText('You decided this review, so somebody else has to send its letter.'),
    ).toBeInTheDocument()
  })

  it('opens a sent letter to read it', async () => {
    renderAdminPage(<AdminDecisionsPage />)

    const sent = (await screen.findByText(/Sent to Amina Owner/)).closest('li') as HTMLElement
    fireEvent.click(within(sent).getByRole('button', { name: 'Read letter' }))

    expect(within(sent).getByText(/Congratulations!/)).toBeInTheDocument()
  })

  it('offers no send button without the permission', async () => {
    renderAdminPage(<AdminDecisionsPage />, { capabilities: READ_ONLY_ADMIN })

    await screen.findByText('Waiting to be sent (1)')
    expect(screen.queryByRole('button', { name: /Send to owner/ })).toBeNull()
  })
})
