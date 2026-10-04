import { IdeasWorkspace } from '../features/ideas/components/IdeasWorkspace'

/**
 * The Ideas area at `/app/ideas`.
 *
 * Rendered inside `AppLayout`, so it inherits `RequireAuth` and the shared
 * `OrganizationProvider` - which is what supplies the active organization the
 * ideas belong to. There is deliberately no organization picker on this page:
 * the switcher in the layout header is the one the whole authenticated app
 * shares, so a second one here could point the screen at an organization the
 * user is not in, and the server would refuse every write anyway.
 */
export function IdeasPage() {
  return (
    <>
      <div className="max-w-2xl">
        <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
          Put a problem forward.
        </h1>
        <p className="mt-3 text-base leading-7 text-gray-600">
          Describe something that should be automated. Save it as a draft while you think it
          through, then submit it when it is ready to be looked at.
        </p>
      </div>

      <IdeasWorkspace />
    </>
  )
}
