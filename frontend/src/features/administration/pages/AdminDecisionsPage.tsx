import { useState } from 'react'
import { Link } from 'react-router-dom'

import {
  decisionLettersRequest,
  sendDecisionLetterRequest,
  type DecisionLetter,
} from '../../reviews/api/decisionLettersApi'
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
import { formatDateTime } from '../utils/format'

function DecisionBadge({ decision }: { decision: DecisionLetter['decision'] }) {
  return decision === 'approved' ? (
    <Badge tone="good">Approved</Badge>
  ) : (
    <Badge tone="bad">Rejected</Badge>
  )
}

/**
 * One decision waiting to be sent: what the review team decided and said, and the
 * letter - drafted from a template - which the administrator can adjust before
 * sending it to the owner.
 */
function PendingLetter({
  letter,
  canSend,
  onSent,
}: {
  letter: DecisionLetter
  canSend: boolean
  onSent: () => void
}) {
  const [message, setMessage] = useState(letter.message)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const approved = letter.decision === 'approved'
  const edited = message !== letter.message

  async function send() {
    setBusy(true)
    setNotice(null)
    try {
      const result = await sendDecisionLetterRequest(letter.id, message)
      setNotice({ tone: result.success ? 'success' : 'error', text: result.message })
      if (result.success) onSent()
    } catch {
      setNotice({ tone: 'error', text: 'We could not reach the server. Please try again.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div
        className={`flex flex-wrap items-start justify-between gap-3 border-b px-5 py-4 ${
          approved ? 'border-emerald-100 bg-emerald-50' : 'border-rose-100 bg-rose-50'
        }`}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <DecisionBadge decision={letter.decision} />
            <Link
              to={`/app/admin/ideas/${letter.ideaId}`}
              className="text-base font-bold text-slate-900 hover:underline"
            >
              {letter.ideaTitle}
            </Link>
          </div>
          <p className="mt-1 text-xs text-slate-600">
            Owner: <span className="font-semibold">{letter.ownerName}</span> ({letter.ownerEmail}) ·
            decided by {letter.reviewerName} in round {letter.reviewRound} ·{' '}
            {formatDateTime(letter.createdAt)}
          </p>
        </div>
      </div>

      <div className="space-y-4 px-5 py-4">
        {letter.reviewFeedback && (
          <div>
            <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
              What the review team said
            </p>
            <blockquote className="mt-1 rounded-lg border-l-4 border-slate-300 bg-slate-50 px-3 py-2 text-sm whitespace-pre-line text-slate-700">
              {letter.reviewFeedback}
            </blockquote>
          </div>
        )}

        <div>
          <label
            htmlFor={`letter-${letter.id}`}
            className="flex items-center justify-between text-xs font-semibold tracking-wide text-slate-500 uppercase"
          >
            <span>Letter to the owner {approved ? '(congratulations)' : '(a kind no)'}</span>
            {edited && (
              <button
                type="button"
                onClick={() => setMessage(letter.message)}
                className="text-xs font-semibold tracking-normal text-slate-600 normal-case underline"
              >
                Restore the template
              </button>
            )}
          </label>
          <textarea
            id={`letter-${letter.id}`}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            readOnly={!canSend}
            rows={16}
            // `h-auto` over the shared control height, which is sized for one-line inputs.
            className={`${controlClasses} mt-1 block h-auto min-h-72 w-full py-2 leading-6`}
          />
          <p className="mt-1 text-xs text-slate-500">
            The owner reads this on their idea&apos;s page. Their email only announces it, with a
            button to read it - the letter itself stays behind their sign-in.
          </p>
        </div>

        {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}
        {canSend && (
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={busy || message.trim() === ''}
              onClick={() => void send()}
              className={primaryButtonClasses}
            >
              {busy
                ? 'Sending…'
                : approved
                  ? 'Send to owner and open the proposal'
                  : 'Send to owner'}
            </button>
            <span className="text-xs text-slate-500">
              {approved
                ? 'Until it is sent, the owner sees the idea as still under review and the review team cannot start the proposal.'
                : 'Until it is sent, the owner sees the idea as still under review.'}
            </span>
          </div>
        )}
      </div>
    </li>
  )
}

function SentLetter({ letter }: { letter: DecisionLetter }) {
  const [open, setOpen] = useState(false)
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <DecisionBadge decision={letter.decision} />
            <Link
              to={`/app/admin/ideas/${letter.ideaId}`}
              className="text-sm font-semibold text-slate-900 hover:underline"
            >
              {letter.ideaTitle}
            </Link>
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            Sent to {letter.ownerName}
            {letter.sentAt ? ` on ${formatDateTime(letter.sentAt)}` : ''}
            {letter.sentByName ? ` by ${letter.sentByName}` : ''}
          </p>
        </div>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          className={secondaryButtonClasses}
        >
          {open ? 'Hide letter' : 'Read letter'}
        </button>
      </div>
      {open && (
        <p className="mt-3 rounded-lg bg-slate-50 px-4 py-3 text-sm leading-6 whitespace-pre-line text-slate-700">
          {letter.message}
        </p>
      )}
    </li>
  )
}

/**
 * The platform's decisions on their way to the owners.
 *
 * A review team's approval or rejection does not reach the owner by itself: it waits
 * here, with a letter drafted from a template, until an administrator who did not
 * decide it reads it and sends it. Changes requests never come here - they go straight
 * from the reviewers to the owner.
 */
export function AdminDecisionsPage() {
  const { capabilities } = useAdminCapabilities()
  const canSend = capabilities.canReleaseProposals
  const letters = useAdminQuery(
    'admin-decision-letters',
    decisionLettersRequest,
    'We could not load the decisions.',
  )
  const pending = (letters.data ?? []).filter((letter) => letter.status === 'pending')
  const sent = (letters.data ?? []).filter((letter) => letter.status === 'sent')

  return (
    <>
      <AdminPageHeader
        title="Decisions"
        description="Approvals and rejections from the review teams, waiting for you to send them to the idea's owner with a letter. The owner hears nothing until you do."
      />
      {letters.error && <ErrorState message={letters.error} onRetry={letters.reload} />}
      {letters.loading && !letters.data && <LoadingState label="Loading decisions…" />}
      {letters.data && (
        <div className="space-y-6">
          <AdminCard title={`Waiting to be sent (${pending.length})`}>
            {pending.length === 0 ? (
              <EmptyState
                title="Nothing is waiting."
                description="When a review team approves or rejects an idea, it appears here."
              />
            ) : (
              <ul className="space-y-4">
                {pending.map((letter) => (
                  <PendingLetter
                    key={letter.id}
                    letter={letter}
                    canSend={canSend}
                    onSent={letters.reload}
                  />
                ))}
              </ul>
            )}
          </AdminCard>

          <AdminCard title={`Sent (${sent.length})`}>
            {sent.length === 0 ? (
              <p className="text-sm text-slate-500">No letters have been sent yet.</p>
            ) : (
              <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
                {sent.map((letter) => (
                  <SentLetter key={letter.id} letter={letter} />
                ))}
              </ul>
            )}
          </AdminCard>
        </div>
      )}
    </>
  )
}
