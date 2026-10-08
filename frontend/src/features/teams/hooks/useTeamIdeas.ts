import { useCallback, useEffect, useState } from 'react'

import {
  teamIdeasRequest,
  type Idea,
  type IdeaPageInfo,
  type SubmissionContext,
} from '../../ideas/api/ideasApi'

/**
 * One team's ideas, from the server.
 *
 * **Server-side, and only the team's.** `teamIdeas` is scoped to the team the
 * reader is a member of and filtered by visibility, so this hook cannot be
 * handed a team it may not see and cannot return a member's private idea from
 * the team's own feed. Both answers for a team the reader is not on are the same
 * one - an empty page - which is also what a team that does not exist gives.
 *
 * The keying is the discipline `useMyTeams` and `useTeamMembers` already use:
 * an answer is matched to the query that produced it by comparing keys during
 * render, `loading` is derived rather than written by the effect, and a failed
 * refresh keeps the rows on screen rather than emptying the list into a lie.
 */
export interface TeamIdeas {
  ideas: Idea[]
  pageInfo: IdeaPageInfo | null
  loading: boolean
  error: string | null
  reload: () => void
}

const FAILED = 'We could not load this team’s ideas. Please try again.'
const NONE: Idea[] = []

export function useTeamIdeas(teamId: string | null): TeamIdeas {
  const [token, setToken] = useState(0)
  const [answer, setAnswer] = useState<{
    teamId: string | null
    token: number
    ideas: Idea[]
    pageInfo: IdeaPageInfo
  } | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)

  // One team's ideas must never be rendered under another's name.
  if (answer !== null && answer.teamId !== teamId) setAnswer(null)

  const shownToken = answer !== null && answer.teamId === teamId ? answer.token : -1
  const key = `${teamId ?? 'none'}:${token}`

  useEffect(() => {
    if (teamId === null) return
    let cancelled = false
    teamIdeasRequest(teamId)
      .then((page) => {
        if (cancelled) return
        setAnswer({ teamId, token, ideas: page.items, pageInfo: page.pageInfo })
        setErrorKey(null)
      })
      .catch(() => {
        if (cancelled) return
        setErrorKey(key)
      })
    return () => {
      cancelled = true
    }
  }, [key, teamId, token])

  const reload = useCallback(() => setToken((value) => value + 1), [])

  const fresh = shownToken === token
  const failed = errorKey === key

  return {
    ideas: shownToken === -1 ? NONE : (answer?.ideas ?? NONE),
    pageInfo: shownToken === -1 ? null : (answer?.pageInfo ?? null),
    loading: teamId !== null && !fresh && !failed,
    error: failed ? FAILED : null,
    reload,
  }
}

/**
 * The owner a team's ideas are filed for, for the empty state's own wording.
 *
 * A team is a collaboration boundary, so this is never "your organization" and
 * never "just you" - and a team idea that somebody has made public is still
 * that team's, which is the sentence the empty state exists to teach.
 */
export const TEAM_OWNER_TITLES: Record<SubmissionContext, string> = {
  INDIVIDUAL: 'Individual Idea',
  TEAM: 'Team Idea',
  ORGANIZATION: 'Organization Idea',
}
