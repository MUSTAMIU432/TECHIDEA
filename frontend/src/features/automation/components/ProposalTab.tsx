import { proposalRequest, type Opportunity, type Proposal } from '../api/automationApi'
import { formatDate } from '../utils/labels'
import { useLoad } from '../utils/useLoad'
import { Badge, cardClass, Empty, Loading, LoadFailed } from './ui'

const SECTIONS: ReadonlyArray<{ key: keyof Proposal; label: string }> = [
  { key: 'executiveSummary', label: 'Executive summary' },
  { key: 'problem', label: 'Problem' },
  { key: 'feasibility', label: 'Feasibility' },
  { key: 'proposedSolution', label: 'Proposed solution' },
  { key: 'requirementsSummary', label: 'Requirements' },
  { key: 'scope', label: 'Scope' },
  { key: 'deliverables', label: 'Deliverables' },
  { key: 'estimatedTimeline', label: 'Overall timeline' },
  { key: 'milestones', label: 'Timeline and milestones' },
  { key: 'estimatedEffort', label: 'Estimated effort' },
  { key: 'financialRequirements', label: 'Financial requirements' },
  { key: 'paymentRequired', label: 'Does the owner pay?' },
  { key: 'paymentPlan', label: 'Payment plan' },
  { key: 'risks', label: 'Risks' },
  { key: 'assumptions', label: 'Assumptions' },
  { key: 'acceptanceCriteria', label: 'Acceptance criteria' },
]

const PAYMENT: Record<string, string> = {
  yes: 'Yes - the owner pays',
  no: 'No - no charge to the owner',
}

/**
 * The proposal this opportunity was opened on: written by the review team, released by a platform
 * admin and accepted by the owner's go-ahead. Read-only here - it is the record of what was agreed.
 */
export function ProposalTab({ opportunity }: { opportunity: Opportunity }) {
  const loaded = useLoad(() => proposalRequest(opportunity.id), `prop:${opportunity.id}`)

  if (loaded.loading) return <Loading what="the proposal" />
  if (loaded.failed) return <LoadFailed what="the proposal" onRetry={loaded.reload} />
  const proposal = loaded.data
  if (!proposal) {
    return (
      <Empty title="No proposal on record.">
        An opportunity opens on a proposal the owner accepted; this one has none attached.
      </Empty>
    )
  }

  const filled = SECTIONS.filter(
    (s) =>
      String(proposal[s.key] ?? '').trim() !== '' &&
      (s.key !== 'paymentPlan' || proposal.paymentRequired === 'yes'),
  )
  return (
    <section aria-labelledby="prop-heading" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 id="prop-heading" className="text-lg font-bold text-gray-900">
          {proposal.title}
        </h3>
        <Badge value={proposal.status} />
      </div>
      <p className="text-sm text-gray-600">
        Accepted by the owner{proposal.reviewedAt ? ` on ${formatDate(proposal.reviewedAt)}` : ''}.
      </p>
      <div className={cardClass}>
        <dl className="space-y-5">
          {filled.map((section) => (
            <div key={section.key}>
              <dt className="text-sm font-bold text-gray-900">{section.label}</dt>
              <dd className="mt-1 text-sm leading-6 whitespace-pre-line text-gray-700">
                {section.key === 'paymentRequired'
                  ? (PAYMENT[proposal.paymentRequired] ?? proposal.paymentRequired)
                  : String(proposal[section.key])}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  )
}
