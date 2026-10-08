import { Link } from 'react-router-dom'

import { opportunityForIdeaRequest, traceabilityRequest } from '../api/automationApi'
import { useLoad } from '../utils/useLoad'
import { Badge, cardClass } from './ui'
import type { Idea } from '../../ideas/api/ideasApi'

/**
 * Where an idea went after the owner's go-ahead: its automation opportunity, and its project once
 * there is one. Nothing is created here - the go-ahead opens the opportunity - so this is only the
 * way from the idea to its delivery, and for a ready idea whose opportunity is not visible to the
 * reader, a plain statement that it is with the delivery team.
 */
export function IdeaAutomationPanel({ idea }: { idea: Idea }) {
  const existing = useLoad(() => opportunityForIdeaRequest(idea.id), `idea-opp:${idea.id}`)
  const chain = useLoad(() => traceabilityRequest(idea.id), `idea-chain:${idea.id}`)

  if (existing.loading) return null
  const opportunity = existing.data

  if (!opportunity) {
    if (idea.status !== 'READY_FOR_IMPLEMENTATION') return null
    return (
      <section aria-labelledby="idea-delivery" className={`${cardClass} mt-6`}>
        <h2 id="idea-delivery" className="text-lg font-bold text-gray-900">
          Automation delivery
        </h2>
        <p className="mt-1 text-sm text-gray-600">
          You gave the go-ahead. The delivery team will assign a developer.
        </p>
      </section>
    )
  }

  const links = chain.data
  return (
    <section aria-labelledby="idea-delivery" className={`${cardClass} mt-6`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="idea-delivery" className="text-lg font-bold text-gray-900">
            Automation delivery
          </h2>
          <p className="mt-1 text-sm text-gray-600">
            This idea is being turned into an automation.
          </p>
        </div>
        <Badge value={opportunity.status} />
      </div>
      <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm font-semibold text-brand-700">
        <li>
          <Link className="hover:underline" to={`/app/automation/opportunities/${opportunity.id}`}>
            View opportunity
          </Link>
        </li>
        {links?.projectId && (
          <li>
            <Link className="hover:underline" to={`/app/automation/projects/${links.projectId}`}>
              View project
            </Link>
          </li>
        )}
      </ul>
    </section>
  )
}
