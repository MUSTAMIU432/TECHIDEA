import { useState } from 'react'

import {
  createRequirement,
  requirementsRequest,
  updateRequirement,
  type Opportunity,
  type Requirement,
} from '../api/automationApi'
import { formatDate, label, REQUIREMENT_TYPES } from '../utils/labels'
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

const PRIORITIES = ['high', 'medium', 'low']
const STATUSES = ['open', 'in_progress', 'satisfied', 'rejected']

function Row({
  requirement,
  editable,
  onChanged,
}: {
  requirement: Requirement
  editable: boolean
  onChanged: () => void
}) {
  const update = useOutcome()
  const change = (input: Record<string, string>) =>
    void update.run(() => updateRequirement({ id: requirement.id, input }), onChanged)

  return (
    <li className={cardClass}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-gray-900">{requirement.title}</p>
          <p className="mt-1 text-xs text-gray-500">
            {label(requirement.type)} · Updated {formatDate(requirement.updatedAt)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge value={requirement.priority} text={`${requirement.priority} priority`} />
          <Badge value={requirement.status} />
        </div>
      </div>
      {requirement.description && (
        <p className="mt-3 text-sm whitespace-pre-line text-gray-700">{requirement.description}</p>
      )}
      {requirement.acceptanceCriteria && (
        <div className="mt-3 rounded-lg bg-gray-50 p-3">
          <p className="text-xs font-semibold text-gray-500 uppercase">Acceptance criteria</p>
          <p className="mt-1 text-sm whitespace-pre-line text-gray-700">
            {requirement.acceptanceCriteria}
          </p>
        </div>
      )}
      {editable && (
        <div className="mt-3 flex flex-wrap gap-3">
          <label className="text-xs font-semibold text-gray-700">
            Priority
            <select
              aria-label={`Priority of ${requirement.title}`}
              className={`${inputClass} mt-1`}
              value={requirement.priority}
              disabled={update.busy}
              onChange={(event) => change({ priority: event.target.value })}
            >
              {PRIORITIES.map((value) => (
                <option key={value} value={value}>
                  {label(value)}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs font-semibold text-gray-700">
            Status
            <select
              aria-label={`Status of ${requirement.title}`}
              className={`${inputClass} mt-1`}
              value={requirement.status}
              disabled={update.busy}
              onChange={(event) => change({ status: event.target.value })}
            >
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {label(value)}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      <NoticeBanner notice={update.notice} />
    </li>
  )
}

export function RequirementsTab({
  opportunity,
  onChanged,
}: {
  opportunity: Opportunity
  onChanged: () => void
}) {
  const list = useLoad(() => requirementsRequest(opportunity.id), `req:${opportunity.id}`)
  const add = useOutcome()
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({
    title: '',
    description: '',
    type: 'functional',
    priority: 'medium',
    acceptanceCriteria: '',
  })
  const editable =
    opportunity.capabilities.canManageRequirements &&
    !['cancelled', 'completed'].includes(opportunity.status)
  const set = (field: keyof typeof form, value: string) =>
    setForm((current) => ({ ...current, [field]: value }))
  const fieldError = (name: string) => (add.notice?.field === name ? add.notice.text : null)

  return (
    <section aria-labelledby="req-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id="req-heading" className="text-lg font-bold text-gray-900">
          Requirements
        </h3>
        {editable && (
          <button type="button" className={primaryButton} onClick={() => setAdding((v) => !v)}>
            {adding ? 'Close' : '+ Add requirement'}
          </button>
        )}
      </div>

      {adding && editable && (
        <form
          aria-label="Add requirement"
          className={`${cardClass} space-y-4`}
          onSubmit={(event) => {
            event.preventDefault()
            void add.run(
              () =>
                createRequirement({
                  opportunityId: opportunity.id,
                  input: form,
                }),
              () => {
                setForm({
                  ...form,
                  title: '',
                  description: '',
                  acceptanceCriteria: '',
                })
                setAdding(false)
                list.reload()
                onChanged()
              },
            )
          }}
        >
          <NoticeBanner notice={add.notice && !add.notice.field ? add.notice : null} />
          <Field id="req-title" label="Title" error={fieldError('title')}>
            <input
              id="req-title"
              className={inputClass}
              value={form.title}
              onChange={(event) => set('title', event.target.value)}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="req-type" label="Type" error={fieldError('type')}>
              <select
                id="req-type"
                className={inputClass}
                value={form.type}
                onChange={(event) => set('type', event.target.value)}
              >
                {REQUIREMENT_TYPES.map((value) => (
                  <option key={value} value={value}>
                    {label(value)}
                  </option>
                ))}
              </select>
            </Field>
            <Field id="req-priority" label="Priority" error={fieldError('priority')}>
              <select
                id="req-priority"
                className={inputClass}
                value={form.priority}
                onChange={(event) => set('priority', event.target.value)}
              >
                {PRIORITIES.map((value) => (
                  <option key={value} value={value}>
                    {label(value)}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field id="req-description" label="Description">
            <textarea
              id="req-description"
              rows={3}
              className={inputClass}
              value={form.description}
              onChange={(event) => set('description', event.target.value)}
            />
          </Field>
          <Field id="req-criteria" label="Acceptance criteria" hint="How will you know it is met?">
            <textarea
              id="req-criteria"
              rows={3}
              className={inputClass}
              value={form.acceptanceCriteria}
              onChange={(event) => set('acceptanceCriteria', event.target.value)}
            />
          </Field>
          <button type="submit" disabled={add.busy} className={primaryButton}>
            Add requirement
          </button>
        </form>
      )}

      {list.loading ? (
        <Loading what="requirements" />
      ) : list.failed ? (
        <LoadFailed what="the requirements" onRetry={list.reload} />
      ) : list.data && list.data.length > 0 ? (
        <ul className="space-y-3">
          {list.data.map((requirement) => (
            <Row
              key={requirement.id}
              requirement={requirement}
              editable={editable}
              onChanged={list.reload}
            />
          ))}
        </ul>
      ) : (
        <Empty title="No requirements yet.">Define what this automation needs to accomplish.</Empty>
      )}
    </section>
  )
}
