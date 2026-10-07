import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { projectRequest, type Project } from '../api/automationApi'
import { CONTEXT_LABEL } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { ActivityTab } from './ActivityTab'
import { DeploymentTab } from './DeploymentTab'
import { ImpactTab } from './ImpactTab'
import { Pipeline } from './Pipeline'
import { ProjectOverview } from './ProjectOverview'
import { TestingTab } from './TestingTab'
import { UatTab } from './UatTab'
import { Badge, Fact, Loading, LoadFailed, Tabs } from './ui'
import { WorkTab } from './WorkTab'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'work', label: 'Tasks & milestones' },
  { id: 'testing', label: 'Testing' },
  { id: 'uat', label: 'UAT' },
  { id: 'deployment', label: 'Deployment' },
  { id: 'impact', label: 'Impact' },
  { id: 'activity', label: 'Activity' },
] as const
type TabId = (typeof TABS)[number]['id']

/** One project at `/app/automation/projects/:id`. */
export function ProjectPage() {
  const { projectId = '' } = useParams()
  const [tab, setTab] = useState<TabId>('overview')
  const loaded = useLoad(() => projectRequest(projectId), `project:${projectId}`)

  if (loaded.loading) return <Loading what="this project" />
  if (loaded.failed) return <LoadFailed what="this project" onRetry={loaded.reload} />
  const project: Project | null = loaded.data
  if (!project) {
    return (
      <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600">
        <p className="font-semibold text-gray-900">This project is not available.</p>
        <p className="mt-1">It may not exist, or it may not be one you are allowed to see.</p>
        <Link
          to="/app/automation/projects"
          className="mt-3 inline-block font-semibold text-brand-700"
        >
          Back to projects
        </Link>
      </section>
    )
  }

  return (
    <article className="mt-6">
      <header className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">Project</p>
            <h2 className="mt-1 text-2xl font-bold tracking-tight text-gray-900">
              {project.title}
            </h2>
          </div>
          <Badge value={project.status} />
        </div>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Fact term="Owner">{project.tenantName ?? project.ownerName}</Fact>
          <Fact term="Idea type">{CONTEXT_LABEL[project.submissionContext]}</Fact>
          <Fact term="Delivered by">{project.assignedName}</Fact>
          <Fact term="Target date">{project.targetDate ?? 'Not set'}</Fact>
        </dl>
        <div className="mt-5">
          <Pipeline stages={project.progress.stages} />
        </div>
      </header>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      <div className="mt-5" role="tabpanel">
        {tab === 'overview' && <ProjectOverview project={project} onChanged={loaded.reload} />}
        {tab === 'work' && <WorkTab project={project} onChanged={loaded.reload} />}
        {tab === 'testing' && <TestingTab project={project} onChanged={loaded.reload} />}
        {tab === 'uat' && <UatTab project={project} onChanged={loaded.reload} />}
        {tab === 'deployment' && <DeploymentTab project={project} onChanged={loaded.reload} />}
        {tab === 'impact' && <ImpactTab project={project} onChanged={loaded.reload} />}
        {tab === 'activity' && <ActivityTab opportunityId={project.opportunityId} />}
      </div>
    </article>
  )
}
