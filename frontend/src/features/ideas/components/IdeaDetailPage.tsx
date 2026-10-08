import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ideaRequest, type Idea } from '../api/ideasApi'
import { IdeaAttachmentsPanel } from './IdeaAttachments'
import { IdeaDiscussionPanel } from './IdeaDiscussion'
import { IdeaOwnershipSummary } from './IdeaOwnership'
import { ownerNameFor } from '../utils/ownership'
import { statusDescription, statusLabel } from '../utils/lifecycle'
import { IdeaProposalPanel } from '../../proposals/components/IdeaProposalPanel'
import { IdeaAutomationPanel } from '../../automation/components/IdeaAutomationPanel'
import { useScrollToHash } from '../hooks/useScrollToHash'
import { DecisionLetterCard } from '../../reviews/components/DecisionLetterCard'
import { RespondToChangesPanel } from '../../reviews/components/RespondToChangesPanel'
import { IdeaReviewSection } from '../../reviews/components/IdeaReviewSection'
import { useAuth } from '../../identity/auth/AuthContext'
import { formatDate } from '../../reviews/utils/reviewLabels'

/**
 * One idea, at `/app/ideas/:ideaId`.
 *
 * **This page is why a reader must never be left guessing.** Four facts, said
 * once each at the top, in this order: what it is (its title), **who it belongs
 * to**, who created it, where it is in the lifecycle - and, beside the owner,
 * **who can see it**. Ownership and visibility are separate lines because they
 * are separate facts, and an idea can belong to a team and be readable by the
 * whole platform.
 *
 * It exists in its own right and not as the card's inline sections: three
 * places already linked here (`NotificationsPage`, two links in `IdeaReportPage`,
 * and the app's own breadcrumb rule) and all three were a 404, because ideas
 * existed only as rows in a list.
 *
 * The sections below stay separate from each other and from the review: a
 * comment is public engagement, a review is a formal decision with its own
 * criteria, and this page does not put one inside the other.
 */
export function IdeaDetailPage() {
  const { ideaId = '' } = useParams()
  const { user } = useAuth()
  /*
    `null` means "no answer yet", and the key it is stored under is the query
    that would answer it - the same discipline `useAdminQuery` and
    `useIdeaDiscovery` use. Comparing keys during render is what makes a retry,
    or a move to another idea, show the loading line instead of the previous
    idea's page while the next request is in flight, with no write inside the
    effect to express it.
  */
  const [token, setToken] = useState(0)
  // The server's words after the owner answered a request for changes, shown once the
  // idea has been read again in its new state.
  const [responded, setResponded] = useState<string | null>(null)
  const queryKey = `${ideaId}#${token}`
  const [answer, setAnswer] = useState<{
    key: string
    idea: Idea | null
  } | null>(null)

  useEffect(() => {
    let cancelled = false
    ideaRequest(ideaId)
      .then((idea) => {
        if (cancelled) return
        // `null` is the server's answer for an idea this reader may not read and
        // for one that does not exist, and the page says so without choosing
        // between them - an id must not be a way to find out what exists.
        setAnswer({ key: queryKey, idea })
      })
      .catch(() => {
        if (!cancelled) setAnswer({ key: queryKey, idea: null })
      })
    return () => {
      cancelled = true
    }
  }, [ideaId, queryKey])

  const reload = useCallback(() => setToken((value) => value + 1), [])
  // A notification's link names the section it is about (`#respond`, `#decision-letter`).
  useScrollToHash(answer !== null && answer.key === queryKey && answer.idea !== null)

  if (answer === null || answer.key !== queryKey) {
    return <p className="mt-8 text-sm text-gray-600">Loading this idea…</p>
  }

  if (answer.idea === null) {
    return (
      <section className="mt-8 rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-600 shadow-sm">
        <p>
          We could not load this idea. It may not exist, or it may not be one you are allowed to
          read.
        </p>
        <div className="mt-3 flex gap-3">
          <button
            type="button"
            onClick={reload}
            className="rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            Try again
          </button>
          <Link to="/app/ideas" className="font-semibold text-brand-700 hover:text-brand-800">
            Back to your ideas
          </Link>
        </div>
      </section>
    )
  }

  const idea = answer.idea
  const ownerName = ownerNameFor(
    idea.submissionContext,
    idea.tenantName,
    idea.authorId,
    user?.id ?? null,
  )

  return (
    <article className="mt-8">
      <header>
        {/* "Idea", never the tenant's name: the owner is named two lines below, and
            saying it twice on one screen is how a reader starts wondering
            whether the two mentions mean the same thing. */}
        <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">Idea</p>
        <h1 className="mt-1 text-3xl font-bold tracking-tight text-gray-900">{idea.title}</h1>

        {/*
          The header block. Two facts on their own lines, in this order, because
          "whose idea is this" is asked before "who wrote it" and both before
          "who may read it".
        */}
        <dl className="mt-4 grid gap-x-6 gap-y-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">Type</dt>
            <dd className="mt-1">
              {/*
                Two badges, two facts. The owner's name is on the row below and
                is deliberately *not* repeated here: the same fact said twice on
                one screen is a place for the two copies to disagree.
              */}
              <IdeaOwnershipSummary
                context={idea.submissionContext}
                ownerName=""
                visibility={idea.visibility}
              />
            </dd>
          </div>
          <div>
            <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
              Owned by
            </dt>
            <dd className="mt-1 text-sm font-semibold text-gray-900">{ownerName}</dd>
          </div>
          <div>
            <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
              Created by
            </dt>
            <dd className="mt-1 text-sm text-gray-700">
              {idea.authorId === user?.id ? 'You' : 'Another member of this platform'}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">Status</dt>
            <dd className="mt-1 text-sm text-gray-700">
              {statusLabel(idea.status, idea.submissionContext)}
              {idea.status !== 'DRAFT' && (
                <span className="mt-0.5 block text-xs text-gray-500">
                  {statusDescription(idea.status, idea.submissionContext)}
                </span>
              )}
            </dd>
          </div>
          {idea.submittedAt && (
            <div>
              <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
                Submitted on
              </dt>
              <dd className="mt-1 text-sm text-gray-700">{formatDate(idea.submittedAt)}</dd>
            </div>
          )}
          {idea.category && (
            <div>
              <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">
                Category
              </dt>
              <dd className="mt-1 text-sm text-gray-700">{idea.category.name}</dd>
            </div>
          )}
        </dl>

        <div className="mt-4 flex flex-wrap gap-3">
          {/*
            Edit is drawn from `viewerCanEdit` - the server's own answer, asked by
            the same code `updateIdea` runs - rather than from "is this mine and is
            it a draft" re-derived here.
          */}
          {idea.viewerCanEdit && (
            <Link
              to={`/app/ideas/${idea.id}/edit`}
              className="inline-flex h-10 items-center justify-center rounded-lg border border-brand-300 bg-white px-4 text-sm font-semibold text-brand-700 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
            >
              {idea.status === 'CHANGES_REQUESTED' ? 'Revise idea' : 'Edit draft'}
            </Link>
          )}
          <Link
            to={`/app/ideas/${idea.id}/report`}
            className="inline-flex h-10 items-center justify-center rounded-lg border border-gray-300 bg-white px-4 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            Review report
          </Link>
        </div>
      </header>

      {responded && (
        <output className="mt-6 block rounded-lg bg-green-50 px-4 py-3 text-sm text-green-800">
          {responded}
        </output>
      )}

      {/* While changes are requested: what was asked, and where the owner answers it. */}
      <RespondToChangesPanel
        idea={idea}
        viewerId={user?.id ?? null}
        onResponded={(message) => {
          setResponded(message)
          reload()
        }}
      />

      {/* The platform's letter to the owner about its decision, once it has been sent. */}
      <DecisionLetterCard ideaId={idea.id} />

      {idea.description && (
        <section aria-labelledby="idea-overview" className="mt-8">
          <h2 id="idea-overview" className="text-lg font-bold tracking-tight text-gray-900">
            Overview
          </h2>
          <p className="mt-2 text-sm leading-6 whitespace-pre-wrap text-gray-700">
            {idea.description}
          </p>
        </section>
      )}

      {/*
        The three sections, each one doing its own thing and none nested in
        another: a comment is public engagement on the idea, evidence is the
        documents filed with it, and a review is a formal decision with its own
        criteria. The list card wraps the same panels in disclosures; here they are
        the page, so the panels themselves are rendered.
      */}
      <IdeaDiscussionPanel idea={idea} />
      {/* The anchor the response panel's "Go to documents" points at. */}
      <div id="idea-evidence" className="scroll-mt-24">
        <IdeaAttachmentsPanel idea={idea} />
      </div>
      <div id="proposal" className="scroll-mt-24 rounded-xl">
        <IdeaProposalPanel ideaId={idea.id} status={idea.status} />
      </div>
      <IdeaAutomationPanel idea={idea} />
      <IdeaReviewSection idea={idea} viewerId={user?.id ?? null} open />
    </article>
  )
}
