import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { GoogleAuthButton } from './GoogleAuthButton'

describe('GoogleAuthButton', () => {
  it('renders with the default label', () => {
    render(<GoogleAuthButton />)

    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeInTheDocument()
  })

  it('renders a custom label when given one', () => {
    render(<GoogleAuthButton label="Sign up with Google" />)

    expect(screen.getByRole('button', { name: 'Sign up with Google' })).toBeInTheDocument()
  })

  it('calls onClick when clicked', () => {
    const onClick = vi.fn()
    render(<GoogleAuthButton onClick={onClick} />)

    fireEvent.click(screen.getByRole('button', { name: 'Continue with Google' }))

    expect(onClick).toHaveBeenCalledOnce()
  })

  it('is disabled when disabled is passed, and does not call onClick', () => {
    const onClick = vi.fn()
    render(<GoogleAuthButton disabled onClick={onClick} />)

    const button = screen.getByRole('button', { name: 'Continue with Google' })
    expect(button).toBeDisabled()

    fireEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })
})
