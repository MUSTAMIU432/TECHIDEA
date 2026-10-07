import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useAsyncKey } from './useAsyncKey'
import {
  PROPOSAL_SECTIONS,
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
              This idea is approved. Write the proposal for it: what would be built, how long it
              would take and how it will be judged done.
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
  return (
    <form
      aria-label="Proposal"
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onSave(values)
      }}
    >
      {PROPOSAL_SECTIONS.map((section) => {
        const id = `proposal-${section.key}`
        return (
          <div key={section.key}>
            <label htmlFor={id} className="mb-1.5 block text-sm font-semibold text-gray-800">
              {section.label}
            </label>
            {'short' in section && section.short ? (
              <input
                id={id}
                className={inputClass}
                value={values[section.key]}
                onChange={(event) =>
                  setValues((v) => ({ ...v, [section.key]: event.target.value }))
                }
              />
            ) : (
              <textarea
                id={id}
                rows={3}
                className={inputClass}
                value={values[section.key]}
                onChange={(event) =>
                  setValues((v) => ({ ...v, [section.key]: event.target.value }))
                }
              />
            )}
            {fieldError === section.key && (
              <p className="mt-1 text-xs text-red-600">Check this section.</p>
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
