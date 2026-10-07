import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { IdeaStory } from '../../ideas/components/IdeaStory'
import { statusLabel, VISIBILITY_LABELS } from '../../ideas/utils/lifecycle'
import {
  adminIdeaRequest,
  downloadAdminAttachmentRequest,
  type AdminAttachment,
} from '../api/administrationApi'
import {
  AdminCard,
  AdminPageHeader,
  DetailList,
  EmptyState,
  ErrorState,
  IdeaStatusBadge,
  LoadingState,
  Restricted,
  secondaryButtonClasses,
} from '../components/AdminUi'
import { AssignReviewTeam } from '../components/AssignReviewTeam'
import { BackLink } from '../components/BackLink'
import { ReviewRoundCard } from '../components/ReviewRoundCard'
import { useAdminQuery } from '../hooks/useAdminQuery'
import { useBackTarget } from '../hooks/useBackTarget'
import { formatBytes, formatDateTime, RESTRICTED_TITLE } from '../utils/format'

/**
 * One idea, as the console sees it: metadata, lifecycle history, review rounds
 * and evidence for every administrator; content and review feedback only when
 * the server returned them - otherwise each section says it is restricted rather
 * than looking empty. Read-only: the console does not move ideas through the
 * lifecycle or decide reviews.
 *
 * **There is no discussion here.** A console reader can see that an idea was
 * discussed - `commentCount`, in the overview - without being handed the
 * conversation: this is an oversight surface, not a place to read an argument,
 * and the thread belongs to the people in it. The count is the fact an
 * administrator acts on ("nobody has said anything"), so it stays; the comments
 * behind it are not fetched, because asking the server for a page of them to
 * then not draw would be work for nobody.
 */
export function AdminIdeaDetailPage() {
  const { ideaId = '' } = useParams()
  // Above the early returns below: this component renders four different ways
  // and a hook that was only called on one of them would be called a different
  // number of times depending on which.
  const backTo = useBackTarget('/app/admin/ideas')
  const {
    data: idea,
    loading,
    error,
    reload,
  } = useAdminQuery(
    `idea:${ideaId}`,
    () => adminIdeaRequest(ideaId),
    'We could not load this idea.',
  )

  if (error) return <ErrorState message={error} onRetry={reload} />
  if (loading && !idea) return <LoadingState label="Loading idea…" />
  if (!idea) return <EmptyState title="Idea not found." />

  return (
    <>
      <AdminPageHeader
        title={idea.title ?? RESTRICTED_TITLE}
        back={<BackLink to={backTo}>All ideas</BackLink>}
      />
      <div className="space-y-6">
        {idea.contentRestricted && (
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
            This idea is not public, and inspecting its content needs the content-inspection
            permission. You can see its metadata and lifecycle only.
          </p>
        )}

        <AdminCard title="Overview">
          <DetailList
            items={[
              ['Status', <IdeaStatusBadge key="status" status={idea.status} />],
              ['Visibility', VISIBILITY_LABELS[idea.visibility]],
              [
                'Organization',
                <Link
                  key="organization"
                  to={`/app/admin/organizations/${idea.organization.id}`}
                  className="hover:underline"
                >
                  {idea.organization.name}
                </Link>,
              ],
              [
                'Author',
                <Link
                  key="author"
                  to={`/app/admin/users/${idea.author.id}`}
                  className="hover:underline"
                >
                  {idea.author.name} ({idea.author.email})
                </Link>,
              ],
              ['Category', idea.categoryName ?? '—'],
              ['Votes', idea.voteCount],
              ['Created', formatDateTime(idea.createdAt)],
              ['Submitted', formatDateTime(idea.submittedAt)],
              ['Last updated', formatDateTime(idea.updatedAt)],
            ]}
          />
        </AdminCard>

        <AdminCard title="The problem">
          {idea.content ? (
            <>
              <p className="text-sm leading-6 whitespace-pre-line text-slate-800">
                {idea.content.description || '—'}
              </p>
              <IdeaStory story={idea.content} />
            </>
          ) : (
            <Restricted />
          )}
        </AdminCard>

        <AdminCard title="Supporting evidence">
          {!idea.canInspectContent ? (
            <Restricted>Evidence needs the content-inspection permission</Restricted>
          ) : idea.attachments.length === 0 ? (
            <p className="text-sm text-slate-500">No files attached.</p>
          ) : (
            <>
              <ul className="divide-y divide-slate-100">
                {idea.attachments.map((attachment) => (
                  <AttachmentRow key={attachment.id} attachment={attachment} />
                ))}
              </ul>
              <p className="mt-2 text-xs text-slate-500">
                Every download is recorded in the administrative audit trail.
              </p>
            </>
          )}
        </AdminCard>

        <AssignReviewTeam ideaId={idea.id} status={idea.status} />

        <AdminCard title="Review history">
          {idea.reviews.length === 0 ? (
            <p className="text-sm text-slate-500">Not reviewed yet.</p>
          ) : (
            <div className="space-y-3">
              {idea.reviews.map((review) => (
                <ReviewRoundCard key={review.id} review={review} />
              ))}
            </div>
          )}
        </AdminCard>

        <AdminCard title="Lifecycle history">
          {idea.transitions.length === 0 ? (
            <p className="text-sm text-slate-500">Still a draft; no lifecycle moves yet.</p>
          ) : (
            <ol className="space-y-2">
              {idea.transitions.map((transition) => (
                <li key={transition.id} className="text-sm">
                  <span className="font-semibold text-slate-900">
                    {statusLabel(transition.fromStatus)} → {statusLabel(transition.toStatus)}
                  </span>
                  <span className="text-slate-500">
                    {' '}
                    · {transition.actor.name} · {formatDateTime(transition.createdAt)}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </AdminCard>
      </div>
    </>
  )
}

function AttachmentRow({ attachment }: { attachment: AdminAttachment }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  async function download() {
    setBusy(true)
    setFailed(false)
    try {
      await downloadAdminAttachmentRequest(attachment)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
      <span>
        <span className="font-semibold text-slate-900">{attachment.filename}</span>
        <span className="text-xs text-slate-500">
          {' '}
          · {formatBytes(attachment.size)} · {attachment.uploadedBy.name} ·{' '}
          {formatDateTime(attachment.createdAt)}
        </span>
      </span>
      <span className="flex items-center gap-2">
        {failed && (
          <span role="alert" className="text-xs text-red-700">
            Download failed.
          </span>
        )}
        <button
          type="button"
          onClick={() => void download()}
          disabled={busy}
          className={secondaryButtonClasses}
        >
          {busy ? 'Downloading…' : 'Download'}
        </button>
      </span>
    </li>
  )
}
