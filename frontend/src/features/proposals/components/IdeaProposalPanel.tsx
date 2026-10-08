import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useAsyncKey } from './useAsyncKey'
import {
  PAYMENT_WORDS,
  PROPOSAL_SECTIONS,
  sectionApplies,
  proposalStateRequest,
  startProposalRequest,
  statusWords,
  submitProposalRequest,
  updateProposalRequest,
  type IdeaProposal,
  type ProposalOutcome,
  type ProposalState,
  type SectionKey,
} from '../api/proposalsApi'
import { ProposalSections } from './ProposalSections'

const inputClass =
  'block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200'
const primary =
  'inline-flex h-10 items-center rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60'
const secondary =
  'inline-flex h-10 items-center rounded-lg border border-brand-300 bg-white px-4 text-sm font-semibold text-brand-700 shadow-sm hover:bg-brand-50 disabled:cursor-not-allowed disabled:opacity-60'

/**
 * An approved idea's proposal, on the idea's page.
 *
 * The review team that approved the idea sees it here and writes it: any member edits, the lead
 * sends it to the admin. The owner sees a pointer to read it once it is released - never the text
 * here, which only the view-only page shows. Everyone else sees nothing.
 */
export function IdeaProposalPanel({ ideaId, status }: { ideaId: string; status: string }) {
  const { state, failed, reload } = useAsyncKey(() => proposalStateRequest(ideaId), ideaId)

  if (failed || state === null) return null
  if (state.viewerRole === 'owner') {
    if (state.proposal?.status !== 'released') {
      return status === 'APPROVED' ? (
        <Section title="Your proposal">
          <p className="text-sm text-gray-600">
            The platform is preparing a proposal for your idea. You will be told when it is ready to
            read; your go-ahead comes after you have read it.
          </p>
        </Section>
      ) : null
    }
    return (
      <Section title="Your proposal is ready">
        <p className="text-sm text-gray-600">Read it carefully, then give your go-ahead.</p>
        <Link
          to={`/app/ideas/${ideaId}/proposal`}
          className="mt-3 inline-block text-sm font-semibold text-brand-700 hover:underline"
        >
          Read the proposal →
        </Link>
      </Section>
    )
  }
  if (state.viewerRole !== 'writer') return null
  return <Writer ideaId={ideaId} state={state} onChanged={reload} />
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      aria-label={title}
      className="mt-6 rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
    >
      <h2 className="text-lg font-bold text-gray-900">{title}</h2>
      <div className="mt-2">{children}</div>
    </section>
  )
}

function Writer({
  ideaId,
  state,
  onChanged,
}: {
  ideaId: string
  state: ProposalState
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{
    ok: boolean
    text: string
    field: string | null
  } | null>(null)

  async function run(action: () => Promise<ProposalOutcome>) {
    setBusy(true)
    setMessage(null)
    try {
      const result = await action()
      setMessage({ ok: result.success, text: result.message, field: result.field })
      if (result.success) onChanged()
    } catch {
      setMessage({
        ok: false,
        text: 'We could not reach the server. Please try again.',
        field: null,
      })
    } finally {
      setBusy(false)
    }
  }

  const proposal = state.proposal
  return (
    <Section title="Proposal">
      {message && (
        <p
          role={message.ok ? 'status' : 'alert'}
          className={`mb-3 rounded-lg px-4 py-3 text-sm ${
            message.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'
          }`}
        >
          {message.text}
        </p>
      )}
      {!proposal ? (
        state.canStart ? (
          <>
            <p className="text-sm text-gray-600">
              This idea is approved. Write the proposal for it: whether it is feasible, what would
              be built and what it needs, the timeline, what it costs and whether the owner pays,
              and how it will be judged done.
            </p>
            <button
              type="button"
              disabled={busy}
              className={`mt-3 ${primary}`}
              onClick={() => void run(() => startProposalRequest({ ideaId }))}
            >
              Start the proposal
            </button>
          </>
        ) : state.waitingForAdmin ? (
          <div role="note" className="rounded-lg bg-amber-50 p-4 text-sm text-amber-900">
            <p className="font-semibold">Approved - waiting for the platform admin</p>
            <p className="mt-1">
              The platform admin confirms the approval and tells the owner first. You can start the
              proposal as soon as they do, and you will be notified.
            </p>
          </div>
        ) : (
          <p className="text-sm text-gray-600">There is no proposal for this idea yet.</p>
        )
      ) : (
        <>
          <p className="mb-3 text-sm font-semibold text-gray-700">{statusWords(proposal.status)}</p>
          {proposal.reviewFeedback && proposal.status === 'changes_requested' && (
            <div role="note" className="mb-4 rounded-lg bg-amber-50 p-4 text-sm text-amber-900">
              <p className="font-semibold">The admin sent it back</p>
              <p className="mt-1 whitespace-pre-line">{proposal.reviewFeedback}</p>
            </div>
          )}
          {state.canEdit ? (
            <Editor
              proposal={proposal}
              busy={busy}
              fieldError={message && !message.ok ? message.field : null}
              onSave={(values) => run(() => updateProposalRequest(ideaId, values))}
            />
          ) : (
            <ProposalSections proposal={proposal} />
          )}
          {state.canSubmit && (
            <button
              type="button"
              disabled={busy}
              className={`mt-4 ${primary}`}
              onClick={() => void run(() => submitProposalRequest({ ideaId }))}
            >
              Send to the admin
            </button>
          )}
          {state.canEdit && !state.canSubmit && (
            <p className="mt-3 text-xs text-gray-500">
              Only the team’s lead sends the proposal to the admin.
            </p>
          )}
        </>
      )}
    </Section>
  )
}

/**
 * The proposal form, as cards that fold: a long document is written a section at a time.
 * Sections already written start folded and empty ones open, so the writer lands on what
 * is left; "Expand all" and "Collapse all" do what they say. Everything is still one form,
 * saved together.
 */
function Editor({
  proposal,
  busy,
  fieldError,
  onSave,
}: {
  proposal: IdeaProposal
  busy: boolean
  fieldError: string | null
  onSave: (values: Partial<Record<SectionKey, string>>) => void
}) {
  const [values, setValues] = useState<Record<SectionKey, string>>(
    () =>
      Object.fromEntries(PROPOSAL_SECTIONS.map((s) => [s.key, proposal[s.key]])) as Record<
        SectionKey,
        string
      >,
  )
  const sections = PROPOSAL_SECTIONS.filter((section) =>
    sectionApplies(section.key, values.paymentRequired),
  )
  // The folded sections, rather than the open ones: a section that appears later (the
  // payment plan, once the owner pays) is then open, like every other empty one.
  const [folded, setFolded] = useState<Set<SectionKey>>(
    () => new Set(PROPOSAL_SECTIONS.filter((s) => proposal[s.key]?.trim()).map((s) => s.key)),
  )
  const written = sections.filter((section) => values[section.key]?.trim()).length
  const toggle = (key: SectionKey) =>
    setFolded((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  return (
    <form
      aria-label="Proposal"
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        onSave(values)
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-gray-500">
          <span className="font-semibold text-gray-800">
            {written} of {sections.length} sections written.
          </span>{' '}
          Sections marked <span className="font-semibold text-red-600">*</span> must be written
          before the lead can send it to the admin.
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setFolded(new Set())}
            className="rounded-lg border border-gray-300 px-2.5 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
          >
            Expand all
          </button>
          <button
            type="button"
            onClick={() => setFolded(new Set(PROPOSAL_SECTIONS.map((s) => s.key)))}
            className="rounded-lg border border-gray-300 px-2.5 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50"
          >
            Collapse all
          </button>
        </div>
      </div>
      {sections.map((section) => {
        const id = `proposal-${section.key}`
        const required =
          ('required' in section && section.required) || section.key === 'paymentPlan'
        const set = (value: string) => setValues((v) => ({ ...v, [section.key]: value }))
        const isOpen = !folded.has(section.key) || fieldError === section.key
        const filled = Boolean(values[section.key]?.trim())
        return (
          <div key={section.key} className="rounded-xl border border-gray-200 bg-white">
            <button
              type="button"
              aria-expanded={isOpen}
              aria-controls={`${id}-body`}
              onClick={() => toggle(section.key)}
              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
            >
              <span className="text-sm font-semibold text-gray-800">
                {section.label}
                {required && (
                  <span aria-hidden="true" className="ml-0.5 text-red-600">
                    *
                  </span>
                )}
              </span>
              <span className="flex items-center gap-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                    filled ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'
                  }`}
                >
                  {filled ? 'Written ✓' : 'Empty'}
                </span>
                <span aria-hidden="true" className="text-gray-400">
                  {isOpen ? '▾' : '▸'}
                </span>
              </span>
            </button>
            {isOpen && (
              <div id={`${id}-body`} className="border-t border-gray-100 px-4 pt-3 pb-4">
                <label htmlFor={id} className="sr-only">
                  {section.label}
                  {required ? '*' : ''}
                </label>
                {'choice' in section && section.choice ? (
                  <select
                    id={id}
                    className={inputClass}
                    value={values[section.key]}
                    onChange={(event) => set(event.target.value)}
                  >
                    <option value="">Choose…</option>
                    <option value="yes">{PAYMENT_WORDS.yes}</option>
                    <option value="no">{PAYMENT_WORDS.no}</option>
                  </select>
                ) : 'short' in section && section.short ? (
                  <input
                    id={id}
                    className={inputClass}
                    value={values[section.key]}
                    onChange={(event) => set(event.target.value)}
                  />
                ) : (
                  <textarea
                    id={id}
                    rows={4}
                    className={inputClass}
                    value={values[section.key]}
                    onChange={(event) => set(event.target.value)}
                  />
                )}
                {fieldError === section.key && (
                  <p className="mt-1 text-xs text-red-600">Check this section.</p>
                )}
              </div>
            )}
          </div>
        )
      })}
      <button type="submit" disabled={busy} className={secondary}>
        Save draft
      </button>
    </form>
  )
}
