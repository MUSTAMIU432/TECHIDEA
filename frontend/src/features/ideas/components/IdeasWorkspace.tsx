import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import {
  submitIdeaRequest,
  transitionIdeaRequest,
  type Idea,
  type IdeaFilters,
  type IdeaStatus,
} from '../api/ideasApi'
import { useDebouncedCallback } from '../../../lib/useDebouncedCallback'
import { IdeaList } from './IdeaList'
import { SEARCH_DEBOUNCE_MS } from './IdeaFiltersBar'
import { readIdeasRedirect, type NoticeTone } from '../utils/savedIdeaNotice'
import { IdeaContextDialog } from './IdeaContextDialog'

/**
 * The Ideas area: every idea the reader may see, at every level. Filing a new idea
 * (`/app/ideas/new`) and editing one (`/app/ideas/:ideaId/edit`) are pages of
 * their own, both the same guided form, and both redirect back here with
 * their confirmation in router state. There is deliberately no second editor
 * on this page: one form, one way to change an idea.
 *
 * Composition and nothing else. The mutations, the filtering and the rules all
 * live behind `ideasApi` and the backend; this component's whole job is to
 * decide which of the two things the user is looking at - a new idea, an
 * existing draft, or the list - and to keep them in step with each other
 * after a save or a submission.
 *
 * It does own two pieces of discovery state, because they are decisions about
 * *the reader's intent* rather than about the ideas:
 *
 * - **the text in the search box, and the search that is in effect.** They
 *   are different values on purpose. The text is what somebody has typed; the
 *   filter is what has been asked for. Collapsing them would either fetch per
 *   keystroke or clear the box on every debounce.
 * - **the filters, and the page they apply to.** Held here rather than in
 *   `IdeaList` so that the one rule connecting them is written once: narrowing
 *   returns to the first page, and paging changes nothing else. That rule is
 *   what stops "showing 40-60 of 12" from ever being rendered.
 *
 * The three outcomes of a submission are kept apart, because conflating them
 * misleads somebody about something that matters:
 *
 * - success: the list reloads, because the card the move was made from no
 *   longer offers it;
 * - a business refusal (an incomplete draft, somebody else's idea, an idea
 *   that is already submitted): shown as the backend's message, list
 *   unchanged, nothing submitted;
 * - a transport failure: the request never reached a decision, so it is
 *   reported as itself rather than as "your idea was not accepted".
 */
export function IdeasWorkspace() {
  const location = useLocation()
  const navigate = useNavigate()
  const [submittingIdeaId, setSubmittingIdeaId] = useState<string | null>(null)
  const [submittingTarget, setSubmittingTarget] = useState<IdeaStatus | null>(null)
  const [filters, setFilters] = useState<IdeaFilters>({})
  // Whether the "Where does this idea belong?" dialog is open. The one piece of
  // create-idea state that lives on the list rather than on a page of its own,
  // because it is a question about the next thing rather than about an idea.
  const [choosingContext, setChoosingContext] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  // Both seeded from the redirect after filing a new idea on `/app/ideas/new`.
  const [redirect] = useState(() => readIdeasRedirect(location.state))
  const [notice, setNotice] = useState<{
    text: string
    tone: NoticeTone
  } | null>(() => (redirect ? { text: redirect.notice, tone: redirect.tone } : null))
  const highlightedIdeaId = redirect?.ideaId ?? null
  const [submitError, setSubmitError] = useState<string | null>(null)

  /*
   * The search box reports every keystroke, and the list is asked only once
   * the reader pauses - so a term is applied when it settles rather than
   * while it is being typed. The text in the box is the bar's own state and is
   * not mirrored here: the box knows what was typed, and this only needs to
   * know what to ask for.
   */
  const searchFor = useDebouncedCallback((search: string) => {
    applyFilters({ ...filters, search: search.trim() === '' ? null : search })
  }, SEARCH_DEBOUNCE_MS)

  /*
   * The confirmation has been taken into local state, so drop it from the
   * history entry: a refresh, or coming Back to this entry, should not
   * announce the same save a second time.
   */
  useEffect(() => {
    if (readIdeasRedirect(location.state) !== null) {
      navigate(`${location.pathname}${location.search}`, {
        replace: true,
        state: null,
      })
    }
  }, [location.state, location.pathname, location.search, navigate])

  function reloadList() {
    setReloadToken((token) => token + 1)
  }

  /**
   * Every change to the filters goes through here, and it always returns to
   * the first page. That is the whole reason the parent owns them: the rule
   * needs the filters and the page in one place, and a list that reset the
   * page in three separate handlers would have three chances to forget.
   */
  function applyFilters(next: IdeaFilters) {
    setFilters({ ...next, offset: next.offset ?? 0 })
  }

  async function handleTransition(idea: Idea, target: IdeaStatus) {
    setSubmitError(null)
    setNotice(null)
    setSubmittingIdeaId(idea.id)
    setSubmittingTarget(target)
    try {
      // A resubmission after requested changes (S3-005) is the author's
      // `submitIdea`, which the server delegates to the same lifecycle move;
      // it creates no review - a reviewer opens the next round.
      const result =
        idea.status === 'CHANGES_REQUESTED' && target === 'SUBMITTED'
          ? await submitIdeaRequest(idea.id)
          : await transitionIdeaRequest(idea.id, target)
      if (result.success) {
        setNotice({ text: result.message, tone: 'success' })
        reloadList()
        return
      }
      setSubmitError(result.message)
    } catch {
      setSubmitError('We could not reach the server. Please try again.')
    } finally {
      setSubmittingIdeaId(null)
      setSubmittingTarget(null)
    }
  }

  return (
    <section aria-labelledby="ideas-heading" className="mt-8">
      {choosingContext && (
        <IdeaContextDialog
          onCancel={() => setChoosingContext(false)}
          onChosen={(choice) => {
            setChoosingContext(false)
            // The choice goes into the URL, so the page it opens can be
            // bookmarked, reloaded and navigated back to - and so the form has
            // one source of truth for "where does this idea belong".
            const query =
              choice.context === 'TEAM'
                ? `?context=team&team=${choice.ownerId ?? ''}`
                : choice.context === 'ORGANIZATION'
                  ? `?context=organization&organization=${choice.ownerId ?? ''}`
                  : '?context=individual'
            navigate(`/app/ideas/new${query}`)
          }}
        />
      )}
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">Ideas</p>
          <h2
            id="ideas-heading"
            className="mt-1 text-2xl font-semibold tracking-tight text-gray-900"
          >
            Your ideas
          </h2>
        </div>
        {/*
          The global entry point, and the only place a reader is asked where an
          idea belongs. It opens a dialog rather than navigating straight to the
          form, because an idea filed for the wrong owner is hard to notice
          afterwards - nothing breaks, it simply never reaches the queue its
          owner can see.
        */}
        <button
          type="button"
          onClick={() => setChoosingContext(true)}
          className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          <span aria-hidden="true">+</span>
          File a new idea
        </button>
      </div>

      {notice && (
        <output
          className={`mt-5 flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm ${
            notice.tone === 'success'
              ? 'border-green-200 bg-green-50 text-green-800'
              : 'border-amber-200 bg-amber-50 text-amber-900'
          }`}
        >
          <span className="flex items-start gap-2">
            <span aria-hidden="true" className="font-bold">
              {notice.tone === 'success' ? '✓' : '!'}
            </span>
            <span>{notice.text}</span>
          </span>
          <button
            type="button"
            onClick={() => setNotice(null)}
            aria-label="Dismiss"
            className="shrink-0 rounded px-1 font-semibold opacity-70 hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            ×
          </button>
        </output>
      )}
      {submitError && (
        <p role="alert" className="mt-5 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {submitError}
        </p>
      )}

      <div className="mt-5">
        <IdeaList
          filters={filters}
          onFiltersChange={applyFilters}
          onSearchChange={searchFor}
          // A token rather than a remount key: a transition invalidates the
          // list and must re-run its fetch, but a remount would also throw
          // away the reader's filters and page, which have nothing to do with
          // the write. The fetch is a side effect of the list's props, so
          // changing one of them is enough.
          reloadToken={reloadToken}
          onEdit={(idea) => navigate(`/app/ideas/${idea.id}/edit`)}
          onTransition={handleTransition}
          submittingIdeaId={submittingIdeaId}
          submittingTarget={submittingTarget}
          highlightedIdeaId={highlightedIdeaId}
        />
      </div>
    </section>
  )
}
