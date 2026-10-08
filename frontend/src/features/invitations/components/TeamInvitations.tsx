import { useId, useState, type FormEvent } from 'react'

import {
  fieldErrorClasses,
  inputClasses,
  labelClasses,
} from '../../identity/components/fieldStyles'
import { SpinnerIcon } from '../../identity/components/icons'
import {
  revokeInvitationRequest,
  sendTeamInvitationRequest,
  type Invitation,
} from '../api/invitationsApi'
import { useTeamInvitations } from '../hooks/useInvitations'
import type { Team } from '../../teams/api/teamsApi'

/**
 * Invite somebody to a team by email, and the list of what has been sent.
 *
 * **The email is not a membership.** What this writes is an invitation: the
 * recipient gets a link, and only accepting it puts them on the roster. So a
 * success here does not change the member count, and the panel says so rather
 * than letting the reader assume otherwise.
 *
 * Inviting somebody who already has an account is also an invitation, not an
 * add: `addTeamMember` needs a user id, which this screen has no way to obtain
 * honestly — it knows an address, not a person. The panel above this one adds
 * colleagues from the organization's roster, which is the only list of people
 * this client can name. Somebody who is already on the roster is offered nothing
 * at all, because the server would refuse it.
 */
export function TeamInvitations({ team }: { team: Team }) {
  const { invitations, loading, error, reload } = useTeamInvitations(team.id)
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [field, setField] = useState<string | null>(null)
  const emailId = useId()
  const messageId = useId()

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage(null)
    setField(null)

    if (!email.trim()) {
      setMessage('Enter the address to invite.')
      setField('email')
      return
    }

    setSending(true)
    try {
      const result = await sendTeamInvitationRequest(team.id, email.trim())
      if (!result.success) {
        setMessage(result.message)
        setField(result.field)
        return
      }
      setEmail('')
      setMessage(result.message)
      reload()
    } catch {
      setMessage('We could not reach the server, so no invitation was sent. Please try again.')
    } finally {
      setSending(false)
    }
  }

  async function handleRevoke(invitation: Invitation) {
    setMessage(null)
    setPendingId(invitation.id)
    try {
      const result = await revokeInvitationRequest(invitation.id)
      setMessage(result.success ? result.message : result.message)
      if (result.success) reload()
    } catch {
      setMessage('We could not reach the server, so the invitation was not revoked.')
    } finally {
      setPendingId(null)
    }
  }

  return (
    <section aria-labelledby={`team-invitations-${team.id}`} className="mt-6">
      <h3 id={`team-invitations-${team.id}`} className="text-base font-semibold text-gray-900">
        Invite somebody to {team.name}
      </h3>
      <p className="mt-1 text-sm leading-6 text-gray-600">
        They get an email with a link. They join when they accept it — nobody is added to the team
        before then.
      </p>

      <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={handleSubmit} noValidate>
        <div className="min-w-0 flex-1">
          <label className={labelClasses} htmlFor={emailId}>
            Email address
          </label>
          <input
            id={emailId}
            type="email"
            className={`${inputClasses(field === 'email')} w-full`}
            placeholder="name@example.com"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-describedby={message ? messageId : undefined}
            aria-invalid={field === 'email'}
          />
        </div>
        <button
          type="submit"
          disabled={sending}
          className="inline-flex h-11 items-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {sending && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
          {sending ? 'Sending…' : 'Send invitation'}
        </button>
      </form>

      {message ? (
        <p
          id={messageId}
          role={field ? 'alert' : 'status'}
          className={`mt-3 text-sm ${field ? fieldErrorClasses : 'text-gray-600'}`}
        >
          {message}
        </p>
      ) : null}

      <div className="mt-4">
        {loading ? (
          <p className="text-sm text-gray-500">Loading invitations…</p>
        ) : error ? (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        ) : invitations.length === 0 ? (
          <p className="text-sm text-gray-500">Nobody has been invited yet.</p>
        ) : (
          <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white">
            {invitations.map((invitation) => (
              <li key={invitation.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900">
                    {invitation.email}
                  </span>
                  <span className="mt-0.5 block text-xs text-gray-500">
                    Invited by {invitation.invitedByFirstName} ·{' '}
                    {invitation.isOpen
                      ? 'Waiting to be accepted'
                      : invitation.acceptedAt
                        ? 'Accepted'
                        : invitation.status === 'REVOKED'
                          ? 'Revoked'
                          : 'Expired'}
                  </span>
                </span>
                {invitation.isOpen && (
                  <button
                    type="button"
                    onClick={() => void handleRevoke(invitation)}
                    disabled={pendingId === invitation.id}
                    className="inline-flex items-center gap-2 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {pendingId === invitation.id && (
                      <SpinnerIcon className="h-3.5 w-3.5 motion-safe:animate-spin" />
                    )}
                    Revoke
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
