import { useCallback, useEffect, useRef, useState } from 'react'

import {
  attachmentsRequest,
  deleteAttachmentRequest,
  downloadAttachmentRequest,
  uploadAttachmentRequest,
  type IdeaAttachment,
  type IdeaPageInfo,
} from '../api/ideasApi'

/**
 * One idea's supporting evidence, and the three things a reader can do with
 * it: list it, add to it, remove from it.
 *
 * Shaped after `useComments` on purpose - same append-on-success rather than
 * refetch, same "a business refusal is a payload, a thrown error is a
 * transport failure" split, same ref-guarded in-flight flag so a double
 * click cannot send two requests. What differs, and why:
 *
 * - **Uploading is not append-only in the same sense a comment is.** A
 *   comment always lands at the end because the server orders by
 *   `created_at`; an upload does too (attachments are also oldest-first), so
 *   the same splice-onto-the-end trick applies, and does here.
 * - **Deleting removes from the middle of the list**, not just the end (an
 *   attachment from earlier in the discussion can be the one removed), so
 *   `remove` filters by id rather than assuming a position.
 * - **Downloading has no state of its own to hold** beyond "is a download in
 *   flight for this id" - there is nothing to splice or replace, since a
 *   successful download does not change the list. It gets its own small
 *   pending/error tracking rather than reusing `writeError`, so a failed
 *   download does not get confused with a failed upload or delete in the UI.
 */
export interface AttachmentGallery {
  attachments: IdeaAttachment[]
  pageInfo: IdeaPageInfo | null
  loading: boolean
  error: string | null
  /** A business refusal from the last upload or delete, with the field it blames. */
  writeError: { message: string; field: string | null } | null
  uploading: boolean
  /** The attachment currently being deleted, by id. */
  deletingId: string | null
  /** The attachment currently being downloaded, by id. */
  downloadingId: string | null
  downloadError: string | null
  upload: (file: File) => Promise<boolean>
  remove: (attachmentId: string) => Promise<boolean>
  download: (attachment: IdeaAttachment) => Promise<void>
  clearWriteError: () => void
  clearDownloadError: () => void
}

const TRANSPORT_FAILURE = 'We could not reach the server. Please try again.'

/** One fetched page, tagged with the idea it belongs to - see `useComments`. */
interface Answer {
  key: string
  attachments: IdeaAttachment[]
  pageInfo: IdeaPageInfo
}

export function useAttachments(ideaId: string | null): AttachmentGallery {
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [errorKey, setErrorKey] = useState<string | null>(null)
  const [writeError, setWriteError] = useState<{ message: string; field: string | null } | null>(
    null,
  )
  const [uploading, setUploading] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)

  // Synchronous in-flight guards - see `useComments` for why these are refs
  // and not the `uploading`/`deletingId` state, and why they are refs local
  // to this hook rather than module-level (no cross-component, cross-test
  // leak).
  const uploadingRef = useRef(false)
  const deletingRef = useRef<string | null>(null)
  const downloadingRef = useRef<string | null>(null)

  const key = ideaId

  useEffect(() => {
    if (key === null) return

    attachmentsRequest(key)
      .then((page) => {
        setAnswer({ key, attachments: page.items, pageInfo: page.pageInfo })
        setErrorKey(null)
      })
      .catch(() => {
        setAnswer(null)
        setErrorKey(key)
      })
    // oxlint-disable-next-line react/exhaustive-effect-dependencies
  }, [key, reloadToken])

  const settled = answer !== null && answer.key === key
  const failed = errorKey === key
  const attachments = answer?.attachments ?? []
  const pageInfo = answer?.pageInfo ?? null
  const loading = key !== null && !settled && !failed
  const error = failed ? TRANSPORT_FAILURE : null

  const upload = useCallback(
    async (file: File) => {
      if (ideaId === null) return false
      if (uploadingRef.current) return false
      uploadingRef.current = true

      setUploading(true)
      setWriteError(null)
      try {
        const result = await uploadAttachmentRequest(ideaId, file)
        if (!result.success || result.attachment === null) {
          setWriteError({ message: result.message, field: result.field })
          return false
        }

        const created = result.attachment
        if (answer === null) {
          setReloadToken((token) => token + 1)
        } else {
          setAnswer({
            ...answer,
            attachments: [...answer.attachments, created],
            pageInfo: { ...answer.pageInfo, totalCount: answer.pageInfo.totalCount + 1 },
          })
        }
        return true
      } catch {
        setWriteError({ message: TRANSPORT_FAILURE, field: null })
        return false
      } finally {
        uploadingRef.current = false
        setUploading(false)
      }
    },
    [ideaId, answer],
  )

  const remove = useCallback(
    async (attachmentId: string) => {
      if (deletingRef.current !== null) return false
      deletingRef.current = attachmentId
      setDeletingId(attachmentId)
      setWriteError(null)
      try {
        const result = await deleteAttachmentRequest(attachmentId)
        if (!result.success) {
          setWriteError({ message: result.message, field: result.field })
          return false
        }
        if (answer !== null) {
          setAnswer({
            ...answer,
            attachments: answer.attachments.filter((item) => item.id !== attachmentId),
            pageInfo: {
              ...answer.pageInfo,
              totalCount: Math.max(0, answer.pageInfo.totalCount - 1),
            },
          })
        }
        return true
      } catch {
        setWriteError({ message: TRANSPORT_FAILURE, field: null })
        return false
      } finally {
        deletingRef.current = null
        setDeletingId(null)
      }
    },
    [answer],
  )

  const download = useCallback(async (attachment: IdeaAttachment) => {
    if (downloadingRef.current !== null) return
    downloadingRef.current = attachment.id
    setDownloadingId(attachment.id)
    setDownloadError(null)
    try {
      await downloadAttachmentRequest(attachment)
    } catch {
      setDownloadError(TRANSPORT_FAILURE)
    } finally {
      downloadingRef.current = null
      setDownloadingId(null)
    }
  }, [])

  return {
    attachments,
    pageInfo,
    loading,
    error,
    writeError,
    uploading,
    deletingId,
    downloadingId,
    downloadError,
    upload,
    remove,
    download,
    clearWriteError: () => setWriteError(null),
    clearDownloadError: () => setDownloadError(null),
  }
}
