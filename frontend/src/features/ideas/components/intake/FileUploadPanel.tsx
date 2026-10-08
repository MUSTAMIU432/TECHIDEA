import { useId, useState, type DragEvent, type ReactNode } from 'react'

import { SpinnerIcon } from '../../../identity/components/icons'
import { ACCEPTED_FILE_TYPES, formatFileSize, MAX_UPLOAD_LABEL } from '../../utils/attachmentRules'

/**
 * The supporting-documents upload surface: a drop zone on one side, the files
 * on the other, each with its own progress bar.
 *
 * Presentation only. It sends nothing and decides nothing: files dropped or
 * chosen are handed to `onFiles`, and every row's state comes from the
 * caller, who uploads through the existing attachment endpoint. Dragging is a
 * convenience on top of "Choose files", which is a real file input and the
 * way in for a keyboard or a screen reader.
 *
 * The bars are honest about what is known. The upload request reports when a
 * file has finished, not how far along it is, so a file in flight shows a
 * moving bar rather than a made-up percentage; a finished one is full, and a
 * refused one is red.
 */

export type UploadStatus = 'ready' | 'uploading' | 'done' | 'failed'

export interface UploadRow {
  key: string
  name: string
  size: number
  status: UploadStatus
  /** Why a failed file failed, or any other short note under the name. */
  note?: string
  /** Offer ×, named "Remove <file>" for assistive technology. */
  onRemove?: () => void
  removeLabel?: string
  /** Extra controls for the row, such as Download. */
  actions?: ReactNode
}

const STATUS_TEXT: Record<UploadStatus, string> = {
  ready: 'Ready to attach',
  uploading: 'Uploading…',
  done: 'Attached',
  failed: 'Could not be attached',
}

function extensionOf(name: string): string {
  const extension = name.split('.').pop()?.toLowerCase() ?? ''
  return extension === name.toLowerCase() ? '' : extension
}

/** A small document glyph with the file's kind on it: IMG, PDF, DOC, XLS... */
function FileGlyph({ name }: { name: string }) {
  const extension = extensionOf(name)
  const label = ['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(extension)
    ? 'IMG'
    : ['doc', 'docx'].includes(extension)
      ? 'DOC'
      : ['xls', 'xlsx', 'csv'].includes(extension)
        ? 'XLS'
        : extension.toUpperCase().slice(0, 3) || 'FILE'
  const tone =
    label === 'PDF'
      ? 'text-red-600'
      : label === 'DOC'
        ? 'text-blue-600'
        : label === 'XLS'
          ? 'text-emerald-600'
          : 'text-gray-500'

  return (
    <svg aria-hidden="true" viewBox="0 0 32 40" className={`h-9 w-7 shrink-0 ${tone}`}>
      <path
        d="M4 1h17l10 10v26a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V3a2 2 0 0 1 2-2Z"
        fill="white"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path d="M21 1v8a2 2 0 0 0 2 2h8" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <text
        x="16"
        y="30"
        textAnchor="middle"
        fontSize="8"
        fontWeight="700"
        fill="currentColor"
        fontFamily="ui-sans-serif, system-ui, sans-serif"
      >
        {label}
      </text>
    </svg>
  )
}

function ProgressBar({ status }: { status: UploadStatus }) {
  return (
    <div aria-hidden="true" className="mt-1.5 h-1 overflow-hidden rounded-full bg-gray-200">
      {status === 'uploading' ? (
        <div className="h-full w-2/5 rounded-full bg-brand-500 motion-safe:animate-[upload-slide_1.1s_ease-in-out_infinite]" />
      ) : (
        <div
          className={`h-full rounded-full ${
            status === 'failed'
              ? 'w-full bg-red-400'
              : status === 'done'
                ? 'w-full bg-brand-600'
                : 'w-0'
          }`}
        />
      )}
    </div>
  )
}

function Row({ row }: { row: UploadRow }) {
  return (
    <li className="flex items-start gap-3 border-b border-gray-200 py-3 last:border-b-0">
      <FileGlyph name={row.name} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          {/* Text, never markup: the name is whatever the uploader's browser sent. */}
          <p className="min-w-0 truncate text-sm font-medium text-gray-800">{row.name}</p>
          <span className="shrink-0 text-xs text-gray-400">{formatFileSize(row.size)}</span>
        </div>
        <ProgressBar status={row.status} />
        <p className={`mt-1 text-xs ${row.status === 'failed' ? 'text-red-700' : 'text-gray-500'}`}>
          {row.note ?? STATUS_TEXT[row.status]}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {row.actions}
        {row.onRemove && (
          <button
            type="button"
            onClick={row.onRemove}
            aria-label={row.removeLabel ?? `Remove ${row.name}`}
            className="flex h-7 w-7 items-center justify-center rounded-md text-lg leading-none text-gray-400 hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            ×
          </button>
        )}
      </div>
    </li>
  )
}

export function FileUploadPanel({
  rows,
  onFiles,
  disabled = false,
  busy = false,
  problems = [],
  emptyText,
  listLabel,
}: {
  rows: readonly UploadRow[]
  onFiles: (files: File[]) => void
  disabled?: boolean
  /** Something is uploading right now: shown in the list's heading. */
  busy?: boolean
  /** Files refused before sending, one line each. */
  problems?: readonly string[]
  emptyText: string
  listLabel: string
}) {
  const inputId = useId()
  const [dragging, setDragging] = useState(false)

  function take(list: FileList | null | undefined) {
    const files = Array.from(list ?? [])
    if (files.length > 0 && !disabled) onFiles(files)
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    if (!disabled) setDragging(true)
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    take(event.dataTransfer?.files)
  }

  return (
    <div className="grid overflow-hidden rounded-xl border border-gray-300 bg-white shadow-sm @2xl:grid-cols-2">
      <div className="flex flex-col p-4">
        <div
          data-testid="file-drop-zone"
          onDragOver={handleDragOver}
          onDragEnter={handleDragOver}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          className={`flex min-h-56 flex-1 flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors ${
            dragging ? 'border-brand-500 bg-brand-50' : 'border-gray-300 bg-gray-50'
          } ${disabled ? 'opacity-60' : ''}`}
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            className="h-10 w-10 text-gray-400"
          >
            <path d="M12 15V4m0 0 4 4m-4-4-4 4" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" strokeLinecap="round" />
          </svg>
          <p className="mt-3 text-base font-medium text-gray-800">Drag files to upload</p>
          <p className="my-3 flex w-full max-w-56 items-center gap-3 text-xs text-gray-500 italic">
            <span aria-hidden="true" className="h-px flex-1 bg-gray-300" />
            or select a file
            <span aria-hidden="true" className="h-px flex-1 bg-gray-300" />
          </p>
          <label
            htmlFor={inputId}
            className="inline-flex cursor-pointer items-center rounded-lg bg-brand-600 px-5 py-2 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 focus-within:ring-2 focus-within:ring-brand-300 focus-within:ring-offset-2 has-disabled:cursor-not-allowed has-disabled:bg-brand-300"
          >
            Choose files
            <input
              id={inputId}
              type="file"
              multiple
              accept={ACCEPTED_FILE_TYPES}
              disabled={disabled}
              onChange={(event) => {
                take(event.target.files)
                // Cleared so choosing the same file again still fires.
                event.target.value = ''
              }}
              className="sr-only"
            />
          </label>
        </div>
        <p className="mt-2 shrink-0 text-xs leading-5 text-gray-600">
          Accepted: PDF, JPG, PNG, GIF, WEBP, DOC, DOCX, XLS, XLSX, CSV, TXT. Up to{' '}
          {MAX_UPLOAD_LABEL} each.
        </p>
      </div>

      <div className="border-t border-gray-200 p-4 @2xl:border-t-0 @2xl:border-l">
        <p className="flex items-center gap-2 text-sm font-semibold text-gray-700">
          {busy && <SpinnerIcon className="h-4 w-4 text-brand-600 motion-safe:animate-spin" />}
          {busy ? 'Uploading…' : listLabel}
        </p>
        {problems.map((problem) => (
          <p key={problem} role="alert" className="mt-2 text-sm text-red-700">
            {problem}
          </p>
        ))}
        {rows.length > 0 ? (
          <ul aria-label={listLabel} className="mt-1">
            {rows.map((row) => (
              <Row key={row.key} row={row} />
            ))}
          </ul>
        ) : (
          <p className="mt-3 rounded-lg bg-gray-50 px-3 py-6 text-center text-sm text-gray-500">
            {emptyText}
          </p>
        )}
      </div>
    </div>
  )
}
