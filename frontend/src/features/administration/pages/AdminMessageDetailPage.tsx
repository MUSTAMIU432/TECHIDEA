import { Link, useParams } from 'react-router-dom'

import {
  adminMessageThreadRequest,
  adminThreadMessagesRequest,
  type AdminMessage,
} from '../api/administrationApi'
import {
  AdminCard,
  AdminPageHeader,
  AdminPagination,
  DetailList,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  Restricted,
} from '../components/AdminUi'
import { BackLink } from '../components/BackLink'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useBackTarget } from '../hooks/useBackTarget'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDay } from '../utils/format'

/**
 * One conversation: who is in it, and — only to an administrator holding the
 * content-inspection permission — what was said.
 *
 * **The participants are the access rule, shown.** A reader here can see exactly
 * who else a conversation is with, which is the same fact the member-facing API
 * authorizes on; what they may not see is the words, and the page says
 * "restricted" rather than rendering a list of empty messages that would read as
 * an empty conversation.
 *
 * Nothing here can change the conversation. There is no delete, no edit and no
 * "mark read": the read position belongs to a participant, and a message is a
 * record of what was said to named people.
 */
export function AdminMessageDetailPage() {
  const { threadId = '' } = useParams()
  const backTo = useBackTarget('/app/admin/messages')
  const filters = useUrlFilters()

  const thread = useAdminQuery(
    `thread:${threadId}`,
    () => adminMessageThreadRequest(threadId),
    'We could not load this conversation.',
  )
  const messages = useAdminQuery(
    `thread-messages:${threadId}:${filters.key}`,
    () => adminThreadMessagesRequest(threadId, { offset: filters.offset }),
    'We could not load these messages.',
  )

  if (thread.error) return <ErrorState message={thread.error} onRetry={thread.reload} />
  if (thread.loading && !thread.data) return <LoadingState label="Loading conversation…" />
  if (!thread.data) return <EmptyState title="Conversation not found." />

  const restricted = thread.data.contentRestricted

  return (
    <>
      <AdminPageHeader
        title={thread.data.subject ?? (restricted ? 'Restricted conversation' : 'Direct message')}
        description={
          thread.data.ideaTitle
            ? `About “${thread.data.ideaTitle}”`
            : thread.data.ideaId
              ? 'About an idea you may not read'
              : 'Not anchored to an idea'
        }
        back={<BackLink to={backTo}>All conversations</BackLink>}
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <AdminCard title="Conversation">
          <DetailList
            items={[
              ['Started by', thread.data.startedBy.name],
              ['People', String(thread.data.participantCount)],
              ['Messages', String(thread.data.messageCount)],
              [
                'Not read by',
                `${thread.data.staleParticipantCount} of ${thread.data.participantCount}`,
              ],
              ['Started', formatDay(thread.data.createdAt)],
              ['Last activity', formatDay(thread.data.latestMessageAt)],
            ]}
          />
          {thread.data.ideaId && (
            <p className="mt-3 text-sm">
              {thread.data.ideaTitle ? (
                <Link
                  to={`/app/admin/ideas/${thread.data.ideaId}`}
                  className="font-semibold text-slate-900 underline"
                >
                  Open the idea
                </Link>
              ) : (
                <Restricted>The idea is restricted</Restricted>
              )}
            </p>
          )}
        </AdminCard>

        <AdminCard title="Participants">
          {/*
            The access rule, made visible. Shown in full even to a reader who may
            not see the messages: who somebody is talking to is platform
            metadata, and a console that hid it would leave the row unreadable.
          */}
          <ul className="space-y-2">
            {thread.data.participants.map((person) => (
              <li key={person.id} className="text-sm">
                <span className="font-semibold text-slate-900">{person.name}</span>
                <span className="block text-xs text-slate-500">{person.email}</span>
              </li>
            ))}
          </ul>
        </AdminCard>

        <AdminCard title="Reading">
          {restricted ? (
            <Notice tone="error">
              This administrator cannot read message bodies. The conversation’s participants and
              shape are shown; the words are not, because they were written for named people rather
              than for the platform.
            </Notice>
          ) : (
            <p className="text-sm text-slate-600">
              This administrator holds the content-inspection permission, so the messages below are
              shown in full.
            </p>
          )}
        </AdminCard>
      </div>

      <div className="mt-4">
        <AdminCard title="Messages">
          {messages.error && <ErrorState message={messages.error} onRetry={messages.reload} />}
          {messages.loading && !messages.data && <LoadingState label="Loading messages…" />}
          {messages.data && messages.data.items.length === 0 && (
            <p className="text-sm text-slate-500">
              {restricted
                ? 'There are messages here, and this administrator may not read them.'
                : 'This conversation has no messages.'}
            </p>
          )}
          {messages.data && messages.data.items.length > 0 && (
            <>
              <ol className="space-y-3">
                {messages.data.items.map((message) => (
                  <MessageRow key={message.id} message={message} restricted={restricted} />
                ))}
              </ol>
              <AdminPagination
                label="Messages pagination"
                pageInfo={messages.data.pageInfo}
                onOffsetChange={filters.setOffset}
              />
            </>
          )}
        </AdminCard>
      </div>
    </>
  )
}

function MessageRow({ message, restricted }: { message: AdminMessage; restricted: boolean }) {
  return (
    <li className="border-l-2 border-slate-200 pl-3">
      <p className="text-xs text-slate-500">
        <span className="font-semibold text-slate-700">{message.sender.name}</span>
        {' · '}
        {formatDay(message.createdAt)}
      </p>
      {/*
        A withheld body is null, never an empty string: an empty bubble would
        read as "nothing was said" rather than "you may not see what was".
      */}
      {message.body === null ? (
        restricted ? (
          <p className="mt-1">
            <Restricted>Message withheld</Restricted>
          </p>
        ) : null
      ) : (
        <p className="mt-1 text-sm whitespace-pre-wrap text-slate-800">{message.body}</p>
      )}
    </li>
  )
}
