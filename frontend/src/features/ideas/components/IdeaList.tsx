import { useEffect, useState } from 'react'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { organizationIdeasRequest, type Idea } from '../api/ideasApi'
import { SpinnerIcon } from '../../identity/components/icons'

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
  onSubmit,
  submittingIdeaId = null,
}: {
  onEdit: (idea: Idea) => void
  onSubmit: (idea: Idea) => void
  /**
   * Which idea's submission is in flight, so exactly one row shows a spinner.
   * Owned by the workspace rather than by this component: the mutation lives
   * there, and a list that owned its own "submitting" flag would have no way
   * to learn that the request had come back.
   */
  submittingIdeaId?: string | null
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
              <span className="shrink-0 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">
                {idea.status === 'DRAFT' ? 'Draft' : 'Submitted'}
              </span>
            </div>

            <dl className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
              <div className="flex items-center gap-1">
                <dt>Visibility</dt>
                <dd className="font-medium text-gray-700">{visibilityLabel(idea)}</dd>
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

            {isMine && (
              <div className="mt-4 flex flex-wrap gap-2">
                {isDraft && (
                  <button
                    type="button"
                    onClick={() => onEdit(idea)}
                    className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
                  >
                    Edit draft
                  </button>
                )}
                {isDraft && (
                  <button
                    type="button"
                    disabled={submittingIdeaId === idea.id}
                    onClick={() => onSubmit(idea)}
                    className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:bg-brand-300"
                  >
                    {submittingIdeaId === idea.id && (
                      <SpinnerIcon className="h-3.5 w-3.5 motion-safe:animate-spin" />
                    )}
                    Submit for review
                  </button>
                )}
              </div>
            )}
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
  switch (idea.visibility) {
    case 'PUBLIC':
      return 'Everyone on the platform'
    case 'ORGANIZATION':
      return 'This organization'
    case 'DEPARTMENT':
      return 'A department'
    default:
      return 'Only you'
  }
}
