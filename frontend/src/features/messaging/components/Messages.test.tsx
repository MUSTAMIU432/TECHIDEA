import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import type { MessageThreadSummary } from '../api/messagingApi'
import { MessageThreadPage } from './MessageThreadPage'
import { MessagesPage } from './MessagesPage'

vi.mock('../api/messagingApi', () => ({
  messageThreadsRequest: vi.fn(),
  threadMessagesRequest: vi.fn(),
  postMessageRequest: vi.fn(),
  markThreadReadRequest: vi.fn(async () => ({
    success: true,
    message: 'ok',
    field: null,
    thread: null,
    sentMessage: null,
  })),
  participantSummary: (others: Array<{ firstName: string }>) =>
    others.length === 0 ? 'Conversation' : others[0].firstName,
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))

const {
  messageThreadsRequest: threadsMock,
  threadMessagesRequest: messagesMock,
  postMessageRequest: postMock,
  markThreadReadRequest: markReadMock,
} = await import('../api/messagingApi')

const RAE = { id: '8', email: 'rae@example.com', firstName: 'Rae', lastName: 'B' }

function summary(overrides: Partial<MessageThreadSummary> = {}): MessageThreadSummary {
  return {
    thread: {
      id: 't1',
      subject: '',
      ideaId: '1',
      ideaTitle: 'Automate the invoice run',
      startedById: '7',
      participants: [{ id: '7', email: 'ada@example.com', firstName: 'Ada', lastName: 'A' }, RAE],
      latestMessageAt: '2026-02-01T00:00:00.000Z',
    },
    otherParticipants: [RAE],
    latestMessage: {
      id: 'm1',
      threadId: 't1',
      senderId: '8',
      senderFirstName: 'Rae',
      body: 'Can you send the monthly volume?',
      createdAt: '2026-02-01T00:00:00.000Z',
    },
    unreadCount: 2,
    hasUnread: true,
    ...overrides,
  }
}

function renderList() {
  return render(
    <RouterProvider
      router={createMemoryRouter([{ path: '/app/messages', element: <MessagesPage /> }], {
        initialEntries: ['/app/messages'],
      })}
    />,
  )
}

function renderThread(threadId = 't1') {
  return render(
    <RouterProvider
      router={createMemoryRouter(
        [{ path: '/app/messages/:threadId', element: <MessageThreadPage /> }],
        {
          initialEntries: [`/app/messages/${threadId}`],
        },
      )}
    />,
  )
}

describe('MessagesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useAuth).mockReturnValue({
      user: { id: '7', email: 'ada@example.com' },
    } as unknown as ReturnType<typeof useAuth>)
    vi.mocked(threadsMock).mockResolvedValue({ threads: [summary()], unreadThreadCount: 1 })
  })

  it('lists the conversations the server returned, with the unread count', async () => {
    renderList()

    expect(await screen.findByText('Rae')).toBeInTheDocument()
    expect(screen.getByText('Can you send the monthly volume?')).toBeInTheDocument()
    expect(screen.getByText('2 new')).toBeInTheDocument()
    // The idea it is anchored to is named: knowing what a conversation is about
    // is what makes a list of them navigable.
    expect(screen.getByText(/Automate the invoice run/)).toBeInTheDocument()
  })

  it('asks for the count in the same request as the list', async () => {
    renderList()
    await screen.findByText('Rae')

    expect(threadsMock).toHaveBeenCalledOnce()
  })

  it('says an empty list is not a failure', async () => {
    vi.mocked(threadsMock).mockResolvedValue({ threads: [], unreadThreadCount: 0 })
    renderList()

    expect(await screen.findByText('You are not in any conversations yet.')).toBeInTheDocument()
  })

  it('reports a failed load as itself, and offers a way to try again', async () => {
    vi.mocked(threadsMock).mockRejectedValue(new Error('offline'))
    renderList()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not load your conversations.',
    )
  })
})

describe('MessageThreadPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useAuth).mockReturnValue({
      user: { id: '7', email: 'ada@example.com' },
    } as unknown as ReturnType<typeof useAuth>)
    vi.mocked(threadsMock).mockResolvedValue({ threads: [summary()], unreadThreadCount: 0 })
    vi.mocked(messagesMock).mockResolvedValue({
      messages: [
        {
          id: 'm1',
          threadId: 't1',
          senderId: '8',
          senderFirstName: 'Rae',
          body: 'Can you send the monthly volume?',
          createdAt: '2026-02-01T00:00:00.000Z',
        },
      ],
      total: 1,
    })
  })

  it('shows the messages and who else is in the conversation', async () => {
    renderThread()

    expect(await screen.findByText('Can you send the monthly volume?')).toBeInTheDocument()
    expect(await screen.findByText(/^With$/)).toBeInTheDocument()
    expect(screen.getByText('Rae')).toBeInTheDocument()
  })

  it('marks the thread read on arrival, once', async () => {
    renderThread()
    await screen.findByText('Can you send the monthly volume?')

    // Opening a conversation is what "read" means here, so the position moves
    // without anybody pressing anything.
    await waitFor(() => expect(markReadMock).toHaveBeenCalledWith('t1'))
    expect(markReadMock).toHaveBeenCalledOnce()
  })

  it('posts a reply and shows it without re-reading the thread', async () => {
    vi.mocked(postMock).mockResolvedValue({
      success: true,
      message: 'Message sent.',
      field: null,
      thread: null,
      sentMessage: {
        id: 'm2',
        threadId: 't1',
        senderId: '7',
        senderFirstName: 'Ada',
        body: 'Here it is.',
        createdAt: '2026-02-01T00:01:00.000Z',
      },
    })
    renderThread()
    await screen.findByText('Can you send the monthly volume?')

    fireEvent.change(screen.getByLabelText('Write a message'), { target: { value: 'Here it is.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByText('Here it is.')).toBeInTheDocument()
    expect(postMock).toHaveBeenCalledWith('t1', 'Here it is.')
    // Spliced, not re-read: the reader keeps their place in the conversation.
    expect(messagesMock).toHaveBeenCalledOnce()
  })

  it('keeps the words in the box when the server refuses', async () => {
    vi.mocked(postMock).mockResolvedValue({
      success: false,
      message: 'You are no longer part of this conversation.',
      field: null,
      thread: null,
      sentMessage: null,
    })
    renderThread()
    await screen.findByText('Can you send the monthly volume?')

    fireEvent.change(screen.getByLabelText('Write a message'), { target: { value: 'Here it is.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('no longer part')
    expect(screen.getByLabelText('Write a message')).toHaveValue('Here it is.')
  })

  it('reports a transport failure without claiming the message was sent', async () => {
    vi.mocked(postMock).mockRejectedValue(new Error('Failed to fetch'))
    renderThread()
    await screen.findByText('Can you send the monthly volume?')

    fireEvent.change(screen.getByLabelText('Write a message'), { target: { value: 'Here it is.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('not sent')
  })

  it('reports a failed read as the thread being unavailable, with a way back', async () => {
    vi.mocked(messagesMock).mockRejectedValue(new Error('offline'))
    renderThread()

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'We could not load this conversation.',
    )
    expect(screen.getByRole('link', { name: 'Back to your conversations' })).toHaveAttribute(
      'href',
      '/app/messages',
    )
  })

  it('never sends an empty message', async () => {
    renderThread()
    await screen.findByText('Can you send the monthly volume?')

    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(postMock).not.toHaveBeenCalled()
  })
})
