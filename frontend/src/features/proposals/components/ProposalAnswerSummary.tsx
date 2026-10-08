import { useEffect, useState } from 'react'

import { AGREEMENT_WORDS, proposalAnswerRequest, type ProposalAnswer } from '../api/proposalsApi'

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

/** The owner's answers to the proposal, as everyone who acts on them reads them. */
export function ProposalAnswerSummary({ answer }: { answer: ProposalAnswer }) {
  const proceed = answer.decision === 'proceed'
  return (
    <div
      className={`rounded-xl border px-4 py-3 text-sm ${
        proceed ? 'border-emerald-200 bg-emerald-50' : 'border-rose-200 bg-rose-50'
      }`}
    >
      <p className={`font-semibold ${proceed ? 'text-emerald-900' : 'text-rose-900'}`}>
        {proceed ? 'The owner wants to go ahead' : 'The owner decided not to go ahead'}
      </p>
      <p className="mt-0.5 text-xs text-gray-600">
        {answer.answeredByName} · {formatDay(answer.answeredAt)}
      </p>
      {proceed ? (
        <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
          <div>
            <dt className="text-xs font-semibold text-gray-500 uppercase">Timeline</dt>
            <dd className="text-gray-800">
              {answer.timeline ? AGREEMENT_WORDS[answer.timeline] : '—'}
            </dd>
          </div>
          {answer.payment && (
            <div>
              <dt className="text-xs font-semibold text-gray-500 uppercase">Payment plan</dt>
              <dd className="text-gray-800">{AGREEMENT_WORDS[answer.payment]}</dd>
            </div>
          )}
          <div>
            <dt className="text-xs font-semibold text-gray-500 uppercase">Preferred start</dt>
            <dd className="text-gray-800">
              {answer.preferredStart ? formatDay(answer.preferredStart) : 'No preference'}
            </dd>
          </div>
          {answer.conditions && (
            <div className="sm:col-span-2">
              <dt className="text-xs font-semibold text-gray-500 uppercase">
                Conditions and notes
              </dt>
              <dd className="whitespace-pre-line text-gray-800">{answer.conditions}</dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="mt-2 whitespace-pre-line text-gray-800">{answer.declineReason}</p>
      )}
    </div>
  )
}

/** Loads and shows the owner's answers for an idea; draws nothing until there are some. */
export function ProposalAnswerCard({ ideaId, title }: { ideaId: string; title?: string }) {
  const [answer, setAnswer] = useState<{ ideaId: string; answer: ProposalAnswer } | null>(null)

  useEffect(() => {
    let cancelled = false
    proposalAnswerRequest(ideaId)
      .then((value) => {
        if (!cancelled && value !== null) setAnswer({ ideaId, answer: value })
      })
      .catch(() => {
        // No answers to show is the same, to the reader, as none yet.
      })
    return () => {
      cancelled = true
    }
  }, [ideaId])

  if (answer === null || answer.ideaId !== ideaId) return null
  return (
    <section aria-label="The owner's answers" className="mt-4">
      {title && <h3 className="mb-2 text-sm font-semibold text-gray-900">{title}</h3>}
      <ProposalAnswerSummary answer={answer.answer} />
    </section>
  )
}
