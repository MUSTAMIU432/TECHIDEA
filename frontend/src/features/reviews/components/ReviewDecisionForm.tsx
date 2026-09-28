import { useState, type FormEvent } from 'react'

import {
  completeReviewRequest,
  type CriterionRating,
  type ReviewCriterion,
  type ReviewDecision,
  type ReviewMutationResult,
} from '../api/reviewsApi'
import { CRITERION_LABELS, DECISION_LABELS, RATING_LABELS } from '../utils/reviewLabels'

const CRITERIA = Object.keys(CRITERION_LABELS) as ReviewCriterion[]
const RATINGS = Object.keys(RATING_LABELS) as CriterionRating[]
const DECISIONS = Object.keys(DECISION_LABELS) as ReviewDecision[]

/** The decisions that must explain themselves to the author - the server's rule too. */
const FEEDBACK_REQUIRED: ReadonlySet<ReviewDecision> = new Set(['CHANGES_REQUESTED', 'REJECTED'])

type Ratings = Partial<Record<ReviewCriterion, CriterionRating>>
type Notes = Partial<Record<ReviewCriterion, string>>

/**
 * Record the decision on the viewer's own open review (S3-004).
 *
 * The checks here mirror the server's so a reviewer is told what is missing
 * before a round trip - every criterion rated, a decision chosen, feedback for
 * a send-back or a rejection. They are a convenience: `completeReview` checks
 * all of it again, and a refusal it returns is shown as the server wrote it.
 *
 * Nothing about the outcome is guessed. On success the parent receives the
 * server's review and idea and reconciles from those.
 */
export function ReviewDecisionForm({
  ideaId,
  reviewId,
  onCompleted,
}: {
  ideaId: string
  reviewId: string
  onCompleted: (result: ReviewMutationResult) => void
}) {
  const [ratings, setRatings] = useState<Ratings>({})
  const [notes, setNotes] = useState<Notes>({})
  const [feedback, setFeedback] = useState('')
  const [decision, setDecision] = useState<ReviewDecision | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function localProblem(): string | null {
    if (CRITERIA.some((criterion) => ratings[criterion] === undefined)) {
      return 'Rate every criterion before deciding.'
    }
    if (decision === null) return 'Choose a decision.'
    if (FEEDBACK_REQUIRED.has(decision) && feedback.trim() === '') {
      return 'Explain this decision to the author.'
    }
    return null
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const found = localProblem()
    if (found !== null || decision === null) {
      setProblem(found)
      return
    }
    setProblem(null)
    setSubmitting(true)
    try {
      const result = await completeReviewRequest({
        ideaId,
        reviewId,
        decision,
        feedback: feedback.trim(),
        assessments: CRITERIA.map((criterion) => ({
          criterion,
          rating: ratings[criterion] as CriterionRating,
          note: (notes[criterion] ?? '').trim(),
        })),
      })
      if (result.success) {
        onCompleted(result)
        return
      }
      setProblem(result.message)
    } catch {
      setProblem('We could not reach the server. Your review was not recorded; please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form
      aria-label="Review decision"
      onSubmit={handleSubmit}
      noValidate
      className="mt-5 space-y-5"
    >
      <fieldset className="space-y-4">
        <legend className="text-sm font-semibold text-gray-900">Criteria</legend>
        {CRITERIA.map((criterion) => (
          <div key={criterion} className="rounded-lg border border-gray-200 p-3">
            <label
              htmlFor={`rating-${criterion}`}
              className="block text-sm font-medium text-gray-800"
            >
              {CRITERION_LABELS[criterion]}
            </label>
            <select
              id={`rating-${criterion}`}
              value={ratings[criterion] ?? ''}
              onChange={(event) =>
                setRatings((current) => ({
                  ...current,
                  [criterion]: event.target.value as CriterionRating,
                }))
              }
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            >
              <option value="" disabled>
                Choose a rating
              </option>
              {RATINGS.map((rating) => (
                <option key={rating} value={rating}>
                  {RATING_LABELS[rating]}
                </option>
              ))}
            </select>
            <label htmlFor={`note-${criterion}`} className="mt-2 block text-xs text-gray-500">
              Note on {CRITERION_LABELS[criterion].toLowerCase()} (optional)
            </label>
            <textarea
              id={`note-${criterion}`}
              rows={2}
              maxLength={1000}
              value={notes[criterion] ?? ''}
              onChange={(event) =>
                setNotes((current) => ({ ...current, [criterion]: event.target.value }))
              }
              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            />
          </div>
        ))}
      </fieldset>

      <div>
        <label htmlFor="review-feedback" className="block text-sm font-semibold text-gray-900">
          Feedback to the author
        </label>
        <p className="text-xs text-gray-500">Required when requesting changes or rejecting.</p>
        <textarea
          id="review-feedback"
          rows={4}
          maxLength={5000}
          value={feedback}
          onChange={(event) => setFeedback(event.target.value)}
          className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
        />
      </div>

      <fieldset>
        <legend className="text-sm font-semibold text-gray-900">Decision</legend>
        <div className="mt-2 flex flex-wrap gap-4">
          {DECISIONS.map((option) => (
            <label key={option} className="inline-flex items-center gap-2 text-sm text-gray-800">
              <input
                type="radio"
                name="decision"
                value={option}
                checked={decision === option}
                onChange={() => setDecision(option)}
              />
              {DECISION_LABELS[option]}
            </label>
          ))}
        </div>
      </fieldset>

      {problem && (
        <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {problem}
        </p>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {submitting ? 'Recording decision…' : 'Record decision'}
      </button>
    </form>
  )
}
