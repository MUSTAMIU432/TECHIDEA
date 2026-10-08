import type { InputHTMLAttributes } from 'react'

import { fieldErrorClasses, inputClasses, labelClasses } from './fieldStyles'

interface TextFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  type?: InputHTMLAttributes<HTMLInputElement>['type']
  autoComplete?: string
  placeholder?: string
  error?: string
  disabled?: boolean
}

/** A labeled text input with an accessible, field-adjacent error message. */
export function TextField({
  id,
  label,
  value,
  onChange,
  type = 'text',
  autoComplete,
  placeholder,
  error,
  disabled,
}: TextFieldProps) {
  const errorId = `${id}-error`

  return (
    <div>
      <label htmlFor={id} className={labelClasses}>
        {label}
      </label>
      <input
        id={id}
        name={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? errorId : undefined}
        className={`${inputClasses(Boolean(error))} w-full`}
      />
      {error && (
        <p id={errorId} role="alert" className={fieldErrorClasses}>
          {error}
        </p>
      )}
    </div>
  )
}
