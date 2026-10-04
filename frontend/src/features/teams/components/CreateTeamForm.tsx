import { useId, useState, type FormEvent } from 'react'

import {
  fieldErrorClasses,
  inputClasses,
  labelClasses,
} from '../../identity/components/fieldStyles'
import { createTeamRequest } from '../api/teamsApi'
import { useTeams } from '../context/useTeams'

/**
 * Create a team, which makes the caller its Owner and first member.
 *
 * Needs no organization, and that is the whole point of the form being here
 * rather than inside an organization's member list: somebody with no tenant can
 * still put an idea forward with a group of people.
 *
 * A refusal is the backend's own message, placed next to the input it names
 * where it named one — the same handling `CreateOrganizationForm` uses, so the
 * two look like one product.
 *
 * `reload` comes from the page's `TeamsProvider`, which is also what `TeamList`
 * reads: a team just created is now one of the caller's teams, and the list
 * beside this form has to say so without asking the server a second time for
 * work this page already knows it started.
 */
export function CreateTeamForm() {
  const { reload } = useTeams()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [field, setField] = useState<string | null>(null)
  const [created, setCreated] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const nameId = useId()
  const descriptionId = useId()
  const messageId = useId()

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage(null)
    setField(null)
    setCreated(null)

    if (!name.trim()) {
      setMessage('Give the team a name.')
      setField('name')
      return
    }

    setIsSubmitting(true)
    try {
      const result = await createTeamRequest({
        name: name.trim(),
        description: description.trim(),
      })
      if (!result.success) {
        setMessage(result.message)
        setField(result.field)
        return
      }
      setName('')
      setDescription('')
      setCreated(result.message)
      // The list beside this form is the caller's own teams, and a team just
      // created is now one of them.
      reload()
    } catch {
      // The request never reached a decision; it did not create anything.
      setMessage('We could not reach the server. Please try again.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <form
      className="rounded-xl border border-gray-300 border-t-4 border-t-brand-600 bg-white p-6 shadow-md shadow-gray-900/5"
      onSubmit={handleSubmit}
      noValidate
    >
      <div>
        <h2 className="text-lg font-bold text-gray-900">Create a team</h2>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          A team is a group of people who put an idea forward together. You will be its first member
          and its owner, and it does not need an organization.
        </p>
      </div>

      <div className="mt-5 space-y-4">
        <div>
          <label className={labelClasses} htmlFor={nameId}>
            Team name
          </label>
          <input
            id={nameId}
            className={`${inputClasses(field === 'name')} w-full`}
            maxLength={200}
            placeholder="Registrar"
            value={name}
            onChange={(event) => setName(event.target.value)}
            aria-describedby={message ? messageId : undefined}
            aria-invalid={field === 'name'}
          />
        </div>

        <div>
          <label className={labelClasses} htmlFor={descriptionId}>
            What is this team for? <span className="font-normal text-gray-500">(optional)</span>
          </label>
          <textarea
            id={descriptionId}
            className={`${inputClasses(field === 'description', { multiline: true })} w-full resize-y`}
            rows={3}
            maxLength={1000}
            placeholder="The people who allocate rooms to students."
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            aria-invalid={field === 'description'}
          />
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
        {isSubmitting ? 'Creating…' : 'Create team'}
      </button>

      {created ? <output className="sr-only">{created}</output> : null}
    </form>
  )
}
