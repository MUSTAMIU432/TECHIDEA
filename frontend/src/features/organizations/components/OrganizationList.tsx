import { Link } from 'react-router-dom'

import { useOrganization } from '../context/useOrganization'

function statusLabel(status: 'active' | 'inactive'): string {
  return status === 'active' ? 'Active membership' : 'Inactive membership'
}

export function OrganizationList() {
  const { status, memberships, activeOrganization, error, setActiveOrganization, reload } =
    useOrganization()

  if (status === 'loading' && memberships.length === 0) {
    return (
      <output className="block rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <span className="sr-only">Loading organizations…</span>
        <span className="block h-4 w-32 animate-pulse rounded bg-gray-200" />
        <span className="mt-3 block h-3 w-48 animate-pulse rounded bg-gray-100" />
      </output>
    )
  }

  if (status === 'error') {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-5" role="alert">
        <p className="text-sm font-semibold text-red-800">Organizations unavailable</p>
        <p className="mt-1 text-sm text-red-700">{error}</p>
        <button
          type="button"
          className="mt-4 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-800 hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
          onClick={() => void reload()}
        >
          Try again
        </button>
      </div>
    )
  }

  if (memberships.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 bg-white p-5">
        <p className="text-sm font-semibold text-gray-900">No organizations yet</p>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          Create an organization to give your workspace a shared home.
        </p>
      </div>
    )
  }

  return (
    <ul className="space-y-3" aria-label="Your organizations">
      {memberships.map(({ organization, membership }) => {
        const isActive = activeOrganization?.id === organization.id
        return (
          <li key={organization.id}>
            <button
              type="button"
              aria-pressed={isActive}
              className={`w-full rounded-xl border p-4 text-left shadow-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 ${
                isActive
                  ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-500'
                  : 'border-gray-300 bg-white hover:border-brand-300 hover:shadow-md'
              }`}
              onClick={() => setActiveOrganization(organization.id)}
            >
              <span className="flex items-start justify-between gap-4">
                <span>
                  <span className="block text-base font-bold text-gray-900">
                    {organization.name}
                  </span>
                  <span className="mt-1 block text-sm text-gray-500">/{organization.slug}</span>
                </span>
                {isActive ? (
                  <span className="rounded-full bg-brand-600 px-2.5 py-1 text-xs font-bold text-white">
                    Active
                  </span>
                ) : null}
              </span>
              <span className="mt-3 block text-xs font-medium text-gray-500">
                {statusLabel(membership.status)}
              </span>
              {membership.roles.length > 0 ? (
                <span className="mt-2 block text-xs font-semibold text-brand-700">
                  Roles: {membership.roles.map((role) => role.name).join(', ')}
                </span>
              ) : null}
            </button>
            {/*
              A way *into* the organization, as well as a way to make it the
              active one. Choosing it in the header changes what the rest of the
              app is pointed at; opening it changes nothing and shows the ideas
              it owns, its members and its own "Create Idea" button.
            */}
            <Link
              to={`/app/organizations/${organization.id}`}
              className="mt-2 inline-block text-sm font-semibold text-brand-700 hover:text-brand-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
            >
              Open {organization.name} →
            </Link>
          </li>
        )
      })}
    </ul>
  )
}
