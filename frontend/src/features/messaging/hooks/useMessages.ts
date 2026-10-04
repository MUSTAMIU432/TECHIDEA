import { useCallback, useEffect, useState } from 'react'

import {
  messageThreadsRequest,
  postMessageRequest,
  threadMessagesRequest,
  unreadMessageThreadsRequest,
  type Message,
  type MessageThreadSummary,
} from '../api/messagingApi'

/**
 * The number of conversations with unread messages, for the header badge.
 *
 * **Its own request, once per mount** — the same bargain
 * `useUnreadNotifications` strikes for the notifications bell, and for the same
 * two reasons. The badge is in the app chrome, so it renders on every
 * authenticated page; asking again on each render would be a request per page
 * the reader visits. And the count is asked of the server rather than counted
 * from a list this app does not hold, which is the only way it stays right when
 * the messages page has never been opened in this session.
 *
 * A badge is not worth an error panel, so a failed read is zero: the header
 * stays usable and the messages page is where a failure is reported properly.
 */
export function useUnreadMessageThreads(): number {
  const [count, setCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    unreadMessageThreadsRequest()
      .then((badge) => {
        if (!cancelled) setCount(badge)
      })
      .catch(() => {
        if (!cancelled) setCount(0)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return count
}

/**
 * The caller's conversations, with the unread count from the same response.
 *
 * One request for both, because the list shows each row's unread count and the
 * header's total; a second query for the total would be a round trip for a
 * number already in the response.
 *
 * A post or a read splices into the list rather than re-reading it — the same
 * append/remove shape `useComments` and `useAttachments` use on an idea card,
 * and for the same reason: the reader keeps their place.
 *
 * `loading` is derived by comparing the query during render rather than written
 * inside the effect — the discipline `useMyTeams` uses.
 */
export function useMessageThreads() {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    token: number
    threads: MessageThreadSummary[]
    unread: number
  } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  const key = `threads:${token}`

  useEffect(() => {
    let cancelled = false
    messageThreadsRequest()
      .then((result) => {
        if (cancelled) return
        setAnswer({ token, threads: result.threads, unread: result.unreadThreadCount })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setErrorKey(key)
      })
    return () => {
      cancelled = true
    }
    // `key` carries the token, so it *is* the query; `token` is named too
    // because the answer records which token produced it.
  }, [key, token])

  const reload = useCallback(() => setToken((value) => value + 1), [])

  const fresh = answer !== null && answer.token === token
  const failed = errorKey === key

  return {
    threads: answer?.threads ?? [],
    unreadThreadCount: fresh ? (answer?.unread ?? 0) : 0,
    loading: !fresh && !failed,
    error: failed ? 'We could not load your conversations. Please try again.' : null,
    reload,
  }
}

/**
 * One conversation's messages, oldest first.
 *
 * `null` for "no thread is open" rather than an empty list, because an empty
 * list is also what a thread nobody can read answers with — and this hook is
 * mounted per page, so the difference between "not open" and "open and empty"
 * has to be representable. It is: a list is empty only once the server has
 * answered.
 */
export function useThreadMessages(threadId: string | null) {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    threadId: string | null
    token: number
    messages: Message[]
  } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  // One thread's messages must never be rendered under another's heading.
  if (answer !== null && answer.threadId !== threadId) setAnswer(null)

  const key = `${threadId ?? 'none'}:${token}`

  useEffect(() => {
    if (threadId === null) return
    let cancelled = false
    threadMessagesRequest(threadId)
      .then((page) => {
        if (cancelled) return
        setAnswer({ threadId, token, messages: page.messages })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setErrorKey(key)
      })
    return () => {
      cancelled = true
    }
    // `key` carries both the thread and the token, so it *is* the query;
    // `token` is named too because the answer records which one produced it.
  }, [key, threadId, token])

  const reload = useCallback(() => setToken((value) => value + 1), [])

  /**
   * Post a reply and splice it onto the end.
   *
   * Returns the server's own `(success, message)` rather than throwing, so the
   * composer can keep the words in the box on a refusal — the same shape every
   * other mutation in this app reports a business failure with.
   */
  const post = useCallback(
    async (body: string) => {
      if (threadId === null) return { success: false, message: 'No conversation is open.' }
      const result = await postMessageRequest(threadId, body)
      const sent = result.sentMessage
      if (result.success && sent !== null) {
        setAnswer((current) =>
          current === null ? current : { ...current, messages: [...current.messages, sent] },
        )
      }
      return { success: result.success, message: result.message }
    },
    [threadId],
  )

  const fresh = answer !== null && answer.token === token
  const failed = errorKey === key

  return {
    messages: answer?.messages ?? [],
    loading: threadId !== null && !fresh && !failed,
    error: failed ? 'We could not load this conversation. Please try again.' : null,
    reload,
    post,
  }
}
