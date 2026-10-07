import { Link } from 'react-router-dom'

import { useTeamIdeas } from '../hooks/useTeamIdeas'
import { IdeaOwnershipSummary } from '../../ideas/components/IdeaOwnership'
import { statusLabel } from '../../ideas/utils/lifecycle'
import { formatDate } from '../../reviews/utils/reviewLabels'

/**
 * The ideas one team has filed.
 *
 * **Server-filtered, and the filter is the team's.** `useTeamIdeas` calls
 * `teamIdeas(teamId)`, which the server scopes to the reader's own membership in
 * that team and then applies the visibility filter - so this list is "what my
 * team is putting forward", never "every idea in a tenant" fetched and filtered
 * here. A colleague's private idea is not in it, and a team the reader is not on
 * answers empty.
 *
 * The empty state says who owns an idea filed from here, because that is the
 * question a reader arriving from a team's page is holding: an idea filed from
 * this page is this team's, whatever it is later published to.
 */
export function TeamIdeasPanel({ teamId }: { teamId: string }) {
  const { ideas, loading, error, reload } = useTeamIdeas(teamId)

  return (
    <section aria-labelledby="team-ideas-heading" className="mt-8">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
        <div>
          <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">Team ideas</p>
          <h2
            id="team-ideas-heading"
            className="mt-1 text-xl font-bold tracking-tight text-gray-900"
          >
            Team Ideas
          </h2>
        </div>
        <Link
          to={`/app/ideas/new?context=team&team=${teamId}`}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-brand-300 bg-white px-4 text-sm font-semibold text-brand-700 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          <span aria-hidden="true">+</span>
          Create Idea
        </Link>
      </div>

      {error && (
        <div className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
          <p>{error}</p>
          <button
            type="button"
            onClick={reload}
            className="mt-3 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm font-semibold text-red-800 hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
          >
            Try again
          </button>
        </div>
      )}

      {loading && ideas.length === 0 && (
        <p className="mt-4 text-sm text-gray-600">Loading your team’s ideas…</p>
      )}

      {!loading && !error && ideas.length === 0 && (
        <div className="mt-4 rounded-xl border border-dashed border-gray-300 bg-white p-5">
          <p className="text-sm font-semibold text-gray-900">This team has no ideas yet.</p>
          <p className="mt-1 text-sm leading-6 text-gray-600">
            An idea you file from here belongs to this team, whatever you later choose about who can
            see it.
          </p>
          <Link
            to={`/app/ideas/new?context=team&team=${teamId}`}
            className="mt-4 inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            Create the first idea
          </Link>
        </div>
      )}

      {ideas.length > 0 && (
        <ul className="mt-4 space-y-3">
          {ideas.map((idea) => (
            <li key={idea.id}>
              <Link
                to={`/app/ideas/${idea.id}`}
                className="block rounded-xl border border-gray-300 bg-white p-4 shadow-sm transition-colors hover:border-brand-300 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
              >
                <span className="flex flex-wrap items-start justify-between gap-3">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-gray-900">
                      {idea.title}
                    </span>
                    <span className="mt-2 block">
                      <IdeaOwnershipSummary
                        context={idea.submissionContext}
                        ownerName={idea.tenantName}
                        visibility={idea.visibility}
                      />
                    </span>
                  </span>
                  <span className="shrink-0 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">
                    {statusLabel(idea.status, idea.submissionContext)}
                  </span>
                </span>
                {idea.submittedAt && (
                  <span className="mt-2 block text-xs text-gray-500">
                    Submitted {formatDate(idea.submittedAt)}
                  </span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
