import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { SpinnerIcon } from '../../identity/components/icons'
import { useAuth } from '../../identity/auth/AuthContext'
import { TeamInvitations } from '../../invitations/components/TeamInvitations'
import { formatDate } from '../../reviews/utils/reviewLabels'
import { useTeamMembers } from '../hooks/useTeamMembers'
import {
  leaveTeamRequest,
  setTeamReviewerRequest,
  teamRequest,
  teamRoleLabel,
  type Team,
} from '../api/teamsApi'
import { TeamAddMember } from './TeamAddMember'
import { TeamReviewQueue } from './TeamReviewQueue'
import { TeamIdeasPanel } from './TeamIdeasPanel'

/**
 * One team at `/app/teams/:teamId`: who is in it, what can be done about that,
 * and the invitations it has sent.
 *
 * **What this screen can show is the server's decision.** `team(id)` answers
 * null for a team the caller is not in *and* for one that does not exist, and
 * this page says only that it is unavailable — so a team id cannot be used to
 * find out whether some other team's team exists.
 *
 * Four things are on offer, and each is drawn from what the server said rather
 * than from a client-side rule: the roster, the invitations (for somebody who
 * can send them), adding a colleague who already has an account, and leaving.
 * There is no "can remove this member" control — the server owns team
 * membership, and a control that offered a removal it would refuse is worse
 * than none.
 */
/** An owner's control for who checks the team's ideas. The server decides who may use it. */
function ReviewerToggle({
  teamId,
  userId,
  isReviewer,
  onChanged,
}: {
  teamId: string
  userId: string
  isReviewer: boolean
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  return (
    <span className="flex flex-col items-end">
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setBusy(true)
          setFailed(null)
          setTeamReviewerRequest(teamId, userId, !isReviewer)
            .then((result) => {
              if (result.success) onChanged()
              else setFailed(result.message)
            })
            .catch(() => setFailed('We could not reach the server. Please try again.'))
            .finally(() => setBusy(false))
        }}
        className="rounded border border-gray-300 px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-60"
      >
        {isReviewer ? 'Remove as reviewer' : 'Make reviewer'}
      </button>
      {failed && (
        <span role="alert" className="mt-1 text-xs text-red-700">
          {failed}
        </span>
      )}
    </span>
  )
}

export function TeamWorkspace() {
  const { teamId = '' } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const [leaving, setLeaving] = useState(false)
  const roster = useTeamMembers(teamId)

  /*
    The team and its roster are keyed on the id in the URL, so an answer for one
    team can never be rendered under another's name - which is what a plain
    `useState` would do for the frame between a navigation and its request
    landing. `loading` is *derived* from that key rather than written as a state
    transition, for the reason `useMyTeams` gives: a write inside the effect
    would be a second render to express something knowable already.

    Three answers, not two, because "there is no such team" and "you are not in
    it" are the same answer from the server but neither is "we could not ask".
  */
  const [answer, setAnswer] = useState<{
    teamId: string
    team: Team | null
    failed: boolean
  } | null>(null)

  useEffect(() => {
    let cancelled = false
    teamRequest(teamId)
      .then((found) => {
        if (cancelled) return
        setAnswer({ teamId, team: found, failed: false })
      })
      .catch(() => {
        if (cancelled) return
        setAnswer({ teamId, team: null, failed: true })
      })
    return () => {
      cancelled = true
    }
  }, [teamId])

  async function handleLeave() {
    if (answer?.team == null) return
    setLeaving(true)
    setError(null)
    try {
      const result = await leaveTeamRequest(answer.team.id)
      if (!result.success) {
        setError(result.message)
        return
      }
      // The caller is no longer a member, so `team(id)` would now answer null.
      // Going back to the list says that honestly rather than leaving a page
      // whose every request is now unauthorized.
      navigate('/app/teams', { replace: true })
    } catch {
      setError('We could not reach the server, so you are still in the team.')
    } finally {
      setLeaving(false)
    }
  }

  if (answer === null || answer.teamId !== teamId) {
    return <p className="mt-8 text-sm text-gray-600">Loading this team…</p>
  }

  const team = answer.team

  if (team === null) {
    return (
      <div className="mt-8 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600 shadow-sm">
        <p>
          {answer.failed
            ? 'We could not load this team. Please refresh the page.'
            : 'This team is not available.'}
        </p>
        <Link
          to="/app/teams"
          className="mt-3 inline-block font-semibold text-brand-700 hover:text-brand-800"
        >
          Back to your teams
        </Link>
      </div>
    )
  }

  const isOwner = user?.id === team.ownerId

  return (
    <section aria-labelledby="team-heading" className="mt-8">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">Team</p>
          <h1 id="team-heading" className="mt-1 text-3xl font-bold tracking-tight text-gray-900">
            {team.name}
          </h1>
          {team.description ? (
            <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-600">{team.description}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2 self-start">
          {/*
            The team's own entry point, and it goes **straight to the form**: the
            context is not a question on this page, because the page *is* the
            answer. No dialog - the global "File a new idea" button is the only
            place a reader is asked where an idea belongs, and this is not it.
          */}
          <Link
            to={`/app/ideas/new?context=team&team=${team.id}`}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            <span aria-hidden="true">+</span>
            Create Idea
          </Link>
          <button
            type="button"
            onClick={() => void handleLeave()}
            disabled={leaving}
            className="inline-flex h-11 items-center gap-2 rounded-lg border border-gray-300 px-4 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {leaving && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
            {leaving ? 'Leaving…' : 'Leave team'}
          </button>
        </div>
      </div>

      {error ? (
        <p role="alert" className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      ) : null}

      <section aria-labelledby="roster-heading" className="mt-6">
        <h2 id="roster-heading" className="text-base font-semibold text-gray-900">
          Members
        </h2>
        {roster.error ? (
          <p role="alert" className="mt-2 text-sm text-red-700">
            {roster.error}
          </p>
        ) : roster.members.length === 0 ? (
          <p className="mt-2 text-sm text-gray-600">This team has no members listed.</p>
        ) : (
          <ul
            className="mt-3 divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white"
            aria-labelledby="roster-heading"
          >
            {roster.members.map((member) => (
              <li key={member.userId} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900">
                    {[member.firstName, member.lastName].filter(Boolean).join(' ') || member.email}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-gray-500">
                    {member.email}
                  </span>
                </span>
                <span className="text-xs text-gray-500">Joined {formatDate(member.joinedAt)}</span>
                {member.roleSlugs.length > 0 && (
                  <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">
                    {member.roleSlugs.map(teamRoleLabel).join(', ')}
                  </span>
                )}
                {isOwner && member.userId !== team.ownerId && (
                  <ReviewerToggle
                    teamId={team.id}
                    userId={member.userId}
                    isReviewer={member.roleSlugs.includes('reviewer')}
                    onChanged={roster.reload}
                  />
                )}
                {member.userId === team.ownerId && (
                  <span className="rounded-full bg-brand-100 px-2.5 py-1 text-xs font-semibold text-brand-800">
                    Team owner
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Only a reviewer of this team is shown the queue; the server answers empty to
          anybody else, so this narrows what is drawn and decides nothing. */}
      {roster.members.some(
        (member) =>
          member.userId === user?.id &&
          (member.roleSlugs.includes('reviewer') || member.roleSlugs.includes('owner')),
      ) && <TeamReviewQueue teamId={team.id} />}

      {/* The team's own ideas, server-filtered to this team. */}
      <TeamIdeasPanel teamId={team.id} />

      {/*
        Only the owner is offered the add form. `addTeamMember` needs
        `team.members.manage`, which the server grants to the Owner role and
        withholds from Member, and `team.ownerId` is the only statement of that
        this client has — so the check here narrows what is drawn and the server
        still decides. See `TeamAddMember` for why it draws from the
        organization's roster at all.
      */}
      {isOwner ? (
        <TeamAddMember
          team={team}
          memberIds={roster.members.map((member) => member.userId)}
          onAdded={roster.reload}
        />
      ) : null}

      {/*
        Invitations are a tenant's business, not a member's: the server refuses
        the query for anybody who cannot manage members, which answers empty
        rather than erroring. So this panel is offered to every member and
        shows "nobody has been invited" to one who is not allowed to see the
        list — never a claim that is not true.
      */}
      <TeamInvitations team={team} />

      {isOwner ? (
        <p className="mt-4 text-sm text-gray-500">
          You own this team, so it is yours to leave whenever you like. A team needs at least one
          member, so the server will refuse the last one.
        </p>
      ) : null}
    </section>
  )
}
