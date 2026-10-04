import { Link } from 'react-router-dom'

import { participantSummary } from '../api/messagingApi'
import { useMessageThreads } from '../hooks/useMessages'
import { formatDate } from '../../reviews/utils/reviewLabels'

/**
 * The caller's conversations at `/app/messages`, newest activity first.
 *
 * **Private messages are not comments.** A conversation is between the people
 * who were put in it, and access comes from that participant list rather than
 * from the idea it may be anchored to — so somebody who can read an idea cannot
 * see that two of its readers are talking about it. This list therefore shows
 * only what the server returned, and there is deliberately no "messages about
 * this idea" view to cross that line.
 *
 * The unread count comes from the same response as the rows, so the badge and
 * the list can never disagree.
 */
export function MessagesPage() {
  const { threads, loading, error, reload } = useMessageThreads()

  return (
    <section aria-labelledby="messages-heading" className="mt-8">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">Messages</p>
        <h1 id="messages-heading" className="mt-1 text-3xl font-bold tracking-tight text-gray-900">
          Your conversations.
        </h1>
        <p className="mt-3 max-w-2xl text-base leading-7 text-gray-600">
          Direct messages with named people. Only the people in a conversation can read it.
        </p>
      </div>

      <div className="mt-6">
        {loading && threads.length === 0 ? (
          <p className="text-sm text-gray-600">Loading your conversations…</p>
        ) : error ? (
          <div className="rounded-xl border border-red-200 bg-red-50 p-5" role="alert">
            <p className="text-sm font-semibold text-red-800">Conversations unavailable</p>
            <p className="mt-1 text-sm text-red-700">{error}</p>
            <button
              type="button"
              onClick={reload}
              className="mt-4 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-800 hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
            >
              Try again
            </button>
          </div>
        ) : threads.length === 0 ? (
          <p className="text-sm text-gray-600">You are not in any conversations yet.</p>
        ) : (
          <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white">
            {threads.map(({ thread, otherParticipants, latestMessage, unreadCount }) => (
              <li key={thread.id}>
                <Link
                  to={`/app/messages/${thread.id}`}
                  className="flex flex-wrap items-center gap-3 px-4 py-3 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-300"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-gray-900">
                      {participantSummary(otherParticipants)}
                    </span>
                    {latestMessage ? (
                      <span className="mt-0.5 block truncate text-sm text-gray-600">
                        {latestMessage.body}
                      </span>
                    ) : (
                      <span className="mt-0.5 block text-sm text-gray-500">No messages yet.</span>
                    )}
                    {thread.ideaTitle ? (
                      <span className="mt-1 block text-xs text-gray-500">
                        About: {thread.ideaTitle}
                      </span>
                    ) : null}
                  </span>
                  <span className="text-xs text-gray-500">
                    {formatDate(thread.latestMessageAt)}
                  </span>
                  {unreadCount > 0 && (
                    <span className="rounded-full bg-brand-600 px-2.5 py-1 text-xs font-bold text-white">
                      {unreadCount} new
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
