import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { organizationIdeasRequest, type Idea } from '../../ideas/api/ideasApi'
import { IdeaOwnershipSummary } from '../../ideas/components/IdeaOwnership'
import { statusLabel } from '../../ideas/utils/lifecycle'
import { organizationMembersRequest, type OrganizationMember } from '../api/organizationApi'
import { ReviewsWorkspace } from '../../reviews/components/ReviewsWorkspace'
import { useCanReview } from '../../reviews/hooks/useCanReview'
import { useOrganization } from '../context/useOrganization'

/**
 * One organization: who is in it, and the ideas it has put forward.
 *
 * **This is the organization's own page, which the platform did not have.** Until
 * now the header's switcher chose an organization for shared work and the ideas
 * list was its feed; there was nowhere to stand *inside* one organization and
 * be told "everything here belongs to MUNA".
 *
 * The create button here is the organization's entry point, and like a team's it
 * goes **straight to the form** with the context already decided - no dialog,
 * because the page names the organization. The dialog is the global button's
 * job alone.
 *
 * Membership is read through the organization domain's own query, and the idea
 * list through `organizationIdeas`, which is scoped server-side to a membership
 * in that organization. A reader who is not a member gets an unavailable page
 * rather than an empty one, because "you are not in this organization" and "this
 * organization has no ideas" are different facts.
 */
export function OrganizationDetailPage() {
  const { organizationId = '' } = useParams()
  const { memberships, activeOrganization, setActiveOrganization } = useOrganization()
  const membership = memberships.find((entry) => entry.organization.id === organizationId)
  const organization = membership?.organization ?? null

  // Opening an organization is how a person chooses to work in it: the ideas
  // list, the review queue and the "Create Idea" shortcuts all follow the active
  // organization, so this is what the header's switcher used to do.
  useEffect(() => {
    if (organizationId !== '' && membership) setActiveOrganization(organizationId)
  }, [organizationId, membership, setActiveOrganization])
  const canReview = useCanReview(membership ? organizationId : null)

  const [members, setMembers] = useState<OrganizationMember[]>([])
  const [ideas, setIdeas] = useState<Idea[]>([])
  const [failed, setFailed] = useState<string | null>(null)

  // Both reads are keyed on the id, so an answer for one organization can never
  // be rendered under another's name while a navigation is in flight.
  useEffect(() => {
    if (organizationId === '' || !membership) return
    let cancelled = false
    Promise.all([
      organizationMembersRequest(organizationId),
      organizationIdeasRequest(organizationId),
    ])
      .then(([memberRows, ideaPage]) => {
        if (cancelled) return
        setMembers(memberRows.map((row) => row.user))
        setIdeas(ideaPage.items)
      })
      .catch(() => {
        if (!cancelled) setFailed('We could not load this organization. Please try again.')
      })
    return () => {
      cancelled = true
    }
  }, [organizationId, membership])

  const reload = useCallback(() => {
    setFailed(null)
    Promise.all([
      organizationMembersRequest(organizationId),
      organizationIdeasRequest(organizationId),
    ])
      .then(([memberRows, ideaPage]) => {
        setMembers(memberRows.map((row) => row.user))
        setIdeas(ideaPage.items)
      })
      .catch(() => setFailed('We could not load this organization. Please try again.'))
  }, [organizationId])

  if (!membership || !organization) {
    return (
      <section aria-labelledby="organization-unavailable" className="mt-8">
        <h1 id="organization-unavailable" className="text-2xl font-bold text-gray-900">
          This organization is not available.
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-gray-600">
          You are not an active member of it, or it does not exist. Both answer the same way, so an
          organization id cannot be used to find out about somebody else&apos;s.
        </p>
        <Link
          to="/app"
          className="mt-4 inline-block font-semibold text-brand-700 hover:text-brand-800"
        >
          Back to Home
        </Link>
      </section>
    )
  }

  const roles = membership.membership.roles

  return (
    <>
      <section aria-labelledby="organization-heading" className="mt-8">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
          <div>
            <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">
              Organization
            </p>
            <h1
              id="organization-heading"
              className="mt-1 text-3xl font-bold tracking-tight text-gray-900"
            >
              {organization.name}
            </h1>
            <p className="mt-1 text-sm text-gray-500">/{organization.slug}</p>
            {roles.length > 0 && (
              <p className="mt-2 flex flex-wrap gap-1.5">
                {roles.map((role) => (
                  <span
                    key={role.id}
                    className="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-800"
                  >
                    {role.name}
                  </span>
                ))}
              </p>
            )}
          </div>
          {/*
            The organization's entry point. Straight to the form with the context
            decided, because the page already names the organization - the dialog
            is for the global button, where nothing is known yet.
          */}
          <Link
            to={`/app/ideas/new?context=organization&organization=${organization.id}`}
            className="inline-flex h-11 items-center justify-center gap-2 self-start rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            <span aria-hidden="true">+</span>
            Create Idea
          </Link>
        </div>

        {failed && (
          <div className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">
            <p>{failed}</p>
            <button
              type="button"
              onClick={reload}
              className="mt-3 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-sm font-semibold text-red-800 hover:bg-red-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
            >
              Try again
            </button>
          </div>
        )}
      </section>

      {/* Only for reviewers, and only once this organization is the active one
          the queue reads from - so it can never show another organization's. */}
      {canReview === true && activeOrganization?.id === organizationId && <ReviewsWorkspace />}

      <section aria-labelledby="organization-ideas" className="mt-8">
        <h2 id="organization-ideas" className="text-xl font-bold tracking-tight text-gray-900">
          Organization Ideas
        </h2>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          Ideas this organization has put forward. An idea you file from here belongs to this
          organization, and it is the organization that confirms it before the platform sees it.
        </p>

        {ideas.length === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-gray-300 bg-white p-5">
            <p className="text-sm font-semibold text-gray-900">
              This organization has no ideas yet.
            </p>
            <Link
              to={`/app/ideas/new?context=organization&organization=${organization.id}`}
              className="mt-4 inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
            >
              Create the first idea
            </Link>
          </div>
        ) : (
          <ul className="mt-4 space-y-3">
            {ideas.map((idea) => (
              <li key={idea.id}>
                <Link
                  to={`/app/ideas/${idea.id}`}
                  className="block rounded-xl border border-gray-300 bg-white p-4 shadow-sm transition-colors hover:border-brand-300 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
                >
                  <span className="flex flex-wrap items-start justify-between gap-3">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold text-gray-900">
                        {idea.title}
                      </span>
                      <span className="mt-2 block">
                        <IdeaOwnershipSummary
                          context={idea.submissionContext}
                          ownerName={idea.tenantName}
                          visibility={idea.visibility}
                        />
                      </span>
                    </span>
                    <span className="shrink-0 rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">
                      {statusLabel(idea.status)}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="organization-members" className="mt-8">
        <h2 id="organization-members" className="text-xl font-bold tracking-tight text-gray-900">
          Members
        </h2>
        {members.length === 0 ? (
          <p className="mt-3 text-sm text-gray-600">No members are listed.</p>
        ) : (
          <ul className="mt-3 divide-y divide-gray-100 rounded-lg border border-gray-200 bg-white">
            {members.map((person) => (
              <li key={person.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900">
                    {[person.firstName, person.lastName].filter(Boolean).join(' ') || person.email}
                  </span>
                  <span className="mt-0.5 block truncate text-xs text-gray-500">
                    {person.email}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  )
}
