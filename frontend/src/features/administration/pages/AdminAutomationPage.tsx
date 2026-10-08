import { Link } from 'react-router-dom'

import { adminOpportunitiesRequest, adminProjectsRequest } from '../../automation/api/automationApi'
import { CONTEXT_LABEL, label } from '../../automation/utils/labels'
import {
  AdminPageHeader,
  AdminTable,
  EmptyState,
  ErrorState,
  LoadingState,
  cellClasses,
} from '../components/AdminUi'
import { useAdminQuery } from '../hooks/useAdminQuery'

/**
 * Every automation opportunity and project on the platform, read-only.
 *
 * Oversight is not a bypass: this page lists and links, it has no action that
 * moves anything, and the server returns nothing to an administrator who cannot
 * inspect idea content. Each row opens the same page its owners use, where the
 * server decides what the administrator may see.
 */
export function AdminAutomationPage() {
  const opportunities = useAdminQuery(
    'automation:opportunities',
    () => adminOpportunitiesRequest(),
    'We could not load the automation opportunities.',
  )
  const projects = useAdminQuery(
    'automation:projects',
    () => adminProjectsRequest(),
    'We could not load the automation projects.',
  )

  return (
    <>
      <AdminPageHeader
        title="Automation"
        description="Opportunities and projects from the automation delivery lifecycle, across every organization and team. Read-only."
      />

      <h2 className="mb-3 text-lg font-bold text-slate-900">Opportunities</h2>
      {opportunities.error && (
        <ErrorState message={opportunities.error} onRetry={opportunities.reload} />
      )}
      {opportunities.loading && !opportunities.data && (
        <LoadingState label="Loading opportunities…" />
      )}
      {opportunities.data && opportunities.data.length === 0 && (
        <EmptyState
          title="No automation opportunities."
          description="They appear once an approved idea is opened for delivery."
        />
      )}
      {opportunities.data && opportunities.data.length > 0 && (
        <AdminTable
          label="Automation opportunities"
          columns={['Opportunity', 'Owner', 'Type', 'Stage', 'Priority']}
        >
          {opportunities.data.map((o) => (
            <tr key={o.id}>
              <td className={cellClasses}>
                <Link
                  to={`/app/automation/opportunities/${o.id}`}
                  className="font-semibold text-slate-900 hover:underline"
                >
                  {o.title}
                </Link>
              </td>
              <td className={cellClasses}>{o.tenantName ?? o.ownerName}</td>
              <td className={cellClasses}>{CONTEXT_LABEL[o.submissionContext]}</td>
              <td className={cellClasses}>{label(o.status)}</td>
              <td className={cellClasses}>{label(o.priority)}</td>
            </tr>
          ))}
        </AdminTable>
      )}

      <h2 className="mt-8 mb-3 text-lg font-bold text-slate-900">Projects</h2>
      {projects.error && <ErrorState message={projects.error} onRetry={projects.reload} />}
      {projects.loading && !projects.data && <LoadingState label="Loading projects…" />}
      {projects.data && projects.data.length === 0 && (
        <EmptyState
          title="No projects."
          description="A project starts once an opportunity is assigned."
        />
      )}
      {projects.data && projects.data.length > 0 && (
        <AdminTable
          label="Automation projects"
          columns={['Project', 'Owner', 'Delivered by', 'Status', 'Tasks']}
        >
          {projects.data.map((p) => (
            <tr key={p.id}>
              <td className={cellClasses}>
                <Link
                  to={`/app/automation/projects/${p.id}`}
                  className="font-semibold text-slate-900 hover:underline"
                >
                  {p.title}
                </Link>
              </td>
              <td className={cellClasses}>{p.tenantName ?? p.ownerName}</td>
              <td className={cellClasses}>{p.assignedName}</td>
              <td className={cellClasses}>{label(p.status)}</td>
              <td className={`${cellClasses} tabular-nums`}>
                {p.progress.tasksDone}/{p.progress.tasksTotal}
              </td>
            </tr>
          ))}
        </AdminTable>
      )}
    </>
  )
}
