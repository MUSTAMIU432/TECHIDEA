import { useId, useState } from 'react'

import { useOrganization } from '../../organizations/context/useOrganization'
import { useOrganizationMembers } from '../../organizations/hooks/useOrganizationMembers'
import {
  fieldErrorClasses,
  inputClasses,
  labelClasses,
} from '../../identity/components/fieldStyles'
import { SpinnerIcon } from '../../identity/components/icons'
import { addTeamMemberRequest, type Team } from '../api/teamsApi'

/**
 * Add somebody who already has an account to a team, as a plain Member.
 *
 * **The other way in, not the main one.** The invitation panel below this is
 * how somebody joins a team: it works for an address with no account, and it is
 * revocable, expiring and single-use, because an email address typed into a form
 * is not consent. This panel is for the one case the invitation flow cannot
 * serve — a colleague the reader already works with, who needs no round trip.
 *
 * **The candidates come from the reader's organization roster**, which is the
 * only list of people this client can name honestly: there is no people
 * directory in this API, and typing an email here would be an invitation wearing
 * a different hat. So the panel asks the organization for its members, drops
 * the ones already on the team, and offers what is left. Somebody outside that
 * organization is invited by email instead, one screen down.
 *
 * **Drawn for the owner alone.** `addTeamMember` needs `team.members.manage`,
 * which the server grants to the Owner role and withholds from Member, and
 * `team.ownerId` is the only statement of that this client has. That is a
 * narrowing of what is offered, never a grant: the server asks again, and a
 * refusal is shown as the server's own message.
 */
export function TeamAddMember({
  team,
  memberIds,
  onAdded,
}: {
  team: Team
  /** Who is already on the roster, so they are not offered again. */
  memberIds: string[]
  onAdded: () => void
}) {
  const { activeOrganization } = useOrganization()
  const roster = useOrganizationMembers(activeOrganization?.id ?? null)
  const [userId, setUserId] = useState('')
  const [adding, setAdding] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [refused, setRefused] = useState(false)
  const selectId = useId()
  const messageId = useId()

  const candidates = roster.members.filter(
    (membership) => membership.status === 'active' && !memberIds.includes(membership.user.id),
  )

  async function handleAdd() {
    if (userId === '') return
    setAdding(true)
    setMessage(null)
    setRefused(false)
    try {
      const result = await addTeamMemberRequest(team.id, userId)
      setMessage(result.message)
      setRefused(!result.success)
      if (result.success) {
        setUserId('')
        // The roster this panel reads is now one person short of the truth, and
        // so is the count on the team beside it — both are the caller's own.
        onAdded()
      }
    } catch {
      setMessage('We could not reach the server, so nobody was added. Please try again.')
      setRefused(true)
    } finally {
      setAdding(false)
    }
  }

  // No organization, so no roster to name anybody from. The invitation panel is
  // the way in, so this says so rather than offering an empty picker.
  if (activeOrganization === null) {
    return (
      <p className="mt-6 text-sm leading-6 text-gray-600">
        You have no organization, so there is no roster to add somebody from. Invite by email
        instead — they join when they accept.
      </p>
    )
  }

  return (
    <section aria-labelledby={`team-add-${team.id}`} className="mt-6">
      <h3 id={`team-add-${team.id}`} className="text-base font-semibold text-gray-900">
        Add somebody from {activeOrganization.name}
      </h3>
      <p className="mt-1 text-sm leading-6 text-gray-600">
        Somebody who already has an account joins straight away, as a Member. They will not need to
        accept anything.
      </p>

      {roster.error ? (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {roster.error}
        </p>
      ) : roster.loading ? (
        <p className="mt-3 text-sm text-gray-500">Loading your colleagues…</p>
      ) : candidates.length === 0 ? (
        <p className="mt-3 text-sm text-gray-600">
          Everybody in {activeOrganization.name} is already on this team. Invite somebody else by
          email below.
        </p>
      ) : (
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1">
            <label className={labelClasses} htmlFor={selectId}>
              Colleague
            </label>
            <select
              id={selectId}
              className={`${inputClasses(refused)} w-full`}
              value={userId}
              onChange={(event) => {
                setUserId(event.target.value)
                setMessage(null)
                setRefused(false)
              }}
              aria-describedby={message ? messageId : undefined}
              aria-invalid={refused}
            >
              <option value="">Choose somebody…</option>
              {candidates.map((membership) => (
                <option key={membership.user.id} value={membership.user.id}>
                  {[membership.user.firstName, membership.user.lastName]
                    .filter(Boolean)
                    .join(' ') || membership.user.email}{' '}
                  — {membership.user.email}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            onClick={() => void handleAdd()}
            disabled={adding || userId === ''}
            className="inline-flex h-11 items-center gap-2 rounded-lg border border-gray-300 px-4 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {adding && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
            {adding ? 'Adding…' : 'Add to team'}
          </button>
        </div>
      )}

      {message ? (
        <p
          id={messageId}
          role={refused ? 'alert' : 'status'}
          className={`mt-3 text-sm ${refused ? fieldErrorClasses : 'text-gray-600'}`}
        >
          {message}
        </p>
      ) : null}
    </section>
  )
}
