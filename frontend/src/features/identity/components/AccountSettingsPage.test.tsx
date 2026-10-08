import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AccountSettingsPage } from './AccountSettingsPage'

const changePasswordMock = vi.fn()
const updateProfileMock = vi.fn()
const updateUserMock = vi.fn()
const uploadAvatarMock = vi.fn()

vi.mock('../auth/authApi', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  changePasswordRequest: (...args: unknown[]) => changePasswordMock(...args),
  updateProfileRequest: (...args: unknown[]) => updateProfileMock(...args),
  resendActivationEmailRequest: vi.fn(),
  uploadAvatarRequest: (...args: unknown[]) => uploadAvatarMock(...args),
  removeAvatarRequest: vi.fn(),
}))

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    user: {
      id: '1',
      email: 'ada@example.com',
      firstName: 'Ada',
      lastName: 'Lovelace',
      phoneNumber: '+255712345678',
      isActive: true,
      isVerified: true,
    },
    updateUser: updateUserMock,
  }),
}))

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/app/settings/:section" element={<AccountSettingsPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AccountSettingsPage', () => {
  beforeEach(() => vi.clearAllMocks())

  it('saves the profile and updates the signed-in user', async () => {
    const saved = { id: '1', firstName: 'Grace' }
    updateProfileMock.mockResolvedValue({
      success: true,
      message: 'Your profile has been updated.',
      field: null,
      user: saved,
    })
    renderAt('/app/settings/profile')

    fireEvent.change(screen.getByLabelText('First name'), {
      target: { value: 'Grace' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(updateUserMock).toHaveBeenCalledWith(saved))
    expect(updateProfileMock).toHaveBeenCalledWith({
      firstName: 'Grace',
      lastName: 'Lovelace',
      phoneNumber: '+255712345678',
    })
    expect(await screen.findByText('Your profile has been updated.')).toBeInTheDocument()
  })

  it('refuses mismatched passwords without calling the server', () => {
    renderAt('/app/settings/password')

    fireEvent.change(screen.getByLabelText('Current password'), {
      target: { value: 'old-pass-1' },
    })
    fireEvent.change(screen.getByLabelText('New password'), {
      target: { value: 'new-pass-123' },
    })
    fireEvent.change(screen.getByLabelText('Confirm new password'), {
      target: { value: 'different-123' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))

    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument()
    expect(changePasswordMock).not.toHaveBeenCalled()
  })

  it("shows the server's complaint next to the current password", async () => {
    changePasswordMock.mockResolvedValue({
      success: false,
      message: 'Your current password is incorrect.',
      field: 'currentPassword',
      user: null,
    })
    renderAt('/app/settings/password')

    fireEvent.change(screen.getByLabelText('Current password'), {
      target: { value: 'wrong-pass' },
    })
    fireEvent.change(screen.getByLabelText('New password'), {
      target: { value: 'new-pass-123' },
    })
    fireEvent.change(screen.getByLabelText('Confirm new password'), {
      target: { value: 'new-pass-123' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))

    expect(await screen.findByText('Your current password is incorrect.')).toBeInTheDocument()
  })

  it('shows email verification on the security tab', () => {
    renderAt('/app/settings/security')
    expect(screen.getByText('ada@example.com is confirmed.')).toBeInTheDocument()
  })

  it('refuses a photo that is not an image before uploading it', () => {
    renderAt('/app/settings/profile')

    const file = new File(['x'], 'cv.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByLabelText('Profile photo file'), {
      target: { files: [file] },
    })

    expect(screen.getByText('Choose a JPEG, PNG or WebP image.')).toBeInTheDocument()
    expect(uploadAvatarMock).not.toHaveBeenCalled()
  })

  it('uploads a valid photo and puts its address on the signed-in user', async () => {
    uploadAvatarMock.mockResolvedValue({
      success: true,
      message: 'Your photo has been updated.',
      avatarUrl: '/users/1/avatar/?v=abc',
    })
    renderAt('/app/settings/profile')

    const file = new File(['x'], 'me.png', { type: 'image/png' })
    fireEvent.change(screen.getByLabelText('Profile photo file'), {
      target: { files: [file] },
    })

    await waitFor(() =>
      expect(updateUserMock).toHaveBeenCalledWith(
        expect.objectContaining({ avatarUrl: '/users/1/avatar/?v=abc' }),
      ),
    )
  })
})
