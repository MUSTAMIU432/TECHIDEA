import { useState } from 'react'

import {
  solutionRequest,
  updateSolution,
  type Opportunity,
  type Solution,
} from '../api/automationApi'
import { useLoad } from '../utils/useLoad'
import { useOutcome } from '../utils/useOutcome'
import {
  cardClass,
  Field,
  inputClass,
  Loading,
  LoadFailed,
  NoticeBanner,
  primaryButton,
} from './ui'

const FIELDS: ReadonlyArray<{
  key: keyof Omit<Solution, 'opportunityId' | 'updatedAt'>
  label: string
}> = [
  { key: 'summary', label: 'Solution summary' },
  { key: 'businessWorkflow', label: 'Business workflow' },
  { key: 'inScope', label: 'In scope' },
  { key: 'outOfScope', label: 'Out of scope' },
  { key: 'systemsIntegrations', label: 'Systems and integrations' },
  { key: 'technicalConsiderations', label: 'Technical considerations' },
  { key: 'assumptions', label: 'Assumptions' },
  { key: 'constraints', label: 'Constraints' },
  { key: 'risks', label: 'Risks' },
  { key: 'expectedOutput', label: 'Expected output' },
]

type Values = Record<(typeof FIELDS)[number]['key'], string>

function SolutionForm({
  opportunity,
  solution,
}: {
  opportunity: Opportunity
  solution: Solution | null
}) {
  const save = useOutcome()
  const [values, setValues] = useState<Values>(
    () => Object.fromEntries(FIELDS.map((f) => [f.key, solution?.[f.key] ?? ''])) as Values,
  )
  const editable =
    opportunity.capabilities.canEditSolution &&
    !['cancelled', 'completed'].includes(opportunity.status)

  if (!editable) {
    const filled = FIELDS.filter((f) => values[f.key].trim() !== '')
    return filled.length === 0 ? (
      <p className="text-sm text-gray-600">
        No solution has been written yet. The delivery team defines it after the requirements.
      </p>
    ) : (
      <dl className="space-y-4">
        {filled.map((f) => (
          <div key={f.key}>
            <dt className="text-sm font-bold text-gray-900">{f.label}</dt>
            <dd className="mt-1 text-sm leading-6 whitespace-pre-line text-gray-700">
              {values[f.key]}
            </dd>
          </div>
        ))}
      </dl>
    )
  }

  return (
    <form
      aria-label="Solution and scope"
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        void save.run(() => updateSolution({ input: { opportunityId: opportunity.id, ...values } }))
      }}
    >
      <NoticeBanner notice={save.notice} />
      {FIELDS.map((f) => (
        <Field key={f.key} id={`sol-${f.key}`} label={f.label}>
          <textarea
            id={`sol-${f.key}`}
            rows={f.key === 'summary' ? 4 : 3}
            className={inputClass}
            value={values[f.key]}
            onChange={(event) => setValues((v) => ({ ...v, [f.key]: event.target.value }))}
          />
        </Field>
      ))}
      <button type="submit" disabled={save.busy} className={primaryButton}>
        Save solution
      </button>
    </form>
  )
}

/** The solution and scope, kept connected to the requirements next to it. */
export function SolutionTab({ opportunity }: { opportunity: Opportunity }) {
  const loaded = useLoad(() => solutionRequest(opportunity.id), `sol:${opportunity.id}`)
  return (
    <section aria-labelledby="sol-heading" className={cardClass}>
      <h3 id="sol-heading" className="mb-4 text-lg font-bold text-gray-900">
        Solution and scope
      </h3>
      {loaded.loading ? (
        <Loading what="the solution" />
      ) : loaded.failed ? (
        <LoadFailed what="the solution" onRetry={loaded.reload} />
      ) : (
        <SolutionForm opportunity={opportunity} solution={loaded.data} />
      )}
    </section>
  )
}
