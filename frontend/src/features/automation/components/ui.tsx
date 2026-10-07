import type { ReactNode } from 'react'

import { badgeClasses, label } from '../utils/labels'
import type { Notice } from '../utils/useOutcome'

export const primaryButton =
  'inline-flex h-10 items-center justify-center rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:bg-brand-300'
export const secondaryButton =
  'inline-flex h-10 items-center justify-center rounded-lg border border-brand-300 bg-white px-4 text-sm font-semibold text-brand-700 shadow-sm hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60'
export const dangerButton =
  'inline-flex h-10 items-center justify-center rounded-lg border border-red-300 bg-white px-4 text-sm font-semibold text-red-700 shadow-sm hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:cursor-not-allowed disabled:opacity-60'
export const inputClass =
  'block w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 shadow-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-200 disabled:bg-gray-50'
export const cardClass = 'rounded-xl border border-gray-200 bg-white p-5 shadow-sm'

export function Badge({ value, text }: { value: string; text?: string }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-xs font-semibold ${badgeClasses(value)}`}
    >
      {text ?? label(value)}
    </span>
  )
}

export function NoticeBanner({ notice }: { notice: Notice | null }) {
  if (!notice) return null
  return (
    <p
      role={notice.kind === 'error' ? 'alert' : 'status'}
      className={`rounded-lg px-4 py-3 text-sm ${
        notice.kind === 'error' ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-800'
      }`}
    >
      {notice.text}
    </p>
  )
}

export function Loading({ what = 'this' }: { what?: string }) {
  return <output className="mt-6 block text-sm text-gray-600">Loading {what}…</output>
}

export function LoadFailed({ what, onRetry }: { what: string; onRetry: () => void }) {
  return (
    <div role="alert" className="mt-6 rounded-xl border border-red-200 bg-red-50 p-5">
      <p className="text-sm font-semibold text-red-800">We could not load {what}.</p>
      <p className="mt-1 text-sm text-red-700">Check your connection and try again.</p>
      <button type="button" onClick={onRetry} className={`mt-3 ${secondaryButton}`}>
        Try again
      </button>
    </div>
  )
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-white p-6">
      <p className="text-sm font-semibold text-gray-900">{title}</p>
      {children && <div className="mt-1 text-sm leading-6 text-gray-600">{children}</div>}
    </div>
  )
}

export function Field({
  id,
  label: text,
  error,
  hint,
  children,
}: {
  id: string
  label: string
  error?: string | null
  hint?: string
  children: ReactNode
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-semibold text-gray-800">
        {text}
      </label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-gray-500">{hint}</p>}
      {error && (
        <p role="alert" className="mt-1.5 text-sm text-red-600">
          {error}
        </p>
      )}
    </div>
  )
}

export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: ReadonlyArray<{ id: T; label: string }>
  active: T
  onChange: (id: T) => void
}) {
  return (
    <div
      role="tablist"
      aria-label="Sections"
      className="mt-6 flex gap-1 overflow-x-auto border-b border-gray-200"
    >
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={active === tab.id}
          onClick={() => onChange(tab.id)}
          className={`-mb-px shrink-0 border-b-2 px-4 py-2.5 text-sm font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 ${
            active === tab.id
              ? 'border-brand-600 text-brand-800'
              : 'border-transparent text-gray-600 hover:text-gray-900'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  )
}

/** A short fact in a header: "Owner: MUNA". */
export function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-semibold tracking-wide text-gray-500 uppercase">{term}</dt>
      <dd className="mt-0.5 text-sm text-gray-900">{children}</dd>
    </div>
  )
}
