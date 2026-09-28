import { useEffect, useId, useState, type FormEvent } from 'react'

import {
  inputClasses,
  labelClasses,
  fieldErrorClasses,
} from '../../identity/components/fieldStyles'
import {
  SELECTABLE_VISIBILITIES,
  categoriesRequest,
  createIdeaRequest,
  updateIdeaRequest,
  type Idea,
  type IdeaCategory,
  type IdeaVisibility,
} from '../api/ideasApi'
import { SpinnerIcon } from '../../identity/components/icons'
import { ReviewHistory } from '../../reviews/components/ReviewHistory'

/**
 * Create or edit one idea.
 *
 * One component for both, because they are one form: the same four fields,
 * the same validation, the same payload - differing only in which mutation is
 * called and in whether the idea being edited is already on the server. Two
 * components would drift, and the drift would show up as "the edit form
 * validates differently from the create form", which is exactly the kind of
 * thing nobody notices until a user hits it.
 *
 * What this form deliberately does not decide:
 *
 * - **Who the author is.** Not a field, not a hidden value - the server takes
 *   it from the access token.
 * - **Which organization the idea belongs to.** The active organization is
 *   passed in, and it is an input to a server-side decision rather than a
 *   value written onto the idea.
 * - **Whether the idea may be edited or submitted.** The caller decides what
 *   to *offer* (this is only rendered for a draft the signed-in user
 *   authored); the server decides what is *allowed*, and a refusal comes back
 *   as an ordinary business error to be shown, not as a broken form.
 *
 * Validation is deliberately duplicated client-side even though the server
 * validates everything: the client copy exists to give immediate feedback on
 * a field as it is typed, and the server copy is the one that counts. They are
 * kept in step deliberately - the minimum description length here mirrors
 * `ideas.services.MIN_DESCRIPTION_LENGTH`, and the test for that is a comment
 * rather than a constant nobody can check.
 */

export const MIN_DESCRIPTION_LENGTH = 20

interface IdeaFormProps {
  organizationId: string
  /**
   * The idea being edited: a draft, or (S3-005) an idea a reviewer sent back
   * with `CHANGES_REQUESTED`. `null` means "create a new one".
   */
  idea?: Idea | null
  onSaved: (idea: Idea) => void
  onCancel?: () => void
}

interface FieldErrors {
  title?: string
  description?: string
  category?: string
  visibility?: string
}

function validate(values: { title: string; description: string }): FieldErrors {
  const errors: FieldErrors = {}
  if (!values.title.trim()) errors.title = 'Give the idea a title.'
  if (values.description.trim().length < MIN_DESCRIPTION_LENGTH) {
    errors.description = `Describe the problem in at least ${MIN_DESCRIPTION_LENGTH} characters.`
  }
  return errors
}

export function IdeaForm({ organizationId, idea = null, onSaved, onCancel }: IdeaFormProps) {
  const isEditing = idea !== null
  // Sent back by a reviewer (S3-005): the same form, answering the feedback.
  const isRevising = idea?.status === 'CHANGES_REQUESTED'
  const [title, setTitle] = useState(idea?.title ?? '')
  const [description, setDescription] = useState(idea?.description ?? '')
  const [categoryId, setCategoryId] = useState(idea?.category?.id ?? '')
  const [visibility, setVisibility] = useState<IdeaVisibility>(idea?.visibility ?? 'PRIVATE')
  const [categories, setCategories] = useState<IdeaCategory[]>([])
  const [errors, setErrors] = useState<FieldErrors>({})
  const [attempted, setAttempted] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isLoadingCategories, setIsLoadingCategories] = useState(true)

  const visibilityGroupId = useId()
  const titleId = useId()
  const descriptionId = useId()
  const categoryIdForLabel = useId()
  const messageId = useId()

  // Loaded once per mount rather than per keystroke: the list is
  // platform-wide reference data that changes on a different cadence from
  // this form, and a request per render would be a request per character.
  useEffect(() => {
    let cancelled = false
    categoriesRequest()
      .then((next) => {
        if (!cancelled) setCategories(next)
      })
      .catch(() => {
        // Not fatal: the category is optional on a draft, so a failed lookup
        // leaves a usable form that simply cannot classify the idea yet.
        if (!cancelled) setCategories([])
      })
      .finally(() => {
        if (!cancelled) setIsLoadingCategories(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  function updateField<K extends 'title' | 'description'>(field: K, value: string) {
    if (field === 'title') setTitle(value)
    else setDescription(value)
    if (attempted) {
      setErrors(
        validate({
          title: field === 'title' ? value : title,
          description: field === 'description' ? value : description,
        }),
      )
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)
    setFormError(null)

    const nextErrors = validate({ title, description })
    setErrors(nextErrors)
    if (nextErrors.title || nextErrors.description) return

    setIsSubmitting(true)
    try {
      const input = {
        title: title.trim(),
        description: description.trim(),
        categoryId: categoryId || null,
        visibility,
      }
      const result = isEditing
        ? await updateIdeaRequest(idea.id, input)
        : await createIdeaRequest(organizationId, input)

      if (result.success && result.idea) {
        onSaved(result.idea)
        return
      }

      // A `field` failure belongs next to the input that caused it; anything
      // else is a whole-form problem and goes at the top.
      if (result.field === 'title') setErrors({ title: result.message })
      else if (result.field === 'description') setErrors({ description: result.message })
      else if (result.field === 'category') setErrors({ category: result.message })
      else if (result.field === 'visibility') setErrors({ visibility: result.message })
      else setFormError(result.message)
    } catch {
      // The request never reached a decision. Deliberately not the backend's
      // wording: it never answered.
      setFormError('We could not reach the server. Please try again.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <form
      className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm"
      onSubmit={handleSubmit}
      noValidate
    >
      <div>
        <h2 className="text-base font-semibold text-gray-900">
          {isRevising ? 'Revise this idea' : isEditing ? 'Edit this draft' : 'File a new idea'}
        </h2>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          {isRevising
            ? 'A reviewer asked for changes. Edit the idea and save, then use Submit again on the idea to put it forward for the next review. Who can see it is fixed now that it has been submitted.'
            : isEditing
              ? 'A draft can be saved half-finished. Submitting puts it forward for review; it does not change who can see it.'
              : 'Describe a problem worth automating. Saving keeps it as a draft only you can edit; who can read it is set by its visibility below.'}
        </p>
      </div>

      {isRevising && idea && (
        // The feedback being answered, above the fields it is about. Read
        // from the server's history, unchanged by anything saved here.
        <section
          aria-label="Reviewer feedback"
          className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3"
        >
          <h3 className="text-sm font-semibold text-amber-900">Reviewer feedback</h3>
          <div className="mt-2">
            <ReviewHistory ideaId={idea.id} viewerId={null} />
          </div>
        </section>
      )}

      {formError && (
        <p role="alert" className="mt-4 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {formError}
        </p>
      )}

      <div className="mt-5 space-y-4">
        <div>
          <label className={labelClasses} htmlFor={titleId}>
            Title
          </label>
          <input
            id={titleId}
            className={`${inputClasses(Boolean(errors.title))} w-full`}
            maxLength={200}
            placeholder="Automate the monthly invoice run"
            value={title}
            disabled={isSubmitting}
            onChange={(event) => updateField('title', event.target.value)}
            aria-describedby={errors.title ? `${titleId}-error` : undefined}
          />
          {errors.title && (
            <p id={`${titleId}-error`} className={fieldErrorClasses}>
              {errors.title}
            </p>
          )}
        </div>

        <div>
          <label className={labelClasses} htmlFor={descriptionId}>
            Description
          </label>
          <textarea
            id={descriptionId}
            rows={5}
            className={`${inputClasses(Boolean(errors.description))} w-full`}
            placeholder="What happens today, who does it, and what it costs."
            value={description}
            disabled={isSubmitting}
            onChange={(event) => updateField('description', event.target.value)}
            aria-describedby={errors.description ? `${descriptionId}-error` : undefined}
          />
          {errors.description ? (
            <p id={`${descriptionId}-error`} className={fieldErrorClasses}>
              {errors.description}
            </p>
          ) : (
            <p className="mt-1.5 text-sm text-gray-500">
              {isRevising
                ? `Your revised description must contain at least ${MIN_DESCRIPTION_LENGTH} characters.`
                : `A draft can be incomplete, but submitting needs at least ${MIN_DESCRIPTION_LENGTH} characters.`}
            </p>
          )}
        </div>

        <div>
          <label className={labelClasses} htmlFor={categoryIdForLabel}>
            Category
          </label>
          <select
            id={categoryIdForLabel}
            className={`${inputClasses(Boolean(errors.category))} w-full`}
            value={categoryId}
            disabled={isSubmitting || isLoadingCategories}
            onChange={(event) => setCategoryId(event.target.value)}
          >
            <option value="">
              {isLoadingCategories ? 'Loading categories…' : 'Not classified yet'}
            </option>
            {/*
              A draft that is already classified must not flash "Not
              classified yet" while the list loads, which is what a bare
              empty-option list would do: the select would have a value with
              no matching option and fall back to the first one. Showing the
              draft's own category until the real list arrives keeps the
              control honest, and the saved payload is unaffected either way -
              `categoryId` state was already correct.
            */}
            {isLoadingCategories && categoryId && idea?.category && (
              <option value={categoryId}>{idea.category.name}</option>
            )}
            {categories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
          {errors.category ? (
            <p className={fieldErrorClasses}>{errors.category}</p>
          ) : (
            <p className="mt-1.5 text-sm text-gray-500">
              {isRevising
                ? 'Category remains optional while revising, but it is required to submit.'
                : 'Optional while drafting; you will need one to submit.'}
            </p>
          )}
        </div>

        <fieldset>
          <legend className={labelClasses}>Who can see this</legend>
          <div className="space-y-2">
            {SELECTABLE_VISIBILITIES.map((option) => {
              // Derived from one `useId()` above rather than a hook inside the
              // map callback, which React does not allow: a hook called in a
              // callback would be re-created on every render in a different
              // order, and the ids would drift.
              const optionId = `${visibilityGroupId}-${option.value.toLowerCase()}`
              return (
                <label
                  key={option.value}
                  htmlFor={optionId}
                  className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-gray-200 p-3 text-sm font-semibold text-gray-900 hover:bg-gray-50"
                >
                  <input
                    id={optionId}
                    type="radio"
                    name="idea-visibility"
                    value={option.value}
                    checked={visibility === option.value}
                    // Fixed once submitted; the server refuses a change too.
                    disabled={isSubmitting || isRevising}
                    onChange={() => setVisibility(option.value)}
                    className="mt-0.5"
                  />
                  {option.label}
                  <span className="block font-normal text-gray-600">{option.hint}</span>
                </label>
              )
            })}
          </div>
          {errors.visibility && <p className={fieldErrorClasses}>{errors.visibility}</p>}
          <p id={messageId} className="mt-1.5 text-sm text-gray-500">
            A new idea is private until you widen it. Submitting does not change who can see it, and
            a reviewer can only review an idea they can see - choose before you submit, because
            visibility can only be changed while the idea is a draft.
          </p>
        </fieldset>
      </div>

      <div className="mt-6 flex items-center gap-3">
        <button
          type="submit"
          disabled={isSubmitting}
          className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:bg-brand-300"
        >
          {isSubmitting && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
          <span>{isSubmitting ? 'Saving…' : isRevising ? 'Save changes' : 'Save draft'}</span>
        </button>
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300"
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
