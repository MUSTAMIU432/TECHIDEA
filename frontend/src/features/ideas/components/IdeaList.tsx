import { useState } from 'react'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { SpinnerIcon } from '../../identity/components/icons'
import type { Idea, IdeaFilters, IdeaStatus } from '../api/ideasApi'
import { useCategories } from '../hooks/useCategories'
import { useIdeaDiscovery } from '../hooks/useIdeaDiscovery'
import { useIdeaVotes } from '../hooks/useIdeaVotes'
import {
  statusClasses,
  statusDescription,
  statusLabel,
  transitionLabel,
  visibilityLabel as visibilityLabelFor,
} from '../utils/lifecycle'
import { IdeaAttachments } from './IdeaAttachments'
import { IdeaDiscussion } from './IdeaDiscussion'
import { IdeaVoteButton } from './IdeaVoteButton'
import { IdeaFiltersBar } from './IdeaFiltersBar'
import { IdeaPagination } from './IdeaPagination'

/**
 * The organization's ideas, narrowed by the filters above them, and what can
 * be done with them.
 *
 * Three rules shape this component:
 *
 * - **The list comes from the server already filtered.** Every read goes
 *   through `organizationIdeas`, which applies the tenant rule, the
 *   visibility rule and the discovery filters server-side; this component
 *   never filters a list to decide what somebody may see, because that is the
 *   one thing a client cannot be trusted with. It does arrange a list for
 *   *presentation* - it is what renders the server's page - and the empty
 *   states distinguish "there is nothing here" from "nothing matches what you
 *   asked for", which are different facts.
 * - **Filters only ever narrow.** The bar can offer a category, a status and
 *   a search, and there is no control that could widen a result, because the
 *   backend has no such argument to send.
 * - **What is offered is decided from the signed-in user's id.**
 *   `idea.authorId === user.id` is what puts Edit and Submit on an idea, and
 *   it is only ever a question of what to *offer*: the server refuses an
 *   edit or a submission it should refuse whether or not the buttons were
 *   rendered, and a failure to render them is not a security control.
 */
export function IdeaList({
  filters,
  onFiltersChange,
  onSearchChange,
  onEdit,
  onTransition,
  reloadToken,
  submittingIdeaId = null,
  submittingTarget = null,
}: {
  /** The filters in effect. Sent to the server; never applied here. */
  filters: IdeaFilters
  /**
   * Replace the filters. The parent owns this because it owns the rule that a
   * narrowing filter returns to the first page — the rule is one line, and
   * having it in two places is how "page 4 of 3" gets rendered.
   */
  onFiltersChange: (next: IdeaFilters) => void
  /** The raw text in the search box, which is not yet a filter. */
  onSearchChange: (search: string) => void
  onEdit: (idea: Idea) => void
  /**
   * Perform a lifecycle move. The list decides nothing about whether the move
   * is allowed - it was handed `availableTransitions` by the backend - and it
   * has no opinion about the outcome either; the workspace reports that.
   */
  onTransition: (idea: Idea, target: IdeaStatus) => void
  /**
   * Bumped after a write. A remount would do the same job, and was what this
   * used instead; a token is here because the component now holds filter
   * state above the list, and remounting would throw that away and send the
   * reader back to page 1 after every save.
   */
  reloadToken: number
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
  // Which idea's discussion is open, at most one.
  //
  // Held here rather than inside `IdeaDiscussion` for two reasons. The hook
  // that fetches the thread is keyed on whether the thread is open, so its
  // position decides when a fetch happens - and `IdeaList` is the component
  // that already re-renders wholesale on a filter change, so an open
  // discussion cannot outlive the results it belongs to. And one at a time
  // keeps a page from accumulating twenty open threads.
  const [openDiscussionId, setOpenDiscussionId] = useState<string | null>(null)
  // Independent of the discussion toggle above: a reader may want to see an
  // idea's evidence without opening its discussion, or both at once, so
  // there is no shared "one section open" rule between the two - each has
  // its own single-open-at-a-time state instead.
  const [openAttachmentsId, setOpenAttachmentsId] = useState<string | null>(null)
  const { categories } = useCategories()
  const { ideas, pageInfo, loading, error } = useIdeaDiscovery(
    activeOrganization?.id ?? null,
    filters,
    reloadToken,
  )
  // Seeded from the ideas the list was just given, so the vote controls render
  // the server's numbers on the first paint - no per-idea request, and nothing
  // to reload when a vote changes.
  const { votes, toggle: toggleVote, dismissError: dismissVoteError } = useIdeaVotes(ideas)

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
  // with no organization there is nothing to fetch, so no request is ever made
  // and the loading guard would spin indefinitely.
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

  const filtersActive = Boolean(filters.search || filters.categoryId || filters.status)
  // `pageInfo === null` is "no answer yet"; `loading` afterwards is a refresh
  // under a filter that is already different from the rows on screen.
  const firstLoad = pageInfo === null

  if (firstLoad) return <LoadingPanel />

  return (
    <div>
      <IdeaFiltersBar
        filters={filters}
        categories={categories}
        onSearchChange={onSearchChange}
        onCategoryChange={(categoryId) => onFiltersChange({ ...filters, categoryId })}
        onStatusChange={(status) => onFiltersChange({ ...filters, status })}
        onClear={() => {
          onSearchChange('')
          onFiltersChange({})
        }}
      />

      {/*
        The rows stay mounted while a new filter is in flight, dimmed rather
        than replaced. Replacing them would flash an empty panel on every
        keystroke; showing them at full strength would claim these are the
        results for the term now in the box. `aria-busy` says which it is to
        anything reading the page rather than looking at it.
      */}
      <div aria-busy={loading} className={loading ? 'mt-3 opacity-60 transition-opacity' : 'mt-3'}>
        {ideas.length === 0 ? (
          <EmptyState
            filtered={filtersActive}
            pastEnd={!filtersActive && (filters.offset ?? 0) > 0}
            onClear={() => {
              onSearchChange('')
              onFiltersChange({})
            }}
            onFirstPage={() => onFiltersChange({ ...filters, offset: 0 })}
          />
        ) : (
          <>
            <ul className="space-y-3">
              {ideas.map((idea) => {
                const isMine = user?.id === idea.authorId
                const isDraft = idea.status === 'DRAFT'
                return (
                  <li
                    key={idea.id}
                    className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-gray-900">
                          {idea.title}
                        </h3>
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
                        <dd className="font-medium text-gray-700">
                          {idea.category?.name ?? 'None'}
                        </dd>
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
                        The vote control, next to the idea's other facts. The
                        count and whether *this* reader voted both arrive on
                        the idea from the server, so there is no request per
                        card and no client-side count to drift.
                      */}
                    <div className="mt-3 flex items-center gap-2">
                      <IdeaVoteButton
                        ideaId={idea.id}
                        control={votes[idea.id]}
                        onToggle={toggleVote}
                        onDismissError={dismissVoteError}
                      />
                    </div>

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
                    {/*
                        The discussion, collapsed. Fetched when it is opened
                        and not before - see `IdeaDiscussion`.
                      */}
                    <IdeaDiscussion
                      idea={idea}
                      open={openDiscussionId === idea.id}
                      onToggle={() =>
                        setOpenDiscussionId((current) => (current === idea.id ? null : idea.id))
                      }
                    />
                    {/*
                        Supporting evidence (S2-007), collapsed and fetched
                        only while open - the same reasoning as the
                        discussion above. Rendered after it so a card's
                        layout stays predictable: status, then discussion,
                        then evidence, in the order S2-005 and S2-007
                        shipped.
                      */}
                    <IdeaAttachments
                      idea={idea}
                      open={openAttachmentsId === idea.id}
                      onToggle={() =>
                        setOpenAttachmentsId((current) => (current === idea.id ? null : idea.id))
                      }
                    />
                  </li>
                )
              })}
            </ul>

            {pageInfo && (
              <IdeaPagination
                pageInfo={pageInfo}
                onOffsetChange={(offset) => onFiltersChange({ ...filters, offset })}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}

/**
 * Three different "no rows", because one message would be a lie twice over.
 *
 * - Nothing here at all: file the first one.
 * - Nothing matches: the filters are doing their job, and the reader can see
 *   which ones.
 * - Nothing on *this page*: the reader is past the end, which happens when
 *   rows were removed or a filter narrowed between paging. The way back is
 *   offered rather than left to be deduced.
 */
function EmptyState({
  filtered,
  pastEnd,
  onClear,
  onFirstPage,
}: {
  filtered: boolean
  pastEnd: boolean
  onClear: () => void
  onFirstPage: () => void
}) {
  if (pastEnd) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5">
        <p className="text-sm font-semibold text-gray-900">Nothing on this page</p>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          There are ideas on an earlier page. Ideas change while you read.
        </p>
        <button
          type="button"
          onClick={onFirstPage}
          className="mt-3 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          Back to the first page
        </button>
      </div>
    )
  }

  if (filtered) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5">
        <p className="text-sm font-semibold text-gray-900">Nothing matches those filters</p>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          Ideas you can read exist, but not with this category, status and search together.
        </p>
        <button
          type="button"
          onClick={onClear}
          className="mt-3 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          Clear filters
        </button>
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5">
      <p className="text-sm font-semibold text-gray-900">No ideas here yet</p>
      <p className="mt-1 text-sm leading-6 text-gray-600">
        File the first one. It stays private to you until you widen its visibility.
      </p>
    </div>
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
