import { useState } from 'react'

import {
  createUatScenario,
  recordUatResult,
  uatRecordsRequest,
  type Project,
  type UatRecord,
} from '../api/automationApi'
import { formatDate } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { useOutcome } from '../utils/useOutcome'
import {
  Badge,
  cardClass,
  dangerButton,
  Empty,
  inputClass,
  Loading,
  LoadFailed,
  NoticeBanner,
  primaryButton,
  secondaryButton,
} from './ui'

function Scenario({
  record,
  canDecide,
  onChanged,
}: {
  record: UatRecord
  canDecide: boolean
  onChanged: () => void
}) {
  const decide = useOutcome()
  const [feedback, setFeedback] = useState('')
  const send = (result: string) =>
    void decide.run(() => recordUatResult({ id: record.id, result, feedback }), onChanged)

  return (
    <li className={`${cardClass} space-y-3`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-gray-900">{record.scenario}</p>
          {record.expectedOutcome && (
            <p className="mt-1 text-sm text-gray-600">Expected: {record.expectedOutcome}</p>
          )}
          {record.recordedAt && (
            <p className="mt-1 text-xs text-gray-500">Decided {formatDate(record.recordedAt)}</p>
          )}
        </div>
        <Badge value={record.result} />
      </div>
      {record.feedback && (
        <p className="rounded-lg bg-gray-50 p-3 text-sm whitespace-pre-line text-gray-700">
          {record.feedback}
        </p>
      )}
      {canDecide && record.result === 'pending' && (
        <div className="space-y-2">
          <input
            aria-label={`Feedback for ${record.scenario}`}
            className={inputClass}
            placeholder="Feedback (required if it fails)"
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={decide.busy}
              className={primaryButton}
              onClick={() => send('passed')}
            >
              Pass
            </button>
            <button
              type="button"
              disabled={decide.busy}
              className={dangerButton}
              onClick={() => send('failed')}
            >
              Fail
            </button>
          </div>
          <NoticeBanner notice={decide.notice} />
        </div>
      )}
    </li>
  )
}

/** User acceptance: only the idea's owner passes or fails a scenario. */
export function UatTab({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const records = useLoad(
    () => uatRecordsRequest(project.id),
    `uat:${project.id}:${project.status}`,
  )
  const add = useOutcome()
  const [scenario, setScenario] = useState('')
  const [expected, setExpected] = useState('')
  const inUat = project.status === 'uat'
  const canAdd = inUat && (project.capabilities.canManage || project.capabilities.canPerformUat)
  const refresh = () => {
    records.reload()
    onChanged()
  }

  return (
    <section aria-labelledby="uat-heading" className="space-y-4">
      <h3 id="uat-heading" className="text-lg font-bold text-gray-900">
        User acceptance testing
      </h3>
      <p className="text-sm text-gray-600">
        The owner of the idea decides whether the work does what they needed. A failed scenario
        sends the project back to development and is kept as history.
      </p>
      {canAdd && (
        <form
          aria-label="Add acceptance scenario"
          className={`${cardClass} space-y-3`}
          onSubmit={(event) => {
            event.preventDefault()
            void add.run(
              () =>
                createUatScenario({ projectId: project.id, scenario, expectedOutcome: expected }),
              () => {
                setScenario('')
                setExpected('')
                refresh()
              },
            )
          }}
        >
          <NoticeBanner notice={add.notice} />
          <input
            aria-label="Scenario"
            className={inputClass}
            placeholder="Scenario, e.g. Finance sees every payment"
            value={scenario}
            onChange={(event) => setScenario(event.target.value)}
          />
          <input
            aria-label="Expected outcome"
            className={inputClass}
            placeholder="Expected outcome"
            value={expected}
            onChange={(event) => setExpected(event.target.value)}
          />
          <button type="submit" disabled={add.busy} className={secondaryButton}>
            Add scenario
          </button>
        </form>
      )}

      {records.loading ? (
        <Loading what="acceptance scenarios" />
      ) : records.failed ? (
        <LoadFailed what="the scenarios" onRetry={records.reload} />
      ) : records.data && records.data.length > 0 ? (
        <ul className="space-y-3">
          {records.data.map((record) => (
            <Scenario
              key={record.id}
              record={record}
              canDecide={inUat && project.capabilities.canPerformUat}
              onChanged={refresh}
            />
          ))}
        </ul>
      ) : (
        <Empty title="No acceptance scenarios yet.">
          UAT begins once every required test has passed.
        </Empty>
      )}
    </section>
  )
}
