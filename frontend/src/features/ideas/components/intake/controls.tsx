import { useId, type ReactNode } from 'react'

import {
  fieldErrorClasses,
  inputClasses,
  labelClasses,
} from '../../../identity/components/fieldStyles'

/**
 * The intake form's question controls. Each renders one question the way the
 * whole form asks them: the question as the label, a line of help in plain
 * words, an optional example, and - when there is one - the error, all
 * wired to the control with `aria-describedby` so a screen reader hears the
 * same guidance a sighted author reads.
 */

const helpClasses = 'mb-2 text-sm leading-6 text-gray-500'

function Example({ children }: { children: ReactNode }) {
  return <p className="mt-1 text-sm leading-6 text-gray-500 italic">For example: {children}</p>
}

function describedBy(...ids: Array<string | false | undefined>): string | undefined {
  const present = ids.filter(Boolean)
  return present.length > 0 ? present.join(' ') : undefined
}

interface TextQuestionProps {
  label: string
  value: string
  onChange: (value: string) => void
  help?: ReactNode
  example?: string
  placeholder?: string
  error?: string
  required?: boolean
  disabled?: boolean
  maxLength: number
  /** Rows for a multi-line answer; a single-line input when omitted. */
  rows?: number
  large?: boolean
  /** Guidance shown under the control while there is no error to show instead. */
  note?: string
  inputMode?: 'numeric' | 'text'
}

export function TextQuestion({
  label,
  value,
  onChange,
  help,
  example,
  placeholder,
  error,
  required = false,
  disabled = false,
  maxLength,
  rows,
  large = false,
  note,
  inputMode,
}: TextQuestionProps) {
  const id = useId()
  const helpId = `${id}-help`
  const exampleId = `${id}-example`
  const errorId = `${id}-error`
  const noteId = `${id}-note`
  const shared = {
    id,
    value,
    disabled,
    maxLength,
    placeholder,
    'aria-invalid': error ? true : undefined,
    'aria-required': required || undefined,
    'aria-describedby': describedBy(
      help ? helpId : undefined,
      example ? exampleId : undefined,
      error ? errorId : note ? noteId : undefined,
    ),
  }

  return (
    <div>
      <label className={labelClasses} htmlFor={id}>
        {label}
        {required && (
          <span className="ml-1 text-red-600" aria-hidden="true">
            *
          </span>
        )}
      </label>
      {help && (
        <p id={helpId} className={helpClasses}>
          {help}
        </p>
      )}
      {rows ? (
        <textarea
          {...shared}
          rows={rows}
          className={`${inputClasses(Boolean(error), { multiline: true, large })} w-full resize-y`}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          {...shared}
          inputMode={inputMode}
          className={`${inputClasses(Boolean(error), { large })} w-full`}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {example && (
        <div id={exampleId}>
          <Example>{example}</Example>
        </div>
      )}
      {error ? (
        <p id={errorId} className={fieldErrorClasses}>
          {error}
        </p>
      ) : (
        note && (
          <p id={noteId} className="mt-1.5 text-sm text-gray-500">
            {note}
          </p>
        )
      )}
    </div>
  )
}

interface ChoiceOption<T extends string> {
  value: T
  label: string
}

/**
 * The row a choice is drawn on, shared by every picker in the form: the
 * control, its label, and the two states that make a choice readable at a
 * glance - selected, and unavailable. Exported because the context picker is
 * the one question here with three options *and* a reason attached to some of
 * them, and it should look like the questions around it rather than introduce a
 * third design.
 */
export const choiceClasses =
  'flex cursor-pointer items-center gap-2.5 rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-800 transition-colors hover:border-brand-200 hover:bg-gray-50 has-checked:border-brand-400 has-checked:bg-brand-50 has-disabled:cursor-not-allowed has-disabled:opacity-60'

/** Pick any number of options: "select all that apply". */
export function CheckboxQuestion<T extends string>({
  legend,
  help,
  options,
  value,
  onChange,
  disabled = false,
  error,
}: {
  legend: string
  help?: string
  options: ReadonlyArray<ChoiceOption<T>>
  value: readonly T[]
  onChange: (value: T[]) => void
  disabled?: boolean
  error?: string
}) {
  const id = useId()

  function toggle(option: T, checked: boolean) {
    const next = new Set(value)
    if (checked) next.add(option)
    else next.delete(option)
    // Kept in the options' own order, so the same answer is always the same value.
    onChange(options.map((o) => o.value).filter((v) => next.has(v)))
  }

  return (
    <fieldset aria-describedby={describedBy(help && `${id}-help`, error && `${id}-error`)}>
      <legend className={labelClasses}>{legend}</legend>
      {help && (
        <p id={`${id}-help`} className={helpClasses}>
          {help}
        </p>
      )}
      <div className="grid gap-2 @xl:grid-cols-2">
        {options.map((option) => (
          <label key={option.value} className={choiceClasses}>
            <input
              type="checkbox"
              value={option.value}
              checked={value.includes(option.value)}
              disabled={disabled}
              onChange={(event) => toggle(option.value, event.target.checked)}
              className="h-4 w-4 shrink-0 accent-brand-600"
            />
            {option.label}
          </label>
        ))}
      </div>
      {error && (
        <p id={`${id}-error`} className={fieldErrorClasses}>
          {error}
        </p>
      )}
    </fieldset>
  )
}

/** Pick one option, or none: every story question may be left unanswered. */
export function RadioQuestion<T extends string>({
  legend,
  options,
  value,
  onChange,
  disabled = false,
  error,
}: {
  legend: string
  options: ReadonlyArray<ChoiceOption<T>>
  value: T | ''
  onChange: (value: T | '') => void
  disabled?: boolean
  error?: string
}) {
  const name = useId()

  return (
    <fieldset aria-describedby={error ? `${name}-error` : undefined}>
      <legend className={labelClasses}>{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <label key={option.value} className={`${choiceClasses} py-2`}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              disabled={disabled}
              onChange={() => onChange(option.value)}
              className="h-4 w-4 shrink-0 accent-brand-600"
            />
            {option.label}
          </label>
        ))}
      </div>
      {value !== '' && !disabled && (
        <button
          type="button"
          onClick={() => onChange('')}
          className="mt-2 text-sm font-semibold text-gray-500 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          Clear answer
        </button>
      )}
      {error && (
        <p id={`${name}-error`} className={fieldErrorClasses}>
          {error}
        </p>
      )}
    </fieldset>
  )
}

/** One-tap answers that fill a free-text question, which stays editable. */
export function Suggestions({
  label,
  suggestions,
  onPick,
  disabled = false,
}: {
  label: string
  suggestions: readonly string[]
  onPick: (value: string) => void
  disabled?: boolean
}) {
  return (
    <fieldset className="mt-2">
      <legend className="sr-only">{label}</legend>
      <div className="flex flex-wrap gap-2">
        {suggestions.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            disabled={disabled}
            onClick={() => onPick(suggestion)}
            className="rounded-full border border-gray-200 bg-white px-3 py-1 text-sm text-gray-700 hover:border-brand-300 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-60"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </fieldset>
  )
}
