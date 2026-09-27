import { useEffect, useState } from 'react'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { organizationIdeasRequest, type Idea, type IdeaStatus } from '../api/ideasApi'
import { SpinnerIcon } from '../../identity/components/icons'
import {
  statusClasses,
  statusDescription,
  statusLabel,
  transitionLabel,
  visibilityLabel as visibilityLabelFor,
} from '../utils/lifecycle'

/**
 * The organization's ideas, and what can be done with them.
 *
 * Two rules shape this component:
 *
 * - **The list comes from the server already filtered.** `organizationIdeas`
 *   applies the tenant and visibility rules; this component never filters a
 *   list to decide what somebody may see, because that is the one thing a
 *   client cannot be trusted with. It does filter for *presentation* - a
 *   "Your drafts" heading is a label, not a control.
 * - **What is offered is decided from the signed-in user's id.**
 *   `idea.authorId === user.id` is what puts Edit and Submit on an idea, and
 *   it is only ever a question of what to *offer*: the server refuses an
 *   edit or a submission it should refuse whether or not the buttons were
 *   rendered, and a failure to render them is not a security control.
 */
export function IdeaList({
  onEdit,
  onTransition,
  submittingIdeaId = null,
  submittingTarget = null,
}: {
  onEdit: (idea: Idea) => void
  /**
   * Perform a lifecycle move. The list decides nothing about whether the move
   * is allowed - it was handed `availableTransitions` by the backend - and it
   * has no opinion about the outcome either; the workspace reports that.
   */
  onTransition: (idea: Idea, target: IdeaStatus) => void
  /**
   * Which idea's submission is in flight, so exactly one row shows a spinner.
   * Owned by the workspace rather than by this component: the mutation lives
   * there, and a list that owned its own "submitting" flag would have no way
   * to learn that the request had come back.
   */
  submittingIdeaId?: string | null
  /** Which move is in flight, so only its own button shows a spinner. */
  submittingTarget?: IdeaStatus | null
}) {
  const { user } = useAuth()
  const { activeOrganization, status: organizationStatus } = useOrganization()
  // `null` means "not fetched yet", which is the loading state. Deriving it
  // rather than setting it imperatively keeps the first render and the render
  // after a failed request from disagreeing about what is on screen.
  const [ideas, setIdeas] = useState<Idea[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // No organization means there is nothing to ask for; that case is
    // rendered from `activeOrganization` below rather than fetched into an
    // empty list, so no state is written synchronously here.
    if (!activeOrganization) return

    let cancelled = false
    organizationIdeasRequest(activeOrganization.id)
      .then((next) => {
        if (!cancelled) {
          setIdeas(next)
          setError(null)
        }
      })
      .catch(() => {
        // A transport failure is not a business outcome, so it is shown as
        // itself rather than as an empty list - an empty list would read as
        // "this organization has no ideas", which is a different claim.
        if (!cancelled) {
          setIdeas([])
          setError('We could not reach the server. Please try again.')
        }
      })
    return () => {
      cancelled = true
    }
  }, [activeOrganization])

  if (organizationStatus === 'loading') return <LoadingPanel />

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-5" role="alert">
        <p className="text-sm font-semibold text-red-800">Ideas unavailable</p>
        <p className="mt-1 text-sm text-red-700">{error}</p>
      </div>
    )
  }

  // Checked *before* the "not fetched yet" guard below, and the order matters:
  // with no organization there is nothing to fetch, so `ideas` stays null
  // forever and the loading guard would spin indefinitely.
  if (!activeOrganization) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5">
        <p className="text-sm font-semibold text-gray-900">No organization selected</p>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          Ideas belong to an organization, so choose one first.
        </p>
      </div>
    )
  }

  // A single-condition guard, on purpose: `ideas === null && !error` would read
  // the same but would not narrow the type for what follows.
  if (ideas === null) return <LoadingPanel />

  if (ideas.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5">
        <p className="text-sm font-semibold text-gray-900">No ideas here yet</p>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          File the first one. It stays private to you until you widen its visibility.
        </p>
      </div>
    )
  }

  return (
    <ul className="space-y-3">
      {ideas.map((idea) => {
        const isMine = user?.id === idea.authorId
        const isDraft = idea.status === 'DRAFT'
        return (
          <li key={idea.id} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate text-sm font-semibold text-gray-900">{idea.title}</h3>
                <p className="mt-1 line-clamp-2 text-sm text-gray-600">
                  {idea.description || 'No description yet.'}
                </p>
              </div>
              <span
                className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${statusClasses(idea.status)}`}
              >
                {statusLabel(idea.status)}
              </span>
            </div>

            {/*
              What the state *means*, not just which state it is. Worth the
              extra line for the two states where the next step is the author's
              to take: a changes-requested idea says so here, rather than the
              author having to infer it from a badge colour.
            */}
            {idea.status !== 'DRAFT' && (
              <p className="mt-2 text-xs text-gray-500">{statusDescription(idea.status)}</p>
            )}

            <dl className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
              <div className="flex items-center gap-1">
                <dt>Visibility</dt>
                <dd className="font-medium text-gray-700">{visibilityLabel(idea)}</dd>
              </div>
              <div className="flex items-center gap-1">
                <dt>Status</dt>
                <dd className="font-medium text-gray-700">{statusLabel(idea.status)}</dd>
              </div>
              <div className="flex items-center gap-1">
                <dt>Category</dt>
                <dd className="font-medium text-gray-700">{idea.category?.name ?? 'None'}</dd>
              </div>
              {idea.submittedAt && (
                <div className="flex items-center gap-1">
                  <dt>Submitted on</dt>
                  <dd className="font-medium text-gray-700">
                    {new Date(idea.submittedAt).toLocaleDateString()}
                  </dd>
                </div>
              )}
            </dl>

            {/*
                Actions are rendered from `availableTransitions`, which the
                backend computed for *this* viewer. So there is no client-side
                rule saying "the author may submit" or "a reviewer may approve"
                that could disagree with the server's, and an author who also
                holds the Owner role is offered nothing on their own submitted
                idea — because the server declined to allow it.

                Rendering nothing when the list is empty is a courtesy, not a
                control: the server refuses an unlisted move whether or not a
                button was drawn.
              */}
            {(isMine && isDraft) || idea.availableTransitions.length > 0 ? (
              <div className="mt-4 flex flex-wrap gap-2">
                {isMine && isDraft && (
                  <button
                    type="button"
                    onClick={() => onEdit(idea)}
                    className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
                  >
                    Edit draft
                  </button>
                )}
                {idea.availableTransitions.map((target) => {
                  const label = transitionLabel(target)
                  if (label === null) return null
                  return (
                    <button
                      key={target}
                      type="button"
                      disabled={submittingIdeaId === idea.id}
                      onClick={() => onTransition(idea, target)}
                      className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {submittingIdeaId === idea.id && submittingTarget === target && (
                        <SpinnerIcon className="h-3.5 w-3.5 motion-safe:animate-spin" />
                      )}
                      {label}
                    </button>
                  )
                })}
              </div>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

function LoadingPanel() {
  return (
    <output className="block rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <span className="sr-only">Loading ideas…</span>
      <span className="block h-4 w-40 animate-pulse rounded bg-gray-200" />
      <span className="mt-3 block h-3 w-56 animate-pulse rounded bg-gray-100" />
    </output>
  )
}

function visibilityLabel(idea: Idea): string {
  return visibilityLabelFor(idea.visibility)
}
