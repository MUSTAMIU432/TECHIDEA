import {
  PAYMENT_WORDS,
  PROPOSAL_SECTIONS,
  sectionApplies,
  type IdeaProposal,
} from '../api/proposalsApi'

/**
 * A proposal's sections as plain text, whitespace kept. Never HTML: a proposal is written by
 * people and read by others, so nothing in it is ever interpreted as markup.
 */
export function ProposalSections({ proposal }: { proposal: IdeaProposal }) {
  const filled = PROPOSAL_SECTIONS.filter(
    (section) =>
      section.key !== 'title' &&
      sectionApplies(section.key, proposal.paymentRequired) &&
      proposal[section.key].trim() !== '',
  )
  if (filled.length === 0) {
    return <p className="text-sm text-gray-600">Nothing has been written yet.</p>
  }
  return (
    <dl className="space-y-5">
      {filled.map((section) => (
        <div key={section.key}>
          <dt className="text-sm font-bold text-gray-900">{section.label}</dt>
          <dd className="mt-1 text-sm leading-6 whitespace-pre-line text-gray-700">
            {section.key === 'paymentRequired'
              ? (PAYMENT_WORDS[proposal.paymentRequired as 'yes' | 'no'] ??
                proposal.paymentRequired)
              : proposal[section.key]}
          </dd>
        </div>
      ))}
    </dl>
  )
}
