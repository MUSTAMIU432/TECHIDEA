import { useState } from 'react'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { SpinnerIcon } from '../../identity/components/icons'
import type { Idea, IdeaFilters, IdeaStatus } from '../api/ideasApi'
import { useCategories } from '../hooks/useCategories'
import { useIdeaDiscovery } from '../hooks/useIdeaDiscovery'
import { useIdeaVotes } from '../hooks/useIdeaVotes'
import {
  isResubmission,
  statusClasses,
  statusDescription,
  statusLabel,
  transitionLabel,
} from '../utils/lifecycle'
import { IdeaOwnershipSummary } from './IdeaOwnership'
import { ownerNameFor } from '../utils/ownership'
import { IdeaAttachments } from './IdeaAttachments'
import { IdeaDiscussion } from './IdeaDiscussion'
import { IdeaReviewSection } from '../../reviews/components/IdeaReviewSection'
import { IdeaCardActions, type IdeaCardSection } from './IdeaCardActions'
import { IdeaFiltersBar } from './IdeaFiltersBar'
import { IdeaPagination } from './IdeaPagination'

/**
 * The one open section on the page: which idea, and which of its three
 * disclosures. A null is "the list is closed", which is the state a reader
 * arrives in and the state a filter change should return them to.
 */
interface OpenSection {
  ideaId: string
  section: IdeaCardSection
}

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
/** Bring the just-filed idea into view once its card is rendered. */
function scrollIntoView(element: HTMLLIElement | null) {
  element?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
}

export function IdeaList({
  filters,
  onFiltersChange,
  onSearchChange,
  onEdit,
  onTransition,
  reloadToken,
  submittingIdeaId = null,
  submittingTarget = null,
  highlightedIdeaId = null,
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
  /** The idea just filed from `/app/ideas/new`: marked, and scrolled into view. */
  highlightedIdeaId?: string | null
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
  /*
    Which section is open, if any - **at most one on the whole page**, and it is
    one value rather than one flag per section per idea.

    That shape is the whole rule, and it is what makes opening a card's evidence
    close another card's comments instead of stacking them: there is nowhere to
    record a second one. The reasons it is worth doing that way:

    - **A list is scanned, not read.** Somebody comparing two ideas opens one
      card, then the next. Two open sections means the reader is deciding which
      one is real, and the page gets taller than the amount of content on it
      warrants - every closed section is content that has to be scrolled past.
    - **Each section is a request.** A discussion, an evidence list and a review
      history are three fetches, keyed on being open
      (`useComments(open ? idea.id : null, ...)`, `useAttachments`,
      `IdeaReviewSection`). Leaving twenty cards open would leave sixty requests
      in flight for content nobody asked for.
    - **`IdeaList` already re-renders wholesale on a filter change**, so an open
      section cannot outlive the results it belongs to. Holding the position
      here rather than inside each panel is what gives it that.
  */
  const [openSection, setOpenSection] = useState<OpenSection | null>(null)
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

  /** Whether this idea's section is the open one. The only question a row asks. */
  function isSectionOpen(ideaId: string, section: IdeaCardSection): boolean {
    return openSection !== null && openSection.ideaId === ideaId && openSection.section === section
  }

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
        onContextChange={(submissionContext) => onFiltersChange({ ...filters, submissionContext })}
        onVisibilityChange={(visibility) => onFiltersChange({ ...filters, visibility })}
        onMineChange={(mine) => onFiltersChange({ ...filters, mine })}
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
            {/*
              `aria-label` on the list, not just a class: it is what lets a test
              ask "what is in the rows?" of the rows rather than of the whole
              page, which now includes a filter bar that offers the same words
              the badges use.
            */}
            <ul className="space-y-3" aria-label="Ideas">
              {ideas.map((idea) => {
                const isMine = user?.id === idea.authorId
                const isDraft = idea.status === 'DRAFT'
                // Sent back for changes (S3-005): the author revises and
                // submits again. Either track's changes-requested state counts -
                // somebody has read the idea and written down what to change, and
                // which track asked is a question the status badge answers rather
                // than one the actions have to repeat.
                //
                // Authorship is only what decides whether to *offer* the edit;
                // `updateIdea` checks it again.
                const isRevisable = isResubmission(idea.status)
                const canEdit = isMine && (isDraft || isRevisable)
                return (
                  <li
                    key={idea.id}
                    ref={idea.id === highlightedIdeaId ? scrollIntoView : undefined}
                    aria-current={idea.id === highlightedIdeaId ? 'true' : undefined}
                    // Clear of the pinned header and breadcrumb when scrolled to.
                    className={`scroll-mt-[calc(var(--app-chrome-height)+1rem)] rounded-xl border bg-white p-4 shadow-sm ${
                      idea.id === highlightedIdeaId
                        ? 'border-brand-400 ring-2 ring-brand-200'
                        : 'border-gray-200'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-gray-900">
                          {idea.title}
                        </h3>
                        {idea.id === highlightedIdeaId && (
                          <p className="mt-0.5 text-xs font-semibold text-brand-700">Just filed</p>
                        )}
                        <p className="mt-1 line-clamp-2 text-sm text-gray-600">
                          {idea.description || 'No description yet.'}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${statusClasses(idea.status)}`}
                      >
                        {statusLabel(idea.status, idea.submissionContext)}
                      </span>
                    </div>

                    {/*
                      What the state *means*, not just which state it is. Worth the
                      extra line for the two states where the next step is the author's
                      to take: a changes-requested idea says so here, rather than the
                      author having to infer it from a badge colour.
                    */}
                    {idea.status !== 'DRAFT' && (
                      <p className="mt-2 text-xs text-gray-500">
                        {statusDescription(idea.status, idea.submissionContext)}
                      </p>
                    )}

                    {/*
                      Ownership and audience, as two badges and never as one.

                      This is the line that answers "whose idea is this?" and it
                      sits above the metadata because it is the first thing a
                      reader of a *list* needs - a list of ideas from three
                      different owners is otherwise unreadable. The status badge
                      above already says the state, so it is not repeated here.
                    */}
                    <div className="mt-3">
                      <IdeaOwnershipSummary
                        context={idea.submissionContext}
                        ownerName={ownerNameFor(
                          idea.submissionContext,
                          idea.tenantName,
                          idea.authorId,
                          user?.id ?? null,
                        )}
                        visibility={idea.visibility}
                      />
                    </div>

                    <dl className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
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
                    {canEdit || idea.availableTransitions.length > 0 ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        {canEdit && (
                          <button
                            type="button"
                            onClick={() => onEdit(idea)}
                            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
                          >
                            {isDraft ? 'Edit draft' : 'Revise idea'}
                          </button>
                        )}
                        {idea.availableTransitions.map((target) => {
                          // A re-submission reads the same whichever track asked
                          // for the changes: pressing it sends the idea back
                          // where it came from, and the card's own state says
                          // where that is.
                          const label =
                            isRevisable &&
                            (target === 'SUBMITTED' || target === 'SUBMITTED_TO_ORGANIZATION')
                              ? 'Submit again'
                              : transitionLabel(target)
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
                        The card's footer: the vote control and the three
                        disclosures that open the discussion, the evidence and
                        the review history, in one evenly spaced row. It owns
                        the toggles, so the sections below it are content-only -
                        see `IdeaCardActions`.

                        Last, because it is the foot of the card and the
                        lifecycle buttons above it are what the author came
                        for; a reader scanning for what can be done to an idea
                        should not have to pass the bar to reach the status.
                      */}
                    <IdeaCardActions
                      idea={idea}
                      viewerId={user?.id ?? null}
                      vote={votes[idea.id]}
                      onToggleVote={toggleVote}
                      onDismissVoteError={dismissVoteError}
                      open={{
                        discussion: isSectionOpen(idea.id, 'discussion'),
                        evidence: isSectionOpen(idea.id, 'evidence'),
                        review: isSectionOpen(idea.id, 'review'),
                      }}
                      onToggleSection={(section) => {
                        // The one rule, written once: opening a section closes
                        // whatever was open, whether that was another section of
                        // this card or another card's - and opening the section
                        // that is already open closes it, which is what a
                        // disclosure that says `aria-expanded` has to do.
                        setOpenSection((current) =>
                          current !== null &&
                          current.ideaId === idea.id &&
                          current.section === section
                            ? null
                            : { ideaId: idea.id, section },
                        )
                      }}
                    />
                    <IdeaDiscussion idea={idea} open={isSectionOpen(idea.id, 'discussion')} />
                    {/*
                        Supporting evidence (S2-007), collapsed and fetched
                        only while open - the same reasoning as the
                        discussion above. Rendered after it so a card's
                        layout stays predictable: status, then discussion,
                        then evidence, in the order S2-005 and S2-007
                        shipped.
                      */}
                    <IdeaAttachments idea={idea} open={isSectionOpen(idea.id, 'evidence')} />
                    {/*
                        Review history (S3-003), for the author once the idea
                        is put forward and for reviewers - fetched only while
                        open, like the two sections above.
                      */}
                    <IdeaReviewSection
                      idea={idea}
                      viewerId={user?.id ?? null}
                      open={isSectionOpen(idea.id, 'review')}
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
