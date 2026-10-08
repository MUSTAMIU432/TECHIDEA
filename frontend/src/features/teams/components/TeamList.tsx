import { Link } from 'react-router-dom'

import type { Team } from '../api/teamsApi'
import { useTeams } from '../context/useTeams'

/**
 * The caller's teams, as a list of links into each one.
 *
 * Deliberately a list of links and not a switcher like the organization's: a
 * team is a *collaboration boundary*, not the tenant an idea belongs to, so
 * picking one does not change what the rest of the app is pointed at. It is a
 * place you go, not a mode you switch into — which is why it offers no "active"
 * state and no second picker in the header.
 *
 * The teams come from the page's `TeamsProvider`, so this list and the form
 * beside it share one request rather than making one each.
 */
export function TeamList() {
  const { teams, loading, error, reload } = useTeams()

  if (loading && teams.length === 0) {
    return (
      <output className="block rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <span className="sr-only">Loading teams…</span>
        <span className="block h-4 w-32 animate-pulse rounded bg-gray-200" />
        <span className="mt-3 block h-3 w-48 animate-pulse rounded bg-gray-100" />
      </output>
    )
  }

  if (error) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-5" role="alert">
        <p className="text-sm font-semibold text-red-800">Teams unavailable</p>
        <p className="mt-1 text-sm text-red-700">{error}</p>
        <button
          type="button"
          className="mt-4 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-800 hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
          onClick={reload}
        >
          Try again
        </button>
      </div>
    )
  }

  if (teams.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5">
        <p className="text-sm font-semibold text-gray-900">You are not in a team yet</p>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          A team is a group of people who put an idea forward together. Create one, and you will be
          its first member.
        </p>
      </div>
    )
  }

  return (
    <ul className="space-y-3" aria-label="Your teams">
      {teams.map((team) => (
        <TeamRow key={team.id} team={team} />
      ))}
    </ul>
  )
}

function TeamRow({ team }: { team: Team }) {
  return (
    <li>
      <Link
        to={`/app/teams/${team.id}`}
        className="block rounded-xl border border-gray-300 bg-white p-4 shadow-sm transition-colors hover:border-brand-300 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
      >
        <span className="flex items-start justify-between gap-4">
          <span className="min-w-0">
            <span className="block text-base font-bold text-gray-900">{team.name}</span>
            <span className="mt-1 block text-sm text-gray-500">/{team.slug}</span>
          </span>
          <span className="shrink-0 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-bold text-gray-700">
            {team.memberCount === 1 ? '1 member' : `${team.memberCount} members`}
          </span>
        </span>
        {team.description ? (
          <span className="mt-2 block text-sm leading-6 text-gray-600">{team.description}</span>
        ) : null}
      </Link>
    </li>
  )
}
