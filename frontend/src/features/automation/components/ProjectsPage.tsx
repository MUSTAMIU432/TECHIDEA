import { Link } from 'react-router-dom'

import { projectsRequest, type Project } from '../api/automationApi'
import { CONTEXT_LABEL } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { Badge, Empty, Loading, LoadFailed } from './ui'

function ProjectCard({ project }: { project: Project }) {
  const percent = project.progress.taskCompletionPercent
  return (
    <li>
      <Link
        to={`/app/automation/projects/${project.id}`}
        className="block rounded-xl border border-gray-300 bg-white p-4 shadow-sm transition-colors hover:border-brand-300 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
      >
        <span className="flex flex-wrap items-start justify-between gap-3">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-gray-900">
              {project.title}
            </span>
            <span className="mt-1 block text-xs text-gray-600">
              {CONTEXT_LABEL[project.submissionContext]}
              {project.tenantName ? ` · ${project.tenantName}` : ''} · Delivered by{' '}
              {project.assignedName}
            </span>
            <span className="mt-2 block text-xs text-gray-600">
              {percent === null
                ? 'No tasks yet'
                : `${project.progress.tasksDone} of ${project.progress.tasksTotal} tasks done`}
            </span>
          </span>
          <Badge value={project.status} />
        </span>
      </Link>
    </li>
  )
}

/** Every project the reader may see. */
export function ProjectsPage() {
  const projects = useLoad(() => projectsRequest(), 'projects')

  return (
    <section aria-labelledby="projects-heading" className="mt-6">
      <h2 id="projects-heading" className="text-xl font-bold text-gray-900">
        Projects
      </h2>
      {projects.loading ? (
        <Loading what="projects" />
      ) : projects.failed ? (
        <LoadFailed what="the projects" onRetry={projects.reload} />
      ) : projects.data && projects.data.length > 0 ? (
        <ul className="mt-4 space-y-3">
          {projects.data.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </ul>
      ) : (
        <div className="mt-4">
          <Empty title="No projects yet.">
            A project is created once an opportunity has an accepted proposal and a developer.
          </Empty>
        </div>
      )}
    </section>
  )
}
