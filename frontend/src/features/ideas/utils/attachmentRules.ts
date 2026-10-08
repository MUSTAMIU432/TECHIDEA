/**
 * The courtesy checks a file gets before it is sent to the attachment
 * endpoint. Shared by the evidence uploader on an existing idea and the
 * intake form's "Supporting documents" step, which holds files until the new
 * idea exists - so both say the same thing about the same file.
 *
 * None of this is validation the platform relies on. The server's allow-list
 * (`ideas.attachments.ALLOWED_ATTACHMENT_TYPES`) is checked against the
 * file's own bytes, and its size limit is the deployment's; these only spare
 * an obviously doomed round trip.
 */

/** Files this domain accepts, for the file picker's own `accept` filter. */
export const ACCEPTED_EXTENSIONS: readonly string[] = [
  '.pdf',
  '.png',
  '.jpg',
  '.jpeg',
  '.gif',
  '.webp',
  '.csv',
  '.txt',
  '.doc',
  '.docx',
  '.xls',
  '.xlsx',
]

export const ACCEPTED_FILE_TYPES = ACCEPTED_EXTENSIONS.join(',')

/**
 * Matches the backend's default `ATTACHMENT_MAX_UPLOAD_BYTES`. If a
 * deployment changes the server's limit, the server's own message on a
 * refused upload is what a reader sees for anything this let through.
 */
export const COURTESY_MAX_UPLOAD_BYTES = 200 * 1024 * 1024

/**
 * The limit in the words a person reads, derived rather than written.
 *
 * This number used to appear four times - here, in the refusal message, and in
 * the two places that tell a reader what is accepted - which is four chances to
 * change one and forget the others. Now it is stated once and everything that
 * shows it to somebody asks for it, so the hint beside the file picker cannot
 * quietly promise a limit the uploader does not enforce.
 */
export const MAX_UPLOAD_LABEL = `${COURTESY_MAX_UPLOAD_BYTES / (1024 * 1024)} MB`

/** Why `file` would obviously be refused, or `null` if it is worth sending. */
export function attachmentProblem(file: File): string | null {
  const extension = `.${file.name.split('.').pop()?.toLowerCase() ?? ''}`
  if (!ACCEPTED_EXTENSIONS.includes(extension)) return 'That file type is not supported.'
  if (file.size === 0) return 'That file is empty.'
  if (file.size > COURTESY_MAX_UPLOAD_BYTES) {
    return `That file is larger than ${MAX_UPLOAD_LABEL}.`
  }
  return null
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
