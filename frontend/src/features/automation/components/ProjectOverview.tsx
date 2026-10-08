import { Link } from 'react-router-dom'

import { moveProject, type Project, type ProjectStep } from '../api/automationApi'
import { formatDate } from '../utils/labels'
import { useOutcome } from '../utils/useOutcome'
import { cardClass, NoticeBanner, primaryButton } from './ui'

const STEPS: Record<string, { step: ProjectStep; text: string; hint: string }> = {
  planning: {
    step: 'start',
    text: 'Start development',
    hint: 'Work begins. Add tasks and milestones.',
  },
  active: { step: 'testing', text: 'Move to testing', hint: 'Development is done; test it.' },
  testing: {
    step: 'uat',
    text: 'Submit for UAT',
    hint: 'Every required test case must pass first.',
  },
  deployed: {
    step: 'complete',
    text: 'Complete project',
    hint: 'Needs a verified deployment and an impact decision.',
  },
}

export function ProjectOverview({
  project,
  onChanged,
}: {
  project: Project
  onChanged: () => void
}) {
  const move = useOutcome()
  const next = STEPS[project.status]
  const { progress } = project

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <section className={cardClass} aria-labelledby="po-progress">
        <h3 id="po-progress" className="text-base font-bold text-gray-900">
          Progress
        </h3>
        {progress.taskCompletionPercent === null ? (
          <p className="mt-2 text-sm text-gray-600">No tasks have been added yet.</p>
        ) : (
          <>
            <p className="mt-2 text-sm text-gray-700">
              {progress.tasksDone} of {progress.tasksTotal} tasks done
              {progress.tasksBlocked > 0 ? `, ${progress.tasksBlocked} blocked` : ''}.
            </p>
            <progress
              aria-label="Tasks done"
              max={100}
              value={progress.taskCompletionPercent}
              className="mt-3 h-2 w-full overflow-hidden rounded-full [&::-moz-progress-bar]:bg-brand-600 [&::-webkit-progress-bar]:bg-gray-100 [&::-webkit-progress-value]:bg-brand-600"
            />
          </>
        )}
        <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-semibold text-gray-500 uppercase">Started</dt>
            <dd>{project.startDate ? formatDate(project.startDate) : 'Not started'}</dd>
          </div>
          <div>
            <dt className="text-xs font-semibold text-gray-500 uppercase">Completed</dt>
            <dd>{project.completedAt ? formatDate(project.completedAt) : 'Not yet'}</dd>
          </div>
        </dl>
        <div className="mt-5 flex flex-wrap gap-4 text-sm">
          <Link
            to={`/app/ideas/${project.ideaId}`}
            className="font-semibold text-brand-700 hover:underline"
          >
            View original idea
          </Link>
          <Link
            to={`/app/automation/opportunities/${project.opportunityId}`}
            className="font-semibold text-brand-700 hover:underline"
          >
            View opportunity
          </Link>
        </div>
      </section>

      <aside className={cardClass} aria-labelledby="po-next">
        <h3 id="po-next" className="text-base font-bold text-gray-900">
          What happens next
        </h3>
        <NoticeBanner notice={move.notice} />
        {next && project.capabilities.canManage ? (
          <>
            <p className="mt-2 text-sm text-gray-600">{next.hint}</p>
            <button
              type="button"
              disabled={move.busy}
              className={`mt-3 w-full ${primaryButton}`}
              onClick={() => void move.run(() => moveProject(next.step, project.id), onChanged)}
            >
              {next.text}
            </button>
          </>
        ) : (
          <p className="mt-2 text-sm text-gray-600">
            {project.status === 'uat'
              ? 'Waiting for acceptance, then deployment. See the UAT and Deployment tabs.'
              : project.status === 'completed'
                ? 'This project is complete.'
                : 'The delivery team moves this forward.'}
          </p>
        )}
      </aside>
    </div>
  )
}
