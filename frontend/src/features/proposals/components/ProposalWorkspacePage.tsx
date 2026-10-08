import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ideaRequest, type Idea } from '../../ideas/api/ideasApi'
import { IdeaStory } from '../../ideas/components/IdeaStory'
import { statusLabel } from '../../ideas/utils/lifecycle'
import { IdeaProposalPanel } from './IdeaProposalPanel'
import { ProposalAnswerCard } from './ProposalAnswerSummary'

/**
 * The review team's workspace for one idea's proposal, at `/app/reviews/proposals/:ideaId`
 * - where *My work* and the proposal notifications lead.
 *
 * The idea is at the top, folded by default (the team has read it; they open it when they
 * need it), then the proposal itself - written, sent, or read - and, once given, the
 * owner's answers. What the reader may do is the proposal panel's, which asks the server.
 */
export function ProposalWorkspacePage() {
  const { ideaId = '' } = useParams()
  const [answer, setAnswer] = useState<{ ideaId: string; idea: Idea | null } | null>(null)
  const [ideaOpen, setIdeaOpen] = useState(false)

  useEffect(() => {
    let cancelled = false
    ideaRequest(ideaId)
      .then((idea) => {
        if (!cancelled) setAnswer({ ideaId, idea })
      })
      .catch(() => {
        if (!cancelled) setAnswer({ ideaId, idea: null })
      })
    return () => {
      cancelled = true
    }
  }, [ideaId])

  if (answer === null || answer.ideaId !== ideaId) {
    return <p className="mt-8 text-sm text-gray-600">Loading the proposal…</p>
  }
  const idea = answer.idea
  if (idea === null) {
    return (
      <section className="mt-8 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600">
        <p>This idea is not available to you.</p>
        <Link to="/app/reviews" className="mt-3 inline-block font-semibold text-brand-700">
          Back to my work
        </Link>
      </section>
    )
  }

  return (
    <article className="mt-8 max-w-5xl">
      <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">Proposal</p>
      <h1 className="mt-1 text-3xl font-bold tracking-tight text-gray-900">{idea.title}</h1>
      <p className="mt-1 text-sm text-gray-600">
        {idea.tenantName ? `${idea.tenantName} · ` : ''}
        {statusLabel(idea.status, idea.submissionContext)}
      </p>

      <section
        aria-labelledby="workspace-idea-heading"
        className="mt-6 rounded-2xl border border-gray-200 bg-white"
      >
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
          <h2 id="workspace-idea-heading" className="text-base font-semibold text-gray-900">
            The idea
          </h2>
          <button
            type="button"
            aria-expanded={ideaOpen}
            aria-controls="workspace-idea"
            onClick={() => setIdeaOpen((open) => !open)}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-semibold text-gray-700 hover:bg-gray-50"
          >
            {ideaOpen ? 'Hide the idea' : 'Show the idea'}
          </button>
        </div>
        {ideaOpen ? (
          <div id="workspace-idea" className="border-t border-gray-100 px-5 pb-5">
            <p className="mt-4 text-sm leading-6 whitespace-pre-line text-gray-700">
              {idea.description}
            </p>
            <IdeaStory story={idea} />
            <Link
              to={`/app/ideas/${idea.id}`}
              className="mt-4 inline-block text-sm font-semibold text-brand-700"
            >
              Open the full idea page
            </Link>
          </div>
        ) : (
          <p
            id="workspace-idea"
            className="line-clamp-2 border-t border-gray-100 px-5 py-3 text-sm text-gray-600"
          >
            {idea.description}
          </p>
        )}
      </section>

      <IdeaProposalPanel ideaId={idea.id} status={idea.status} />
      <ProposalAnswerCard ideaId={idea.id} title="The owner's answers" />

      <Link to="/app/reviews" className="mt-8 inline-block font-semibold text-brand-700">
        Back to my work
      </Link>
    </article>
  )
}
