import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import type { Idea } from '../../ideas/api/ideasApi'
import { formatDate } from '../../reviews/utils/reviewLabels'
import { teamReviewQueueRequest } from '../../reviews/api/reviewsApi'

/**
 * The team's ideas waiting for a reviewer of this team to verify them.
 *
 * Shown to the team's reviewers only (the page decides who is one; the server
 * answers empty to anybody else, and never lists the viewer's own ideas). Each row
 * opens the idea, where the reviewer reads it and either verifies it or sends it
 * back asking for changes or more documents. Once verified, an idea leaves this
 * list and goes back to its owner to publish to the platform.
 */
export function TeamReviewQueue({ teamId }: { teamId: string }) {
  const [answer, setAnswer] = useState<{ teamId: string; ideas: Idea[] | null } | null>(null)

  useEffect(() => {
    let cancelled = false
    teamReviewQueueRequest(teamId)
      .then((ideas) => {
        if (!cancelled) setAnswer({ teamId, ideas })
      })
      .catch(() => {
        if (!cancelled) setAnswer({ teamId, ideas: null })
      })
    return () => {
      cancelled = true
    }
  }, [teamId])

  const ready = answer !== null && answer.teamId === teamId

  return (
    <section aria-labelledby="team-queue-heading" className="mt-6">
      <h2 id="team-queue-heading" className="text-base font-semibold text-gray-900">
        Waiting for your review
      </h2>
      {!ready ? (
        <output className="mt-2 block text-sm text-gray-600">Loading the review queue…</output>
      ) : answer.ideas === null ? (
        <p role="alert" className="mt-2 text-sm text-red-700">
          We could not load the review queue. Please refresh the page.
        </p>
      ) : answer.ideas.length === 0 ? (
        <p className="mt-2 text-sm text-gray-600">
          No team ideas are waiting for you. Ideas you wrote yourself never appear here.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {answer.ideas.map((idea) => (
            <li key={idea.id}>
              <Link
                to={`/app/ideas/${idea.id}`}
                className="block rounded-lg border border-gray-200 bg-white p-3 hover:border-brand-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
              >
                <span className="block text-sm font-semibold text-gray-900">{idea.title}</span>
                <span className="mt-0.5 block text-xs text-gray-500">
                  {idea.submittedAt
                    ? `Submitted ${formatDate(idea.submittedAt)}`
                    : 'Waiting for review'}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
