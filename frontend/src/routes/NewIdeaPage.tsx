import { useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import type { Idea, SubmissionContext } from '../features/ideas/api/ideasApi'
import {
  IdeaForm,
  IdeaContextBanner,
  type SubmitOutcome,
} from '../features/ideas/components/IdeaForm'
import {
  createdDraftNotice,
  submittedIdeaNotice,
  type IdeasRedirectState,
} from '../features/ideas/utils/savedIdeaNotice'
import { useOrganization } from '../features/organizations/context/useOrganization'
import { useMyTeams } from '../features/teams/hooks/useMyTeams'

/** Where this page sends the reader when it is done - saved or cancelled. */
export const IDEAS_PATH = '/app/ideas'

/**
 * Filing a new idea at `/app/ideas/new`.
 *
 * **The URL carries the ownership decision, not React state.** `?context=team&
 * team=9` is the whole of it, and it is a real address: the reader can bookmark
 * it, the back button works, and a Team page's "Create idea" button can link
 * straight here. It also means the form cannot be in a state the URL does not
 * describe — there is no "which context is it again?" to answer after a reload.
 *
 * Three ways in, and they are deliberately not the same journey:
 *
 * - **from a team**, `?context=team&team=9` — the team is already known, so the
 *   dialog is skipped entirely and the form opens locked to it;
 * - **from an organization**, `?context=organization&organization=3` — the same,
 *   with the organization's id rather than the header's active one, because the
 *   reader named a specific organization;
 * - **from the global button**, which opens the "Where does this idea belong?"
 *   dialog first and only then navigates here with a context in hand.
 *
 * A URL with no `context` is a page opened directly, and the form offers all
 * three ways in its own step 7 — the older journey, still correct, and now the
 * fallback rather than the front door.
 *
 * Like every `/app` child it inherits `RequireAuth` and the shared
 * `OrganizationProvider`.
 */
export function NewIdeaPage() {
  const navigate = useNavigate()
  const { activeOrganization } = useOrganization()
  const [params] = useSearchParams()

  const locked = useChosenContext(
    params.get('context'),
    params.get('team'),
    params.get('organization'),
  )

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
    <div className="flex w-full max-w-none flex-col lg:h-[calc(100dvh-var(--app-chrome-height,0px)-4.5rem)]">
      {/*
        Pinned under the app's header and breadcrumb (whose height the layout
        publishes as `--app-chrome-height`), so only the form scrolls. The
        context banner sits here rather than inside the form so it stays put
        while the reader moves between the eight steps.
      */}
      <div className="sticky top-[var(--app-chrome-height)] z-20 -mt-8 border-b border-gray-200 bg-slate-50 pt-8 pb-3">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          Tell us about a problem.
        </h1>
        {locked && (
          <div className="mt-3">
            <IdeaContextBanner context={locked} />
          </div>
        )}
      </div>

      <div className="mt-4 flex flex-col lg:min-h-0 lg:flex-1">
        <IdeaForm
          organizationId={locked?.organizationId ?? activeOrganization?.id ?? null}
          lockedContext={locked}
          onSaved={handleSaved}
          onSubmitted={handleSubmitted}
          onCancel={() => navigate(IDEAS_PATH)}
        />
      </div>
    </div>
  )
}

/**
 * The ownership the URL names, resolved against the reader's own memberships —
 * or `null` when the URL does not name one that can be used.
 *
 * **Resolved here rather than trusted.** A pasted or hand-edited
 * `?context=team&team=999` cannot put an idea into a team the reader is not on:
 * the id is matched against `useMyTeams`, and one that is not in the answer is
 * treated as no choice at all, so the page falls back to the form's own step
 * rather than opening a form that would be refused on save. The server makes the
 * same check independently on create; this is what keeps the page honest before
 * anybody presses anything.
 */
function useChosenContext(
  context: string | null,
  teamId: string | null,
  organizationId: string | null,
) {
  const { teams } = useMyTeams()
  const { memberships } = useOrganization()

  return useMemo(() => {
    if (context === 'INDIVIDUAL') {
      return {
        context: 'INDIVIDUAL' as SubmissionContext,
        ownerName: 'yourself',
      }
    }

    if (context === 'TEAM') {
      const team = teams.find((entry) => entry.id === teamId)
      // No team, or not one of the reader's: no choice. The server would refuse
      // it, so the form must not offer it either.
      if (!team) return null
      return {
        context: 'TEAM' as SubmissionContext,
        teamId: team.id,
        ownerName: team.name,
      }
    }

    if (context === 'ORGANIZATION') {
      const membership = memberships.find((entry) => entry.organization.id === organizationId)
      if (!membership) return null
      return {
        context: 'ORGANIZATION' as SubmissionContext,
        organizationId: membership.organization.id,
        ownerName: membership.organization.name,
      }
    }

    return null
    // `teams` and `memberships` are the authorities; re-resolving on every
    // identity change means a reader who leaves a team mid-session cannot keep
    // filing for it.
  }, [context, teamId, organizationId, teams, memberships])
}
