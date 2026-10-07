import { useState } from 'react'

import { assignIdeaToReviewTeamRequest, reviewTeamsRequest } from '../api/reviewersApi'
import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { AdminCard, Notice, controlClasses, primaryButtonClasses } from './AdminUi'

/** The stages in which a submission can be routed: it has reached the platform. */
const ROUTABLE = ['SUBMITTED', 'UNDER_REVIEW', 'CHANGES_REQUESTED']

/**
 * Route an idea that has reached the platform to a review team.
 *
 * Offered to an administrator who can route work; the server checks the same permission and
 * refuses an idea that is already under review, a retired team, or a team that includes the
 * idea's author - and says so in its own words, which are shown as they arrive.
 */
export function AssignReviewTeam({ ideaId, status }: { ideaId: string; status: string }) {
  const { capabilities } = useAdminCapabilities()
  const teams = useAdminQuery('assign-teams', reviewTeamsRequest, 'We could not load the teams.')
  const [teamId, setTeamId] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)

  if (!capabilities.canAssignPlatformReviewers || !ROUTABLE.includes(status)) return null

  const active = (teams.data ?? []).filter((team) => team.isActive)

  async function assign() {
    setBusy(true)
    setNotice(null)
    try {
      const result = await assignIdeaToReviewTeamRequest(ideaId, teamId)
      setNotice({ tone: result.success ? 'success' : 'error', text: result.message })
    } catch {
      setNotice({ tone: 'error', text: 'We could not reach the server. Please try again.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <AdminCard title="Assign to a review team">
      <p className="mb-3 text-sm text-slate-600">
        The team reads the idea together. Any member can send it back to its owner for changes; only
        the team’s lead approves or rejects.
      </p>
      {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}
      {teams.data && active.length === 0 ? (
        <p className="text-sm text-slate-500">There are no active review teams yet.</p>
      ) : (
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm font-semibold text-slate-700">
            Review team
            <select
              value={teamId}
              onChange={(event) => setTeamId(event.target.value)}
              className={`${controlClasses} mt-1 block w-64`}
            >
              <option value="">Choose a team…</option>
              {active.map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || teamId === ''}
            onClick={() => void assign()}
            className={primaryButtonClasses}
          >
            Assign
          </button>
        </div>
      )}
    </AdminCard>
  )
}
