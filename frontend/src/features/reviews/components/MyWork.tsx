import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { reviewerWorkRequest, type ReviewerWork, type WorkItem } from '../api/workApi'
import { formatDate } from '../utils/reviewLabels'

/** The journey's steps as a bar: done, the current step, and the rest. */
function Journey({ steps, step }: { steps: string[]; step: number }) {
  return (
    <ol aria-label="Progress" className="mt-3 grid grid-cols-6 gap-1">
      {steps.map((name, index) => (
        <li key={name} aria-current={index === step ? 'step' : undefined}>
          <span
            className={`block h-1.5 rounded-full ${
              index < step ? 'bg-brand-600' : index === step ? 'bg-amber-400' : 'bg-gray-200'
            }`}
          />
          <span
            className={`mt-1 block truncate text-[11px] ${
              index === step ? 'font-semibold text-gray-900' : 'text-gray-500'
            }`}
          >
            {name}
          </span>
        </li>
      ))}
    </ol>
  )
}

function WorkCard({ item, steps }: { item: WorkItem; steps: string[] }) {
  return (
    <li
      className={`rounded-xl border bg-white p-4 shadow-sm ${
        item.needsMe ? 'border-amber-300 ring-1 ring-amber-200' : 'border-gray-200'
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-base font-semibold text-gray-900">{item.title}</p>
          <p className="mt-0.5 text-xs text-gray-500">
            {item.ownerLabel}
            {item.teamName ? ` · ${item.teamName}${item.isLead ? ' (you lead)' : ''}` : ''} ·
            updated {formatDate(item.updatedAt)}
          </p>
        </div>
        {item.needsMe && item.actionPath ? (
          <Link
            to={item.actionPath}
            className="inline-flex shrink-0 rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-brand-700"
          >
            {item.actionLabel}
          </Link>
        ) : (
          item.actionPath && (
            <Link
              to={item.actionPath}
              className="inline-flex shrink-0 rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              View
            </Link>
          )
        )}
      </div>
      <p className="mt-2 text-sm text-gray-700">
        {item.needsMe && (
          <span className="mr-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800">
            Needs you
          </span>
        )}
        {item.stageLabel}
      </p>
      <Journey steps={steps} step={item.step} />
    </li>
  )
}

/**
 * Everything the reviewer's review teams - or the reviewer - are handling, from the review
 * to delivery: where each idea stands, and a button to the page where the next action is
 * taken when it is theirs. The notifications announce; this is where the work is run from.
 */
export function MyWork() {
  const [work, setWork] = useState<ReviewerWork | null>(null)
  const [failed, setFailed] = useState(false)
  const [filter, setFilter] = useState<'all' | 'mine'>('all')

  useEffect(() => {
    let cancelled = false
    reviewerWorkRequest()
      .then((answer) => {
        if (!cancelled) setWork(answer)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (failed) {
    return (
      <p role="alert" className="mt-8 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
        We could not load your work. Please refresh the page.
      </p>
    )
  }
  if (work === null) return <p className="mt-8 text-sm text-gray-500">Loading your work…</p>

  const waiting = work.items.filter((item) => item.needsMe).length
  const shown = filter === 'mine' ? work.items.filter((item) => item.needsMe) : work.items

  return (
    <section aria-labelledby="my-work-heading" className="mt-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-[0.18em] text-brand-700 uppercase">
            My work
          </p>
          <h2 id="my-work-heading" className="mt-1 text-2xl font-semibold text-gray-900">
            {waiting > 0
              ? `${waiting} ${waiting === 1 ? 'idea needs' : 'ideas need'} you`
              : 'Nothing is waiting on you'}
          </h2>
        </div>
        <fieldset className="flex rounded-lg border border-gray-300 p-0.5">
          <legend className="sr-only">Show</legend>
          {(
            [
              ['all', `All (${work.items.length})`],
              ['mine', `Needs me (${waiting})`],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
              className="rounded-md px-3 py-1 text-sm font-semibold text-gray-700 aria-pressed:bg-brand-600 aria-pressed:text-white"
            >
              {label}
            </button>
          ))}
        </fieldset>
      </div>

      {shown.length === 0 ? (
        <p className="mt-5 rounded-2xl border border-dashed border-gray-300 bg-white px-5 py-6 text-sm text-gray-600">
          {work.items.length === 0
            ? 'No ideas have been routed to your review teams yet.'
            : 'Nothing needs you right now.'}
        </p>
      ) : (
        <ul aria-label="Ideas you are handling" className="mt-5 grid gap-3 lg:grid-cols-2">
          {shown.map((item) => (
            <WorkCard key={item.ideaId} item={item} steps={work.steps} />
          ))}
        </ul>
      )}
    </section>
  )
}
