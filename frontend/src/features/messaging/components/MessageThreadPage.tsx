import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  markThreadReadRequest,
  messageThreadsRequest,
  participantSummary,
} from '../api/messagingApi'
import { useThreadMessages } from '../hooks/useMessages'
import { formatDate } from '../../reviews/utils/reviewLabels'
import { fieldErrorClasses, inputClasses } from '../../identity/components/fieldStyles'
import { SpinnerIcon } from '../../identity/components/icons'
import { useAuth } from '../../identity/auth/AuthContext'

/**
 * One conversation at `/app/messages/:threadId`.
 *
 * **Reachable only by somebody in it.** `threadMessages` answers empty for a
 * thread the caller is not in *and* for one that does not exist, so this page
 * cannot become a way of finding out whether a conversation exists — it says the
 * thread is unavailable and offers the list, which is all the caller can read
 * anyway.
 *
 * Opening a conversation is what "read" means here, so this page marks it read
 * on arrival: a read position that only moves when somebody presses something is
 * a read position that lies about what they have seen.
 */
export function MessageThreadPage() {
  const { threadId = '' } = useParams()
  const { user } = useAuth()
  const { messages, loading, error, post } = useThreadMessages(threadId)
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  // A second submission in one tick is dropped by a ref, not by state: state
  // cannot be read synchronously from a click handler, and a double-posted
  // message is a duplicate somebody has to delete by hand.
  const inFlight = useRef(false)
  const lastRead = useRef<string | null>(null)

  /*
    Opening a conversation is what "read" means here, so the read position moves
    on arrival. Once per thread, guarded by a ref rather than by state: opening
    this page twice for the same thread should not send the request twice, and
    state cannot be read synchronously to make that decision.

    Declared before the effect that calls it, and as a named function so the
    effect can depend on it honestly rather than reading it mid-initialisation.
  */
  useEffect(() => {
    if (threadId === '' || lastRead.current === threadId) return
    lastRead.current = threadId
    let cancelled = false
    markThreadReadRequest(threadId).catch(() => {
      // A read position that could not be moved is not worth interrupting the
      // conversation for; it moves the next time the thread is opened.
      if (!cancelled) return
    })
    return () => {
      cancelled = true
    }
  }, [threadId])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (inFlight.current) return
    const trimmed = body.trim()
    if (!trimmed) return

    inFlight.current = true
    setSending(true)
    setRefusal(null)
    try {
      const result = await post(trimmed)
      if (result.success) {
        setBody('')
        return
      }
      // A refusal keeps the words in the box: the reader has not lost what they
      // wrote, and the message says why nothing was sent.
      setRefusal(result.message)
    } catch {
      setRefusal('We could not reach the server, so your message was not sent.')
    } finally {
      setSending(false)
      inFlight.current = false
    }
  }

  return (
    <section aria-labelledby="thread-heading" className="mt-8">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">Messages</p>
      <h1 id="thread-heading" className="mt-1 text-2xl font-bold tracking-tight text-gray-900">
        Conversation
      </h1>

      <div className="mt-5">
        {loading && messages.length === 0 ? (
          <p className="text-sm text-gray-600">Loading this conversation…</p>
        ) : error ? (
          <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-5">
            <p className="text-sm font-semibold text-red-800">Conversation unavailable</p>
            <p className="mt-1 text-sm text-red-700">{error}</p>
            <Link
              to="/app/messages"
              className="mt-4 inline-block text-sm font-semibold text-brand-700 hover:text-brand-800"
            >
              Back to your conversations
            </Link>
          </div>
        ) : (
          <>
            {/*
              The participant list is the access rule, shown: a reader can see
              exactly who else this conversation is with, which is the same fact
              the server authorizes on.
            */}
            <ThreadParticipants threadId={threadId} />

            <ul className="mt-4 space-y-3" aria-label="Messages">
              {messages.length === 0 ? (
                <li className="text-sm text-gray-600">No messages yet. Say something.</li>
              ) : (
                messages.map((message) => {
                  const mine = message.senderId === user?.id
                  return (
                    <li
                      key={message.id}
                      className={`flex flex-col ${mine ? 'items-end' : 'items-start'}`}
                    >
                      <span
                        className={`max-w-2xl rounded-2xl px-4 py-2.5 text-sm ${
                          mine
                            ? 'bg-brand-600 text-white'
                            : 'border border-gray-200 bg-white text-gray-800'
                        }`}
                      >
                        {message.body}
                      </span>
                      <span className="mt-1 text-xs text-gray-500">
                        {mine ? 'You' : message.senderFirstName} · {formatDate(message.createdAt)}
                      </span>
                    </li>
                  )
                })
              )}
            </ul>

            <form className="mt-6" onSubmit={handleSubmit} noValidate>
              <label className="sr-only" htmlFor="message-body">
                Write a message
              </label>
              <textarea
                id="message-body"
                className={`${inputClasses(Boolean(refusal), { multiline: true })} w-full resize-y`}
                rows={3}
                maxLength={5000}
                placeholder="Write a message"
                value={body}
                onChange={(event) => setBody(event.target.value)}
                disabled={sending}
                aria-invalid={refusal !== null}
              />
              {/* A refusal is said, and the words stay: the reader has not lost
                  what they wrote, and the message says why nothing was sent. */}
              {refusal ? (
                <p role="alert" className={fieldErrorClasses}>
                  {refusal}
                </p>
              ) : null}
              <button
                type="submit"
                disabled={sending || body.trim() === ''}
                className="mt-3 inline-flex h-11 items-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {sending && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
                {sending ? 'Sending…' : 'Send'}
              </button>
            </form>
          </>
        )}
      </div>
    </section>
  )
}

/**
 * Who else is in the conversation.
 *
 * The thread's own metadata, which the messages query does not return — so it is
 * a second read rather than a copy of something already in hand. Refuses to
 * render anything for a thread the caller cannot read, rather than showing an
 * empty "Conversation" heading that would read as "you are alone in here".
 */
function ThreadParticipants({ threadId }: { threadId: string }) {
  const [summary, setSummary] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    messageThreadsRequest()
      .then((answer) => {
        if (cancelled) return
        const found = answer.threads.find((entry) => entry.thread.id === threadId)
        setSummary(found === undefined ? null : participantSummary(found.otherParticipants))
      })
      .catch(() => {
        if (!cancelled) setSummary(null)
      })
    return () => {
      cancelled = true
    }
  }, [threadId])

  if (summary === null) return null
  return (
    <p className="text-sm text-gray-600">
      With <span className="font-semibold text-gray-900">{summary}</span>
    </p>
  )
}
