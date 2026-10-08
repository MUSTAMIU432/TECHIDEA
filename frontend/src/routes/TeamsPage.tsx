import { CreateTeamForm } from '../features/teams/components/CreateTeamForm'
import { TeamList } from '../features/teams/components/TeamList'
import { TeamsProvider } from '../features/teams/context/TeamsProvider'

/**
 * The caller's teams at `/app/teams`, and the form that creates one.
 *
 * A team is a collaboration boundary rather than a tenant, so this is a page
 * under `/app` like any other and not a mode the header switches into — see
 * `TeamList` for why there is no "active team".
 *
 * It is deliberately reachable with **no organization at all**, which is the
 * case the whole Teams domain exists for: somebody with no tenant can still
 * create a team, put a team idea forward and take it to the platform.
 */
export function TeamsPage() {
  return (
    <>
      <div className="max-w-2xl">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">Your teams.</h1>
        <p className="mt-3 text-base leading-7 text-gray-600">
          A team is a group of people who put an idea forward together. It is not an organization:
          an organization is shared work with its own reviewers, while a team is simply who writes
          the idea with you.
        </p>
      </div>

      <section aria-labelledby="teams-heading" className="mt-8">
        <div className="grid gap-x-6 lg:grid-cols-2 lg:items-end">
          <div>
            <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand-700">Teams</p>
            <h2 id="teams-heading" className="mt-1 text-2xl font-bold tracking-tight text-gray-900">
              Teams you are in
            </h2>
          </div>
          <p className="text-sm leading-6 text-gray-600">
            Open a team to see who is in it, invite somebody, or leave it.
          </p>
        </div>

        {/* One load for both halves: the list draws the caller's teams and the
            form refreshes them, so a provider here is the difference between one
            request on this page and two. */}
        <TeamsProvider>
          <div className="mt-5 grid items-start gap-5 lg:grid-cols-2">
            <TeamList />
            <CreateTeamForm />
          </div>
        </TeamsProvider>
      </section>
    </>
  )
}
