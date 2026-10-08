import { useId, useState } from 'react'

import { fieldErrorClasses, inputClasses, labelClasses } from './fieldStyles'
import { EyeIcon, EyeOffIcon } from './icons'

interface PasswordFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  autoComplete: string
  error?: string
  disabled?: boolean
}

/** Password input with a show/hide toggle that preserves the entered value. */
export function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  error,
  disabled,
}: PasswordFieldProps) {
  const [visible, setVisible] = useState(false)
  const errorId = useId()

  return (
    <div>
      <label htmlFor={id} className={labelClasses}>
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          name={id}
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          disabled={disabled}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          className={`${inputClasses(Boolean(error))} w-full pr-11`}
        />
        <button
          type="button"
          onClick={() => setVisible((current) => !current)}
          disabled={disabled}
          aria-pressed={visible}
          aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-gray-400 hover:text-gray-600 focus:outline-none focus-visible:text-brand-600 disabled:cursor-not-allowed disabled:text-gray-300"
        >
          {visible ? <EyeOffIcon className="h-5 w-5" /> : <EyeIcon className="h-5 w-5" />}
        </button>
      </div>
      {error && (
        <p id={errorId} role="alert" className={fieldErrorClasses}>
          {error}
        </p>
      )}
    </div>
  )
}
