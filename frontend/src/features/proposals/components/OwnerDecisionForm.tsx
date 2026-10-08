import { useEffect, useState } from 'react'

import {
  answerProposalRequest,
  proposalAnswerRequest,
  type Agreement,
  type IdeaProposal,
  type ProposalAnswer,
} from '../api/proposalsApi'
import { ProposalAnswerSummary } from './ProposalAnswerSummary'

type Decision = 'proceed' | 'decline'

const field =
  'mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-200 focus:outline-none'

function AgreementChoice({
  name,
  legend,
  hint,
  value,
  onChange,
}: {
  name: string
  legend: string
  hint: string
  value: Agreement | ''
  onChange: (value: Agreement) => void
}) {
  return (
    <fieldset>
      <legend className="text-sm font-semibold text-gray-900">{legend}</legend>
      <p className="text-xs text-gray-500">{hint}</p>
      <div className="mt-2 flex flex-wrap gap-4">
        {(
          [
            ['yes', 'Yes, I agree'],
            ['discuss', 'I would like to discuss it'],
          ] as const
        ).map(([option, label]) => (
          <label key={option} className="inline-flex items-center gap-2 text-sm text-gray-800">
            <input
              type="radio"
              name={name}
              checked={value === option}
              onChange={() => onChange(option)}
              className="accent-brand-600"
            />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}

/**
 * The owner's decision on the released proposal: go ahead with development, and on what
 * terms - or not, and why. Going ahead is the go-ahead itself: the idea is handed to the
 * delivery team in the same step, with these answers in front of whoever assigns it.
 * Once answered, the answers are shown instead of the form.
 */
export function OwnerDecisionForm({
  ideaId,
  proposal,
}: {
  ideaId: string
  proposal: IdeaProposal
}) {
  const [existing, setExisting] = useState<ProposalAnswer | null | undefined>(undefined)
  const [decision, setDecision] = useState<Decision | null>(null)
  const [timeline, setTimeline] = useState<Agreement | ''>('')
  const [payment, setPayment] = useState<Agreement | ''>('')
  const [preferredStart, setPreferredStart] = useState('')
  const [conditions, setConditions] = useState('')
  const [declineReason, setDeclineReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const ownerPays = proposal.paymentRequired === 'yes'

  useEffect(() => {
    let cancelled = false
    proposalAnswerRequest(ideaId)
      .then((answer) => {
        if (!cancelled) setExisting(answer)
      })
      .catch(() => {
        if (!cancelled) setExisting(null)
      })
    return () => {
      cancelled = true
    }
  }, [ideaId])

  const ready =
    decision === 'proceed'
      ? timeline !== '' && (!ownerPays || payment !== '')
      : decision === 'decline'
        ? declineReason.trim() !== ''
        : false

  async function send() {
    if (decision === null) return
    setBusy(true)
    setError(null)
    try {
      const result = await answerProposalRequest({
        ideaId,
        decision,
        timeline,
        payment: ownerPays ? payment : '',
        preferredStart: preferredStart || null,
        conditions,
        declineReason,
      })
      if (result.success) {
        setDone(result.message)
        // Show the answers as recorded, in place of the form.
        setExisting(await proposalAnswerRequest(ideaId))
      } else {
        setError(result.message)
      }
    } catch {
      setError('We could not reach the server, so nothing has changed. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      aria-labelledby="owner-decision-heading"
      className="mt-6 overflow-hidden rounded-2xl border border-brand-200 bg-white shadow-sm"
    >
      <div className="border-b border-brand-100 bg-brand-50 px-6 py-4">
        <h2 id="owner-decision-heading" className="text-lg font-bold text-brand-900">
          Your decision
        </h2>
        <p className="mt-1 text-sm text-brand-900">
          Read the proposal, then tell us whether to go ahead with development. It is yours to
          decide, and nobody else&apos;s.
        </p>
      </div>

      <div className="space-y-5 px-6 py-5">
        {done && (
          <output className="block rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
            {done}
          </output>
        )}
        {existing === undefined ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : existing !== null ? (
          <ProposalAnswerSummary answer={existing} />
        ) : (
          <>
            <fieldset>
              <legend className="text-sm font-semibold text-gray-900">
                1. Do you want to go ahead with development?
              </legend>
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                {(
                  [
                    ['proceed', 'Yes, go ahead', 'Hand it to the delivery team to build.'],
                    ['decline', 'No, not now', 'The idea will not be developed.'],
                  ] as const
                ).map(([option, label, hint]) => (
                  <div
                    key={option}
                    className={`flex gap-3 rounded-xl border p-4 ${
                      decision === option
                        ? 'border-brand-400 bg-brand-50 ring-1 ring-brand-300'
                        : 'border-gray-200 hover:bg-gray-50'
                    }`}
                  >
                    <input
                      id={`decision-${option}`}
                      type="radio"
                      name="decision"
                      checked={decision === option}
                      onChange={() => setDecision(option)}
                      aria-describedby={`decision-${option}-hint`}
                      className="mt-1 accent-brand-600"
                    />
                    <div>
                      <label
                        htmlFor={`decision-${option}`}
                        className="block cursor-pointer text-sm font-semibold text-gray-900"
                      >
                        {label}
                      </label>
                      <p id={`decision-${option}-hint`} className="text-xs text-gray-600">
                        {hint}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </fieldset>

            {decision === 'proceed' && (
              <div className="space-y-5 rounded-xl bg-gray-50 p-4">
                <AgreementChoice
                  name="timeline"
                  legend="2. Do you accept the proposed timeline?"
                  hint={proposal.estimatedTimeline || 'See the timeline and milestones above.'}
                  value={timeline}
                  onChange={setTimeline}
                />
                {ownerPays && (
                  <AgreementChoice
                    name="payment"
                    legend="3. Do you accept the payment plan?"
                    hint={proposal.paymentPlan || 'See the payment plan above.'}
                    value={payment}
                    onChange={setPayment}
                  />
                )}
                <label className="block text-sm font-semibold text-gray-900">
                  {ownerPays ? '4.' : '3.'} When would you like work to start? (optional)
                  <input
                    type="date"
                    value={preferredStart}
                    min={new Date().toISOString().slice(0, 10)}
                    onChange={(event) => setPreferredStart(event.target.value)}
                    className={`${field} max-w-56`}
                  />
                </label>
                <label className="block text-sm font-semibold text-gray-900">
                  {ownerPays ? '5.' : '4.'} Anything the delivery team should know? (optional)
                  <textarea
                    value={conditions}
                    onChange={(event) => setConditions(event.target.value)}
                    rows={3}
                    placeholder="Conditions, people to involve, dates to avoid…"
                    className={field}
                  />
                </label>
              </div>
            )}

            {decision === 'decline' && (
              <label className="block rounded-xl bg-gray-50 p-4 text-sm font-semibold text-gray-900">
                2. Why not? This helps the team understand your decision.
                <textarea
                  value={declineReason}
                  onChange={(event) => setDeclineReason(event.target.value)}
                  rows={3}
                  className={field}
                />
              </label>
            )}

            {error && (
              <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
                {error}
              </p>
            )}

            {decision !== null && (
              <button
                type="button"
                disabled={busy || !ready}
                onClick={() => void send()}
                className="inline-flex h-11 items-center rounded-lg bg-brand-600 px-5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy
                  ? 'Sending…'
                  : decision === 'proceed'
                    ? 'Give the go-ahead'
                    : 'Send my decision'}
              </button>
            )}
          </>
        )}
      </div>
    </section>
  )
}
