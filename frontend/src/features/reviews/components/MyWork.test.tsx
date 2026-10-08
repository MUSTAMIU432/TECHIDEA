import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import type { WorkItem } from '../api/workApi'
import { MyWork } from './MyWork'

vi.mock('../api/workApi', () => ({ reviewerWorkRequest: vi.fn() }))
const { reviewerWorkRequest } = await import('../api/workApi')

const STEPS = ['Review', 'Decision', 'Proposal', 'Platform admin', 'Owner', 'Delivery']

function item(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    ideaId: '4',
    title: 'Tax collection',
    ownerLabel: 'Owner Test',
    stage: 'writing',
    stageLabel: 'Proposal being written',
    step: 2,
    needsMe: true,
    actionLabel: 'Continue the proposal',
    actionPath: '/app/reviews/proposals/4',
    teamName: 'Team1',
    isLead: true,
    updatedAt: '2026-10-08T09:00:00Z',
    ...overrides,
  }
}

function renderWork() {
  render(
    <MemoryRouter>
      <MyWork />
    </MemoryRouter>,
  )
}

describe('MyWork', () => {
  it('shows what needs the reviewer, how far each idea has come, and where to act', async () => {
    vi.mocked(reviewerWorkRequest).mockResolvedValue({
      steps: STEPS,
      items: [
        item(),
        item({
          ideaId: '5',
          title: 'Hostel payments',
          stage: 'with_admin',
          stageLabel: 'Proposal with the platform admin',
          step: 3,
          needsMe: false,
          actionLabel: null,
        }),
      ],
    })
    renderWork()

    expect(await screen.findByRole('heading', { name: '1 idea needs you' })).toBeInTheDocument()
    expect(
      within(screen.getByRole('list', { name: 'Ideas you are handling' })).getAllByText(/Proposal/)
        .length,
    ).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'Continue the proposal' })).toHaveAttribute(
      'href',
      '/app/reviews/proposals/4',
    )
    expect(screen.getAllByRole('link', { name: 'View' })).toHaveLength(1)
  })

  it('narrows to what needs the reviewer', async () => {
    vi.mocked(reviewerWorkRequest).mockResolvedValue({
      steps: STEPS,
      items: [item(), item({ ideaId: '5', title: 'Hostel payments', needsMe: false })],
    })
    renderWork()

    fireEvent.click(await screen.findByRole('button', { name: 'Needs me (1)' }))

    expect(screen.getByText('Tax collection')).toBeInTheDocument()
    expect(screen.queryByText('Hostel payments')).toBeNull()
  })

  it('says so when nothing has been routed yet', async () => {
    vi.mocked(reviewerWorkRequest).mockResolvedValue({ steps: STEPS, items: [] })
    renderWork()

    expect(
      await screen.findByText('No ideas have been routed to your review teams yet.'),
    ).toBeInTheDocument()
  })
})
