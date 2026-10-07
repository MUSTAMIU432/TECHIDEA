import { useState, type FormEvent } from 'react'

import {
  createReviewTeamRequest,
  grantReviewerRequest,
  reviewersRequest,
  reviewTeamsRequest,
  revokeReviewerRequest,
  updateReviewTeamRequest,
  type Outcome,
  type ReviewTeam,
  type Reviewer,
} from '../api/reviewersApi'
import {
  AdminCard,
  AdminPageHeader,
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  controlClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
} from '../components/AdminUi'
import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { useAdminQuery } from '../hooks/useAdminQuery'

/** Runs one action and keeps the server's own words for the result. */
function useAction() {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  async function run(action: () => Promise<Outcome>, onDone: () => void) {
    setBusy(true)
    setNotice(null)
    try {
      const result = await action()
      setNotice({ tone: result.success ? 'success' : 'error', text: result.message })
      if (result.success) onDone()
    } catch {
      setNotice({ tone: 'error', text: 'We could not reach the server. Please try again.' })
    } finally {
      setBusy(false)
    }
  }
  return { busy, notice, run }
}

function ReviewerList({
  reviewers,
  canManage,
  onChanged,
}: {
  reviewers: Reviewer[]
  canManage: boolean
  onChanged: () => void
}) {
  const grant = useAction()
  const revoke = useAction()
  const [email, setEmail] = useState('')

  return (
    <AdminCard title="Reviewers">
      <p className="mb-3 text-sm text-slate-600">
        People who can review submissions for the platform. They are made from existing accounts.
      </p>
      {canManage && (
        <form
          aria-label="Make a reviewer"
          className="mb-4 flex flex-wrap items-end gap-2"
          onSubmit={(event: FormEvent) => {
            event.preventDefault()
            void grant.run(
              () => grantReviewerRequest(email),
              () => {
                setEmail('')
                onChanged()
              },
            )
          }}
        >
          <label className="text-sm font-semibold text-slate-700">
            Account email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className={`${controlClasses} mt-1 block w-72`}
              placeholder="person@example.com"
            />
          </label>
          <button
            type="submit"
            disabled={grant.busy || email.trim() === ''}
            className={primaryButtonClasses}
          >
            Make reviewer
          </button>
        </form>
      )}
      {grant.notice && <Notice tone={grant.notice.tone}>{grant.notice.text}</Notice>}
      {revoke.notice && <Notice tone={revoke.notice.tone}>{revoke.notice.text}</Notice>}
      {reviewers.length === 0 ? (
        <EmptyState title="No reviewers yet." description="Make an account a reviewer to begin." />
      ) : (
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {reviewers.map((reviewer) => (
            <li
              key={reviewer.id}
              className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
            >
              <span>
                <span className="block text-sm font-semibold text-slate-900">{reviewer.name}</span>
                <span className="block text-xs text-slate-500">{reviewer.email}</span>
              </span>
              {canManage && (
                <button
                  type="button"
                  disabled={revoke.busy}
                  className={secondaryButtonClasses}
                  onClick={() =>
                    void revoke.run(() => revokeReviewerRequest(reviewer.id), onChanged)
                  }
                >
                  Remove as reviewer
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </AdminCard>
  )
}

function NewTeamForm({ reviewers, onCreated }: { reviewers: Reviewer[]; onCreated: () => void }) {
  const create = useAction()
  const [name, setName] = useState('')
  const [leadId, setLeadId] = useState('')
  const [memberIds, setMemberIds] = useState<string[]>([])
  const toggle = (id: string) =>
    setMemberIds((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    )

  return (
    <form
      aria-label="Form a review team"
      className="space-y-3"
      onSubmit={(event: FormEvent) => {
        event.preventDefault()
        void create.run(
          () => createReviewTeamRequest(name, leadId, memberIds),
          () => {
            setName('')
            setLeadId('')
            setMemberIds([])
            onCreated()
          },
        )
      }}
    >
      {create.notice && <Notice tone={create.notice.tone}>{create.notice.text}</Notice>}
      <label className="block text-sm font-semibold text-slate-700">
        Team name
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          className={`${controlClasses} mt-1 block w-72`}
        />
      </label>
      <label className="block text-sm font-semibold text-slate-700">
        Lead
        <select
          value={leadId}
          onChange={(event) => setLeadId(event.target.value)}
          className={`${controlClasses} mt-1 block w-72`}
        >
          <option value="">Choose the lead…</option>
          {reviewers.map((reviewer) => (
            <option key={reviewer.id} value={reviewer.id}>
              {reviewer.name}
            </option>
          ))}
        </select>
      </label>
      <fieldset>
        <legend className="text-sm font-semibold text-slate-700">Members</legend>
        <div className="mt-1 space-y-1">
          {reviewers.map((reviewer) => (
            <label key={reviewer.id} className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={memberIds.includes(reviewer.id)}
                onChange={() => toggle(reviewer.id)}
              />
              {reviewer.name}
            </label>
          ))}
        </div>
      </fieldset>
      <button
        type="submit"
        disabled={create.busy || name.trim() === '' || leadId === ''}
        className={primaryButtonClasses}
      >
        Create team
      </button>
    </form>
  )
}

function TeamRow({
  team,
  canManage,
  onChanged,
}: {
  team: ReviewTeam
  canManage: boolean
  onChanged: () => void
}) {
  const change = useAction()
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-slate-900">{team.name}</p>
          <p className="mt-1 text-xs text-slate-500">
            {team.members.map((m) => (m.isLead ? `${m.name} (lead)` : m.name)).join(', ')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={team.isActive ? 'good' : 'neutral'}>
            {team.isActive ? 'Active' : 'Retired'}
          </Badge>
          {canManage && (
            <button
              type="button"
              disabled={change.busy}
              className={secondaryButtonClasses}
              onClick={() =>
                void change.run(
                  () => updateReviewTeamRequest(team.id, { isActive: !team.isActive }),
                  onChanged,
                )
              }
            >
              {team.isActive ? 'Retire' : 'Restore'}
            </button>
          )}
        </div>
      </div>
      {change.notice && <Notice tone={change.notice.tone}>{change.notice.text}</Notice>}
    </li>
  )
}

/**
 * Who reviews for the platform, and the teams they work in.
 *
 * A team has one lead and any number of members. Every member can send an idea back to
 * its owner; only the lead approves or rejects. The forms are offered to someone who can
 * manage reviewers; the server decides regardless.
 */
export function AdminReviewersPage() {
  const { capabilities } = useAdminCapabilities()
  const canManage = capabilities.canManageReviewers
  const reviewers = useAdminQuery(
    'admin-reviewers',
    reviewersRequest,
    'We could not load the reviewers.',
  )
  const teams = useAdminQuery(
    'admin-review-teams',
    reviewTeamsRequest,
    'We could not load the teams.',
  )
  const reload = () => {
    reviewers.reload()
    teams.reload()
  }

  return (
    <>
      <AdminPageHeader
        title="Reviewers"
        description="Create platform reviewers and form them into review teams. A team has one lead; every member can send an idea back for changes, and only the lead approves."
      />
      {reviewers.error && <ErrorState message={reviewers.error} onRetry={reviewers.reload} />}
      {reviewers.loading && !reviewers.data && <LoadingState label="Loading reviewers…" />}
      {reviewers.data && (
        <div className="space-y-6">
          <ReviewerList reviewers={reviewers.data} canManage={canManage} onChanged={reload} />

          <AdminCard title="Review teams">
            {teams.error && <ErrorState message={teams.error} onRetry={teams.reload} />}
            {teams.data && teams.data.length === 0 && (
              <EmptyState title="No review teams yet." description="Form one below." />
            )}
            {teams.data && teams.data.length > 0 && (
              <ul className="mb-5 space-y-3">
                {teams.data.map((team) => (
                  <TeamRow key={team.id} team={team} canManage={canManage} onChanged={reload} />
                ))}
              </ul>
            )}
            {canManage && <NewTeamForm reviewers={reviewers.data} onCreated={reload} />}
          </AdminCard>
        </div>
      )}
    </>
  )
}
