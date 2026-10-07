import { useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { Navigate, NavLink, useParams } from 'react-router-dom'

import {
  AVATAR_MAX_BYTES,
  AVATAR_TYPES,
  changePasswordRequest,
  removeAvatarRequest,
  uploadAvatarRequest,
  NETWORK_ERROR_MESSAGE,
  resendActivationEmailRequest,
  updateProfileRequest,
} from '../auth/authApi'
import { useAuth } from '../auth/AuthContext'
import {
  hasErrors,
  MIN_PASSWORD_LENGTH,
  validatePhoneNumber,
  type FieldErrors,
} from '../schemas/authValidation'
import { Avatar } from './Avatar'
import { PasswordField } from './PasswordField'
import { TextField } from './TextField'

const SECTIONS = [
  { path: 'profile', label: 'Profile' },
  { path: 'security', label: 'Security' },
  { path: 'password', label: 'Change password' },
] as const

const BUTTON =
  'inline-flex h-11 items-center justify-center rounded-lg bg-brand-600 px-5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:bg-brand-300'

interface Notice {
  kind: 'success' | 'error'
  text: string
}

function NoticeBanner({ notice }: { notice: Notice | null }) {
  if (!notice) return null
  return (
    <p
      role={notice.kind === 'error' ? 'alert' : 'status'}
      className={`rounded-lg px-4 py-3 text-sm ${
        notice.kind === 'error' ? 'bg-red-50 text-red-700' : 'bg-brand-50 text-brand-800'
      }`}
    >
      {notice.text}
    </p>
  )
}

/** Account settings: `/app/settings/profile`, `/security` and `/password`. */
export function AccountSettingsPage() {
  const { section } = useParams()
  if (!SECTIONS.some((entry) => entry.path === section)) {
    return <Navigate to="/app/settings/profile" replace />
  }

  return (
    <div className="w-full">
      <h1 className="text-3xl font-bold tracking-tight text-gray-900 sm:text-4xl">
        Account settings
      </h1>
      <div className="mt-6 grid gap-6 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <nav aria-label="Account settings" className="flex gap-2 overflow-x-auto lg:flex-col">
          {SECTIONS.map((entry) => (
            <NavLink
              key={entry.path}
              to={`/app/settings/${entry.path}`}
              className={({ isActive }) =>
                `rounded-lg px-4 py-2.5 text-sm font-semibold whitespace-nowrap ${
                  isActive
                    ? 'bg-brand-600 text-white shadow-sm'
                    : 'bg-white text-gray-700 ring-1 ring-gray-200 hover:bg-gray-50'
                }`
              }
            >
              {entry.label}
            </NavLink>
          ))}
        </nav>
        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          {section === 'profile' && <ProfileSection />}
          {section === 'security' && <SecuritySection />}
          {section === 'password' && <PasswordSection />}
        </div>
      </div>
    </div>
  )
}

function PhotoPicker() {
  const { user, updateUser } = useAuth()
  const inputRef = useRef<HTMLInputElement>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState(false)

  async function apply(action: () => ReturnType<typeof uploadAvatarRequest>) {
    if (!user) return
    setBusy(true)
    setNotice(null)
    try {
      const result = await action()
      if (result.success) updateUser({ ...user, avatarUrl: result.avatarUrl })
      setNotice({
        kind: result.success ? 'success' : 'error',
        text: result.message,
      })
    } catch {
      setNotice({ kind: 'error', text: NETWORK_ERROR_MESSAGE })
    } finally {
      setBusy(false)
    }
  }

  function handleChoose(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!AVATAR_TYPES.includes(file.type)) {
      setNotice({ kind: 'error', text: 'Choose a JPEG, PNG or WebP image.' })
    } else if (file.size > AVATAR_MAX_BYTES) {
      setNotice({
        kind: 'error',
        text: 'The photo must be 100 MB or smaller.',
      })
    } else {
      void apply(() => uploadAvatarRequest(file))
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-4">
        <Avatar
          user={user}
          className="flex h-20 w-20 shrink-0 items-center justify-center rounded-full bg-brand-600 text-2xl font-bold text-white"
        />
        <div className="flex flex-wrap gap-2">
          <input
            ref={inputRef}
            type="file"
            accept={AVATAR_TYPES.join(',')}
            aria-label="Profile photo file"
            onChange={handleChoose}
            className="sr-only"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            className={BUTTON}
          >
            {busy ? 'Working…' : 'Change photo'}
          </button>
          {user?.avatarUrl && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void apply(removeAvatarRequest)}
              className="inline-flex h-11 items-center rounded-lg px-4 text-sm font-semibold text-gray-700 ring-1 ring-gray-300 hover:bg-gray-50 disabled:opacity-60"
            >
              Remove
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-gray-500">JPEG, PNG or WebP, up to 100 MB.</p>
      <NoticeBanner notice={notice} />
    </div>
  )
}

function ProfileSection() {
  const { user, updateUser } = useAuth()
  const [values, setValues] = useState({
    firstName: user?.firstName ?? '',
    lastName: user?.lastName ?? '',
    phoneNumber: user?.phoneNumber ?? '',
  })
  const [errors, setErrors] = useState<FieldErrors<typeof values>>({})
  const [notice, setNotice] = useState<Notice | null>(null)
  const [saving, setSaving] = useState(false)

  function set(field: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const next: FieldErrors<typeof values> = {}
    if (!values.firstName.trim()) next.firstName = 'First name is required.'
    if (!values.lastName.trim()) next.lastName = 'Last name is required.'
    const phoneError = validatePhoneNumber(values.phoneNumber)
    if (phoneError) next.phoneNumber = phoneError
    setErrors(next)
    setNotice(null)
    if (hasErrors(next)) return

    setSaving(true)
    try {
      const result = await updateProfileRequest(values)
      if (result.success && result.user) {
        updateUser(result.user)
        setNotice({ kind: 'success', text: result.message })
      } else if (result.field && result.field in values) {
        setErrors({ [result.field]: result.message })
      } else {
        setNotice({ kind: 'error', text: result.message })
      }
    } catch {
      setNotice({ kind: 'error', text: NETWORK_ERROR_MESSAGE })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="max-w-xl space-y-5">
      <div>
        <h2 className="text-xl font-bold text-gray-900">Profile</h2>
        <p className="mt-1 text-sm text-gray-500">How you appear to your team and reviewers.</p>
      </div>
      <PhotoPicker />
      <NoticeBanner notice={notice} />
      <div className="grid gap-5 sm:grid-cols-2">
        <TextField
          id="profile-first-name"
          label="First name"
          autoComplete="given-name"
          value={values.firstName}
          onChange={(value) => set('firstName', value)}
          error={errors.firstName}
        />
        <TextField
          id="profile-last-name"
          label="Last name"
          autoComplete="family-name"
          value={values.lastName}
          onChange={(value) => set('lastName', value)}
          error={errors.lastName}
        />
      </div>
      <TextField
        id="profile-phone"
        label="Phone number"
        type="tel"
        autoComplete="tel"
        placeholder="+255712345678"
        value={values.phoneNumber}
        onChange={(value) => set('phoneNumber', value)}
        error={errors.phoneNumber}
      />
      <TextField
        id="profile-email"
        label="Email address"
        value={user?.email ?? ''}
        onChange={() => undefined}
        disabled
      />
      <button type="submit" disabled={saving} className={BUTTON}>
        {saving ? 'Saving…' : 'Save changes'}
      </button>
    </form>
  )
}

function SecuritySection() {
  const { user } = useAuth()
  const [notice, setNotice] = useState<Notice | null>(null)
  const [sending, setSending] = useState(false)

  async function resend() {
    if (!user) return
    setSending(true)
    try {
      const result = await resendActivationEmailRequest(user.email)
      setNotice({
        kind: result.success ? 'success' : 'error',
        text: result.message,
      })
    } catch {
      setNotice({ kind: 'error', text: NETWORK_ERROR_MESSAGE })
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="max-w-xl space-y-5">
      <div>
        <h2 className="text-xl font-bold text-gray-900">Security</h2>
        <p className="mt-1 text-sm text-gray-500">How your account is protected.</p>
      </div>
      <NoticeBanner notice={notice} />
      <div className="rounded-lg border border-gray-200 p-4">
        <p className="text-sm font-semibold text-gray-900">Email verification</p>
        <p className="mt-1 text-sm text-gray-600">
          {user?.isVerified
            ? `${user.email} is confirmed.`
            : `${user?.email ?? 'Your address'} has not been confirmed yet.`}
        </p>
        {!user?.isVerified && (
          <button type="button" onClick={resend} disabled={sending} className={`mt-3 ${BUTTON}`}>
            {sending ? 'Sending…' : 'Resend confirmation email'}
          </button>
        )}
      </div>
      <div className="rounded-lg border border-gray-200 p-4">
        <p className="text-sm font-semibold text-gray-900">Password</p>
        <p className="mt-1 text-sm text-gray-600">
          Changing your password signs you out everywhere else.
        </p>
        <NavLink
          to="/app/settings/password"
          className="mt-3 inline-block text-sm font-semibold text-brand-700 hover:underline"
        >
          Change password
        </NavLink>
      </div>
    </div>
  )
}

function PasswordSection() {
  const [values, setValues] = useState({ current: '', next: '', confirm: '' })
  const [errors, setErrors] = useState<FieldErrors<typeof values>>({})
  const [notice, setNotice] = useState<Notice | null>(null)
  const [saving, setSaving] = useState(false)

  function set(field: keyof typeof values, value: string) {
    setValues((current) => ({ ...current, [field]: value }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const next: FieldErrors<typeof values> = {}
    if (!values.current) next.current = 'Enter your current password.'
    if (values.next.length < MIN_PASSWORD_LENGTH) {
      next.next = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`
    }
    if (values.confirm !== values.next) next.confirm = 'Passwords do not match.'
    setErrors(next)
    setNotice(null)
    if (hasErrors(next)) return

    setSaving(true)
    try {
      const result = await changePasswordRequest(values.current, values.next)
      if (result.success) {
        setValues({ current: '', next: '', confirm: '' })
        setNotice({ kind: 'success', text: result.message })
      } else if (result.field === 'currentPassword') {
        setErrors({ current: result.message })
      } else if (result.field === 'newPassword') {
        setErrors({ next: result.message })
      } else {
        setNotice({ kind: 'error', text: result.message })
      }
    } catch {
      setNotice({ kind: 'error', text: NETWORK_ERROR_MESSAGE })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate className="max-w-xl space-y-5">
      <div>
        <h2 className="text-xl font-bold text-gray-900">Change password</h2>
        <p className="mt-1 text-sm text-gray-500">
          Every other device is signed out when you change it.
        </p>
      </div>
      <NoticeBanner notice={notice} />
      <PasswordField
        id="current-password"
        label="Current password"
        autoComplete="current-password"
        value={values.current}
        onChange={(value) => set('current', value)}
        error={errors.current}
      />
      <PasswordField
        id="new-password"
        label="New password"
        autoComplete="new-password"
        value={values.next}
        onChange={(value) => set('next', value)}
        error={errors.next}
      />
      <PasswordField
        id="confirm-new-password"
        label="Confirm new password"
        autoComplete="new-password"
        value={values.confirm}
        onChange={(value) => set('confirm', value)}
        error={errors.confirm}
      />
      <button type="submit" disabled={saving} className={BUTTON}>
        {saving ? 'Changing…' : 'Change password'}
      </button>
    </form>
  )
}
