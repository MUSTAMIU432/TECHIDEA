import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ideaRequest, type Idea } from '../features/ideas/api/ideasApi'
import { IdeaForm } from '../features/ideas/components/IdeaForm'
import { savedIdeaNotice, type IdeasRedirectState } from '../features/ideas/utils/savedIdeaNotice'
import { useAuth } from '../features/identity/auth/AuthContext'
import { IDEAS_PATH } from './NewIdeaPage'

/** The statuses whose content the author may change; mirrors `ideas.services.EDITABLE_STATUSES`. */
const EDITABLE: ReadonlyArray<Idea['status']> = ['DRAFT', 'CHANGES_REQUESTED']

type Load =
  | { state: 'loading' }
  | { state: 'ready'; idea: Idea }
  | { state: 'unavailable' }
  | { state: 'error' }

/**
 * Editing an idea at `/app/ideas/:ideaId/edit` - a draft, or an idea a
 * reviewer sent back with changes requested.
 *
 * The one place an idea is edited: the same guided form a new idea is filed
 * with, on a page of its own, rather than a second, smaller editor beside the
 * list. It shows the idea the server returns and offers the form only when
 * it is the viewer's own and still editable - a courtesy, since `updateIdea`
 * refuses anything else whatever is rendered - and, like the create page,
 * returns to `/app/ideas` with the confirmation in router state.
 */
export function EditIdeaPage() {
  const { ideaId = '' } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const [load, setLoad] = useState<Load>({ state: 'loading' })

  useEffect(() => {
    let cancelled = false
    ideaRequest(ideaId)
      .then((idea) => {
        if (!cancelled) setLoad(idea ? { state: 'ready', idea } : { state: 'unavailable' })
      })
      .catch(() => {
        if (!cancelled) setLoad({ state: 'error' })
      })
    return () => {
      cancelled = true
    }
  }, [ideaId])

  function handleSaved(idea: Idea) {
    const state: IdeasRedirectState = {
      notice: savedIdeaNotice(idea),
      tone: 'success',
      ideaId: idea.id,
    }
    navigate(IDEAS_PATH, { replace: true, state })
  }

  const idea = load.state === 'ready' ? load.idea : null
  const editable = idea !== null && idea.authorId === user?.id && EDITABLE.includes(idea.status)

  return (
    <div className="flex w-full max-w-none flex-col lg:h-[calc(100dvh-var(--app-chrome-height,0px)-4.5rem)]">
      <div className="sticky top-[var(--app-chrome-height)] z-20 -mt-8 border-b border-gray-200 bg-slate-50 pt-8 pb-3">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          {idea?.status === 'CHANGES_REQUESTED' ? 'Revise your idea.' : 'Edit your draft.'}
        </h1>
      </div>

      <div className="mt-4 flex flex-col lg:min-h-0 lg:flex-1">
        {load.state === 'loading' ? (
          <p className="text-sm text-gray-600">Loading your idea…</p>
        ) : load.state === 'error' ? (
          <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
            We could not load this idea. Please refresh the page.
          </p>
        ) : editable && idea ? (
          <IdeaForm
            organizationId={idea.organizationId}
            idea={idea}
            onSaved={handleSaved}
            onCancel={() => navigate(IDEAS_PATH)}
          />
        ) : (
          <div className="rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600 shadow-sm">
            <p>
              {load.state === 'unavailable'
                ? 'This idea is not available.'
                : 'Only your own draft, or an idea a reviewer sent back for changes, can be edited.'}
            </p>
            <Link
              to={IDEAS_PATH}
              className="mt-3 inline-block font-semibold text-brand-700 hover:text-brand-800"
            >
              Back to your ideas
            </Link>
          </div>
        )}
      </div>
    </div>
  )
}
