import { useState } from 'react'

import { impactRequest, recordImpact, type Impact, type Project } from '../api/automationApi'
import { label } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { useOutcome } from '../utils/useOutcome'
import {
  Badge,
  cardClass,
  Empty,
  Field,
  inputClass,
  Loading,
  LoadFailed,
  NoticeBanner,
  primaryButton,
} from './ui'

const MEASURES = [
  { id: 'ProcessingMinutes', label: 'Processing time (minutes)' },
  { id: 'PeopleInvolved', label: 'People involved' },
  { id: 'ErrorRate', label: 'Error rate (%)' },
  { id: 'Cost', label: 'Cost' },
] as const

const PAIRS = [
  ['estimatedHoursSavedPerWeek', 'Estimated hours saved per week'],
  ['measuredHoursSavedPerWeek', 'Measured hours saved per week'],
  ['estimatedCostSavings', 'Estimated cost savings'],
  ['measuredCostSavings', 'Measured cost savings'],
] as const

function show(value: string | number | null | undefined, suffix = ''): string {
  return value === null || value === undefined ? 'Not recorded' : `${value}${suffix}`
}

function Results({ impact }: { impact: Impact }) {
  const r = impact.results
  const rows: Array<[string, string | null, string]> = [
    ['Time saved', r.timeSavedPercent, '%'],
    ['People reduced', r.peopleReduced, ''],
    ['Error reduction', r.errorReductionPercent, '%'],
  ]
  return (
    <section className={cardClass} aria-labelledby="imp-results">
      <h4 id="imp-results" className="text-base font-bold text-gray-900">
        Results
      </h4>
      <dl className="mt-3 grid gap-3 sm:grid-cols-3">
        {rows.map(([name, value, suffix]) => (
          <div key={name}>
            <dt className="text-xs font-semibold text-gray-500 uppercase">{name}</dt>
            <dd className="mt-0.5 text-lg font-bold text-gray-900">
              {value === null ? (
                <span className="text-sm font-normal text-gray-500">Not enough data</span>
              ) : (
                `${value}${suffix}`
              )}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase">Estimated</p>
          <p className="text-sm text-gray-900">
            {show(r.estimatedHoursSavedPerWeek, ' hours/week')}
          </p>
        </div>
        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase">Measured</p>
          <p className="text-sm text-gray-900">
            {show(r.measuredHoursSavedPerWeek, ' hours/week')}
          </p>
        </div>
      </div>
      {r.hoursSavedVariance !== null && (
        <p className="mt-2 text-xs text-gray-500">
          Measured vs estimated: {r.hoursSavedVariance} hours/week.
        </p>
      )}
    </section>
  )
}

function ImpactForm({
  project,
  impact,
  onSaved,
}: {
  project: Project
  impact: Impact | null
  onSaved: () => void
}) {
  const save = useOutcome()
  const [status, setStatus] = useState(impact?.status ?? 'recorded')
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {}
    if (impact) {
      const source = impact as unknown as Record<string, unknown>
      for (const key of Object.keys(source)) {
        const value = source[key]
        if (typeof value === 'string' || typeof value === 'number') initial[key] = String(value)
      }
    }
    return initial
  })
  const set = (key: string, value: string) => setValues((v) => ({ ...v, [key]: value }))
  const FIELD_KEYS = [
    ...MEASURES.flatMap((m) => [`before${m.id}`, `after${m.id}`]),
    ...PAIRS.map(([key]) => key),
    'usersAffected',
    'qualitativeOutcome',
    'notes',
  ]

  return (
    <form
      aria-label="Record impact"
      className={`${cardClass} space-y-4`}
      onSubmit={(event) => {
        event.preventDefault()
        const sent: Record<string, string> = {}
        for (const key of FIELD_KEYS) if (values[key] !== undefined) sent[key] = values[key]
        void save.run(() => recordImpact({ projectId: project.id, status, values: sent }), onSaved)
      }}
    >
      <h4 className="text-base font-bold text-gray-900">Record the impact</h4>
      <p className="text-sm text-gray-600">
        Enter only what you actually know. Nothing here is required, and nothing is filled in for
        you.
      </p>
      <NoticeBanner notice={save.notice} />
      <Field id="imp-status" label="Status">
        <select
          id="imp-status"
          className={inputClass}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          {['recorded', 'pending', 'unavailable'].map((value) => (
            <option key={value} value={value}>
              {label(value)}
            </option>
          ))}
        </select>
      </Field>
      {MEASURES.map((m) => (
        <div key={m.id} className="grid gap-3 sm:grid-cols-2">
          <Field id={`imp-before${m.id}`} label={`Before: ${m.label}`}>
            <input
              id={`imp-before${m.id}`}
              inputMode="decimal"
              className={inputClass}
              value={values[`before${m.id}`] ?? ''}
              onChange={(event) => set(`before${m.id}`, event.target.value)}
            />
          </Field>
          <Field id={`imp-after${m.id}`} label={`After: ${m.label}`}>
            <input
              id={`imp-after${m.id}`}
              inputMode="decimal"
              className={inputClass}
              value={values[`after${m.id}`] ?? ''}
              onChange={(event) => set(`after${m.id}`, event.target.value)}
            />
          </Field>
        </div>
      ))}
      <div className="grid gap-3 sm:grid-cols-2">
        {PAIRS.map(([key, text]) => (
          <Field key={key} id={`imp-${key}`} label={text}>
            <input
              id={`imp-${key}`}
              inputMode="decimal"
              className={inputClass}
              value={values[key] ?? ''}
              onChange={(event) => set(key, event.target.value)}
            />
          </Field>
        ))}
      </div>
      <Field id="imp-qualitative" label="What changed, in words">
        <textarea
          id="imp-qualitative"
          rows={3}
          className={inputClass}
          value={values.qualitativeOutcome ?? ''}
          onChange={(event) => set('qualitativeOutcome', event.target.value)}
        />
      </Field>
      <button type="submit" disabled={save.busy} className={primaryButton}>
        Save impact
      </button>
    </form>
  )
}

/** What changed because of this automation: estimated and measured kept apart. */
export function ImpactTab({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const loaded = useLoad(() => impactRequest(project.id), `impact:${project.id}:${project.status}`)
  const canRecord =
    project.capabilities.canRecordImpact && ['deployed', 'completed'].includes(project.status)
  const refresh = () => {
    loaded.reload()
    onChanged()
  }

  if (loaded.loading) return <Loading what="the impact" />
  if (loaded.failed) return <LoadFailed what="the impact" onRetry={loaded.reload} />
  const impact = loaded.data

  return (
    <section aria-labelledby="impact-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id="impact-heading" className="text-lg font-bold text-gray-900">
          Automation impact
        </h3>
        {impact && <Badge value={impact.status} />}
      </div>
      {impact ? (
        impact.status === 'recorded' ? (
          <Results impact={impact} />
        ) : (
          <Empty
            title={impact.status === 'pending' ? 'Impact is pending.' : 'Impact is unavailable.'}
          >
            {impact.status === 'pending'
              ? 'It will be measured once there is enough data.'
              : 'It was marked as not measurable for this project.'}
          </Empty>
        )
      ) : (
        <Empty title="No impact data yet.">Impact measurement has not been recorded.</Empty>
      )}
      {impact?.qualitativeOutcome && (
        <p className={`${cardClass} text-sm whitespace-pre-line text-gray-700`}>
          {impact.qualitativeOutcome}
        </p>
      )}
      {canRecord && <ImpactForm project={project} impact={impact} onSaved={refresh} />}
    </section>
  )
}
