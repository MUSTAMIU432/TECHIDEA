import type { IdeaVisibility, SubmissionContext } from '../api/ideasApi'
import { ownershipTitle, visibilityTitle } from '../utils/ownership'

/**
 * The two facts a card has to carry, kept as two things.
 *
 * **Ownership and visibility are not the same question and must never be drawn
 * as one.** An idea can belong to a team and be readable by everybody on the
 * platform; an idea can belong to one person and be readable by nobody else.
 * Collapsing them - showing a single "Team idea" pill and letting a reader infer
 * that the team can see it - is how a reader ends up filing something into the
 * wrong audience and never finds out.
 *
 * So there are two components, one for each, and the pair is what a card shows.
 */

const TONES: Record<SubmissionContext, string> = {
  INDIVIDUAL: 'bg-violet-50 text-violet-800 ring-violet-200',
  TEAM: 'bg-sky-50 text-sky-800 ring-sky-200',
  ORGANIZATION: 'bg-brand-50 text-brand-800 ring-brand-200',
}

const VISIBILITY_TONES: Record<IdeaVisibility, string> = {
  PUBLIC: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  ORGANIZATION: 'bg-amber-50 text-amber-800 ring-amber-200',
  TEAM: 'bg-sky-50 text-sky-800 ring-sky-200',
  PRIVATE: 'bg-gray-100 text-gray-700 ring-gray-200',
  DEPARTMENT: 'bg-gray-100 text-gray-700 ring-gray-200',
}

/**
 * "Team Idea" and, underneath it, who owns it.
 *
 * `ownerName` is the tenant's name from the server, and it is deliberately not
 * the author's name: an individual idea's owner *is* its author, so the two
 * would be the same string said twice.
 */
export function IdeaOwnershipBadge({
  context,
  ownerName,
}: {
  context: SubmissionContext
  ownerName: string
}) {
  const title = ownershipTitle(context)
  return (
    <span className="inline-flex flex-col gap-0.5">
      <span
        className={`inline-flex w-fit items-center rounded-full px-2 py-0.5 text-xs font-bold ring-1 ring-inset ${TONES[context]}`}
      >
        {title}
      </span>
      {ownerName ? (
        <span className="text-xs text-gray-600">
          Owned by <span className="font-semibold text-gray-900">{ownerName}</span>
        </span>
      ) : null}
    </span>
  )
}

/**
 * Who may read it — and only that.
 *
 * Never says "Team" or "Organization" on its own: a bare word here reads as the
 * *kind of idea* rather than the audience, which is the exact confusion the two
 * badges exist to prevent.
 */
export function IdeaVisibilityBadge({ visibility }: { visibility: IdeaVisibility }) {
  return (
    <span
      className={`inline-flex w-fit items-center rounded-full px-2 py-0.5 text-xs font-bold ring-1 ring-inset ${VISIBILITY_TONES[visibility]}`}
    >
      {visibilityTitle(visibility)}
    </span>
  )
}

/**
 * The one-line answer to "who owns this, and who can see it?" for a card footer
 * or a detail header. Both facts, side by side, never merged.
 */
/**
 * The pair, for a place that shows both facts and has room for both.
 *
 * An **empty `ownerName` draws the type badge alone**, which is what a header
 * wants when the owner has its own labelled row directly beneath. The reason to
 * have one component rather than two badges at every call site is that they have
 * to be two facts - but two *copies* of one fact is the opposite mistake.
 */
export function IdeaOwnershipSummary({
  context,
  ownerName,
  visibility,
}: {
  context: SubmissionContext
  /** Empty string: draw the type without an owner line. */
  ownerName: string
  visibility: IdeaVisibility
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      <IdeaOwnershipBadge context={context} ownerName={ownerName} />
      <IdeaVisibilityBadge visibility={visibility} />
    </span>
  )
}
