import { useState } from 'react'

import { cancelOpportunity, updateOpportunity, type Opportunity } from '../api/automationApi'
import { label } from '../utils/labels'
import { useOutcome } from '../utils/useOutcome'
import { dangerButton, Field, inputClass, NoticeBanner, secondaryButton, cardClass } from './ui'

const CANCELLABLE = new Set(['ready_for_assignment', 'assigned'])

export function OverviewTab({
  opportunity,
  onChanged,
}: {
  opportunity: Opportunity
  onChanged: () => void
}) {
  const stage = useOutcome()
  const edit = useOutcome()
  const [title, setTitle] = useState(opportunity.title)
  const [summary, setSummary] = useState(opportunity.summary)
  const [priority, setPriority] = useState<string>(opportunity.priority)
  const closed = opportunity.status === 'cancelled' || opportunity.status === 'completed'

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="space-y-5">
        <section className={cardClass} aria-labelledby="ov-problem">
          <h3 id="ov-problem" className="text-base font-bold text-gray-900">
            The problem
          </h3>
          <p className="mt-2 text-sm leading-6 whitespace-pre-line text-gray-700">
            {opportunity.problemStatement || 'No problem statement was recorded.'}
          </p>
          <h3 className="mt-5 text-base font-bold text-gray-900">Goal</h3>
          <p className="mt-2 text-sm leading-6 whitespace-pre-line text-gray-700">
            {opportunity.automationGoal || 'No goal was recorded.'}
          </p>
          <h3 className="mt-5 text-base font-bold text-gray-900">Expected benefit</h3>
          <p className="mt-2 text-sm leading-6 whitespace-pre-line text-gray-700">
            {opportunity.expectedBenefit || 'No expected benefit was recorded.'}
          </p>
        </section>

        {opportunity.capabilities.canManageRequirements && !closed && (
          <form
            className={`${cardClass} space-y-4`}
            aria-label="Edit opportunity"
            onSubmit={(event) => {
              event.preventDefault()
              void edit.run(
                () =>
                  updateOpportunity({
                    input: { id: opportunity.id, title, summary, priority },
                  }),
                onChanged,
              )
            }}
          >
            <h3 className="text-base font-bold text-gray-900">Edit details</h3>
            <NoticeBanner notice={edit.notice} />
            <Field
              id="opp-title"
              label="Title"
              error={edit.notice?.field === 'title' ? edit.notice.text : null}
            >
              <input
                id="opp-title"
                className={inputClass}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </Field>
            <Field id="opp-summary" label="Summary">
              <textarea
                id="opp-summary"
                rows={3}
                className={inputClass}
                value={summary}
                onChange={(event) => setSummary(event.target.value)}
              />
            </Field>
            <Field id="opp-priority" label="Priority">
              <select
                id="opp-priority"
                className={inputClass}
                value={priority}
                onChange={(event) => setPriority(event.target.value)}
              >
                {['high', 'medium', 'low'].map((value) => (
                  <option key={value} value={value}>
                    {label(value)}
                  </option>
                ))}
              </select>
            </Field>
            <button type="submit" disabled={edit.busy} className={secondaryButton}>
              Save details
            </button>
          </form>
        )}
      </div>

      <aside className="space-y-5">
        <section className={cardClass} aria-labelledby="ov-next">
          <h3 id="ov-next" className="text-base font-bold text-gray-900">
            What happens next
          </h3>
          <NoticeBanner notice={stage.notice} />
          {closed ? (
            <p className="mt-2 text-sm text-gray-600">
              This opportunity is {label(opportunity.status).toLowerCase()}.
            </p>
          ) : (
            <p className="mt-2 text-sm text-gray-600">
              {opportunity.status === 'ready_for_assignment'
                ? 'The owner accepted the proposal. A developer or team is assigned next - see the Assignment tab.'
                : opportunity.status === 'assigned'
                  ? 'It is assigned. The developer creates the project - see the Assignment tab.'
                  : 'The project is under way. Follow it in the Project tab.'}
            </p>
          )}
          {CANCELLABLE.has(opportunity.status) && opportunity.capabilities.canTransition && (
            <button
              type="button"
              disabled={stage.busy}
              onClick={() => {
                if (
                  window.confirm('Cancel this opportunity? The idea can start again afterwards.')
                ) {
                  void stage.run(() => cancelOpportunity(opportunity.id), onChanged)
                }
              }}
              className={`mt-3 w-full ${dangerButton}`}
            >
              Cancel opportunity
            </button>
          )}
        </section>
      </aside>
    </div>
  )
}
