import { useState } from 'react'

import { ProposalAnswerCard } from '../../proposals/components/ProposalAnswerSummary'
import { ProposalSections } from '../../proposals/components/ProposalSections'
import {
  declineProposalRequest,
  proposalProgressRequest,
  releaseProposalRequest,
  requestProposalChangesRequest,
  sectionLabel,
  statusWords,
  type IdeaProposal,
  type ProposalActivity,
  type ProposalOutcome,
  type ProposalParticipant,
  type ProposalProgress,
} from '../../proposals/api/proposalsApi'
import {
  AdminCard,
  AdminPageHeader,
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  Notice,
  controlClasses,
  dangerButtonClasses,
  primaryButtonClasses,
  secondaryButtonClasses,
} from '../components/AdminUi'
import { useAdminCapabilities } from '../context/useAdminCapabilities'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { formatDateTime } from '../utils/format'

function Decision({ proposal, onDone }: { proposal: IdeaProposal; onDone: () => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)

  async function run(action: () => Promise<ProposalOutcome>) {
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
  const id = proposal.ideaId

  return (
    <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
      {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}
      <label className="block text-sm font-semibold text-slate-700">
        Reason or feedback
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={3}
          className={`${controlClasses} mt-1 block w-full`}
          placeholder="Needed to send it back or to decline it"
        />
      </label>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          className={primaryButtonClasses}
          onClick={() => void run(() => releaseProposalRequest({ ideaId: id }))}
        >
          Release to the owner
        </button>
        <button
          type="button"
          disabled={busy}
          className={secondaryButtonClasses}
          onClick={() =>
            void run(() => requestProposalChangesRequest({ ideaId: id, feedback: text }))
          }
        >
          Send back to the team
        </button>
        <button
          type="button"
          disabled={busy}
          className={dangerButtonClasses}
          onClick={() => void run(() => declineProposalRequest({ ideaId: id, reason: text }))}
        >
          Decline
        </button>
      </div>
    </div>
  )
}

const TONE = {
  not_started: 'neutral',
  draft: 'info',
  changes_requested: 'warn',
  submitted: 'admin',
  released: 'good',
  declined: 'bad',
} as const

const FILTERS = [
  {
    key: 'preparing',
    label: 'Being prepared',
    statuses: ['not_started', 'draft', 'changes_requested'],
  },
  { key: 'waiting', label: 'Waiting for you', statuses: ['submitted'] },
  { key: 'decided', label: 'Decided', statuses: ['released', 'declined'] },
  { key: 'all', label: 'All', statuses: null },
] as const

type FilterKey = (typeof FILTERS)[number]['key']

function inFilter(row: ProposalProgress, key: FilterKey): boolean {
  const statuses = FILTERS.find((filter) => filter.key === key)?.statuses
  return statuses == null || (statuses as readonly string[]).includes(row.status)
}

function list(keys: string[]): string {
  return keys.map(sectionLabel).join(', ')
}

function Progress({ row }: { row: ProposalProgress }) {
  const filled = row.filledSections.length
  const percent = Math.round((filled / Math.max(row.totalSections, 1)) * 100)
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between text-xs text-slate-600">
        <span>
          {filled} of {row.totalSections} sections written
        </span>
        <span className="tabular-nums">{percent}%</span>
      </div>
      <progress
        aria-label={`Progress of the proposal for ${row.ideaTitle}`}
        max={row.totalSections}
        value={filled}
        className="mt-1 block h-2 w-full appearance-none overflow-hidden rounded-full bg-slate-100 [&::-moz-progress-bar]:bg-brand-500 [&::-webkit-progress-bar]:bg-slate-100 [&::-webkit-progress-value]:bg-brand-500"
      />
      <p className="mt-1 text-xs text-slate-500">
        {row.missingRequired.length === 0
          ? 'Every required section is written.'
          : `Still needed before it can be sent: ${list(row.missingRequired)}.`}
      </p>
    </div>
  )
}

const ROLE_WORDS = { lead: 'Lead', member: 'Member', former: 'Former member' } as const

function contributionWords(person: ProposalParticipant): string {
  if (person.contributions === 0) return 'Has not contributed yet'
  const count = `${person.contributions} ${person.contributions === 1 ? 'contribution' : 'contributions'}`
  const sections = person.sections.length ? ` · ${list(person.sections)}` : ''
  return `${count}${sections} · last ${formatDateTime(person.lastContributedAt)}`
}

function Participants({ people }: { people: ProposalParticipant[] }) {
  if (people.length === 0) {
    return <p className="mt-3 text-xs text-slate-500">No review team is assigned to write it.</p>
  }
  return (
    <div className="mt-3">
      <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Participants</p>
      <ul aria-label="Participants" className="mt-1 divide-y divide-slate-100">
        {people.map((person) => (
          <li key={person.email} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5">
            <span className="text-sm font-medium text-slate-900">{person.name}</span>
            <Badge tone={person.role === 'lead' ? 'admin' : 'neutral'}>
              {ROLE_WORDS[person.role]}
            </Badge>
            <span className="text-xs text-slate-500">{contributionWords(person)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

const ACTION_WORDS = {
  started: 'started the proposal',
  edited: 'edited',
  submitted: 'sent it to the admin',
} as const

function Activity({ items }: { items: ProposalActivity[] }) {
  if (items.length === 0) return null
  return (
    <div className="mb-4">
      <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
        Recent activity
      </p>
      <ol aria-label="Recent activity" className="mt-1 space-y-1">
        {items.map((item, index) => (
          <li key={`${item.at}-${index}`} className="text-sm text-slate-700">
            <span className="text-xs text-slate-500 tabular-nums">{formatDateTime(item.at)}</span>{' '}
            <span className="font-medium">{item.name}</span> {ACTION_WORDS[item.action]}
            {item.sections.length > 0 && ` ${list(item.sections)}`}
          </li>
        ))}
      </ol>
    </div>
  )
}

function Row({
  row,
  canDecide,
  onChanged,
}: {
  row: ProposalProgress
  canDecide: boolean
  onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const proposal = row.proposal
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-900">{proposal?.title ?? row.ideaTitle}</p>
          <p className="mt-1 text-xs text-slate-500">
            For “{row.ideaTitle}”{row.teamName ? ` · ${row.teamName}` : ''}
            {proposal ? ` · last change ${formatDateTime(proposal.updatedAt)}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={TONE[row.status as keyof typeof TONE] ?? 'neutral'}>
            {statusWords(row.status)}
          </Badge>
          {proposal && (
            <button
              type="button"
              className={secondaryButtonClasses}
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
            >
              {open ? 'Hide' : 'Read'}
            </button>
          )}
        </div>
      </div>
      <Progress row={row} />
      <Participants people={row.participants} />
      {row.status === 'released' && (
        // Once released, what the owner answered: go ahead (on which terms) or not.
        <ProposalAnswerCard ideaId={row.ideaId} title="The owner's answers" />
      )}
      {open && proposal && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <Activity items={row.activity} />
          {proposal.reviewFeedback && (
            <p className="mb-3 rounded-lg bg-amber-50 p-3 text-sm whitespace-pre-line text-amber-900">
              {proposal.reviewFeedback}
            </p>
          )}
          <ProposalSections proposal={proposal} />
          {canDecide && proposal.status === 'submitted' && (
            <Decision proposal={proposal} onDone={onChanged} />
          )}
        </div>
      )}
    </li>
  )
}

/**
 * Every approved idea's proposal, as far as it has got: not started, being written by its review
 * team, waiting for a decision, or decided - with who is on the team and what each of them has
 * written. Read any of them at any point; release, send back or decline once the lead sends one.
 * The people who wrote a proposal never decide it; the server refuses them even if they hold the
 * permission.
 */
export function AdminProposalsPage() {
  const { capabilities } = useAdminCapabilities()
  const [filter, setFilter] = useState<FilterKey>('all')
  const { data, loading, error, reload } = useAdminQuery(
    'admin-proposal-progress',
    proposalProgressRequest,
    'We could not load the proposals.',
  )
  const shown = data?.filter((row) => inFilter(row, filter)) ?? []
  return (
    <>
      <AdminPageHeader
        title="Proposals"
        description="Follow each approved idea’s proposal as its review team writes it, and see who is taking part. Release one and its owner can read it and give the go-ahead."
      />
      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading proposals…" />}
      {data && data.length === 0 && (
        <EmptyState
          title="No approved ideas yet."
          description="An idea appears here once a review team approves it, and its proposal’s progress shows as the team writes it."
        />
      )}
      {data && data.length > 0 && (
        <AdminCard title="Proposals">
          <fieldset className="mb-4 flex flex-wrap gap-2">
            <legend className="sr-only">Show</legend>
            {FILTERS.map((option) => (
              <button
                key={option.key}
                type="button"
                aria-pressed={filter === option.key}
                onClick={() => setFilter(option.key)}
                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  filter === option.key
                    ? 'bg-brand-600 text-white'
                    : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
                }`}
              >
                {option.label} ({data.filter((row) => inFilter(row, option.key)).length})
              </button>
            ))}
          </fieldset>
          {shown.length === 0 ? (
            <p className="text-sm text-slate-500">Nothing here right now.</p>
          ) : (
            <ul className="space-y-3">
              {shown.map((row) => (
                <Row
                  key={row.ideaId}
                  row={row}
                  canDecide={capabilities.canReleaseProposals}
                  onChanged={reload}
                />
              ))}
            </ul>
          )}
        </AdminCard>
      )}
    </>
  )
}
