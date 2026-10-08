import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { opportunityRequest } from '../api/automationApi'
import { CONTEXT_LABEL } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { ActivityTab } from './ActivityTab'
import { AssignmentTab } from './AssignmentTab'
import { OverviewTab } from './OverviewTab'
import { ProjectLinkTab } from './ProjectLinkTab'
import { ProposalTab } from './ProposalTab'
import { RequirementsTab } from './RequirementsTab'
import { SolutionTab } from './SolutionTab'
import { Badge, Fact, Loading, LoadFailed, Tabs } from './ui'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'requirements', label: 'Requirements' },
  { id: 'solution', label: 'Solution' },
  { id: 'proposal', label: 'Proposal' },
  { id: 'assignment', label: 'Assignment' },
  { id: 'project', label: 'Project' },
  { id: 'activity', label: 'Activity' },
] as const
type TabId = (typeof TABS)[number]['id']

/** One opportunity at `/app/automation/opportunities/:id`, in the order the work happens. */
export function OpportunityPage() {
  const { opportunityId = '' } = useParams()
  // A notification can open the page on the tab its news is about (`?tab=uat`).
  const [params] = useSearchParams()
  const requested = TABS.find((item) => item.id === params.get('tab'))?.id
  const [tab, setTab] = useState<TabId>(requested ?? 'overview')
  const loaded = useLoad(() => opportunityRequest(opportunityId), `opp:${opportunityId}`)

  if (loaded.loading) return <Loading what="this opportunity" />
  if (loaded.failed) return <LoadFailed what="this opportunity" onRetry={loaded.reload} />
  const opportunity = loaded.data
  if (!opportunity) {
    return (
      <section className="mt-6 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600">
        <p className="font-semibold text-gray-900">This opportunity is not available.</p>
        <p className="mt-1">It may not exist, or it may not be one you are allowed to see.</p>
        <Link
          to="/app/automation/opportunities"
          className="mt-3 inline-block font-semibold text-brand-700"
        >
          Back to opportunities
        </Link>
      </section>
    )
  }

  return (
    <article className="mt-6">
      <header className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">
              Automation opportunity
            </p>
            <h2 className="mt-1 text-2xl font-bold tracking-tight text-gray-900">
              {opportunity.title}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <Badge value={opportunity.priority} text={`${opportunity.priority} priority`} />
            <Badge value={opportunity.status} />
          </div>
        </div>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Fact term="Idea type">{CONTEXT_LABEL[opportunity.submissionContext]}</Fact>
          <Fact term="Owned by">
            {opportunity.tenantName ?? opportunity.ownerName}
            {opportunity.tenantName && (
              <span className="block text-xs text-gray-500">{opportunity.ownerName}</span>
            )}
          </Fact>
          <Fact term="Source idea">{opportunity.ideaTitle}</Fact>
          <Fact term="Original idea">
            <Link
              to={`/app/ideas/${opportunity.ideaId}`}
              className="font-semibold text-brand-700 hover:underline"
            >
              View original idea →
            </Link>
          </Fact>
        </dl>
      </header>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      <div className="mt-5" role="tabpanel">
        {tab === 'overview' && <OverviewTab opportunity={opportunity} onChanged={loaded.reload} />}
        {tab === 'requirements' && (
          <RequirementsTab opportunity={opportunity} onChanged={loaded.reload} />
        )}
        {tab === 'solution' && <SolutionTab opportunity={opportunity} />}
        {tab === 'proposal' && <ProposalTab opportunity={opportunity} />}
        {tab === 'assignment' && (
          <AssignmentTab opportunity={opportunity} onChanged={loaded.reload} />
        )}
        {tab === 'project' && <ProjectLinkTab opportunity={opportunity} />}
        {tab === 'activity' && <ActivityTab opportunityId={opportunity.id} />}
      </div>
    </article>
  )
}
