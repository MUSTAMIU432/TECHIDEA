import { useRef, useState } from 'react'

import { useAuth } from '../../../identity/auth/AuthContext'
import type { Idea } from '../../api/ideasApi'
import { useAttachments } from '../../hooks/useAttachments'
import { attachmentProblem } from '../../utils/attachmentRules'
import { FileUploadPanel, type UploadRow, type UploadStatus } from './FileUploadPanel'

interface QueuedFile {
  id: number
  file: File
  status: Extract<UploadStatus, 'ready' | 'uploading' | 'failed'>
  note?: string
}

/**
 * An existing idea's supporting documents, in the intake form's last step.
 *
 * The same upload surface as a new idea's, wired to the live evidence: files
 * go straight to the existing attachment endpoint through `useAttachments`,
 * one after another, and land in the list as they are accepted. A refused
 * file stays in the list, in red, with the server's own reason, until it is
 * dismissed.
 *
 * Upload and delete are offered to the author only - a courtesy, as in
 * `IdeaAttachments`: the server refuses anyone else whatever is rendered.
 */
export function IdeaEvidenceUploader({ idea }: { idea: Idea }) {
  const { user } = useAuth()
  const gallery = useAttachments(idea.id)
  const isOwner = user?.id === idea.authorId
  const [queue, setQueue] = useState<QueuedFile[]>([])
  const [problems, setProblems] = useState<string[]>([])
  const nextId = useRef(0)

  function update(id: number, change: Partial<QueuedFile>) {
    setQueue((current) => current.map((item) => (item.id === id ? { ...item, ...change } : item)))
  }

  async function add(picked: File[]) {
    const refused: string[] = []
    const accepted: QueuedFile[] = []
    for (const file of picked) {
      const problem = attachmentProblem(file)
      if (problem === null) accepted.push({ id: nextId.current++, file, status: 'ready' })
      else refused.push(`${file.name}: ${problem}`)
    }
    setProblems(refused)
    if (accepted.length === 0) return
    setQueue((current) => [...current, ...accepted])

    // One at a time: the hook allows one upload in flight, and the list
    // should grow in the order the files were chosen.
    for (const item of accepted) {
      update(item.id, { status: 'uploading' })
      gallery.clearWriteError()
      const uploaded = await gallery.upload(item.file)
      if (uploaded) {
        // It is in the attachment list now; the queued row has done its job.
        setQueue((current) => current.filter((queued) => queued.id !== item.id))
      } else {
        update(item.id, { status: 'failed', note: 'Could not be attached' })
      }
    }
  }

  const attachedRows: UploadRow[] = gallery.attachments.map((attachment) => ({
    key: `attachment-${attachment.id}`,
    name: attachment.filename,
    size: attachment.size,
    status: gallery.deletingId === attachment.id ? 'uploading' : 'done',
    note: gallery.deletingId === attachment.id ? 'Removing…' : 'Attached',
    actions: (
      <button
        type="button"
        disabled={gallery.downloadingId === attachment.id}
        onClick={() => void gallery.download(attachment)}
        className="rounded px-1.5 py-0.5 text-xs font-semibold text-brand-700 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-50"
      >
        Download
      </button>
    ),
    onRemove:
      isOwner && gallery.deletingId === null ? () => void gallery.remove(attachment.id) : undefined,
    removeLabel: `Delete ${attachment.filename}`,
  }))

  const queuedRows: UploadRow[] = queue.map((item) => ({
    key: `queued-${item.id}`,
    name: item.file.name,
    size: item.file.size,
    status: item.status,
    note: item.note,
    onRemove:
      item.status === 'failed'
        ? () => setQueue((current) => current.filter((queued) => queued.id !== item.id))
        : undefined,
    removeLabel: `Dismiss ${item.file.name}`,
  }))

  return (
    <div>
      {gallery.error && (
        <p role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
          {gallery.error}
        </p>
      )}
      {isOwner ? (
        <FileUploadPanel
          listLabel="Attached files"
          emptyText={
            gallery.loading && gallery.pageInfo === null
              ? 'Loading supporting documents…'
              : 'No files attached yet. This step is optional.'
          }
          busy={gallery.uploading}
          problems={gallery.writeError ? [...problems, gallery.writeError.message] : problems}
          onFiles={(files) => void add(files)}
          rows={[...attachedRows, ...queuedRows]}
        />
      ) : (
        <ul aria-label="Attached files" className="space-y-2">
          {gallery.attachments.map((attachment) => (
            <li key={attachment.id} className="text-sm text-gray-800">
              {attachment.filename}
            </li>
          ))}
        </ul>
      )}
      {gallery.downloadError && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {gallery.downloadError}
        </p>
      )}
    </div>
  )
}
