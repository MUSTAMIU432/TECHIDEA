import { useEffect, useState } from 'react'

import { ideaDecisionLetterRequest, type OwnerDecisionLetter } from '../api/decisionLettersApi'
import { formatDate } from '../utils/reviewLabels'

/**
 * The letter the platform sent the owner about its decision, at the top of their idea.
 *
 * Drawn only when the server returns one, which it does for the idea's author and only
 * once a platform administrator has sent it. An approval is a celebration; a rejection
 * is a warm, respectful letter - the same text the administrator sent, as written.
 */
export function DecisionLetterCard({ ideaId }: { ideaId: string }) {
  const [letter, setLetter] = useState<{ ideaId: string; letter: OwnerDecisionLetter } | null>(null)

  useEffect(() => {
    let cancelled = false
    ideaDecisionLetterRequest(ideaId)
      .then((answer) => {
        if (!cancelled && answer !== null) setLetter({ ideaId, letter: answer })
      })
      .catch(() => {
        // No letter to show is the same, to the reader, as no letter yet.
      })
    return () => {
      cancelled = true
    }
  }, [ideaId])

  if (letter === null || letter.ideaId !== ideaId) return null
  const { decision, message, sentAt } = letter.letter
  const approved = decision === 'approved'

  return (
    <section
      id="decision-letter"
      aria-labelledby="decision-letter-heading"
      className={`mt-6 scroll-mt-24 overflow-hidden rounded-2xl border shadow-sm ${
        approved ? 'border-emerald-200' : 'border-slate-200'
      }`}
    >
      <div
        className={`px-6 py-6 text-center ${
          approved
            ? 'bg-gradient-to-br from-emerald-600 via-emerald-500 to-teal-500 text-white'
            : 'bg-gradient-to-br from-slate-100 to-indigo-50 text-slate-800'
        }`}
      >
        <div aria-hidden="true" className="text-4xl leading-none">
          {approved ? '🎉' : '💌'}
        </div>
        <h2 id="decision-letter-heading" className="mt-3 text-2xl font-bold tracking-tight">
          {approved ? 'Congratulations! Your idea has been approved' : 'A letter about your idea'}
        </h2>
        <p className={`mt-1 text-sm ${approved ? 'text-emerald-50' : 'text-slate-600'}`}>
          From the platform team · {formatDate(sentAt)}
        </p>
      </div>
      <div className="bg-white px-6 py-6 sm:px-10">
        <p className="mx-auto max-w-2xl text-[15px] leading-7 whitespace-pre-line text-gray-700">
          {message}
        </p>
        {approved && (
          <p className="mx-auto mt-6 max-w-2xl rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
            <span className="font-semibold">What happens next:</span> the review team is preparing
            your proposal. You will be notified when it is ready to read, and nothing goes ahead
            until you give your go-ahead.
          </p>
        )}
      </div>
    </section>
  )
}
