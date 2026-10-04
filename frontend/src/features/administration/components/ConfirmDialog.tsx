import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

import { dangerButtonClasses, primaryButtonClasses, secondaryButtonClasses } from './AdminUi'

interface ConfirmDialogProps {
  title: string
  /** What will happen, in plain words - the consequences, not just the verb. */
  children: ReactNode
  confirmLabel: string
  /** Destructive actions get the red button. */
  danger?: boolean
  /** Offer an optional reason, recorded in the audit trail with the action. */
  withReason?: boolean
  busy?: boolean
  error?: string | null
  onConfirm: (reason: string) => void
  onCancel: () => void
}

/**
 * The console's confirmation step: nothing that deactivates, removes or
 * retires anything happens on one click.
 *
 * A modal dialog with its consequences spelled out. Focus starts on Cancel -
 * the safe choice - and Escape cancels. The action's result (success or the
 * server's refusal) is the caller's to show; `error` is rendered inside the
 * dialog so a refusal is read where the decision was made.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  danger = false,
  withReason = false,
  busy = false,
  error = null,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const titleId = useId()
  const reasonId = useId()
  const cancelRef = useRef<HTMLButtonElement>(null)
  const [reason, setReason] = useState('')

  // The latest `onCancel`, so Escape always calls the current one while the
  // listener - and the initial focus - are set up once, on open.
  const cancel = useRef(onCancel)
  useEffect(() => {
    cancel.current = onCancel
  }, [onCancel])

  useEffect(() => {
    cancelRef.current?.focus()
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') cancel.current()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
      <dialog
        open
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative m-0 w-full max-w-md rounded-xl bg-white p-5 text-left shadow-2xl"
      >
        <h2 id={titleId} className="text-base font-bold text-slate-900">
          {title}
        </h2>
        <div className="mt-2 space-y-2 text-sm text-slate-600">{children}</div>
        {withReason && (
          <div className="mt-4">
            <label htmlFor={reasonId} className="block text-xs font-semibold text-slate-700">
              Reason (optional, recorded in the audit trail)
            </label>
            <textarea
              id={reasonId}
              rows={2}
              maxLength={500}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:ring-2 focus:ring-slate-200 focus:outline-none"
            />
          </div>
        )}
        {error && (
          <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={busy}
            className={secondaryButtonClasses}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(reason.trim())}
            disabled={busy}
            className={danger ? dangerButtonClasses : primaryButtonClasses}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </dialog>
    </div>
  )
}
