import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { SpinnerIcon } from '../../identity/components/icons'
import { giveGoAheadRequest } from '../../ideas/api/ideasApi'
import {
  ideaReviewReportRequest,
  ideaSubmissionVersionsRequest,
  type PlatformReviewReport,
  type SubmissionVersion,
} from '../api/reviewsApi'
import { formatDate } from '../utils/reviewLabels'

/**
 * The platform's review and approval report for an idea, at
 * `/app/ideas/:ideaId/report`.
 *
 * **This is not the approval, and this page must never read as one.** The report
 * says what the platform concluded after reading the idea; the author's own
 * decision to proceed is a separate act with a separate button
 * (`giveGoAheadRequest` in `ideasApi`). Saying "approved" on its own would tell
 * the reader the matter is finished when the one decision that is theirs has not
 * been made, so the heading is the reviewer's conclusion and the approval is
 * described as what it is: a recommendation, with the author's confirmation
 * still outstanding.
 *
 * `ideaReviewReport` answers null for an idea with no report, for one that was
 * not approved, and for a viewer who is neither the author nor a platform
 * reviewer — one answer for all three, so this screen cannot be used to find
 * out whether an idea has been approved. It says only that there is nothing here.
 *
 * **Categorical, never numeric.** The criteria carry a rating and a note. There
 * is no score, no total and no ranking anywhere on this page, because a number
 * would be read as a measurement the platform did not make.
 */
export function IdeaReportPage() {
  const { ideaId = '' } = useParams()
  const [report, setReport] = useState<PlatformReviewReport | null>(null)
  const [versions, setVersions] = useState<SubmissionVersion[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    Promise.all([ideaReviewReportRequest(ideaId), ideaSubmissionVersionsRequest(ideaId)])
      .then(([found, history]) => {
        if (cancelled) return
        setReport(found)
        setVersions(history)
      })
      .catch(() => {
        if (cancelled) return
        // Null is the answer this page renders for "nothing to show", and a
        // failed request is that same honest absence rather than a claim.
        setReport(null)
        setVersions([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [ideaId])

  if (loading) {
    return <p className="mt-8 text-sm text-gray-600">Loading the review report…</p>
  }

  if (report === null) {
    return (
      <section className="mt-8">
        <div className="rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600 shadow-sm">
          <p>There is no review report for this idea yet.</p>
          <Link
            to={`/app/ideas/${ideaId}`}
            className="mt-3 inline-block font-semibold text-brand-700 hover:text-brand-800"
          >
            Back to the idea
          </Link>
        </div>
      </section>
    )
  }

  return (
    <section aria-labelledby="report-heading" className="mt-8">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">
        Platform review
      </p>
      <h1 id="report-heading" className="mt-1 text-3xl font-bold tracking-tight text-gray-900">
        What the platform concluded.
      </h1>
      <p className="mt-3 max-w-2xl text-base leading-7 text-gray-600">
        This is the reviewer&rsquo;s assessment, not the approval. Whether your idea goes ahead is
        still your decision.
      </p>

      <div className="mt-6 space-y-6">
        <Panel title="The decision">
          <p className="text-sm font-semibold text-gray-900">{report.decision}</p>
          <p className="mt-1 text-sm leading-6 text-gray-600">{report.approvalSummary}</p>
          <p className="mt-2 text-xs text-gray-500">
            Reviewed by {report.reviewerFirstName} · approved {formatDate(report.approvedAt)} ·
            round {report.round}
          </p>
        </Panel>

        <Panel title="The reviewer's summary">
          <Prose text={report.reviewSummary} />
        </Panel>

        <Panel title="How it was assessed">
          {/*
            A list of criterion, rating and note — in the reviewer's own words,
            with no arithmetic over them. Deliberately not a score card.
          */}
          <dl className="space-y-3">
            {report.criteria.map((entry) => (
              <div key={entry.criterion}>
                <dt className="text-sm font-semibold text-gray-900">{entry.criterion}</dt>
                <dd className="mt-0.5 text-sm text-gray-600">
                  <span className="font-medium text-gray-700">{entry.rating}</span>
                  {entry.note ? ` — ${entry.note}` : ''}
                </dd>
              </div>
            ))}
          </dl>
        </Panel>

        {report.feedback ? (
          <Panel title="Feedback">
            <Prose text={report.feedback} />
          </Panel>
        ) : null}

        {report.recommendations ? (
          <Panel title="What they recommend">
            <Prose text={report.recommendations} />
          </Panel>
        ) : null}

        {report.constraints ? (
          <Panel title="Constraints">
            <Prose text={report.constraints} />
          </Panel>
        ) : null}

        {report.importantConsiderations ? (
          <Panel title="Things to keep in mind">
            <Prose text={report.importantConsiderations} />
          </Panel>
        ) : null}

        {report.nextSteps ? (
          <Panel title="What happens next">
            <Prose text={report.nextSteps} />
          </Panel>
        ) : null}

        {versions.length > 0 && (
          <Panel title="What was submitted">
            {/*
              One frozen version per submission, so a resubmission is visible as
              a *new* version rather than having overwritten the last. The report
              is about the version it names.
            */}
            <ul className="space-y-2">
              {versions.map((version) => (
                <li key={version.id} className="text-sm text-gray-600">
                  <span className="font-medium text-gray-900">Version {version.version}</span> ·{' '}
                  {formatDate(version.submittedAt)} · {version.title}
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>

      <GoAheadPanel ideaId={ideaId} decided={report.decision !== 'APPROVED'} />

      <Link
        to={`/app/ideas/${ideaId}`}
        className="mt-8 inline-block font-semibold text-brand-700 hover:text-brand-800"
      >
        Back to the idea
      </Link>
    </section>
  )
}

/**
 * The one button on this page, and it is the author's.
 *
 * Platform approval is a recommendation; giving the go-ahead is a separate act
 * that only the author may make, which is why it gets its own control rather
 * than being folded into "approved". `decided` comes from the report's own
 * decision, so a report that did not recommend approval offers nothing here
 * rather than offering a button the server would refuse.
 */
function GoAheadPanel({ ideaId, decided }: { ideaId: string; decided: boolean }) {
  const [state, setState] = useState<'idle' | 'pending' | 'done'>('idle')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (decided) return null

  async function handleGoAhead() {
    setState('pending')
    setMessage(null)
    setError(null)
    try {
      const result = await giveGoAheadRequest(ideaId)
      if (!result.success) {
        setError(result.message)
        setState('idle')
        return
      }
      setMessage(result.message)
      setState('done')
    } catch {
      setError('We could not reach the server, so nothing has changed. Please try again.')
      setState('idle')
    }
  }

  return (
    <section
      aria-label="Your decision"
      className="mt-6 rounded-xl border border-brand-200 bg-brand-50 p-5"
    >
      <h2 className="text-base font-semibold text-brand-900">Your decision</h2>
      {state === 'done' ? (
        <output className="mt-2 block text-sm text-brand-800">{message}</output>
      ) : (
        <>
          <p className="mt-2 text-sm leading-6 text-brand-900">
            The platform recommends this idea. Whether it goes ahead is yours to say, and nobody
            else&rsquo;s — giving the go-ahead is what moves it on.
          </p>
          {error ? (
            <p role="alert" className="mt-3 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => void handleGoAhead()}
            disabled={state === 'pending'}
            className="mt-4 inline-flex h-11 items-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {state === 'pending' && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
            {state === 'pending' ? 'Recording…' : 'Give the go-ahead'}
          </button>
        </>
      )}
    </section>
  )
}

/** One titled block of the report. */
function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      aria-label={title}
      className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
    >
      <h2 className="text-base font-semibold text-gray-900">{title}</h2>
      <div className="mt-2">{children}</div>
    </section>
  )
}

/**
 * Report prose, whitespace preserved.
 *
 * `whitespace-pre-line` and nothing else: the server sends plain text with
 * newlines, and this client never renders anything as HTML — a report is
 * reviewer-authored text, and `dangerouslySetInnerHTML` would make the one piece
 * of user-authored content in the app the one piece that can run code.
 */
function Prose({ text }: { text: string }) {
  return <p className="whitespace-pre-line text-sm leading-6 text-gray-700">{text}</p>
}
