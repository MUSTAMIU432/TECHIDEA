import { Link } from 'react-router-dom'

import type { Opportunity } from '../api/automationApi'
import { CONTEXT_LABEL } from '../utils/labels'
import { Badge } from './ui'

/** One opportunity as a card, for the list and the developer queue. */
export function OpportunityCard({ opportunity }: { opportunity: Opportunity }) {
  return (
    <li>
      <Link
        to={`/app/automation/opportunities/${opportunity.id}`}
        className="block rounded-xl border border-gray-300 bg-white p-4 shadow-sm transition-colors hover:border-brand-300 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
      >
        <span className="flex flex-wrap items-start justify-between gap-3">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-gray-900">
              {opportunity.title}
            </span>
            <span className="mt-1 block text-xs text-gray-600">
              {CONTEXT_LABEL[opportunity.submissionContext]}
              {opportunity.tenantName ? ` · ${opportunity.tenantName}` : ''} · Owned by{' '}
              {opportunity.ownerName}
            </span>
            {opportunity.problemStatement && (
              <span className="mt-2 line-clamp-2 block text-sm text-gray-600">
                {opportunity.problemStatement}
              </span>
            )}
          </span>
          <span className="flex shrink-0 items-center gap-2">
            <Badge value={opportunity.priority} text={`${opportunity.priority} priority`} />
            <Badge value={opportunity.status} />
          </span>
        </span>
      </Link>
    </li>
  )
}
