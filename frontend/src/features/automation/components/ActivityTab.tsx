import { activityRequest } from '../api/automationApi'
import { label } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { Empty, Loading, LoadFailed } from './ui'

/** The audit trail: what happened, and when, oldest first. */
export function ActivityTab({ opportunityId }: { opportunityId: string }) {
  const loaded = useLoad(() => activityRequest(opportunityId), `act:${opportunityId}`)

  if (loaded.loading) return <Loading what="the activity" />
  if (loaded.failed) return <LoadFailed what="the activity" onRetry={loaded.reload} />
  if (!loaded.data || loaded.data.length === 0) {
    return <Empty title="Nothing has happened yet." />
  }
  return (
    <ol className="space-y-3" aria-label="Activity">
      {loaded.data.map((event) => (
        <li key={event.id} className="rounded-lg border border-gray-200 bg-white px-4 py-3">
          <p className="text-sm font-semibold text-gray-900">{label(event.action)}</p>
          <p className="mt-0.5 text-xs text-gray-500">
            {event.fromStatus && event.toStatus
              ? `${label(event.fromStatus)} → ${label(event.toStatus)} · `
              : ''}
            {new Date(event.createdAt).toLocaleString()}
          </p>
        </li>
      ))}
    </ol>
  )
}
