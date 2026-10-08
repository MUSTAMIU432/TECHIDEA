import { useState } from 'react'

import { opportunitiesRequest } from '../api/automationApi'
import { label } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { OpportunityCard } from './OpportunityCard'
import { Empty, inputClass, Loading, LoadFailed } from './ui'

const STATUSES = [
  'draft',
  'discovery',
  'requirements',
  'solution_design',
  'proposal',
  'ready_for_assignment',
  'assigned',
  'project_created',
  'completed',
  'cancelled',
]

/** Every opportunity the reader may see, newest first, filterable by stage. */
export function OpportunitiesPage() {
  const [status, setStatus] = useState('')
  const list = useLoad(() => opportunitiesRequest(status || undefined), `opps:${status}`)

  return (
    <section aria-labelledby="opps-heading" className="mt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id="opps-heading" className="text-xl font-bold text-gray-900">
          Automation opportunities
        </h2>
        <div className="w-56">
          <label htmlFor="opp-status" className="block text-xs font-semibold text-gray-700">
            Stage
          </label>
          <select
            id="opp-status"
            className={inputClass}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">Any stage</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {list.loading ? (
        <Loading what="opportunities" />
      ) : list.failed ? (
        <LoadFailed what="the opportunities" onRetry={list.reload} />
      ) : list.data && list.data.length > 0 ? (
        <ul className="mt-4 space-y-3">
          {list.data.map((opportunity) => (
            <OpportunityCard key={opportunity.id} opportunity={opportunity} />
          ))}
        </ul>
      ) : (
        <div className="mt-4">
          <Empty title="No automation opportunities yet.">
            Approved ideas will appear here when they are ready for implementation.
          </Empty>
        </div>
      )}
    </section>
  )
}
