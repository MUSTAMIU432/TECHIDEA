import { NavLink, Outlet } from 'react-router-dom'

const LINKS = [
  { to: '/app/automation/opportunities', label: 'Opportunities' },
  { to: '/app/automation/queue', label: 'Developer Queue' },
  { to: '/app/automation/projects', label: 'Projects' },
] as const

/** The Automation area: opportunities, the developer queue and projects, one step each. */
export function AutomationLayout() {
  return (
    <>
      <div className="max-w-2xl">
        <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">Automation</p>
        <h1 className="mt-1 text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          From approved idea to measured result.
        </h1>
      </div>
      <nav aria-label="Automation" className="mt-5 flex gap-2 overflow-x-auto">
        {LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className={({ isActive }) =>
              `rounded-lg px-4 py-2 text-sm font-semibold whitespace-nowrap ${
                isActive
                  ? 'bg-brand-600 text-white shadow-sm'
                  : 'bg-white text-gray-700 ring-1 ring-gray-200 hover:bg-gray-50'
              }`
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </>
  )
}
