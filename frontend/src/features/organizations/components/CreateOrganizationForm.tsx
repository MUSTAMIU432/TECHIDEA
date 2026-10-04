import { useId, useState, type FormEvent } from 'react'

import {
  inputClasses,
  labelClasses,
  fieldErrorClasses,
} from '../../identity/components/fieldStyles'
import { useOrganization } from '../context/useOrganization'

export function CreateOrganizationForm() {
  const { createOrganization } = useOrganization()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [field, setField] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const nameId = useId()
  const slugId = useId()
  const messageId = useId()

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage(null)
    setField(null)

    if (!name.trim()) {
      setMessage('Organization name is required.')
      setField('name')
      return
    }

    setIsSubmitting(true)
    const result = await createOrganization(name.trim(), slug.trim() || undefined)
    setIsSubmitting(false)

    if (!result.success) {
      setMessage(result.message)
      setField(result.field)
      return
    }

    setName('')
    setSlug('')
    setMessage(result.message)
  }

  return (
    <form
      className="rounded-xl border border-gray-300 border-t-4 border-t-brand-600 bg-white p-6 shadow-md shadow-gray-900/5"
      onSubmit={handleSubmit}
    >
      <div>
        <h2 className="text-lg font-bold text-gray-900">Create an organization</h2>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          Start a shared workspace for your team. You will be its first member and owner.
        </p>
      </div>

      <div className="mt-5 space-y-4">
        <div>
          <label className={labelClasses} htmlFor={nameId}>
            Organization name
          </label>
          <input
            id={nameId}
            className={`${inputClasses(field === 'name')} w-full`}
            maxLength={200}
            placeholder="Organization name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            aria-describedby={message ? messageId : undefined}
            aria-invalid={field === 'name'}
          />
        </div>

        <div>
          <label className={labelClasses} htmlFor={slugId}>
            Slug <span className="font-normal text-gray-500">(optional)</span>
          </label>
          <input
            id={slugId}
            className={`${inputClasses(field === 'slug')} w-full`}
            maxLength={100}
            placeholder="organization-name"
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            aria-invalid={field === 'slug'}
          />
          <p className="mt-1.5 text-xs text-gray-500">
            A stable URL-friendly name, generated from the organization name if left blank.
          </p>
        </div>
      </div>

      {message ? (
        <p
          className={`mt-4 text-sm ${field ? fieldErrorClasses : 'text-gray-600'}`}
          id={messageId}
          role={field ? 'alert' : 'status'}
        >
          {message}
        </p>
      ) : null}

      <button
        type="submit"
        className="mt-6 h-11 w-full rounded-lg bg-brand-600 px-4 text-sm font-bold text-white shadow-md shadow-brand-900/20 transition-colors hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
        disabled={isSubmitting}
      >
        {isSubmitting ? 'Creating…' : 'Create organization'}
      </button>
    </form>
  )
}
