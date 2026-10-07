import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  assigneeOptionsRequest,
  assignmentsRequest,
  assignOpportunity,
  createProject,
  type Opportunity,
} from '../api/automationApi'
import { formatDate } from '../utils/labels'
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

function AssignForm({
  opportunity,
  onChanged,
}: {
  opportunity: Opportunity
  onChanged: () => void
}) {
  const options = useLoad(assigneeOptionsRequest, 'assignees')
  const assign = useOutcome()
  const [choice, setChoice] = useState('')

  if (options.loading) return <Loading what="who can be assigned" />
  const users = options.data?.users ?? []
  const teams = options.data?.teams ?? []

  return (
    <form
      aria-label="Assign opportunity"
      className={`${cardClass} space-y-4`}
      onSubmit={(event) => {
        event.preventDefault()
        const [kind, id] = choice.split(':')
        void assign.run(
          () =>
            assignOpportunity({
              opportunityId: opportunity.id,
              assigneeUserId: kind === 'user' ? id : null,
              assigneeTeamId: kind === 'team' ? id : null,
            }),
          onChanged,
        )
      }}
    >
      <h3 className="text-base font-bold text-gray-900">Assign a developer or team</h3>
      <NoticeBanner notice={assign.notice} />
      <Field
        id="assignee"
        label="Assign to"
        error={assign.notice?.field === 'assignee' ? assign.notice.text : null}
      >
        <select
          id="assignee"
          className={inputClass}
          value={choice}
          onChange={(event) => setChoice(event.target.value)}
        >
          <option value="">Choose…</option>
          {users.length > 0 && (
            <optgroup label="Developers">
              {users.map((u) => (
                <option key={u.id} value={`user:${u.id}`}>
                  {u.name} ({u.detail})
                </option>
              ))}
            </optgroup>
          )}
          {teams.length > 0 && (
            <optgroup label="Teams">
              {teams.map((t) => (
                <option key={t.id} value={`team:${t.id}`}>
                  {t.name}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </Field>
      {users.length === 0 && teams.length === 0 && (
        <p className="text-sm text-gray-600">
          Nobody can be assigned yet. A developer needs the assignable permission.
        </p>
      )}
      <button type="submit" disabled={assign.busy || choice === ''} className={primaryButton}>
        Assign
      </button>
    </form>
  )
}

function CreateProjectForm({ opportunity }: { opportunity: Opportunity }) {
  const navigate = useNavigate()
  const create = useOutcome()
  const [start, setStart] = useState('')
  const [target, setTarget] = useState('')

  return (
    <form
      aria-label="Create project"
      className={`${cardClass} space-y-4`}
      onSubmit={(event) => {
        event.preventDefault()
        void create.run(
          () =>
            createProject({
              opportunityId: opportunity.id,
              startDate: start || null,
              targetDate: target || null,
            }),
          (payload) => {
            if (payload.project) navigate(`/app/automation/projects/${payload.project.id}`)
          },
        )
      }}
    >
      <h3 className="text-base font-bold text-gray-900">Create the project</h3>
      <p className="text-sm text-gray-600">
        The opportunity is assigned. Creating the project starts delivery.
      </p>
      <NoticeBanner notice={create.notice} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="proj-start" label="Start date">
          <input
            id="proj-start"
            type="date"
            className={inputClass}
            value={start}
            onChange={(event) => setStart(event.target.value)}
          />
        </Field>
        <Field
          id="proj-target"
          label="Target date"
          error={create.notice?.field === 'targetDate' ? create.notice.text : null}
        >
          <input
            id="proj-target"
            type="date"
            className={inputClass}
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          />
        </Field>
      </div>
      <button type="submit" disabled={create.busy} className={primaryButton}>
        Create project
      </button>
    </form>
  )
}

/** Who is delivering it, and the two actions that follow acceptance: assign, then create the project. */
export function AssignmentTab({
  opportunity,
  onChanged,
}: {
  opportunity: Opportunity
  onChanged: () => void
}) {
  const key = `assign:${opportunity.id}:${opportunity.status}`
  const assignments = useLoad(() => assignmentsRequest(opportunity.id), key)

  return (
    <section aria-labelledby="assign-heading" className="space-y-4">
      <h3 id="assign-heading" className="text-lg font-bold text-gray-900">
        Assignment
      </h3>
      {assignments.loading ? (
        <Loading what="the assignment" />
      ) : assignments.failed ? (
        <LoadFailed what="the assignment" onRetry={assignments.reload} />
      ) : assignments.data && assignments.data.length > 0 ? (
        <ul className="space-y-3">
          {assignments.data.map((a) => (
            <li
              key={a.id}
              className={`${cardClass} flex flex-wrap items-center justify-between gap-3`}
            >
              <div>
                <p className="text-sm font-semibold text-gray-900">{a.assigneeName}</p>
                <p className="text-xs text-gray-500">Assigned {formatDate(a.assignedAt)}</p>
              </div>
              <Badge value={a.status} />
            </li>
          ))}
        </ul>
      ) : (
        <Empty title="Not assigned yet.">
          An opportunity can be assigned once its proposal has been accepted.
        </Empty>
      )}

      {opportunity.status === 'ready_for_assignment' && opportunity.capabilities.canTransition && (
        <AssignForm opportunity={opportunity} onChanged={onChanged} />
      )}
      {opportunity.status === 'assigned' && <CreateProjectForm opportunity={opportunity} />}
    </section>
  )
}
