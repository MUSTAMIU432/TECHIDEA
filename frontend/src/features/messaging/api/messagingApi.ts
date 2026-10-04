/**
 * Private messages between named people - **not** comments on an idea.
 *
 * The distinction is the whole reason this module exists, and it is structural
 * rather than a flag: access comes from the participant list on a thread, so
 * somebody who can read an idea cannot see that two of its readers are talking
 * about it. Every read here goes through `messageThread`, and there is
 * deliberately no "messages for idea X" query - who may see a message is a
 * question about who was invited into the conversation, not about the idea.
 *
 * One row per conversation carries the unread state *for this viewer*, so the
 * list and the badge are one request rather than a list plus a count.
 */

import { graphqlClient } from '../../../graphql/client'

export interface MessageAuthor {
  id: string
  email: string
  firstName: string
  lastName: string
}

export interface Message {
  id: string
  threadId: string
  senderId: string
  senderFirstName: string
  body: string
  createdAt: string
}

export interface MessageThread {
  id: string
  /** Empty for a direct message; the idea's title when it is anchored to one. */
  subject: string
  ideaId: string | null
  ideaTitle: string
  startedById: string
  participants: MessageAuthor[]
  latestMessageAt: string
}

export interface MessageThreadSummary {
  thread: MessageThread
  otherParticipants: MessageAuthor[]
  latestMessage: Message | null
  unreadCount: number
  hasUnread: boolean
}

export interface MessagePage {
  messages: Message[]
  total: number
}

export interface MessagePayload {
  success: boolean
  message: string
  field: string | null
  thread: MessageThread | null
  sentMessage: Message | null
}

export interface StartMessageThreadInput {
  recipientIds: string[]
  body: string
  ideaId?: string | null
  subject?: string
}

const USER_FIELDS = `
  id
  email
  firstName
  lastName
`

const MESSAGE_FIELDS = `
  id
  threadId
  senderId
  senderFirstName
  body
  createdAt
`

const THREAD_FIELDS = `
  id
  subject
  ideaId
  ideaTitle
  startedById
  latestMessageAt
  participants { ${USER_FIELDS} }
`

const MESSAGE_THREADS_QUERY = `
  query MessageThreads {
    messageThreads {
      thread { ${THREAD_FIELDS} }
      otherParticipants { ${USER_FIELDS} }
      latestMessage { ${MESSAGE_FIELDS} }
      unreadCount
      hasUnread
    }
    unreadThreadCount
  }
`

const THREAD_MESSAGES_QUERY = `
  query ThreadMessages($threadId: ID!, $limit: Int) {
    threadMessages(threadId: $threadId, limit: $limit) {
      messages { ${MESSAGE_FIELDS} }
      total
    }
  }
`

const START_THREAD_MUTATION = `
  mutation StartMessageThread($input: StartMessageThreadInput!) {
    startMessageThread(input: $input) {
      success
      message
      field
      thread { ${THREAD_FIELDS} }
      sentMessage { ${MESSAGE_FIELDS} }
    }
  }
`

const POST_MESSAGE_MUTATION = `
  mutation PostMessage($input: PostMessageInput!) {
    postMessage(input: $input) {
      success
      message
      field
      thread { ${THREAD_FIELDS} }
      sentMessage { ${MESSAGE_FIELDS} }
    }
  }
`

const MARK_THREAD_READ_MUTATION = `
  mutation MarkThreadRead($threadId: ID!) {
    markThreadRead(threadId: $threadId) { success message field }
  }
`

const UNREAD_THREAD_COUNT_QUERY = `
  query UnreadThreadCount {
    unreadThreadCount
  }
`

/**
 * Every conversation the caller is in, newest activity first, with the badge
 * count from the same response.
 *
 * One request for both, because this list shows the count on each row and a
 * second query for the total would be a second round trip for a number already
 * in hand.
 */
export async function messageThreadsRequest(): Promise<{
  threads: MessageThreadSummary[]
  unreadThreadCount: number
}> {
  const data = await graphqlClient.request<{
    messageThreads: MessageThreadSummary[]
    unreadThreadCount: number
  }>(MESSAGE_THREADS_QUERY)
  return { threads: data.messageThreads, unreadThreadCount: data.unreadThreadCount }
}

/**
 * How many of the caller's conversations have unread messages.
 *
 * Its own one-field query, and that is the whole reason it is not read out of
 * the list above: the badge sits in the app chrome, on every authenticated
 * page, while that list is on one of them. Reading the count from the list would
 * mean asking for every thread on every page the reader visits, so the badge
 * asks the server for the number alone.
 */
export async function unreadMessageThreadsRequest(): Promise<number> {
  const data = await graphqlClient.request<{ unreadThreadCount: number }>(UNREAD_THREAD_COUNT_QUERY)
  return data.unreadThreadCount
}

/**
 * One conversation's messages, oldest first.
 *
 * Empty - not an error - for a thread the caller is not in and for one that does
 * not exist, so a thread id cannot be used to find out whether a conversation
 * exists.
 */
export async function threadMessagesRequest(threadId: string, limit = 200): Promise<MessagePage> {
  const data = await graphqlClient.request<{ threadMessages: MessagePage }>(THREAD_MESSAGES_QUERY, {
    threadId,
    limit,
  })
  return data.threadMessages
}

/**
 * Start a conversation, or continue the one already open with these people
 * about this idea.
 *
 * `ideaId` is refused for an idea the caller cannot read, so an idea cannot be
 * used as a way to reach people; and the *recipients* are not required to be
 * able to read it, because being told about the conversation directly is the
 * entire point of a private message.
 */
export async function startMessageThreadRequest(
  input: StartMessageThreadInput,
): Promise<MessagePayload> {
  const data = await graphqlClient.request<{ startMessageThread: MessagePayload }>(
    START_THREAD_MUTATION,
    {
      input: {
        recipientIds: input.recipientIds,
        body: input.body,
        ideaId: input.ideaId ?? null,
        subject: input.subject ?? '',
      },
    },
  )
  return data.startMessageThread
}

/** Reply in a conversation the caller is part of. */
export async function postMessageRequest(threadId: string, body: string): Promise<MessagePayload> {
  const data = await graphqlClient.request<{ postMessage: MessagePayload }>(POST_MESSAGE_MUTATION, {
    input: { threadId, body },
  })
  return data.postMessage
}

/**
 * Move the caller's own read position to the end of a thread.
 *
 * Personal and nobody else's: opening a conversation is what "read" means here,
 * and no other participant sees it.
 */
export async function markThreadReadRequest(threadId: string): Promise<MessagePayload> {
  const data = await graphqlClient.request<{ markThreadRead: MessagePayload }>(
    MARK_THREAD_READ_MUTATION,
    { threadId },
  )
  return data.markThreadRead
}

/** "Ada" for one participant, "Ada and 2 others" for several. */
export function participantSummary(others: MessageAuthor[]): string {
  const names = others.map((user) => user.firstName).filter(Boolean)
  if (names.length === 0) return 'Conversation'
  if (names.length === 1) return names[0]
  if (names.length === 2) return `${names[0]} and ${names[1]}`
  return `${names[0]} and ${names.length - 1} others`
}
