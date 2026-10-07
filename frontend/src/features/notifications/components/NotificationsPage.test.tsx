import { fireEvent, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { render } from '@testing-library/react'
import type { Notification } from '../api/notificationsApi'
import { NotificationsPage } from './NotificationsPage'

vi.mock('../api/notificationsApi', () => ({
  notificationsRequest: vi.fn(),
  unreadNotificationsRequest: vi.fn(),
  markNotificationReadRequest: vi.fn(),
  markAllNotificationsReadRequest: vi.fn(),
  hasReportLink: (kind: string) => kind.startsWith('idea.'),
}))

const {
  notificationsRequest: listMock,
  unreadNotificationsRequest: badgeMock,
  markNotificationReadRequest: markOneMock,
  markAllNotificationsReadRequest: markAllMock,
} = await import('../api/notificationsApi')

function notification(overrides: Partial<Notification> = {}): Notification {
  return {
    id: 'n1',
    kind: 'idea.platform_approved',
    // The server spells the kind and builds the destination; a fixture that
    // disagreed with it would be testing a client the product does not have.
    label: 'Platform review completed',
    title: 'Your idea was approved by the platform',
    body: 'Read the report and decide whether to give the go-ahead.',
    ideaId: '1',
    reportId: '9',
    isRead: false,
    actionPath: '/app/ideas/1',
    createdAt: '2026-02-01T00:00:00.000Z',
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <NotificationsPage />
    </MemoryRouter>,
  )
}

describe('NotificationsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(listMock).mockResolvedValue({ items: [notification()], totalCount: 1 })
    vi.mocked(badgeMock).mockResolvedValue({ count: 1, hasUnread: true })
  })

  it('lists what the server sent, with its own wording', async () => {
    renderPage()

    expect(await screen.findByText('Your idea was approved by the platform')).toBeInTheDocument()
    expect(
      screen.getByText('Read the report and decide whether to give the go-ahead.'),
    ).toBeInTheDocument()
  })

  it('offers the report link on a decision', async () => {
    renderPage()

    expect(await screen.findByRole('link', { name: 'Read the review report' })).toHaveAttribute(
      'href',
      '/app/ideas/1/report',
    )
  })

  it('offers no report link on a queue nudge, even when the row carries one', async () => {
    // A nudge says "somebody submitted something"; pointing it at an approval
    // report would be nonsense. The kind decides, not the presence of an id.
    vi.mocked(listMock).mockResolvedValue({
      items: [notification({ kind: 'review.platform_queue', reportId: '9' })],
      totalCount: 1,
    })
    renderPage()

    await screen.findByText('Your idea was approved by the platform')
    expect(screen.queryByRole('link', { name: 'Read the review report' })).toBeNull()
    // The idea itself is still linked: that is a link into the app, not content.
    expect(screen.getByRole('link', { name: 'Open the idea' })).toHaveAttribute(
      'href',
      '/app/ideas/1',
    )
  })

  it('marks one read and moves the badge with it, without re-reading the list', async () => {
    vi.mocked(markOneMock).mockResolvedValue({
      success: true,
      message: 'Marked as read.',
      notification: notification({ isRead: true }),
    })
    renderPage()
    await screen.findByText('Your idea was approved by the platform')

    fireEvent.click(screen.getByRole('button', { name: 'Mark as read' }))

    await waitFor(() => expect(markOneMock).toHaveBeenCalledWith('n1'))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Mark as read' })).not.toBeInTheDocument(),
    )
    // One request for the whole page, plus the badge. Re-reading a capped list
    // to change one flag would be a request whose effect the reader cannot see.
    expect(listMock).toHaveBeenCalledOnce()
  })

  it('does not re-request the list when marking one read', async () => {
    // The server's answer comes back already read, so it cannot distinguish a
    // first press from a second; the badge must not drift below the truth.
    vi.mocked(markOneMock).mockResolvedValue({
      success: true,
      message: 'Marked as read.',
      notification: notification({ isRead: true }),
    })
    renderPage()
    await screen.findByText('Your idea was approved by the platform')

    fireEvent.click(screen.getByRole('button', { name: 'Mark as read' }))
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Mark as read' })).not.toBeInTheDocument(),
    )

    expect(markOneMock).toHaveBeenCalledOnce()
  })

  it('marks everything read in one request', async () => {
    vi.mocked(markAllMock).mockResolvedValue({ success: true, message: 'ok', notification: null })
    renderPage()
    await screen.findByText('Your idea was approved by the platform')

    fireEvent.click(screen.getByRole('button', { name: 'Mark all as read' }))

    await waitFor(() => expect(markAllMock).toHaveBeenCalledOnce())
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Mark all as read' })).not.toBeInTheDocument(),
    )
  })

  it('reports a failed load as itself, and offers a way to try again', async () => {
    vi.mocked(listMock).mockRejectedValue(new Error('offline'))
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not load your notifications.',
    )
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('says an empty list is not a failure', async () => {
    vi.mocked(listMock).mockResolvedValue({ items: [], totalCount: 0 })
    vi.mocked(badgeMock).mockResolvedValue({ count: 0, hasUnread: false })
    renderPage()

    expect(await screen.findByText('Nothing to report yet.')).toBeInTheDocument()
  })
})
