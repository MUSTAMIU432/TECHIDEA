import { useState } from 'react'

import {
  createDeployment,
  deploymentsRequest,
  recordDeployment,
  verifyDeployment,
  type Deployment,
  type Project,
} from '../api/automationApi'
import { formatDate, label } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { useOutcome } from '../utils/useOutcome'
import {
  Badge,
  cardClass,
  Empty,
  Field,
  inputClass,
  Loading,
  LoadFailed,
  NoticeBanner,
  primaryButton,
  secondaryButton,
} from './ui'

/** The statuses a deployment can move to from where it is. */
const NEXT: Record<string, string[]> = {
  pending: ['in_progress', 'failed'],
  in_progress: ['successful', 'failed'],
  successful: ['rolled_back'],
}

function DeploymentRow({
  deployment,
  canManage,
  onChanged,
}: {
  deployment: Deployment
  canManage: boolean
  onChanged: () => void
}) {
  const move = useOutcome()
  return (
    <li className={`${cardClass} space-y-3`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">
            Version {deployment.version} to {deployment.environment}
          </p>
          <p className="mt-1 text-xs text-gray-500">
            {deployment.deploymentDate
              ? `Deployed ${formatDate(deployment.deploymentDate)}`
              : 'Not deployed yet'}
            {deployment.verifiedAt ? ` · Verified ${formatDate(deployment.verifiedAt)}` : ''}
          </p>
        </div>
        <Badge value={deployment.status} />
      </div>
      {deployment.deploymentNotes && (
        <p className="text-sm whitespace-pre-line text-gray-700">{deployment.deploymentNotes}</p>
      )}
      {canManage && (
        <div className="flex flex-wrap gap-2">
          {(NEXT[deployment.status] ?? []).map((status) => (
            <button
              key={status}
              type="button"
              disabled={move.busy}
              className={secondaryButton}
              onClick={() =>
                void move.run(() => recordDeployment({ id: deployment.id, status }), onChanged)
              }
            >
              Mark {label(status).toLowerCase()}
            </button>
          ))}
          {deployment.status === 'successful' && !deployment.verifiedAt && (
            <button
              type="button"
              disabled={move.busy}
              className={primaryButton}
              onClick={() =>
                void move.run(() => verifyDeployment({ id: deployment.id }), onChanged)
              }
            >
              Verify deployment
            </button>
          )}
        </div>
      )}
      <NoticeBanner notice={move.notice} />
    </li>
  )
}

/** Deployments. A project only counts as deployed through a successful record here. */
export function DeploymentTab({ project, onChanged }: { project: Project; onChanged: () => void }) {
  const list = useLoad(() => deploymentsRequest(project.id), `dep:${project.id}:${project.status}`)
  const create = useOutcome()
  const [environment, setEnvironment] = useState('production')
  const [version, setVersion] = useState('')
  const refresh = () => {
    list.reload()
    onChanged()
  }

  return (
    <section aria-labelledby="dep-heading" className="space-y-4">
      <h3 id="dep-heading" className="text-lg font-bold text-gray-900">
        Deployment
      </h3>
      {project.status === 'uat' && project.capabilities.canManage && (
        <form
          aria-label="Record deployment"
          className={`${cardClass} space-y-3`}
          onSubmit={(event) => {
            event.preventDefault()
            void create.run(
              () => createDeployment({ projectId: project.id, environment, version }),
              () => {
                setVersion('')
                refresh()
              },
            )
          }}
        >
          <p className="text-sm text-gray-600">
            Deployment needs every required acceptance scenario to have passed.
          </p>
          <NoticeBanner notice={create.notice && !create.notice.field ? create.notice : null} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              id="dep-env"
              label="Environment"
              error={create.notice?.field === 'environment' ? create.notice.text : null}
            >
              <input
                id="dep-env"
                className={inputClass}
                value={environment}
                onChange={(event) => setEnvironment(event.target.value)}
              />
            </Field>
            <Field
              id="dep-version"
              label="Version"
              error={create.notice?.field === 'version' ? create.notice.text : null}
            >
              <input
                id="dep-version"
                className={inputClass}
                value={version}
                onChange={(event) => setVersion(event.target.value)}
              />
            </Field>
          </div>
          <button type="submit" disabled={create.busy} className={primaryButton}>
            Create deployment
          </button>
        </form>
      )}

      {list.loading ? (
        <Loading what="deployments" />
      ) : list.failed ? (
        <LoadFailed what="the deployments" onRetry={list.reload} />
      ) : list.data && list.data.length > 0 ? (
        <ul className="space-y-3">
          {list.data.map((deployment) => (
            <DeploymentRow
              key={deployment.id}
              deployment={deployment}
              canManage={project.capabilities.canManage}
              onChanged={refresh}
            />
          ))}
        </ul>
      ) : (
        <Empty title="No deployments yet.">
          A project is only deployed once a deployment is recorded as successful.
        </Empty>
      )}
    </section>
  )
}
