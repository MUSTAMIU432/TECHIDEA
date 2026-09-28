import { useState } from 'react'

import type { Idea } from '../../ideas/api/ideasApi'
import { IdeaAttachments } from '../../ideas/components/IdeaAttachments'
import { IdeaPagination } from '../../ideas/components/IdeaPagination'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { useCanReview } from '../hooks/useCanReview'
import { useReviewQueue } from '../hooks/useReviewQueue'
import { formatDate } from '../utils/reviewLabels'
import { ReviewHistory } from './ReviewHistory'

/**
 * The reviewer's workspace (S3-003): the active organization's review queue,
 * and the review context of the idea selected from it.
 *
 * Read-only by design. Listing the queue and opening an idea claim nothing;
 * claiming a review and recording a decision are S3-004, and no control for
 * either is drawn here.
 *
 * Every decision about *what* is shown is the server's: the queue arrives
 * already filtered to what this reviewer may take, the history arrives
 * already filtered to what they may read, and whether they are a reviewer at
 * all is `viewerCanReviewIn`. The "not a reviewer" message exists because the
 * queue answers a non-reviewer with an empty page, which on its own would read
 * as "nothing to review" - true, but not the reason.
 */
export function ReviewsWorkspace() {
  const { user } = useAuth()
  const { activeOrganization } = useOrganization()
  const organizationId = activeOrganization?.id ?? null
  const canReview = useCanReview(organizationId)
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Idea | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const { ideas, pageInfo, loading, error } = useReviewQueue(
    canReview ? organizationId : null,
    offset,
    reloadToken,
  )

  if (organizationId === null) {
    return <Panel>Choose an organization to see its review queue.</Panel>
  }
  if (canReview === null) {
    return <Panel>Checking your review access…</Panel>
  }
  if (!canReview) {
    return (
      <Panel>
        You do not review ideas in {activeOrganization?.name ?? 'this organization'}. An owner can
        give you the Reviewer role.
      </Panel>
    )
  }

  return (
    <section aria-labelledby="review-queue-heading" className="mt-8">
      <div className="flex items-end justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">
            Review queue
          </p>
          <h2
            id="review-queue-heading"
            className="mt-1 text-2xl font-semibold tracking-tight text-gray-900"
          >
            {activeOrganization?.name}
          </h2>
        </div>
        <button
          type="button"
          onClick={() => setReloadToken((token) => token + 1)}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          Refresh
        </button>
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,1fr)]">
        <div className="rounded-2xl border border-gray-200 bg-white p-4">
          {error ? (
            <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </p>
          ) : loading && pageInfo === null ? (
            <p className="text-sm text-gray-500">Loading the review queue…</p>
          ) : ideas.length === 0 ? (
            <p className="text-sm text-gray-500">
              Nothing is waiting for review. Submitted ideas appear here, oldest first.
            </p>
          ) : (
            <>
              <ul aria-label="Ideas waiting for review" className="divide-y divide-gray-100">
                {ideas.map((idea) => (
                  <li key={idea.id}>
                    <button
                      type="button"
                      aria-pressed={selected?.id === idea.id}
                      onClick={() => setSelected(idea)}
                      className="w-full rounded-lg px-3 py-3 text-left hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 aria-pressed:bg-brand-50"
                    >
                      <span className="block text-sm font-semibold text-gray-900">
                        {idea.title}
                      </span>
                      <span className="mt-0.5 block text-xs text-gray-500">
                        {idea.category?.name ?? 'Uncategorised'}
                        {idea.submittedAt ? ` · submitted ${formatDate(idea.submittedAt)}` : ''}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              {pageInfo && (
                <IdeaPagination
                  pageInfo={pageInfo}
                  onOffsetChange={(next) => {
                    setSelected(null)
                    setOffset(next)
                  }}
                />
              )}
            </>
          )}
        </div>

        {selected ? (
          <ReviewContext idea={selected} viewerId={user?.id ?? null} />
        ) : (
          <Panel>Select an idea to see its details and review history.</Panel>
        )}
      </div>
    </section>
  )
}

function ReviewContext({ idea, viewerId }: { idea: Idea; viewerId: string | null }) {
  const [evidenceOpen, setEvidenceOpen] = useState(false)

  return (
    <article
      aria-labelledby="review-context-heading"
      className="rounded-2xl border border-gray-200 bg-white p-5"
    >
      <h3 id="review-context-heading" className="text-lg font-semibold text-gray-900">
        {idea.title}
      </h3>
      <p className="mt-1 text-xs text-gray-500">
        {idea.category?.name ?? 'Uncategorised'}
        {idea.submittedAt ? ` · submitted ${formatDate(idea.submittedAt)}` : ''}
      </p>
      {idea.viewerCanStartReview && (
        <p className="mt-3 rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-800">
          Waiting for a reviewer. You can review this idea.
        </p>
      )}
      <p className="mt-4 whitespace-pre-line text-sm leading-6 text-gray-700">{idea.description}</p>

      <IdeaAttachments
        idea={idea}
        open={evidenceOpen}
        onToggle={() => setEvidenceOpen((o) => !o)}
      />

      <h4 className="mt-5 text-sm font-semibold text-gray-900">Review history</h4>
      <div className="mt-2">
        <ReviewHistory ideaId={idea.id} viewerId={viewerId} />
      </div>
    </article>
  )
}

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-8 rounded-2xl border border-dashed border-gray-300 bg-white px-5 py-6 text-sm text-gray-600">
      {children}
    </div>
  )
}
