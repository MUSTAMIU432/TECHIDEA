import { useState } from 'react'

import {
  transitionIdeaRequest,
  type Idea,
  type IdeaFilters,
  type IdeaStatus,
  type IdeaVisibility,
} from '../api/ideasApi'
import { useDebouncedCallback } from '../../../lib/useDebouncedCallback'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeaForm } from './IdeaForm'
import { IdeaList } from './IdeaList'
import { SEARCH_DEBOUNCE_MS } from './IdeaFiltersBar'

/**
 * The Ideas area: the organization's ideas on one side, the create/edit form
 * on the other.
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
/**
 * Who can read a just-saved draft, in words. Derived from the idea's own
 * visibility rather than assumed: a draft is only author-only when it is
 * `PRIVATE`, and saving or submitting never changes that (S2-008).
 */
const DRAFT_READERS: Record<IdeaVisibility, string> = {
  PRIVATE: 'Only you can see it.',
  ORGANIZATION: 'Members of this organization can read it.',
  PUBLIC: 'Anyone signed in to the platform can read it.',
  DEPARTMENT: 'Only you can see it.',
}

export function IdeasWorkspace() {
  const { activeOrganization } = useOrganization()
  const [editing, setEditing] = useState<Idea | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [submittingIdeaId, setSubmittingIdeaId] = useState<string | null>(null)
  const [submittingTarget, setSubmittingTarget] = useState<IdeaStatus | null>(null)
  const [filters, setFilters] = useState<IdeaFilters>({})
  const [reloadToken, setReloadToken] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
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

  function handleSaved(idea: Idea) {
    setEditing(null)
    setIsCreating(false)
    setNotice(
      idea.status === 'DRAFT' ? `Draft saved. ${DRAFT_READERS[idea.visibility]}` : 'Idea saved.',
    )
    reloadList()
  }

  async function handleTransition(idea: Idea, target: IdeaStatus) {
    setSubmitError(null)
    setNotice(null)
    setSubmittingIdeaId(idea.id)
    setSubmittingTarget(target)
    try {
      const result = await transitionIdeaRequest(idea.id, target)
      if (result.success) {
        setNotice(result.message)
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

  const showForm = isCreating || editing !== null

  return (
    <section aria-labelledby="ideas-heading" className="mt-8">
      <div className="flex flex-col justify-between gap-2 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">Ideas</p>
          <h2
            id="ideas-heading"
            className="mt-1 text-2xl font-semibold tracking-tight text-gray-900"
          >
            {activeOrganization ? activeOrganization.name : 'Your ideas'}
          </h2>
        </div>
        <p className="max-w-md text-sm leading-6 text-gray-600">
          File a problem worth automating. Submitting puts it forward for review; who can see it is
          its visibility, which submitting does not change.
        </p>
      </div>

      {notice && (
        <output className="mt-5 block rounded-lg bg-green-50 px-4 py-3 text-sm text-green-800">
          {notice}
        </output>
      )}
      {submitError && (
        <p role="alert" className="mt-5 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {submitError}
        </p>
      )}

      <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(18rem,0.85fr)]">
        <div>
          <IdeaList
            filters={filters}
            onFiltersChange={applyFilters}
            onSearchChange={searchFor}
            // A token rather than a remount key: a save invalidates the list
            // and must re-run its fetch, but a remount would also throw away
            // the reader's filters and page, which have nothing to do with the
            // write. The fetch is a side effect of the list's props, so
            // changing one of them is enough.
            reloadToken={reloadToken}
            onEdit={(idea) => {
              setSubmitError(null)
              setIsCreating(false)
              setEditing(idea)
            }}
            onTransition={handleTransition}
            submittingIdeaId={submittingIdeaId}
            submittingTarget={submittingTarget}
          />
          {!showForm && activeOrganization && (
            <button
              type="button"
              onClick={() => {
                setNotice(null)
                setSubmitError(null)
                setEditing(null)
                setIsCreating(true)
              }}
              className="mt-4 w-full rounded-xl border border-dashed border-gray-300 bg-white px-4 py-3 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
            >
              File a new idea
            </button>
          )}
        </div>

        {showForm && activeOrganization && (
          <IdeaForm
            organizationId={activeOrganization.id}
            idea={editing}
            onSaved={handleSaved}
            onCancel={() => {
              setIsCreating(false)
              setEditing(null)
            }}
          />
        )}
      </div>
    </section>
  )
}
