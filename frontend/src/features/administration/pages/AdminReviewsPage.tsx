import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'

import type { ReviewDecision } from '../../reviews/api/reviewsApi'
import { DECISION_LABELS, VERDICT_LABELS } from '../../reviews/utils/reviewLabels'
import {
  adminReviewedIdeasRequest,
  adminReviewsRequest,
  type AdminReviewFilters,
  type AdminReviewState,
  type AdminReviewedIdeaRow,
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
import { formatDateTime, ideaHomeLabel, RESTRICTED_TITLE } from '../utils/format'

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
                <td className={cellClasses}>{ideaHomeLabel(review.idea)}</td>
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

/**
 * One reviewed idea: a single row standing where its latest round left it, and - on
 * demand - the rounds that got it there, each linking to its own detail.
 */
function ReviewedIdeaRow({
  row,
  here,
  columnCount,
}: {
  row: AdminReviewedIdeaRow
  here: unknown
  columnCount: number
}) {
  const [open, setOpen] = useState(false)
  const roundsId = `rounds-${row.idea.id}`

  return (
    <Fragment>
      <tr>
        <td className={cellClasses}>
          <Link
            to={`/app/admin/reviews/${row.latest.id}`}
            state={here}
            className="font-semibold text-slate-900 hover:underline"
          >
            {row.idea.title ?? <Restricted>{RESTRICTED_TITLE}</Restricted>}
          </Link>
        </td>
        <td className={cellClasses}>{ideaHomeLabel(row.idea)}</td>
        <td className={cellClasses}>{row.latest.reviewer.name}</td>
        <td className={cellClasses}>
          <div className="flex flex-wrap items-center gap-2">
            <DecisionBadge decision={row.latest.decision} />
            <span className="text-xs text-slate-500">round {row.latest.round}</span>
          </div>
        </td>
        <td className={`${cellClasses} whitespace-nowrap`}>{formatDateTime(row.startedAt)}</td>
        <td className={`${cellClasses} whitespace-nowrap`}>{formatDateTime(row.lastActivityAt)}</td>
        <td className={`${cellClasses} whitespace-nowrap`}>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={roundsId}
            onClick={() => setOpen((value) => !value)}
            className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-semibold text-slate-700 hover:bg-slate-50"
          >
            {open ? 'Hide rounds' : `Show rounds (${row.roundCount})`}
          </button>
        </td>
      </tr>
      {open && (
        <tr id={roundsId}>
          <td colSpan={columnCount} className="bg-slate-50 px-4 py-3">
            <ol aria-label={`Rounds of ${row.idea.title ?? 'this idea'}`} className="space-y-2">
              {row.rounds.map((round) => (
                <li
                  key={round.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="text-sm font-semibold text-slate-900">
                      Round {round.round}
                    </span>
                    <DecisionBadge decision={round.decision} />
                    <span className="text-xs text-slate-500">{round.reviewer.name}</span>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                    <span>
                      {formatDateTime(round.createdAt)}
                      {round.completedAt ? ` → ${formatDateTime(round.completedAt)}` : ''}
                    </span>
                    <Link
                      to={`/app/admin/reviews/${round.id}`}
                      state={here}
                      className="font-semibold text-slate-700 hover:underline"
                    >
                      Open
                    </Link>
                  </div>
                </li>
              ))}
            </ol>
          </td>
        </tr>
      )}
    </Fragment>
  )
}

const REVIEWED_IDEA_COLUMNS = [
  'Idea',
  'Belongs to',
  'Reviewer',
  'Current state',
  'First review',
  'Last activity',
  'Rounds',
]

/**
 * The Reviews page: one row per reviewed idea, where it stands now, with its rounds
 * folded inside. Filters read per idea - "in progress" is an idea with a round open,
 * a decision is the latest round's.
 */
function ReviewedIdeasTable() {
  const filters = useUrlFilters()
  const search = filters.get('search')
  const state = filters.get('state') as AdminReviewState | ''
  const decision = filters.get('decision') as ReviewDecision | ''
  const reviewerId = filters.get('reviewerId')
  const request: AdminReviewFilters = {
    search,
    state: state || null,
    decisions: decision ? [decision] : null,
    organizationId: filters.get('organizationId') || null,
    reviewerId: reviewerId || null,
  }

  const { data, loading, error, reload } = useAdminQuery(
    `reviewed-ideas:${filters.key}`,
    () => adminReviewedIdeasRequest(request, { offset: filters.offset }),
    'We could not load the reviews.',
  )

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchBox
          label="Search reviews"
          placeholder="Idea title, reviewer email or organization"
          initialValue={search}
          onSearch={(value) => filters.set('search', value)}
        />
        <select
          aria-label="Review state"
          value={state}
          onChange={(event) => filters.set('state', event.target.value)}
          className={controlClasses}
        >
          <option value="">In progress and decided</option>
          <option value="OPEN">In progress</option>
          <option value="COMPLETED">Decided</option>
        </select>
        <select
          aria-label="Decision"
          value={decision}
          onChange={(event) => filters.set('decision', event.target.value)}
          className={controlClasses}
        >
          <option value="">Any latest decision</option>
          {DECISIONS.map((value) => (
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
          title="No reviewed ideas match."
          description="Try a different search or filter."
        />
      )}
      {data && data.items.length > 0 && (
        <>
          <AdminTable label="Reviews" columns={REVIEWED_IDEA_COLUMNS}>
            {data.items.map((row) => (
              <ReviewedIdeaRow
                key={row.idea.id}
                row={row}
                here={filters.here}
                columnCount={REVIEWED_IDEA_COLUMNS.length}
              />
            ))}
          </AdminTable>
          <AdminPagination
            label="Reviews pagination"
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
        description="Every reviewed idea, once, where its latest round left it. Open a row's rounds to see how it got there. Completed reviews are immutable history; the console only reads them."
      />
      <ReviewedIdeasTable />
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
