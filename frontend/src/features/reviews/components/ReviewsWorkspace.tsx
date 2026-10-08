import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { ideaRequest, type Idea } from '../../ideas/api/ideasApi'
import { IdeaAttachments } from '../../ideas/components/IdeaAttachments'
import { CardDisclosureButton, PaperclipIcon } from '../../ideas/components/IdeaCardActions'
import { IdeaStory } from '../../ideas/components/IdeaStory'
import { IdeaPagination } from '../../ideas/components/IdeaPagination'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { changeResponsesRequest, type ChangeResponse } from '../api/changeResponsesApi'
import {
  platformReviewQueueRequest,
  startReviewRequest,
  type ReviewMutationResult,
} from '../api/reviewsApi'
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
 * card's "Continue review" link points. The same link opens a stalled review
 * (its reviewer can no longer review it) for another reviewer to take over.
 */
export function ReviewsWorkspace({ quiet = false }: { quiet?: boolean } = {}) {
  const { user } = useAuth()
  const { activeOrganization } = useOrganization()
  const organizationId = activeOrganization?.id ?? null
  const canReview = useCanReview(organizationId)
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Idea | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  // Held here rather than in the panel, so hiding the details carries to the next idea.
  const [detailsOpen, setDetailsOpen] = useState(true)
  const [searchParams] = useSearchParams()
  const linkedIdeaId = searchParams.get('idea')

  /*
    **The page belongs to the organization, so it is reset with it.** Page three
    of one organization's queue is not page three of another's: the offsets are
    positions in two different result sets, and carrying one across shows a
    reader an empty second page of an organization they have not looked at yet -
    which is exactly the "nothing is waiting" message they would then believe.

    Adjusted during render rather than in an effect, deliberately. An effect runs
    after React has already committed the new organization's first render, so the
    queue would be asked for at the old offset once before the reset landed: two
    requests per switch, the first one for a page nobody asked for. React
    re-renders immediately here instead, before the effect that fetches.

    The selected idea goes with it, for the same reason - it is a row from the
    organization that was on screen a moment ago, and the panel beside the queue
    would otherwise show one tenant's idea under another tenant's name. A
    `?idea=` deep link is the exception: it names its own idea, so it survives the
    switch and the effect below is left to open it.
  */
  const [shownOrganizationId, setShownOrganizationId] = useState(organizationId)
  if (organizationId !== shownOrganizationId) {
    setShownOrganizationId(organizationId)
    setOffset(0)
    if (linkedIdeaId === null) setSelected(null)
  }

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

  // A platform reviewer has their own queue on this page; telling them they do not review
  // in an organization would read as "you are not a reviewer".
  if (quiet && canReview !== true) return null
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

      {/*
        The queue on top, the selected idea underneath at full width. Side by side,
        a short queue was stretched to the height of a long idea and left a column
        of empty space; stacked, the queue is only as tall as its cards.
      */}
      <div className="mt-5 space-y-5">
        <div className="rounded-2xl border border-gray-200 bg-white p-3">
          {error ? (
            <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </p>
          ) : loading && pageInfo === null ? (
            <p className="text-sm text-gray-500">Loading the review queue…</p>
          ) : ideas.length === 0 ? (
            /*
              The one message that is true in every empty case, and the reason it
              is worded this way: the server never says *why* a queue is empty.
              It excludes a reviewer's own ideas from their own queue and returns
              the same empty page as for an organization with nothing waiting, so
              that `organizationId` cannot be used to find out who has submitted
              what, or who reviews where. Anything that claimed to know -
              "there are no submitted ideas", "nobody has submitted one" - would
              be a guess dressed as a fact, and would be wrong for exactly the
              reader most likely to be looking at this panel.

              So the headline is about *their* queue rather than the
              organization's contents, and the second line states the rule
              itself. It is shown unconditionally, because the one thing this
              client must not do is let the wording vary with what it cannot see.
            */
            <div>
              <p className="text-sm font-semibold text-gray-900">
                No ideas are waiting for your review.
              </p>
              <p className="mt-1 text-sm leading-6 text-gray-500">
                Ideas you submitted yourself are not eligible for your own review.
              </p>
            </div>
          ) : (
            <>
              <ul
                aria-label="Ideas waiting for review"
                className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3"
              >
                {ideas.map((idea) => (
                  <li key={idea.id}>
                    <button
                      type="button"
                      aria-pressed={selected?.id === idea.id}
                      onClick={() => {
                        setNotice(null)
                        setSelected(idea)
                      }}
                      className="h-full w-full rounded-xl border border-gray-200 px-4 py-3 text-left transition-colors hover:border-brand-200 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 aria-pressed:border-brand-300 aria-pressed:bg-brand-50"
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
          <SelectedIdea key={selected.id}>
            <ReviewContext
              // A new idea, or the same idea in a new state, is a new panel:
              // no half-typed decision survives into it.
              key={`${selected.id}:${selected.status}`}
              idea={selected}
              viewerId={user?.id ?? null}
              onChanged={handleChanged}
              detailsOpen={detailsOpen}
              onToggleDetails={() => setDetailsOpen((open) => !open)}
              onClose={() => setSelected(null)}
            />
          </SelectedIdea>
        ) : (
          <Panel inline>Select an idea to see its details and review history.</Panel>
        )}
      </div>
    </section>
  )
}

/**
 * One idea under review: its details, the review controls and its history.
 *
 * The details (description, problem story, evidence) can be hidden, so a reviewer working
 * through many ideas keeps the decision in view instead of scrolling past the story each
 * time. Whether they are shown is held by the workspace, not here, so the choice carries
 * from one idea to the next; the start button and the decision form are never hidden.
 */
function ReviewContext({
  idea,
  viewerId,
  onChanged,
  detailsOpen,
  onToggleDetails,
  onClose,
}: {
  idea: Idea
  viewerId: string | null
  onChanged: (result: ReviewMutationResult) => void
  detailsOpen: boolean
  onToggleDetails: () => void
  onClose: () => void
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(true)
  const detailsId = `review-details-${idea.id}`
  const historyId = `review-history-${idea.id}`
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  // The server offers a start on an idea already under review only when its
  // reviewer can no longer review it: `startReview` then takes the review over.
  const takeOver = idea.status === 'UNDER_REVIEW'

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
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id="review-context-heading" className="text-lg font-semibold text-gray-900">
            {idea.title}
          </h3>
          <p className="mt-1 text-xs text-gray-500">
            {idea.category?.name ?? 'Uncategorised'}
            {idea.submittedAt ? ` · submitted ${formatDate(idea.submittedAt)}` : ''}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            aria-expanded={detailsOpen}
            aria-controls={detailsId}
            onClick={onToggleDetails}
            className={TOGGLE_CLASSES}
          >
            {detailsOpen ? 'Hide details' : 'Show details'}
          </button>
          <button type="button" onClick={onClose} className={TOGGLE_CLASSES}>
            Close
          </button>
        </div>
      </div>
      <OwnerResponse ideaId={idea.id} />
      {idea.viewerCanStartReview && (
        <div className="mt-3 flex flex-wrap items-center gap-3 rounded-lg bg-brand-50 px-3 py-2">
          <p className="text-sm text-brand-800">
            {takeOver
              ? 'The reviewer who started this review can no longer review it.'
              : 'Waiting for a reviewer.'}
          </p>
          <button
            type="button"
            onClick={handleStart}
            disabled={starting}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {starting ? 'Starting…' : takeOver ? 'Take over review' : 'Start review'}
          </button>
        </div>
      )}
      {startError && (
        <p role="alert" className="mt-3 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {startError}
        </p>
      )}
      {detailsOpen ? (
        <div id={detailsId}>
          <p className="mt-4 whitespace-pre-line text-sm leading-6 text-gray-700">
            {idea.description}
          </p>
          {/* What happens today, who it affects, what better looks like - in the
              author's words, so the review starts from the problem, not a guess. */}
          <IdeaStory story={idea} />

          {/*
            The evidence panel is a disclosure of its own here rather than a cell of
            a card's action bar: this is one idea, not a list, so there is no row
            of sibling actions to sit level with. The same icon and wording as the
            bar, so the two places it appears are recognisably the same thing.
          */}
          <div className="mt-5 border-t border-gray-100 pt-4">
            <CardDisclosureButton
              icon={<PaperclipIcon size="sm" />}
              label="Supporting evidence"
              hideLabel="Hide supporting evidence"
              open={evidenceOpen}
              onToggle={() => setEvidenceOpen((o) => !o)}
            />
            <IdeaAttachments idea={idea} open={evidenceOpen} />
          </div>
        </div>
      ) : (
        // Collapsed: two lines of the description, enough to recognise the idea.
        <p
          id={detailsId}
          className="mt-3 line-clamp-2 rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-600"
        >
          {idea.description}
        </p>
      )}

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

      <div className="mt-5 flex items-center justify-between gap-2 border-t border-gray-100 pt-4">
        <h4 className="text-sm font-semibold text-gray-900">Review history</h4>
        <button
          type="button"
          aria-expanded={historyOpen}
          aria-controls={historyId}
          onClick={() => setHistoryOpen((open) => !open)}
          className={TOGGLE_CLASSES}
        >
          {historyOpen ? 'Hide history' : 'Show history'}
        </button>
      </div>
      {historyOpen && (
        <div id={historyId} className="mt-2">
          <ReviewHistory ideaId={idea.id} viewerId={viewerId} />
        </div>
      )}
    </article>
  )
}

/**
 * The **platform** review queue: submissions from every organization, team and individual
 * that have reached the platform, for a platform reviewer.
 *
 * Not tenant-scoped, unlike the organization queue beside it, so it does not follow the
 * organization switcher. The list is the server's (`platformReviewQueue`, nothing a
 * reviewer could not decide, their own ideas excluded); picking one opens the same review
 * panel - start, rate, decide - and every operation is authorized again by the server,
 * including that an idea routed to a review team is worked only by that team.
 */
export function PlatformReviewWorkspace() {
  const { user } = useAuth()
  const [selected, setSelected] = useState<Idea | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  // Held here rather than in the panel, so hiding the details carries to the next idea.
  const [detailsOpen, setDetailsOpen] = useState(true)
  // Each answer remembers which load it answers, so a refresh keeps the last list on
  // screen until the new one arrives and a stale failure is not shown as current.
  const [answer, setAnswer] = useState<{ token: number; ideas: Idea[] } | null>(null)
  const [failedToken, setFailedToken] = useState<number | null>(null)
  const [searchParams] = useSearchParams()
  const linkedIdeaId = searchParams.get('idea')
  const ideas = answer?.ideas ?? null
  const error = failedToken === reloadToken ? 'We could not load the platform review queue.' : null

  useEffect(() => {
    let cancelled = false
    const token = reloadToken
    platformReviewQueueRequest()
      .then((queue) => {
        if (!cancelled) setAnswer({ token, ideas: queue })
      })
      .catch(() => {
        if (!cancelled) setFailedToken(token)
      })
    return () => {
      cancelled = true
    }
  }, [reloadToken])

  useEffect(() => {
    if (linkedIdeaId === null) return
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
  }, [linkedIdeaId])

  function handleChanged(result: ReviewMutationResult) {
    if (result.idea !== null) setSelected(result.idea)
    setNotice(result.message)
    setReloadToken((token) => token + 1)
  }

  return (
    <section aria-labelledby="platform-queue-heading" className="mt-8">
      <div className="flex items-end justify-between gap-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">
            Platform review queue
          </p>
          <h2
            id="platform-queue-heading"
            className="mt-1 text-2xl font-semibold tracking-tight text-gray-900"
          >
            Submitted to the platform
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

      {/*
        The queue on top, the selected idea underneath at full width. Side by side,
        a short queue was stretched to the height of a long idea and left a column
        of empty space; stacked, the queue is only as tall as its cards.
      */}
      <div className="mt-5 space-y-5">
        <div className="rounded-2xl border border-gray-200 bg-white p-3">
          {error ? (
            <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </p>
          ) : ideas === null ? (
            <p className="text-sm text-gray-500">Loading the platform review queue…</p>
          ) : ideas.length === 0 ? (
            <div>
              <p className="text-sm font-semibold text-gray-900">
                No submissions are waiting for platform review.
              </p>
              <p className="mt-1 text-sm leading-6 text-gray-500">
                Ideas you submitted yourself are not eligible for your own review.
              </p>
            </div>
          ) : (
            <ul
              aria-label="Submissions waiting for platform review"
              className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3"
            >
              {ideas.map((idea) => (
                <li key={idea.id}>
                  <button
                    type="button"
                    aria-pressed={selected?.id === idea.id}
                    onClick={() => {
                      setNotice(null)
                      setSelected(idea)
                    }}
                    className="h-full w-full rounded-xl border border-gray-200 px-4 py-3 text-left transition-colors hover:border-brand-200 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 aria-pressed:border-brand-300 aria-pressed:bg-brand-50"
                  >
                    <span className="block text-sm font-semibold text-gray-900">{idea.title}</span>
                    <span className="mt-1 block text-xs text-gray-500">
                      <span
                        className={`mr-1.5 inline-block rounded-full px-2 py-0.5 font-semibold ${
                          idea.status === 'UNDER_REVIEW'
                            ? 'bg-amber-100 text-amber-800'
                            : 'bg-sky-100 text-sky-800'
                        }`}
                      >
                        {idea.status === 'UNDER_REVIEW' ? 'Under review' : 'Waiting for review'}
                      </span>
                      {idea.category?.name ?? 'Uncategorised'}
                      {idea.submittedAt ? ` · submitted ${formatDate(idea.submittedAt)}` : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {selected ? (
          <SelectedIdea key={selected.id}>
            <ReviewContext
              key={`${selected.id}:${selected.status}`}
              idea={selected}
              viewerId={user?.id ?? null}
              onChanged={handleChanged}
              detailsOpen={detailsOpen}
              onToggleDetails={() => setDetailsOpen((open) => !open)}
              onClose={() => setSelected(null)}
            />
          </SelectedIdea>
        ) : (
          <Panel inline>Select a submission to see its details and review history.</Panel>
        )}
      </div>
    </section>
  )
}

/**
 * What the owner said they changed, when they answered the last request for changes.
 * Shown above everything else, details hidden or not: it is the first thing to read
 * when a resubmitted idea comes back for another round.
 */
function OwnerResponse({ ideaId }: { ideaId: string }) {
  const [answer, setAnswer] = useState<{ ideaId: string; responses: ChangeResponse[] } | null>(null)

  useEffect(() => {
    let cancelled = false
    changeResponsesRequest(ideaId)
      .then((responses) => {
        if (!cancelled) setAnswer({ ideaId, responses })
      })
      .catch(() => {
        // Nothing to show is the same, to the reviewer, as no response.
      })
    return () => {
      cancelled = true
    }
  }, [ideaId])

  if (answer === null || answer.ideaId !== ideaId || answer.responses.length === 0) return null
  const [latest, ...earlier] = answer.responses

  return (
    <section
      aria-label="The owner's response"
      className="mt-4 rounded-xl border border-sky-200 bg-sky-50 px-4 py-3"
    >
      <p className="text-xs font-semibold tracking-wide text-sky-800 uppercase">
        The owner&apos;s response to round {latest.reviewRound}
      </p>
      <p className="mt-1 text-sm leading-6 whitespace-pre-line text-gray-800">{latest.message}</p>
      <p className="mt-1 text-xs text-sky-700">
        {latest.authorName} · {formatDate(latest.createdAt)}
      </p>
      {earlier.length > 0 && (
        <details className="mt-2 text-xs text-sky-800">
          <summary className="cursor-pointer font-semibold">
            Earlier responses ({earlier.length})
          </summary>
          <ul className="mt-2 space-y-2">
            {earlier.map((response) => (
              <li key={response.id} className="rounded-lg bg-white/70 px-3 py-2 text-gray-700">
                <span className="font-semibold">Round {response.reviewRound}:</span>{' '}
                <span className="whitespace-pre-line">{response.message}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

const TOGGLE_CLASSES =
  'rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300'

function Panel({ children, inline = false }: { children: React.ReactNode; inline?: boolean }) {
  return (
    <div
      className={`${inline ? '' : 'mt-8 '}rounded-2xl border border-dashed border-gray-300 bg-white px-5 py-6 text-sm text-gray-600`}
    >
      {children}
    </div>
  )
}

/**
 * The selected idea's panel, below the queue. Brought into view when a different idea is
 * picked, because it now opens underneath the cards rather than beside them.
 */
function SelectedIdea({ children }: { children: React.ReactNode }) {
  const anchor = useRef<HTMLDivElement>(null)
  // Keyed by the idea's id where it is used, so this runs once per idea picked.
  useEffect(() => {
    anchor.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }, [])
  return (
    <div ref={anchor} className="scroll-mt-24">
      {children}
    </div>
  )
}
