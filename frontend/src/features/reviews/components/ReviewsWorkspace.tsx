import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { ideaRequest, type Idea } from '../../ideas/api/ideasApi'
import { IdeaAttachments } from '../../ideas/components/IdeaAttachments'
import { IdeaPagination } from '../../ideas/components/IdeaPagination'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { startReviewRequest, type ReviewMutationResult } from '../api/reviewsApi'
import { useCanReview } from '../hooks/useCanReview'
import { useReviewQueue } from '../hooks/useReviewQueue'
import { formatDate } from '../utils/reviewLabels'
import { ReviewDecisionForm } from './ReviewDecisionForm'
import { ReviewHistory } from './ReviewHistory'

/**
 * The reviewer's workspace: the active organization's review queue (S3-003)
 * and, for the idea selected from it, the review itself (S3-004) - start it,
 * then rate every criterion, write the feedback and record the decision.
 *
 * Every decision about *what* is possible is the server's. The queue arrives
 * already filtered to what this reviewer may take; "Start review" is drawn from
 * `viewerCanStartReview` and the decision form from `viewerActiveReviewId`, and
 * neither is a control - `startReview` and `completeReview` ask every rule
 * again. After each operation the panel shows the idea the server returned,
 * not a status this client computed.
 *
 * An idea being reviewed leaves the queue (it is no longer waiting), so an
 * in-progress review is reopened by `?idea=<id>`, which is where the idea
 * card's "Continue review" link points.
 */
export function ReviewsWorkspace() {
  const { user } = useAuth()
  const { activeOrganization } = useOrganization()
  const organizationId = activeOrganization?.id ?? null
  const canReview = useCanReview(organizationId)
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Idea | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const [searchParams] = useSearchParams()
  const linkedIdeaId = searchParams.get('idea')
  const { ideas, pageInfo, loading, error } = useReviewQueue(
    canReview ? organizationId : null,
    offset,
    reloadToken,
  )

  useEffect(() => {
    if (linkedIdeaId === null || !canReview) return
    let cancelled = false
    ideaRequest(linkedIdeaId)
      .then((idea) => {
        if (!cancelled && idea !== null) setSelected(idea)
      })
      .catch(() => {
        // Nothing to open; the queue is still there.
      })
    return () => {
      cancelled = true
    }
  }, [linkedIdeaId, canReview])

  function handleChanged(result: ReviewMutationResult) {
    if (result.idea !== null) setSelected(result.idea)
    setNotice(result.message)
    // Started or decided, the idea is no longer waiting: ask for the queue again.
    setReloadToken((token) => token + 1)
  }

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

      {notice && (
        <output className="mt-5 block rounded-lg bg-green-50 px-4 py-3 text-sm text-green-800">
          {notice}
        </output>
      )}

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
                      onClick={() => {
                        setNotice(null)
                        setSelected(idea)
                      }}
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
          <ReviewContext
            // A new idea, or the same idea in a new state, is a new panel:
            // no half-typed decision survives into it.
            key={`${selected.id}:${selected.status}`}
            idea={selected}
            viewerId={user?.id ?? null}
            onChanged={handleChanged}
          />
        ) : (
          <Panel>Select an idea to see its details and review history.</Panel>
        )}
      </div>
    </section>
  )
}

function ReviewContext({
  idea,
  viewerId,
  onChanged,
}: {
  idea: Idea
  viewerId: string | null
  onChanged: (result: ReviewMutationResult) => void
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  async function handleStart() {
    setStartError(null)
    setStarting(true)
    try {
      const result = await startReviewRequest(idea.id)
      if (result.success) {
        onChanged(result)
        return
      }
      // Somebody else started first, the role was removed, ... - the
      // server's words, as written.
      setStartError(result.message)
    } catch {
      setStartError('We could not reach the server. The review was not started.')
    } finally {
      setStarting(false)
    }
  }

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
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg bg-brand-50 px-3 py-2">
          <p className="text-sm text-brand-800">Waiting for a reviewer.</p>
          <button
            type="button"
            onClick={handleStart}
            disabled={starting}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {starting ? 'Starting…' : 'Start review'}
          </button>
        </div>
      )}
      {startError && (
        <p role="alert" className="mt-3 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {startError}
        </p>
      )}
      <p className="mt-4 whitespace-pre-line text-sm leading-6 text-gray-700">{idea.description}</p>

      <IdeaAttachments
        idea={idea}
        open={evidenceOpen}
        onToggle={() => setEvidenceOpen((o) => !o)}
      />

      {idea.viewerActiveReviewId !== null && (
        <section aria-label="Your review" className="mt-5 border-t border-gray-100 pt-4">
          <h4 className="text-sm font-semibold text-gray-900">Your review</h4>
          <ReviewDecisionForm
            ideaId={idea.id}
            reviewId={idea.viewerActiveReviewId}
            onCompleted={onChanged}
          />
        </section>
      )}

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
