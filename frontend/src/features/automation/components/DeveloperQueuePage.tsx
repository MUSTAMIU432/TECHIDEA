import { developerQueueRequest } from '../api/automationApi'
import { useLoad } from '../utils/useLoad'
import { OpportunityCard } from './OpportunityCard'
import { Empty, Loading, LoadFailed } from './ui'

/** Opportunities waiting for a developer. Only ready-for-assignment ones are ever listed. */
export function DeveloperQueuePage() {
  const queue = useLoad(developerQueueRequest, 'queue')

  return (
    <section aria-labelledby="queue-heading" className="mt-6">
      <h2 id="queue-heading" className="text-xl font-bold text-gray-900">
        Developer queue
      </h2>
      <p className="mt-1 text-sm text-gray-600">
        Opportunities whose proposal the owner accepted and that are waiting to be assigned.
      </p>

      {queue.loading ? (
        <Loading what="the queue" />
      ) : queue.failed ? (
        <LoadFailed what="the developer queue" onRetry={queue.reload} />
      ) : queue.data && queue.data.length > 0 ? (
        <ul className="mt-4 space-y-3">
          {queue.data.map((opportunity) => (
            <OpportunityCard key={opportunity.id} opportunity={opportunity} />
          ))}
        </ul>
      ) : (
        <div className="mt-4">
          <Empty title="Nothing is waiting for a developer.">
            Opportunities appear here once their proposal has been accepted. If you expected to see
            one, you may not have delivery access.
          </Empty>
        </div>
      )}
    </section>
  )
}
