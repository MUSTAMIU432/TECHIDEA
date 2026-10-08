import { useOrganization } from '../context/useOrganization'

export function OrganizationSwitcher() {
  const { status, memberships, activeOrganization, setActiveOrganization } = useOrganization()

  if (memberships.length === 0) return null

  return (
    <label className="flex min-w-0 items-center gap-2 text-sm text-brand-100">
      <span className="hidden shrink-0 font-medium lg:inline">Working in</span>
      <select
        aria-label="Active organization"
        className="h-10 w-full min-w-0 max-w-56 rounded-lg sm:min-w-40 border border-white/20 bg-white px-3 text-sm font-semibold text-brand-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-white/70 disabled:cursor-not-allowed disabled:opacity-60"
        disabled={status === 'loading'}
        value={activeOrganization?.id ?? ''}
        onChange={(event) => setActiveOrganization(event.target.value)}
      >
        {memberships.map(({ organization }) => (
          <option key={organization.id} value={organization.id}>
            {organization.name}
          </option>
        ))}
      </select>
    </label>
  )
}
