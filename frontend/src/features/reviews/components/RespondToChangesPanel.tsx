import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import type { Idea } from '../../ideas/api/ideasApi'
import { respondToChangesRequest } from '../api/changeResponsesApi'
import { ideaReviewsRequest, type Review } from '../api/reviewsApi'
import { CRITERION_LABELS, RATING_LABELS, formatDate } from '../utils/reviewLabels'

const MAX_LENGTH = 3000
const ANSWERABLE = new Set(['CHANGES_REQUESTED', 'ORGANIZATION_CHANGES_REQUESTED'])

/** The completed round that asked for the changes now being answered. */
function roundThatAsked(reviews: Review[], status: string): Review | null {
  const scope = status === 'CHANGES_REQUESTED' ? 'PLATFORM' : 'ORGANIZATION'
  const asked = reviews.filter(
    (review) =>
      review.scope === scope &&
      review.decision === 'CHANGES_REQUESTED' &&
      review.completedAt !== null,
  )
  return asked.length > 0 ? asked[asked.length - 1] : null
}

/**
 * Where the owner answers a request for changes, at the top of their idea's page.
 *
 * Three steps, in the order the work happens: revise the idea, add any documents the
 * reviewers asked for, then say what changed and resubmit - which the server does in
 * one step, so the reviewers never see a response without the version it describes.
 * Drawn only for the author while changes are requested; once resubmitted the idea is
 * back with the reviewers and the panel is gone.
 */
export function RespondToChangesPanel({
  idea,
  viewerId,
  onResponded,
}: {
  idea: Idea
  viewerId: string | null
  onResponded: (message: string) => void
}) {
  const visible = ANSWERABLE.has(idea.status) && viewerId !== null && idea.authorId === viewerId
  const [reviews, setReviews] = useState<{ ideaId: string; reviews: Review[] } | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!visible) return
    let cancelled = false
    ideaReviewsRequest(idea.id)
      .then((answer) => {
        if (!cancelled) setReviews({ ideaId: idea.id, reviews: answer })
      })
      .catch(() => {
        // The panel still lets the owner respond; the feedback is in the history below.
      })
    return () => {
      cancelled = true
    }
  }, [idea.id, visible])

  if (!visible) return null

  const asked =
    reviews !== null && reviews.ideaId === idea.id
      ? roundThatAsked(reviews.reviews, idea.status)
      : null
  const shortfalls = (asked?.assessments ?? []).filter(
    (assessment) =>
      assessment.rating === 'DOES_NOT_MEET' || assessment.rating === 'PARTIALLY_MEETS',
  )
  const who = idea.status === 'CHANGES_REQUESTED' ? 'The platform review team' : 'Your reviewers'
  const tooLong = message.length > MAX_LENGTH

  async function send() {
    setBusy(true)
    setError(null)
    try {
      const result = await respondToChangesRequest(idea.id, message)
      if (result.success) {
        onResponded(result.message)
        return
      }
      setError(result.message)
    } catch {
      setError('We could not reach the server. Your response was not sent.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      id="respond"
      aria-labelledby="respond-heading"
      className="mt-6 scroll-mt-24 overflow-hidden rounded-2xl border border-amber-200 bg-white shadow-sm"
    >
      <div className="border-b border-amber-200 bg-amber-50 px-6 py-4">
        <p className="text-xs font-semibold tracking-[0.18em] text-amber-800 uppercase">
          Changes requested
        </p>
        <h2 id="respond-heading" className="mt-1 text-xl font-bold text-gray-900">
          Respond to the review
        </h2>
        <p className="mt-1 text-sm text-amber-900">
          {who} asked for changes
          {asked?.completedAt ? ` on ${formatDate(asked.completedAt)}` : ''}. Make them, then send
          your response - the idea goes back to them for the next round.
        </p>
      </div>

      <div className="space-y-6 px-6 py-5">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">What they asked</h3>
          {asked?.feedback ? (
            <blockquote className="mt-2 rounded-lg border-l-4 border-amber-400 bg-amber-50/60 px-4 py-3 text-sm leading-6 whitespace-pre-line text-gray-800">
              {asked.feedback}
            </blockquote>
          ) : (
            <p className="mt-2 text-sm text-gray-500">
              Their feedback is in the review history at the bottom of this page.
            </p>
          )}
          {shortfalls.length > 0 && (
            <ul aria-label="Criteria to improve" className="mt-3 flex flex-wrap gap-2">
              {shortfalls.map((assessment) => (
                <li
                  key={assessment.criterion}
                  title={assessment.note || undefined}
                  className={`rounded-full px-3 py-1 text-xs font-semibold ${
                    assessment.rating === 'DOES_NOT_MEET'
                      ? 'bg-red-50 text-red-700'
                      : 'bg-amber-100 text-amber-800'
                  }`}
                >
                  {CRITERION_LABELS[assessment.criterion]}: {RATING_LABELS[assessment.rating]}
                  {assessment.note ? ` - ${assessment.note}` : ''}
                </li>
              ))}
            </ul>
          )}
        </div>

        <ol className="grid gap-3 sm:grid-cols-2">
          <li className="rounded-xl border border-gray-200 p-4">
            <p className="text-xs font-semibold text-brand-700">Step 1</p>
            <p className="mt-1 text-sm font-semibold text-gray-900">Revise your idea</p>
            <p className="mt-1 text-xs leading-5 text-gray-600">
              Update the description and answers. Save your changes there, then come back here to
              send your response.
            </p>
            <Link
              to={`/app/ideas/${idea.id}/edit`}
              className="mt-3 inline-flex rounded-lg border border-brand-300 px-3 py-1.5 text-sm font-semibold text-brand-700 hover:bg-brand-50"
            >
              Revise idea
            </Link>
          </li>
          <li className="rounded-xl border border-gray-200 p-4">
            <p className="text-xs font-semibold text-brand-700">Step 2</p>
            <p className="mt-1 text-sm font-semibold text-gray-900">Add documents</p>
            <p className="mt-1 text-xs leading-5 text-gray-600">
              Attach anything the reviewers asked for - forms, numbers, screenshots.
            </p>
            <a
              href="#idea-evidence"
              className="mt-3 inline-flex rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              Go to documents
            </a>
          </li>
        </ol>

        <div>
          <label htmlFor="change-response" className="text-sm font-semibold text-gray-900">
            Step 3 · Tell the reviewers what you changed
          </label>
          <textarea
            id="change-response"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            rows={5}
            placeholder="For example: I attached the March and April collection forms, and added the number of officers involved to the description."
            className="mt-2 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm leading-6 text-gray-900 focus:border-brand-500 focus:ring-2 focus:ring-brand-200 focus:outline-none"
          />
          <p className={`mt-1 text-xs ${tooLong ? 'text-red-600' : 'text-gray-500'}`}>
            {message.length} / {MAX_LENGTH} characters
          </p>
        </div>

        {error && (
          <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={busy || message.trim() === '' || tooLong}
            onClick={() => void send()}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? 'Sending…' : 'Send response and resubmit'}
          </button>
          <span className="text-xs text-gray-500">
            Your idea goes back to the reviewers with your response.
          </span>
        </div>
      </div>
    </section>
  )
}
