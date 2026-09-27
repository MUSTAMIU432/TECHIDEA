import { useState } from 'react'

import { transitionIdeaRequest, type Idea, type IdeaStatus } from '../api/ideasApi'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeaForm } from './IdeaForm'
import { IdeaList } from './IdeaList'

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
  const { activeOrganization } = useOrganization()
  const [editing, setEditing] = useState<Idea | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [submittingIdeaId, setSubmittingIdeaId] = useState<string | null>(null)
  const [submittingTarget, setSubmittingTarget] = useState<IdeaStatus | null>(null)
  const [listVersion, setListVersion] = useState(0)
  const [notice, setNotice] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)

  function reloadList() {
    setListVersion((version) => version + 1)
  }

  function handleSaved(idea: Idea) {
    setEditing(null)
    setIsCreating(false)
    setNotice(
      idea.status === 'DRAFT'
        ? 'Draft saved. Only you can see it until you submit it.'
        : 'Idea saved.',
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
          File a problem worth automating. Drafts stay private to you; submitting puts it forward.
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
            // Remounted rather than handed a "reload" prop: a save or a
            // submission invalidates the whole list, and a fresh mount re-runs
            // the one fetch that populates it without this component having to
            // know anything about how that fetch works.
            key={listVersion}
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
