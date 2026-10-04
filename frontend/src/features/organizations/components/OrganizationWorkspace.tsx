import { CreateOrganizationForm } from './CreateOrganizationForm'
import { OrganizationList } from './OrganizationList'

/** List and form columns, shared by the heading row so its intro text sits above the form. */
const COLUMNS = 'grid gap-x-6 lg:grid-cols-2'

export function OrganizationWorkspace() {
  return (
    <section aria-labelledby="organizations-heading" className="mt-10">
      <div className={`${COLUMNS} gap-y-2 lg:items-end`}>
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.18em] text-brand-700">Workspace</p>
          <h2
            id="organizations-heading"
            className="mt-1 text-2xl font-bold tracking-tight text-gray-900"
          >
            Your organizations
          </h2>
        </div>
        <p className="text-sm leading-6 text-gray-600">
          Choose an organization for shared work, or create a new one when you are ready.
        </p>
      </div>

      <div className={`${COLUMNS} mt-5 items-start gap-y-5`}>
        <OrganizationList />
        <CreateOrganizationForm />
      </div>
    </section>
  )
}
