import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { PasswordField } from './PasswordField'

function ControlledPasswordField() {
  const [value, setValue] = useState('')
  return (
    <PasswordField
      id="password"
      label="Password"
      autoComplete="new-password"
      value={value}
      onChange={setValue}
    />
  )
}

describe('PasswordField', () => {
  it('toggles between masked and visible text without losing the value', () => {
    render(<ControlledPasswordField />)

    const input = screen.getByLabelText('Password') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'super-secret' } })
    expect(input.type).toBe('password')
    expect(input.value).toBe('super-secret')

    fireEvent.click(screen.getByRole('button', { name: 'Show password' }))
    expect(input.type).toBe('text')
    expect(input.value).toBe('super-secret')

    fireEvent.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(input.type).toBe('password')
    expect(input.value).toBe('super-secret')
  })
})
