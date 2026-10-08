import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  proposalStateRequest,
  recordProposalViewRequest,
  type IdeaProposal,
  type ViewReceipt,
} from '../api/proposalsApi'
import { OwnerDecisionForm } from './OwnerDecisionForm'
import { ProposalSections } from './ProposalSections'
import { ProtectedView } from './ProtectedView'

type Load =
  | { kind: 'loading' }
  | { kind: 'failed' }
  | { kind: 'unavailable' }
  | { kind: 'ready'; proposal: IdeaProposal; receipt: ViewReceipt }

/**
 * The owner's view-only reading of their released proposal, at
 * `/app/ideas/:ideaId/proposal`, and the one place the go-ahead is given.
 *
 * Opening it is recorded (who and when), and the page is watermarked with the reader's name and
 * the time. It says plainly what that does and does not do: it deters passing the proposal on, it
 * cannot stop a photo of the screen.
 */
export function ProposalReadPage() {
  const { ideaId = '' } = useParams()
  const [load, setLoad] = useState<Load>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    async function open() {
      try {
        const state = await proposalStateRequest(ideaId)
        if (
          state.viewerRole !== 'owner' ||
          !state.proposal ||
          state.proposal.status !== 'released'
        ) {
          if (!cancelled) setLoad({ kind: 'unavailable' })
          return
        }
        const recorded = await recordProposalViewRequest(ideaId)
        if (cancelled) return
        if (!recorded.success || !recorded.receipt) {
          setLoad({ kind: 'unavailable' })
          return
        }
        setLoad({ kind: 'ready', proposal: state.proposal, receipt: recorded.receipt })
      } catch {
        if (!cancelled) setLoad({ kind: 'failed' })
      }
    }
    void open()
    return () => {
      cancelled = true
    }
  }, [ideaId])

  if (load.kind === 'loading') {
    return <output className="mt-8 block text-sm text-gray-600">Opening your proposal…</output>
  }
  if (load.kind === 'failed') {
    return (
      <p role="alert" className="mt-8 text-sm text-red-700">
        We could not open the proposal. Please refresh the page.
      </p>
    )
  }
  if (load.kind === 'unavailable') {
    return (
      <section className="mt-8 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600">
        <p className="font-semibold text-gray-900">There is no proposal for you to read.</p>
        <p className="mt-1">
          The platform has not released one for this idea, or it is not yours to read.
        </p>
        <Link
          to={`/app/ideas/${ideaId}`}
          className="mt-3 inline-block font-semibold text-brand-700"
        >
          Back to the idea
        </Link>
      </section>
    )
  }

  const { proposal, receipt } = load
  return (
    <section className="mt-6 max-w-4xl" aria-labelledby="proposal-heading">
      <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">
        Proposal for your idea
      </p>
      <h1 id="proposal-heading" className="mt-1 text-3xl font-bold tracking-tight text-gray-900">
        {proposal.title}
      </h1>
      <p className="mt-2 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-900">
        This proposal is for you only. It cannot be downloaded, copied or printed from here, and
        your name and the time are on every page. A photo of the screen cannot be prevented, so
        please do not share it.
      </p>

      <div className="mt-5">
        <ProtectedView receipt={receipt}>
          <ProposalSections proposal={proposal} />
        </ProtectedView>
      </div>

      <OwnerDecisionForm ideaId={ideaId} proposal={proposal} />

      <Link to={`/app/ideas/${ideaId}`} className="mt-8 inline-block font-semibold text-brand-700">
        Back to the idea
      </Link>
    </section>
  )
}
