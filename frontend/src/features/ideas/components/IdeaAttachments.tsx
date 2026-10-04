import { useRef, useState } from 'react'

import { useAuth } from '../../identity/auth/AuthContext'
import { SpinnerIcon } from '../../identity/components/icons'
import { useAttachments } from '../hooks/useAttachments'
import type { Idea, IdeaAttachment } from '../api/ideasApi'
import {
  ACCEPTED_FILE_TYPES,
  attachmentProblem,
  formatFileSize,
  MAX_UPLOAD_LABEL,
} from '../utils/attachmentRules'

/**
 * One idea's supporting evidence: the files attached to it, and what the
 * signed-in reader may do with them.
 *
 * Rendered behind the evidence cell of `IdeaCardActions` on `/app/ideas`, and
 * behind `CardDisclosureButton` in the review workspace - either way it is
 * fetched only while open, for the same reason `IdeaDiscussion` does: a page
 * holds many ideas, and a page that fetched every idea's attachment list up
 * front would be a request for evidence nobody asked to see yet. Nothing at all
 * is rendered while it is closed: an empty box is a thing to look at.
 *
 * **Upload and delete are offered to the idea's own author only.**
 * `idea.authorId === user.id` decides what to *offer* here exactly the way
 * it decides whether to offer "Edit draft" in `IdeaList` - a courtesy, never
 * a control, since the server refuses an upload or a delete from anyone else
 * whether or not the controls were rendered. Every reader who can see this
 * section at all (because they can read the idea) can *download*, which is
 * the server's own, looser rule for that operation.
 *
 * **The backend remains the security boundary.** The file input's `accept`
 * attribute and the size hint below are guidance for a well-behaved browser,
 * not validation this component trusts - a refused upload is reported from
 * the server's own message, not pre-empted by a client-side guess at why it
 * might fail.
 */
export function IdeaAttachments({ idea, open }: { idea: Idea; open: boolean }) {
  if (!open) return null

  return (
    <section aria-label={`Supporting evidence: ${idea.title}`} className="mt-3">
      <IdeaAttachmentsPanel idea={idea} />
    </section>
  )
}

/**
 * The list and, for the idea's author, the uploader - without the
 * disclosure. Mounted only while it should fetch: by `IdeaAttachments` while
 * open, and by the intake form's "Supporting documents" step while an
 * existing idea is being edited.
 */
export function IdeaAttachmentsPanel({ idea }: { idea: Idea }) {
  const { user } = useAuth()
  const gallery = useAttachments(idea.id)
  const isOwner = user?.id === idea.authorId

  return (
    <>
      <AttachmentList gallery={gallery} isOwner={isOwner} />
      {isOwner && <AttachmentUploader idea={idea} gallery={gallery} />}
    </>
  )
}

function AttachmentList({
  gallery,
  isOwner,
}: {
  gallery: ReturnType<typeof useAttachments>
  isOwner: boolean
}) {
  if (gallery.loading && gallery.pageInfo === null) {
    return (
      <output className="block rounded-lg bg-gray-50 px-3 py-3">
        <span className="sr-only">Loading supporting evidence…</span>
        <span className="block h-3 w-40 animate-pulse rounded bg-gray-200" />
      </output>
    )
  }

  if (gallery.error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-3" role="alert">
        <p className="text-sm text-red-700">{gallery.error}</p>
      </div>
    )
  }

  if (gallery.attachments.length === 0) {
    return (
      <p className="rounded-lg bg-gray-50 px-3 py-3 text-sm text-gray-600">
        No files attached yet.
      </p>
    )
  }

  return (
    <ul className="space-y-2">
      {gallery.attachments.map((attachment) => (
        <AttachmentRow
          key={attachment.id}
          attachment={attachment}
          isOwner={isOwner}
          gallery={gallery}
        />
      ))}
    </ul>
  )
}

function AttachmentRow({
  attachment,
  isOwner,
  gallery,
}: {
  attachment: IdeaAttachment
  isOwner: boolean
  gallery: ReturnType<typeof useAttachments>
}) {
  const downloading = gallery.downloadingId === attachment.id
  const deleting = gallery.deletingId === attachment.id

  return (
    <li className="flex items-center justify-between gap-2 rounded-lg bg-gray-50 px-3 py-2.5">
      <div className="min-w-0">
        {/* Rendered as text: the server has already reduced this to a bare
            display name with no directory component, but it is still never
            interpreted as markup here. */}
        <p className="truncate text-sm font-medium text-gray-800">{attachment.filename}</p>
        <p className="text-xs text-gray-500">{formatFileSize(attachment.size)}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          disabled={downloading}
          onClick={() => {
            void gallery.download(attachment)
          }}
          className="inline-flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs font-semibold text-brand-700 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-50"
        >
          {downloading && <SpinnerIcon className="h-3 w-3 motion-safe:animate-spin" />}
          Download
        </button>
        {isOwner && (
          <button
            type="button"
            disabled={deleting}
            onClick={() => {
              void gallery.remove(attachment.id)
            }}
            className="rounded px-1.5 py-0.5 text-xs font-semibold text-red-700 hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:opacity-50"
          >
            Delete
          </button>
        )}
      </div>
      {gallery.downloadError !== null && downloading === false && (
        <p role="alert" className="sr-only">
          {gallery.downloadError}
        </p>
      )}
    </li>
  )
}

function AttachmentUploader({
  idea,
  gallery,
}: {
  idea: Idea
  gallery: ReturnType<typeof useAttachments>
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [selectionError, setSelectionError] = useState<string | null>(null)
  const inputId = `attachment-upload-${idea.id}`

  function handleChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null
    // Cleared immediately either way, so re-picking the exact same file
    // after fixing whatever was wrong with it fires this handler again -
    // otherwise the browser treats an unchanged value as no change at all.
    event.target.value = ''
    if (file === null) return

    setSelectionError(null)
    gallery.clearWriteError()

    // A courtesy pre-check only - see `utils/attachmentRules`. Whatever
    // passes here still goes to the server, which is the actual,
    // authoritative check; this only saves an obviously-doomed round trip.
    const problem = attachmentProblem(file)
    if (problem !== null) {
      setSelectionError(problem)
      return
    }

    void gallery.upload(file)
  }

  return (
    <div className="mt-3">
      <label
        htmlFor={inputId}
        className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus-within:ring-2 focus-within:ring-brand-300"
      >
        {gallery.uploading && <SpinnerIcon className="h-3.5 w-3.5 motion-safe:animate-spin" />}
        Choose file
      </label>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        accept={ACCEPTED_FILE_TYPES}
        disabled={gallery.uploading}
        onChange={handleChange}
        className="sr-only"
      />
      {/* The limit is asked for rather than written, so this hint cannot
          promise something the uploader does not enforce. */}
      <span className="ml-2 text-xs text-gray-500">
        PDF, image, spreadsheet or document, up to {MAX_UPLOAD_LABEL}
      </span>

      {selectionError !== null && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {selectionError}
        </p>
      )}
      {gallery.writeError !== null && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {gallery.writeError.message}
        </p>
      )}
    </div>
  )
}
