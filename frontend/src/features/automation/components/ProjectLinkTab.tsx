import { Link } from 'react-router-dom'

import { projectForOpportunityRequest, type Opportunity } from '../api/automationApi'
import { useLoad } from '../utils/useLoad'
import { Badge, cardClass, Empty, Loading, LoadFailed } from './ui'

/** The way from an opportunity to the project that delivers it. */
export function ProjectLinkTab({ opportunity }: { opportunity: Opportunity }) {
  const loaded = useLoad(
    () => projectForOpportunityRequest(opportunity.id),
    `oppproj:${opportunity.id}:${opportunity.status}`,
  )
  if (loaded.loading) return <Loading what="the project" />
  if (loaded.failed) return <LoadFailed what="the project" onRetry={loaded.reload} />
  if (!loaded.data) {
    return (
      <Empty title="No project yet.">
        A project is created once the opportunity is assigned to a developer or team.
      </Empty>
    )
  }
  return (
    <section className={cardClass} aria-label="Project">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">{loaded.data.title}</p>
          <p className="mt-1 text-xs text-gray-500">Delivered by {loaded.data.assignedName}</p>
        </div>
        <Badge value={loaded.data.status} />
      </div>
      <Link
        to={`/app/automation/projects/${loaded.data.id}`}
        className="mt-4 inline-block text-sm font-semibold text-brand-700 hover:underline"
      >
        View project →
      </Link>
    </section>
  )
}
