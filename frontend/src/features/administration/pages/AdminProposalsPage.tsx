import { useState } from 'react'

import { ProposalSections } from '../../proposals/components/ProposalSections'
import {
  declineProposalRequest,
  proposalsForReleaseRequest,
  releaseProposalRequest,
  requestProposalChangesRequest,
  statusWords,
  type IdeaProposal,
  type ProposalOutcome,
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

function Row({
  proposal,
  canDecide,
  onChanged,
}: {
  proposal: IdeaProposal
  canDecide: boolean
  onChanged: () => void
}) {
  const [open, setOpen] = useState(false)
  const tone =
    proposal.status === 'released'
      ? 'good'
      : proposal.status === 'declined'
        ? 'bad'
        : proposal.status === 'submitted'
          ? 'warn'
          : 'neutral'
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-900">{proposal.title}</p>
          <p className="mt-1 text-xs text-slate-500">
            For “{proposal.ideaTitle}”
            {proposal.teamName ? ` · written by ${proposal.teamName}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={tone}>{statusWords(proposal.status)}</Badge>
          <button
            type="button"
            className={secondaryButtonClasses}
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? 'Hide' : 'Read'}
          </button>
        </div>
      </div>
      {open && (
        <div className="mt-4">
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
 * Proposals that have reached the admin: read each, then release it to its owner, send it back to
 * the team that wrote it, or decline it. The people who wrote a proposal never decide it; the
 * server refuses them even if they hold the permission.
 */
export function AdminProposalsPage() {
  const { capabilities } = useAdminCapabilities()
  const { data, loading, error, reload } = useAdminQuery(
    'admin-proposals',
    proposalsForReleaseRequest,
    'We could not load the proposals.',
  )
  return (
    <>
      <AdminPageHeader
        title="Proposals"
        description="Proposals written by review teams for approved ideas. Release one and its owner can read it and give the go-ahead."
      />
      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading proposals…" />}
      {data && data.length === 0 && (
        <EmptyState
          title="No proposals have reached you."
          description="They appear once a review team’s lead sends one."
        />
      )}
      {data && data.length > 0 && (
        <AdminCard title="Proposals">
          <ul className="space-y-3">
            {data.map((proposal) => (
              <Row
                key={proposal.ideaId}
                proposal={proposal}
                canDecide={capabilities.canReleaseProposals}
                onChanged={reload}
              />
            ))}
          </ul>
        </AdminCard>
      )}
    </>
  )
}
