import { useState } from 'react'

import {
  completeMilestone,
  createMilestone,
  createTask,
  milestonesRequest,
  tasksRequest,
  updateTask,
  type Project,
} from '../api/automationApi'
import { formatDate, label } from '../utils/labels'
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
  secondaryButton,
} from './ui'

const TASK_STATUSES = ['todo', 'in_progress', 'blocked', 'done']

export function WorkTab({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const milestones = useLoad(() => milestonesRequest(project.id), `ms:${project.id}`)
  const tasks = useLoad(() => tasksRequest(project.id), `tasks:${project.id}`)
  const addMilestone = useOutcome()
  const addTask = useOutcome()
  const change = useOutcome()
  const [milestoneTitle, setMilestoneTitle] = useState('')
  const [taskTitle, setTaskTitle] = useState('')
  const open = ['planning', 'active', 'testing', 'uat'].includes(project.status)
  const editable = project.capabilities.canManage && open
  const refresh = () => {
    milestones.reload()
    tasks.reload()
    onChanged()
  }

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <section aria-labelledby="ms-heading" className="space-y-3">
        <h3 id="ms-heading" className="text-lg font-bold text-gray-900">
          Milestones
        </h3>
        {editable && (
          <form
            aria-label="Add milestone"
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              void addMilestone.run(
                () => createMilestone({ projectId: project.id, title: milestoneTitle }),
                () => {
                  setMilestoneTitle('')
                  refresh()
                },
              )
            }}
          >
            <input
              aria-label="Milestone title"
              className={inputClass}
              placeholder="e.g. MVP"
              value={milestoneTitle}
              onChange={(event) => setMilestoneTitle(event.target.value)}
            />
            <button type="submit" disabled={addMilestone.busy} className={secondaryButton}>
              Add
            </button>
          </form>
        )}
        <NoticeBanner notice={addMilestone.notice} />
        {milestones.loading ? (
          <Loading what="milestones" />
        ) : milestones.failed ? (
          <LoadFailed what="the milestones" onRetry={milestones.reload} />
        ) : milestones.data && milestones.data.length > 0 ? (
          <ul className="space-y-2">
            {milestones.data.map((m) => (
              <li
                key={m.id}
                className={`${cardClass} flex flex-wrap items-center justify-between gap-3`}
              >
                <div>
                  <p className="text-sm font-semibold text-gray-900">{m.title}</p>
                  {m.completedAt && (
                    <p className="text-xs text-gray-500">Completed {formatDate(m.completedAt)}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Badge value={m.status} />
                  {editable && m.status !== 'completed' && (
                    <button
                      type="button"
                      className={secondaryButton}
                      onClick={() =>
                        void change.run(() => completeMilestone({ id: m.id }), refresh)
                      }
                    >
                      Complete
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No milestones yet." />
        )}
      </section>

      <section aria-labelledby="tasks-heading" className="space-y-3">
        <h3 id="tasks-heading" className="text-lg font-bold text-gray-900">
          Tasks
        </h3>
        {editable && (
          <form
            aria-label="Add task"
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              void addTask.run(
                () => createTask({ projectId: project.id, title: taskTitle, priority: 'medium' }),
                () => {
                  setTaskTitle('')
                  refresh()
                },
              )
            }}
          >
            <input
              aria-label="Task title"
              className={inputClass}
              placeholder="e.g. Build the import"
              value={taskTitle}
              onChange={(event) => setTaskTitle(event.target.value)}
            />
            <button type="submit" disabled={addTask.busy} className={secondaryButton}>
              Add
            </button>
          </form>
        )}
        <NoticeBanner notice={addTask.notice ?? change.notice} />
        {tasks.loading ? (
          <Loading what="tasks" />
        ) : tasks.failed ? (
          <LoadFailed what="the tasks" onRetry={tasks.reload} />
        ) : tasks.data && tasks.data.length > 0 ? (
          <ul className="space-y-2">
            {tasks.data.map((t) => (
              <li
                key={t.id}
                className={`${cardClass} flex flex-wrap items-center justify-between gap-3`}
              >
                <p className="text-sm font-semibold text-gray-900">{t.title}</p>
                {editable ? (
                  <select
                    aria-label={`Status of ${t.title}`}
                    className={`${inputClass} w-40`}
                    value={t.status}
                    onChange={(event) =>
                      void change.run(
                        () => updateTask({ input: { id: t.id, status: event.target.value } }),
                        refresh,
                      )
                    }
                  >
                    {TASK_STATUSES.map((value) => (
                      <option key={value} value={value}>
                        {label(value)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Badge value={t.status} />
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No tasks yet.">Break the work into tasks the team can finish.</Empty>
        )}
      </section>
    </div>
  )
}
