import { useState } from 'react'

import { useDebouncedCallback } from '../../../lib/useDebouncedCallback'
import { controlClasses } from './AdminUi'

/**
 * A search box that asks the server once the administrator stops typing,
 * rather than once per keystroke. The search itself runs on the server -
 * the console never loads a table to filter it in the browser.
 */
export function SearchBox({
  label,
  placeholder,
  initialValue,
  onSearch,
}: {
  label: string
  placeholder: string
  initialValue: string
  onSearch: (value: string) => void
}) {
  const [value, setValue] = useState(initialValue)
  const debounced = useDebouncedCallback((next: string) => onSearch(next.trim()), 300)

  return (
    <input
      type="search"
      aria-label={label}
      placeholder={placeholder}
      value={value}
      onChange={(event) => {
        setValue(event.target.value)
        debounced(event.target.value)
      }}
      className={`${controlClasses} w-full sm:w-72`}
    />
  )
}
