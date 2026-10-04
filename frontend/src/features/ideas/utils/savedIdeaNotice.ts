import type { Idea, IdeaVisibility } from '../api/ideasApi'
import type { SubmitOutcome } from '../components/IdeaForm'

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

/**
 * The confirmation shown after an idea is saved. Shared by the inline edit
 * form on `/app/ideas` and the create page at `/app/ideas/new`, which hands
 * it back to the list through the redirect's router state.
 */
export function savedIdeaNotice(idea: Idea): string {
  return draftNotice(idea)
}

/**
 * The same sentence about files that could not be attached, wherever an idea
 * was saved with some: the idea exists, and the author can add the missing
 * files from its supporting evidence.
 */
function missingFilesNotice(failedUploads: readonly string[] | undefined): string {
  if (!failedUploads || failedUploads.length === 0) return ''
  const names = failedUploads.join(', ')
  return ` ${failedUploads.length === 1 ? 'This file' : 'These files'} could not be attached: ${names}. You can add ${failedUploads.length === 1 ? 'it' : 'them'} from the idea's supporting evidence.`
}

/**
 * The confirmation for a new idea saved as a draft from `/app/ideas/new`,
 * including any supporting document that could not be attached.
 */
export function createdDraftNotice(
  idea: Idea,
  failedUploads?: readonly string[],
): IdeasRedirectState {
  const missing = missingFilesNotice(failedUploads)
  return {
    notice: `${draftNotice(idea)}${missing}`,
    tone: missing ? 'warning' : 'success',
    ideaId: idea.id,
  }
}

function draftNotice(idea: Idea): string {
  if (idea.status === 'DRAFT') return `Draft saved. ${DRAFT_READERS[idea.visibility]}`
  if (idea.status === 'CHANGES_REQUESTED') {
    return 'Changes saved. Use Submit again when the idea is ready for the next review.'
  }
  return 'Idea saved.'
}

/**
 * The confirmation for "Submit for review" on the create page. When the
 * second request was refused the idea is a draft, and the reason is kept so
 * the author knows what to change before using Submit on the list.
 */
export function submittedIdeaNotice(outcome: SubmitOutcome): IdeasRedirectState {
  const missing = missingFilesNotice(outcome.failedUploads)
  if (outcome.submitted) {
    return {
      notice: `Your idea "${outcome.idea.title}" was created and submitted for review.${missing}`,
      tone: missing ? 'warning' : 'success',
      ideaId: outcome.idea.id,
    }
  }
  return {
    notice: `Your idea "${outcome.idea.title}" was saved as a draft, but it was not submitted: ${outcome.message}${missing}`,
    tone: 'warning',
    ideaId: outcome.idea.id,
  }
}

export type NoticeTone = 'success' | 'warning'

/**
 * Router state carried by the redirect from `/app/ideas/new` back to the list:
 * what to announce, and which idea to point at.
 */
export interface IdeasRedirectState {
  notice: string
  tone: NoticeTone
  ideaId: string | null
}

export function readIdeasRedirect(state: unknown): IdeasRedirectState | null {
  if (!state || typeof state !== 'object') return null
  const { notice, tone, ideaId } = state as Partial<IdeasRedirectState>
  if (typeof notice !== 'string') return null
  return {
    notice,
    tone: tone === 'warning' ? 'warning' : 'success',
    ideaId: typeof ideaId === 'string' ? ideaId : null,
  }
}
