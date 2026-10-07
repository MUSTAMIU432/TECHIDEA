import { Link } from 'react-router-dom'

import { adminMessageThreadsRequest } from '../api/administrationApi'
import {
  AdminPageHeader,
  AdminPagination,
  AdminTable,
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  Restricted,
  cellClasses,
} from '../components/AdminUi'
import { SearchBox } from '../components/SearchBox'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDay } from '../utils/format'

/**
 * Every private conversation on the platform.
 *
 * **This is a sensitive list, and the page says so rather than pretending
 * otherwise.** A message is addressed to named people: two readers of the same
 * idea can be talking about it and neither can see that. So this list reports
 * the *shape* of each conversation - who started it, how many people are in it,
 * how many messages, when it was last active - and no message content at all. A
 * reader without the content-inspection permission sees exactly this and cannot
 * open a body; one with it can.
 *
 * `staleParticipantCount` is deliberately labelled "not read by", not "unread":
 * unread is a position per reader, and this console has no reader, so the only
 * honest aggregate is how many participants have not opened the thread.
 */
export function AdminMessagesPage() {
  const filters = useUrlFilters()
  const search = filters.get('search')
  const anchored = filters.get('anchored')

  const { data, loading, error, reload } = useAdminQuery(
    `threads:${filters.key}`,
    () =>
      adminMessageThreadsRequest(
        { search, ...(anchored ? { anchored: anchored === 'true' } : {}) },
        { offset: filters.offset },
      ),
    'We could not load the conversations.',
  )

  return (
    <>
      <AdminPageHeader
        title="Messages"
        description="Private conversations between named people. They are not comments on an idea and are not visible to anybody who can read one — which is why this console reports the shape of a conversation and leaves the words to somebody with the content-inspection permission."
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-64 flex-1">
          <SearchBox
            label="Search conversations"
            placeholder="Subject, starter or idea title"
            initialValue={search}
            onSearch={(value) => filters.set('search', value)}
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-slate-600" htmlFor="thread-anchored">
            About an idea
          </label>
          <select
            id="thread-anchored"
            className="mt-1 rounded-lg border border-slate-300 px-3 py-2 text-sm"
            value={anchored}
            onChange={(event) => filters.set('anchored', event.target.value)}
          >
            <option value="">Any conversation</option>
            <option value="true">Anchored to an idea</option>
            <option value="false">Direct messages</option>
          </select>
        </div>
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading conversations…" />}
      {data && data.items.length === 0 && (
        <EmptyState
          title="No conversations match."
          description="Try a different search. A subject is only searchable when this administrator may read the idea it came from."
        />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable
            label="Conversations"
            columns={[
              'Conversation',
              'About',
              'People',
              'Messages',
              'Not read by',
              'Last activity',
            ]}
          >
            {data.items.map((thread) => (
              <tr key={thread.id}>
                <td className={cellClasses}>
                  <Link
                    to={`/app/admin/messages/${thread.id}`}
                    state={filters.here}
                    className="font-semibold text-slate-900 hover:underline"
                  >
                    {/*
                      A null subject is not an unnamed conversation: a thread
                      anchored to an idea takes its subject from that idea's
                      title, so it is withheld whenever the title is.
                    */}
                    {thread.subject ?? (thread.contentRestricted ? 'Restricted' : 'Direct message')}
                  </Link>
                  <p className="text-xs text-slate-500">started by {thread.startedBy.email}</p>
                </td>
                <td className={cellClasses}>
                  {thread.ideaId ? (
                    thread.ideaTitle ? (
                      <Link
                        to={`/app/admin/ideas/${thread.ideaId}`}
                        className="font-semibold text-slate-900 hover:underline"
                      >
                        {thread.ideaTitle}
                      </Link>
                    ) : (
                      <Restricted>An idea you may not read</Restricted>
                    )
                  ) : (
                    <span className="text-slate-500">Nothing — a direct message</span>
                  )}
                </td>
                <td className={`${cellClasses} tabular-nums`}>{thread.participantCount}</td>
                <td className={`${cellClasses} tabular-nums`}>{thread.messageCount}</td>
                <td className={`${cellClasses} tabular-nums`}>
                  {thread.staleParticipantCount > 0 ? (
                    <Badge tone="warn">{thread.staleParticipantCount}</Badge>
                  ) : (
                    <span className="text-slate-500">0</span>
                  )}
                </td>
                <td className={`${cellClasses} whitespace-nowrap`}>
                  {formatDay(thread.latestMessageAt)}
                </td>
              </tr>
            ))}
          </AdminTable>
          <AdminPagination
            label="Conversations pagination"
            pageInfo={data.pageInfo}
            onOffsetChange={filters.setOffset}
          />
        </>
      )}
    </>
  )
}
