import { useNavigate } from 'react-router-dom'

import type { Idea } from '../features/ideas/api/ideasApi'
import { IdeaForm, type SubmitOutcome } from '../features/ideas/components/IdeaForm'
import {
  createdDraftNotice,
  submittedIdeaNotice,
  type IdeasRedirectState,
} from '../features/ideas/utils/savedIdeaNotice'
import { useOrganization } from '../features/organizations/context/useOrganization'

/** Where this page sends the reader when it is done - saved or cancelled. */
export const IDEAS_PATH = '/app/ideas'

/**
 * Filing a new idea at `/app/ideas/new`.
 *
 * A page of its own rather than a panel beside the list, so the form has the
 * reader's whole attention and the URL says what they are doing. Both ways out
 * lead to one place, `/app/ideas`:
 *
 * - **saved as a draft, or submitted for review**: replace this entry with
 *   the list and carry the confirmation and the new idea's id in router
 *   state, so the list announces it once, highlights the idea, and Back does
 *   not reopen a form whose idea already exists;
 * - **cancelled**: back to the list, with nothing saved.
 *
 * Like every `/app` child it inherits `RequireAuth` and the shared
 * `OrganizationProvider`. The organization picked in the header is offered as
 * one of the three ways to file, not required: an idea filed on its own, or for
 * a team, names no organization at all, which is why this page renders the form
 * even when the reader is in none. The form draws the organization context
 * disabled-with-a-reason in that case rather than hiding it, so a reader who
 * expected it can see why it is closed.
 */
export function NewIdeaPage() {
  const navigate = useNavigate()
  const { activeOrganization } = useOrganization()

  function returnToIdeas(state: IdeasRedirectState) {
    navigate(IDEAS_PATH, { replace: true, state })
  }

  function handleSaved(idea: Idea, failedUploads?: string[]) {
    returnToIdeas(createdDraftNotice(idea, failedUploads))
  }

  function handleSubmitted(outcome: SubmitOutcome) {
    returnToIdeas(submittedIdeaNotice(outcome))
  }

  return (
    <div className="mx-auto w-full max-w-6xl">
      {/*
       * Pinned under the app's header and breadcrumb (whose height the layout
       * publishes as `--app-chrome-height`), so only the form scrolls.
       */}
      <div className="sticky top-[var(--app-chrome-height)] z-20 -mt-8 border-b border-gray-200 bg-slate-50 pt-8 pb-5">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          Tell us about a problem.
        </h1>
      </div>

      <div className="mt-6">
        <IdeaForm
          organizationId={activeOrganization?.id ?? null}
          onSaved={handleSaved}
          onSubmitted={handleSubmitted}
          onCancel={() => navigate(IDEAS_PATH)}
        />
      </div>
    </div>
  )
}
