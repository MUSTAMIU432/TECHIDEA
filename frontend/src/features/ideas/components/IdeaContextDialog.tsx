import { useEffect, useId, useRef, useState } from 'react'

import type { SubmissionContext } from '../api/ideasApi'
import { useOrganization } from '../../organizations/context/useOrganization'
import { useMyTeams } from '../../teams/hooks/useMyTeams'
import { inputClasses, labelClasses } from '../../identity/components/fieldStyles'
import { SpinnerIcon } from '../../identity/components/icons'

/**
 * What the reader chose, and where it belongs. Two facts, never one.
 *
 * `context` is the kind of owner (a person, a team, an organization) and
 * `ownerId` is that owner. They travel together because the server needs both
 * and because a client that could send one without the other would be sending
 * exactly the ambiguous request the API refuses.
 */
export interface IdeaContextChoice {
  context: SubmissionContext
  /** Null for an individual idea: the owner is the reader. */
  ownerId: string | null
  /** The owner's name, for the confirmation step and the form's banner. */
  ownerName: string
}

/**
 * "Where does this idea belong?" — the dialog behind the global
 * **File a new idea** button.
 *
 * **Why a dialog and not the form's first step.** Filing an idea is the one
 * action a person can get wrong in a way that is hard to notice afterwards: an
 * idea filed for yourself when they meant their organization will never be
 * confirmed by that organization, and it will sit in a review queue somebody
 * else cannot see. So the ownership question is asked *before* any writing
 * happens, it is asked once, and it is answered with three whole options
 * carrying their own explanation rather than three radio buttons and a sentence
 * of help text.
 *
 * **Three steps, and the middle one only appears when it is needed.** Choosing
 * "mine" is one click and goes straight to the confirmation; choosing a team or
 * an organization asks which one, because "an idea for a team" is not an answer
 * the server can act on.
 *
 * **Only tenants the reader may actually file for are listed.** The backend
 * refuses anything else, so showing it would be offering a choice that ends in
 * an error - and, worse, would teach the reader that the list is arbitrary. A
 * reader with no team is told so, in those words, rather than shown an empty
 * picker.
 *
 * The dialog is presentation only: it navigates with the choice in the URL, and
 * the server re-decides both facts when the idea is created.
 */
export function IdeaContextDialog({
  onCancel,
  onChosen,
}: {
  onCancel: () => void
  onChosen: (choice: IdeaContextChoice) => void
}) {
  const titleId = useId()
  const [step, setStep] = useState<'choose' | 'owner' | 'confirm'>('choose')
  const [context, setContext] = useState<SubmissionContext | null>(null)
  const [ownerId, setOwnerId] = useState('')
  // Resolved when the owner is chosen, so the confirmation names it without
  // re-deriving it from a list it may already have been unloaded from.
  const [ownerName, setOwnerName] = useState('')
  const cancelRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    cancelRef.current?.focus()
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onCancel])

  function chooseIndividual() {
    // Straight to the confirmation, with no tenant picker in between: an
    // individual idea has no owner to choose, because the reader *is* it. It
    // still confirms, because "I meant my organization" is the expensive
    // mistake and this is the last place to catch it.
    setContext('INDIVIDUAL')
    setOwnerName('yourself')
    setStep('confirm')
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 px-4 py-8">
      <dialog
        open
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative m-0 w-full max-w-lg rounded-2xl bg-white p-6 text-left shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-bold tracking-[0.18em] text-brand-700 uppercase">New idea</p>
            <h2 id={titleId} className="mt-1 text-xl font-bold tracking-tight text-gray-900">
              Create a New Idea
            </h2>
          </div>
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            <span className="sr-only">Close</span>
            <svg
              aria-hidden="true"
              viewBox="0 0 20 20"
              className="h-5 w-5"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <path d="M5 5l10 10M15 5 5 15" />
            </svg>
          </button>
        </div>

        {step === 'choose' && (
          <section aria-labelledby={`${titleId}-where`}>
            <h3 id={`${titleId}-where`} className="mt-4 text-base font-semibold text-gray-900">
              Where does this idea belong?
            </h3>
            <p className="mt-1 text-sm text-gray-600">
              This decides who owns it. You can change who can see it later; who it belongs to is
              fixed once it is filed.
            </p>

            <ul className="mt-4 space-y-3">
              <ContextChoice
                title="Individual Idea"
                description="An idea that belongs to you personally."
                detail="The platform reviews it directly. There is no organization to check it first."
                icon={<PersonIcon />}
                onSelect={chooseIndividual}
              />
              <ContextChoice
                title="Team Idea"
                description="An idea created and owned by a team."
                detail="Your team members can see it, and the platform reviews it directly."
                icon={<TeamIcon />}
                onSelect={() => {
                  setContext('TEAM')
                  setStep('owner')
                }}
              />
              <ContextChoice
                title="Organization Idea"
                description="An idea created and owned by an organization."
                detail="Your organization confirms it before the platform sees it."
                icon={<OrganizationIcon />}
                onSelect={() => {
                  setContext('ORGANIZATION')
                  setStep('owner')
                }}
              />
            </ul>
          </section>
        )}

        {step === 'owner' && (
          <OwnerStep
            context={context === 'ORGANIZATION' ? 'ORGANIZATION' : 'TEAM'}
            ownerId={ownerId}
            setOwnerId={setOwnerId}
            onBack={() => {
              setOwnerId('')
              setStep('choose')
            }}
            onContinue={(name) => {
              setOwnerName(name)
              setStep('confirm')
            }}
          />
        )}

        {step === 'confirm' && context && (
          <ConfirmationStep
            context={context}
            ownerName={ownerName}
            onChange={() => {
              setOwnerId('')
              setOwnerName('')
              setStep(context === 'ORGANIZATION' ? 'owner' : 'choose')
            }}
            onContinue={() => onChosen({ context, ownerId, ownerName })}
          />
        )}
      </dialog>
    </div>
  )
}

function ContextChoice({
  title,
  description,
  detail,
  icon,
  onSelect,
}: {
  title: string
  description: string
  detail: string
  icon: React.ReactNode
  onSelect: () => void
}) {
  return (
    <li className="flex items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 text-left transition-colors hover:border-brand-300 hover:bg-brand-50/40">
      <span className="mt-0.5 shrink-0 text-brand-700">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-bold text-gray-900">{title}</span>
        <span className="mt-0.5 block text-sm text-gray-700">{description}</span>
        <span className="mt-1 block text-xs text-gray-500">{detail}</span>
      </span>
      <button
        type="button"
        onClick={onSelect}
        className="mt-0.5 shrink-0 rounded-lg border border-brand-600 px-3 py-1.5 text-sm font-semibold text-brand-700 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
      >
        Select
      </button>
    </li>
  )
}

/**
 * Which team, or which organization.
 *
 * The lists come from the reader's own memberships and from nowhere else, so
 * there is nothing to filter here even in principle — and when the list is
 * empty the step says what to do instead of offering an empty picker.
 */
function OwnerStep({
  context,
  ownerId,
  setOwnerId,
  onBack,
  onContinue,
}: {
  context: 'TEAM' | 'ORGANIZATION'
  ownerId: string
  setOwnerId: (value: string) => void
  onBack: () => void
  /** Called with the chosen owner's name, resolved from the list it came from. */
  onContinue: (ownerName: string) => void
}) {
  const teams = useMyTeams()
  const { memberships } = useOrganization()
  const selectId = useId()
  const isTeam = context === 'TEAM'

  const options = isTeam
    ? teams.teams.map((team) => ({
        id: team.id,
        name: team.name,
        detail: `/${team.slug}`,
      }))
    : memberships.map(({ organization }) => ({
        id: organization.id,
        name: organization.name,
        detail: `/${organization.slug}`,
      }))

  const loading = isTeam ? teams.loading : false

  return (
    <section aria-labelledby={`${selectId}-heading`}>
      <h3 id={`${selectId}-heading`} className="mt-4 text-base font-semibold text-gray-900">
        {isTeam ? 'Select Team' : 'Select Organization'}
      </h3>
      <p className="mt-1 text-sm text-gray-600">
        Only the {isTeam ? 'teams' : 'organizations'} you are an active member of are listed.
      </p>

      {loading ? (
        <p className="mt-4 flex items-center gap-2 text-sm text-gray-600">
          <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />
          Loading your {isTeam ? 'teams' : 'organizations'}…
        </p>
      ) : options.length === 0 ? (
        <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          You are not in {isTeam ? 'a team' : 'an organization'} yet, so there is nothing to file
          for. Create one from your workspace, or go back and file this idea for yourself.
        </p>
      ) : (
        <div className="mt-4">
          <label className={labelClasses} htmlFor={selectId}>
            {isTeam ? 'Team' : 'Organization'}
          </label>
          <select
            id={selectId}
            className={`${inputClasses(false)} mt-1 w-full`}
            value={ownerId}
            onChange={(event) => setOwnerId(event.target.value)}
          >
            <option value="">Choose {isTeam ? 'a team' : 'an organization'}…</option>
            {options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name} — {option.detail}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="mt-6 flex justify-between gap-3">
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          Back
        </button>
        <button
          type="button"
          onClick={() => {
            const chosen = options.find((option) => option.id === ownerId)
            onContinue(chosen?.name ?? '')
          }}
          disabled={ownerId === ''}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Continue
        </button>
      </div>
    </section>
  )
}

/**
 * The confirmation, one click before the form.
 *
 * Cheap, and it is the last chance to be wrong about: ownership is fixed once
 * the idea is filed, and a reader who chose "my team" over "my organization" by
 * accident has created a document the organization will never confirm.
 */
function ConfirmationStep({
  context,
  ownerName,
  onChange,
  onContinue,
}: {
  context: SubmissionContext
  ownerName: string
  onChange: () => void
  onContinue: () => void
}) {
  return (
    <section aria-labelledby={`${context}-confirmation`}>
      <h3 id={`${context}-confirmation`} className="mt-4 text-base font-semibold text-gray-900">
        You are creating:
      </h3>
      <dl className="mt-3 rounded-xl border border-gray-200 bg-gray-50 p-4">
        <div className="flex items-baseline justify-between gap-4">
          <dt className="text-sm text-gray-600">Type</dt>
          <dd className="text-sm font-bold text-gray-900">{CONTEXT_TITLES[context]}</dd>
        </div>
        <div className="mt-2 flex items-baseline justify-between gap-4">
          <dt className="text-sm text-gray-600">Owned by</dt>
          <dd className="text-sm font-bold text-gray-900">{ownerName}</dd>
        </div>
      </dl>
      <p className="mt-3 text-sm text-gray-600">
        You will be able to choose who can see it. Who it belongs to is fixed once it is filed.
      </p>

      <div className="mt-6 flex justify-between gap-3">
        <button
          type="button"
          onClick={onChange}
          className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          Change
        </button>
        <button
          type="button"
          onClick={onContinue}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
        >
          Continue
        </button>
      </div>
    </section>
  )
}

const CONTEXT_TITLES: Record<SubmissionContext, string> = {
  INDIVIDUAL: 'Individual Idea',
  TEAM: 'Team Idea',
  ORGANIZATION: 'Organization Idea',
}

function PersonIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-6 w-6"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </svg>
  )
}

function TeamIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-6 w-6"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20a6 6 0 0 1 12 0" />
      <circle cx="17.5" cy="9.5" r="2.4" />
      <path d="M14.5 19.5a5 5 0 0 1 6.5-4.8" />
    </svg>
  )
}

function OrganizationIcon() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-6 w-6"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 21V5a1 1 0 0 1 1-1h9a1 1 0 0 1 1 1v16" />
      <path d="M15 9h4a1 1 0 0 1 1 1v11" />
      <path d="M8 8h3M8 12h3M8 16h3" />
    </svg>
  )
}

export { CONTEXT_TITLES }
