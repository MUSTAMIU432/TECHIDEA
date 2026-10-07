import type { IdeaVisibility, SubmissionContext } from '../api/ideasApi'

/**
 * The wording for the two facts that are not the same fact.
 *
 * Ownership answers *whose* idea this is; visibility answers *who may read it*.
 * They live in one module because they are always drawn together, and in two
 * separate maps because merging them is the mistake this module exists to
 * prevent: a single "Team" pill would have to mean either "belongs to a team" or
 * "readable by a team", and it would be read as both.
 */

const OWNERSHIP_TITLES: Record<SubmissionContext, string> = {
  INDIVIDUAL: 'Individual Idea',
  TEAM: 'Team Idea',
  ORGANIZATION: 'Organization Idea',
}

/**
 * What each audience means, in a phrase rather than an enum name.
 *
 * `ORGANIZATION` and `TEAM` are spelled "Organization only" and "Team only" for
 * the same reason: a bare word on a card reads as the kind of idea rather than
 * the audience.
 */
const VISIBILITY_TITLES: Record<IdeaVisibility, string> = {
  PUBLIC: 'Public',
  ORGANIZATION: 'Organization only',
  TEAM: 'Team only',
  PRIVATE: 'Private',
  DEPARTMENT: 'A department',
}

export function ownershipTitle(context: SubmissionContext): string {
  return OWNERSHIP_TITLES[context] ?? context
}

export function visibilityTitle(visibility: IdeaVisibility): string {
  return VISIBILITY_TITLES[visibility] ?? visibility
}

/**
 * Who owns an idea, in one string.
 *
 * A team or organization idea names its tenant - `tenantName` comes from the
 * server for whichever the context names, so this costs no extra request and no
 * per-card lookup.
 *
 * An individual idea names its author, because the author *is* the owner there.
 * The API deliberately carries no author's name on an idea: a `PUBLIC` idea is
 * readable platform-wide, so a name would be published with it. That leaves the
 * two honest options, and this is the function that picks between them:
 * "you" when the reader is the author, "the person who filed it" when they are
 * not. Neither invents a name, and the distinction that matters to a reader - is
 * this mine - is still answered.
 */
export function ownerNameFor(
  context: SubmissionContext,
  tenantName: string,
  authorId: string,
  viewerId: string | null,
): string {
  if (context !== 'INDIVIDUAL') return tenantName
  return authorId === viewerId ? 'you' : 'the person who filed it'
}
