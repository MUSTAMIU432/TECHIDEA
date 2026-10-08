import { Link } from 'react-router-dom'

import { CreateOrganizationForm } from '../features/organizations/components/CreateOrganizationForm'
import { useOrganization } from '../features/organizations/context/useOrganization'

/**
 * The caller's organizations at `/app/organizations`.
 *
 * Opening one is how a person chooses where they are working: its page shows the
 * ideas it has put forward and, for a reviewer, the review queue. There is no
 * header switcher any more - the list is the way in, like Teams.
 */
export function OrganizationsPage() {
  const { status, memberships, error, reload } = useOrganization()

  return (
    <>
      <div className="max-w-2xl">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          Your organizations.
        </h1>
        <p className="mt-3 text-base leading-7 text-gray-600">
          Open an organization to see its ideas, its members and, if you review for it, the queue of
          ideas its people have sent in.
        </p>
      </div>

      <section aria-labelledby="organizations-heading" className="mt-8">
        <h2 id="organizations-heading" className="text-2xl font-bold tracking-tight text-gray-900">
          Organizations you are in
        </h2>
        <div className="mt-5 grid items-start gap-5 lg:grid-cols-2">
          <div>
            {status === 'loading' && memberships.length === 0 ? (
              <output className="block text-sm text-gray-600">Loading organizations…</output>
            ) : status === 'error' ? (
              <div className="rounded-xl border border-red-200 bg-red-50 p-5" role="alert">
                <p className="text-sm text-red-700">{error}</p>
                <button
                  type="button"
                  onClick={() => void reload()}
                  className="mt-3 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-800 hover:bg-red-100"
                >
                  Try again
                </button>
              </div>
            ) : memberships.length === 0 ? (
              <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5">
                <p className="text-sm font-semibold text-gray-900">No organizations yet</p>
                <p className="mt-1 text-sm leading-6 text-gray-600">
                  Create one to give your work a shared home.
                </p>
              </div>
            ) : (
              <ul className="space-y-3" aria-label="Your organizations">
                {memberships.map(({ organization, membership }) => (
                  <li key={organization.id}>
                    <Link
                      to={`/app/organizations/${organization.id}`}
                      className="block rounded-xl border border-gray-300 bg-white p-4 shadow-sm transition-colors hover:border-brand-300 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
                    >
                      <span className="block text-base font-bold text-gray-900">
                        {organization.name}
                      </span>
                      <span className="mt-1 block text-sm text-gray-500">/{organization.slug}</span>
                      {membership.roles.length > 0 && (
                        <span className="mt-2 flex flex-wrap gap-1.5">
                          {membership.roles.map((role) => (
                            <span
                              key={role.id}
                              className="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-800"
                            >
                              {role.name}
                            </span>
                          ))}
                        </span>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <CreateOrganizationForm />
        </div>
      </section>
    </>
  )
}
