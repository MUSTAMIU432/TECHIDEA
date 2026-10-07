import { useState } from 'react'

import {
  createTestCase,
  recordTestResult,
  testCasesRequest,
  type Project,
  type TestCase,
} from '../api/automationApi'
import { formatDate } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { useOutcome } from '../utils/useOutcome'
import {
  Badge,
  cardClass,
  Empty,
  inputClass,
  Loading,
  LoadFailed,
  NoticeBanner,
  primaryButton,
  secondaryButton,
} from './ui'

function CaseRow({
  testCase,
  canRun,
  onChanged,
}: {
  testCase: TestCase
  canRun: boolean
  onChanged: () => void
}) {
  const record = useOutcome()
  const [actual, setActual] = useState(testCase.actualResult)
  const run = (status: string) =>
    void record.run(
      () => recordTestResult({ id: testCase.id, status, actualResult: actual }),
      onChanged,
    )

  return (
    <li className={`${cardClass} space-y-3`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-gray-900">
            {testCase.title}
            {!testCase.isRequired && (
              <span className="ml-2 text-xs font-normal text-gray-500">(optional)</span>
            )}
          </p>
          {testCase.expectedResult && (
            <p className="mt-1 text-sm text-gray-600">Expected: {testCase.expectedResult}</p>
          )}
          {testCase.executedAt && (
            <p className="mt-1 text-xs text-gray-500">Last run {formatDate(testCase.executedAt)}</p>
          )}
        </div>
        <Badge value={testCase.status} />
      </div>
      {canRun && (
        <div className="space-y-2">
          <input
            aria-label={`Actual result for ${testCase.title}`}
            className={inputClass}
            placeholder="What actually happened"
            value={actual}
            onChange={(event) => setActual(event.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={record.busy}
              className={secondaryButton}
              onClick={() => run('pass')}
            >
              Pass
            </button>
            <button
              type="button"
              disabled={record.busy}
              className={secondaryButton}
              onClick={() => run('fail')}
            >
              Fail
            </button>
            <button
              type="button"
              disabled={record.busy}
              className={secondaryButton}
              onClick={() => run('blocked')}
            >
              Blocked
            </button>
          </div>
          <NoticeBanner notice={record.notice} />
        </div>
      )}
    </li>
  )
}

/** Technical testing. A required test that has not passed blocks UAT. */
export function TestingTab({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const cases = useLoad(() => testCasesRequest(project.id), `tests:${project.id}:${project.status}`)
  const add = useOutcome()
  const [title, setTitle] = useState('')
  const [expected, setExpected] = useState('')
  const [required, setRequired] = useState(true)
  const open = ['planning', 'active', 'testing', 'uat'].includes(project.status)
  const canAdd = project.capabilities.canManage && open
  const canRun = project.capabilities.canManage && ['active', 'testing'].includes(project.status)
  const refresh = () => {
    cases.reload()
    onChanged()
  }

  return (
    <section aria-labelledby="test-heading" className="space-y-4">
      <h3 id="test-heading" className="text-lg font-bold text-gray-900">
        Test cases
      </h3>
      {canAdd && (
        <form
          aria-label="Add test case"
          className={`${cardClass} space-y-3`}
          onSubmit={(event) => {
            event.preventDefault()
            void add.run(
              () =>
                createTestCase({
                  projectId: project.id,
                  title,
                  expectedResult: expected,
                  isRequired: required,
                }),
              () => {
                setTitle('')
                setExpected('')
                refresh()
              },
            )
          }}
        >
          <NoticeBanner notice={add.notice} />
          <input
            aria-label="Test case title"
            className={inputClass}
            placeholder="Test case title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
          <input
            aria-label="Expected result"
            className={inputClass}
            placeholder="Expected result"
            value={expected}
            onChange={(event) => setExpected(event.target.value)}
          />
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={required}
              onChange={(event) => setRequired(event.target.checked)}
            />
            Required before UAT
          </label>
          <button type="submit" disabled={add.busy} className={primaryButton}>
            Add test case
          </button>
        </form>
      )}

      {cases.loading ? (
        <Loading what="test cases" />
      ) : cases.failed ? (
        <LoadFailed what="the test cases" onRetry={cases.reload} />
      ) : cases.data && cases.data.length > 0 ? (
        <ul className="space-y-3">
          {cases.data.map((testCase) => (
            <CaseRow key={testCase.id} testCase={testCase} canRun={canRun} onChanged={refresh} />
          ))}
        </ul>
      ) : (
        <Empty title="No test cases yet.">
          Add the checks that must pass before acceptance testing.
        </Empty>
      )}
    </section>
  )
}
