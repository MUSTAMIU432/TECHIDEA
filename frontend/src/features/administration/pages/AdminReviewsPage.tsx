import { Link } from 'react-router-dom'

import type { ReviewDecision } from '../../reviews/api/reviewsApi'
import { DECISION_LABELS, VERDICT_LABELS } from '../../reviews/utils/reviewLabels'
import {
  adminReviewsRequest,
  type AdminReviewFilters,
  type AdminReviewState,
} from '../api/administrationApi'
import {
  AdminPageHeader,
  AdminPagination,
  AdminTable,
  EmptyState,
  ErrorState,
  IdeaStatusBadge,
  LoadingState,
  Restricted,
  cellClasses,
  controlClasses,
} from '../components/AdminUi'
import { DecisionBadge } from '../components/ReviewRoundCard'
import { SearchBox } from '../components/SearchBox'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useUrlFilters } from '../hooks/useUrlFilters'
import { formatDateTime, RESTRICTED_TITLE } from '../utils/format'

const VERDICTS = Object.keys(VERDICT_LABELS) as ReviewDecision[]
const DECISIONS = Object.keys(DECISION_LABELS) as ReviewDecision[]

interface ReviewTableProps {
  /** Which view this is: every round, or the completed decisions (approvals). */
  mode: 'reviews' | 'approvals'
}

/**
 * Review rounds across the platform, newest first - one component for two
 * views. "Reviews" is every round, open or completed; "Approvals" is the
 * operational view of decisions: completed rounds whose decision moved an idea
 * to Approved, Changes requested or Rejected, beside the idea's status now.
 *
 * Read-only in both: reviews are decided by reviewers in their organization,
 * and the console has no way to create, change or override one.
 */
function ReviewTable({ mode }: ReviewTableProps) {
  const filters = useUrlFilters()
  const search = filters.get('search')
  const state = filters.get('state') as AdminReviewState | ''
  const decision = filters.get('decision') as ReviewDecision | ''
  const organizationId = filters.get('organizationId')
  const reviewerId = filters.get('reviewerId')

  const request: AdminReviewFilters =
    mode === 'approvals'
      ? { search, state: 'COMPLETED', decisions: decision ? [decision] : VERDICTS }
      : { search, state: state || null, decisions: decision ? [decision] : null }
  request.organizationId = organizationId || null
  request.reviewerId = reviewerId || null

  const { data, loading, error, reload } = useAdminQuery(
    `${mode}:${filters.key}`,
    () => adminReviewsRequest(request, { offset: filters.offset }),
    'We could not load the reviews.',
  )

  const decisionChoices = mode === 'approvals' ? VERDICTS : DECISIONS

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchBox
          label="Search reviews"
          placeholder="Idea title, reviewer email or organization"
          initialValue={search}
          onSearch={(value) => filters.set('search', value)}
        />
        {mode === 'reviews' && (
          <select
            aria-label="Review state"
            value={state}
            onChange={(event) => filters.set('state', event.target.value)}
            className={controlClasses}
          >
            <option value="">Open and completed</option>
            <option value="OPEN">In progress</option>
            <option value="COMPLETED">Completed</option>
          </select>
        )}
        <select
          aria-label="Decision"
          value={decision}
          onChange={(event) => filters.set('decision', event.target.value)}
          className={controlClasses}
        >
          <option value="">{mode === 'approvals' ? 'Every decision' : 'Any decision'}</option>
          {decisionChoices.map((value) => (
            <option key={value} value={value}>
              {DECISION_LABELS[value]}
            </option>
          ))}
        </select>
        {reviewerId && (
          <button
            type="button"
            onClick={() => filters.set('reviewerId', '')}
            className="rounded-full bg-slate-200 px-3 py-1 text-xs font-semibold text-slate-700"
          >
            One reviewer ✕
          </button>
        )}
      </div>

      {error && <ErrorState message={error} onRetry={reload} />}
      {loading && !data && <LoadingState label="Loading reviews…" />}
      {data && data.items.length === 0 && (
        <EmptyState
          title={mode === 'approvals' ? 'No decisions match.' : 'No review rounds match.'}
          description="Try a different search or filter."
        />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable
            label={mode === 'approvals' ? 'Decisions' : 'Reviews'}
            columns={
              mode === 'approvals'
                ? ['Idea', 'Organization', 'Reviewer', 'Round', 'Decision', 'Decided', 'Idea now']
                : ['Idea', 'Organization', 'Reviewer', 'Round', 'Decision', 'Started', 'Completed']
            }
          >
            {data.items.map((review) => (
              <tr key={review.id}>
                <td className={cellClasses}>
                  <Link
                    to={`/app/admin/reviews/${review.id}`}
                    state={filters.here}
                    className="font-semibold text-slate-900 hover:underline"
                  >
                    {review.idea.title ?? <Restricted>{RESTRICTED_TITLE}</Restricted>}
                  </Link>
                </td>
                <td className={cellClasses}>{review.idea.organization.name}</td>
                <td className={cellClasses}>{review.reviewer.name}</td>
                <td className={`${cellClasses} tabular-nums`}>{review.round}</td>
                <td className={cellClasses}>
                  <DecisionBadge decision={review.decision} />
                </td>
                {mode === 'approvals' ? (
                  <>
                    <td className={`${cellClasses} whitespace-nowrap`}>
                      {formatDateTime(review.completedAt)}
                    </td>
                    <td className={cellClasses}>
                      <IdeaStatusBadge status={review.idea.status} />
                    </td>
                  </>
                ) : (
                  <>
                    <td className={`${cellClasses} whitespace-nowrap`}>
                      {formatDateTime(review.createdAt)}
                    </td>
                    <td className={`${cellClasses} whitespace-nowrap`}>
                      {formatDateTime(review.completedAt)}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </AdminTable>
          <AdminPagination
            label={mode === 'approvals' ? 'Decisions pagination' : 'Reviews pagination'}
            pageInfo={data.pageInfo}
            onOffsetChange={filters.setOffset}
          />
        </>
      )}
    </>
  )
}

export function AdminReviewsPage() {
  return (
    <>
      <AdminPageHeader
        title="Reviews"
        description="Every review round on the platform. Completed reviews are immutable history; the console only reads them."
      />
      <ReviewTable mode="reviews" />
    </>
  )
}

export function AdminApprovalsPage() {
  return (
    <>
      <AdminPageHeader
        title="Approvals"
        description="Decisions reviewers have made, with where each idea stands now. Decisions are made by reviewers in the idea's organization - the console observes them and cannot override one."
      />
      <ReviewTable mode="approvals" />
    </>
  )
}
