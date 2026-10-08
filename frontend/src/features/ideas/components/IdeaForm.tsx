import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'

import { audienceDescription, contextDescription } from '../utils/lifecycle'
import { useMyTeams } from '../../teams/hooks/useMyTeams'
import {
  fieldErrorClasses,
  inputClasses,
  labelClasses,
} from '../../identity/components/fieldStyles'
import {
  categoriesRequest,
  createIdeaRequest,
  submitIdeaRequest,
  updateIdeaRequest,
  uploadAttachmentRequest,
  type Idea,
  type IdeaCategory,
  type IdeaMutationResult,
  type SubmissionContext,
} from '../api/ideasApi'
import { SpinnerIcon } from '../../identity/components/icons'
import { ReviewHistory } from '../../reviews/components/ReviewHistory'
import {
  CURRENT_TOOL_OPTIONS,
  FREQUENCY_OPTIONS,
  IMPACT_OPTIONS,
  STORY_LIMITS,
  TIME_SUGGESTIONS,
} from '../utils/problemStory'
import {
  CheckboxQuestion,
  RadioQuestion,
  Suggestions,
  TextQuestion,
  choiceClasses,
} from './intake/controls'
import {
  LAST_STEP,
  MAX_TITLE_LENGTH,
  MIN_DESCRIPTION_LENGTH,
  STEPS,
  fieldFromServer,
  firstStepWithError,
  hasErrors,
  initialValues,
  stepHasAnswers,
  toInput,
  toTarget,
  validate,
  type FieldErrors,
  type FormValues,
  type SaveMode,
} from './intake/formModel'
import { IdeaEvidenceUploader } from './intake/IdeaEvidenceUploader'
import type { UploadStatus } from './intake/FileUploadPanel'
import { PendingFilesPicker } from './intake/PendingFilesPicker'
import { StepProgress, type StepState } from './intake/StepProgress'

export { MIN_DESCRIPTION_LENGTH } from './intake/formModel'

/**
 * Create or edit one idea, as a guided conversation about a problem.
 *
 * Written for the people who have the problem - students, staff, managers,
 * customers - not for developers. Every question is about the real world:
 * what happens, who it touches, what better would look like. None asks about
 * technology; turning the story into requirements and a solution is what the
 * review and later stages are for.
 *
 * The questions are split into steps so nobody faces them all at once, and
 * every step is reachable at any time. All answers live in this component's
 * state, so moving between steps never loses one; only the first step holds
 * anything a draft needs, and submitting adds only what a reviewer needs
 * (see `intake/formModel.validate`).
 *
 * One component for create and edit, because they are one form - the same
 * questions, the same validation, the same payload - differing only in which
 * mutation is called. Two components would drift.
 *
 * What this form deliberately does not decide:
 *
 * - **Who the author is.** Not a field, not a hidden value - the server takes
 *   it from the access token.
 * - **Which organization the idea belongs to.** The active organization is
 *   passed in, and it is an input to a server-side decision rather than a
 *   value written onto the idea.
 * - **Whether the idea may be edited or submitted.** The caller decides what
 *   to *offer*; the server decides what is *allowed*, and a refusal comes back
 *   as an ordinary business error to be shown, not as a broken form.
 * - **Which categories exist.** They come from the backend's `categories`
 *   query and are rendered as returned; there is no list here.
 */

const primaryButtonClasses =
  'inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 text-sm font-semibold text-white shadow-sm shadow-brand-900/10 motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:bg-brand-300'

const secondaryButtonClasses =
  'inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-brand-300 bg-white px-4 text-sm font-semibold text-brand-700 shadow-sm motion-safe:transition-colors motion-safe:duration-150 hover:bg-brand-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:cursor-not-allowed disabled:opacity-60'

const quietButtonClasses =
  'inline-flex h-11 items-center rounded-lg px-4 text-sm font-semibold text-gray-700 hover:bg-gray-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-300 disabled:opacity-60'

interface IdeaFormProps {
  /**
   * The organization an idea is being filed for, or `null` when the caller has
   * none. Only the `ORGANIZATION` context needs it; the other two are why
   * this is nullable - an individual filing, or one for a team, works with no
   * organization at all, which is the case the backend's `_resolve_context`
   * opens with.
   */
  organizationId: string | null
  /**
   * The ownership the reader already chose, when they chose it *before* this
   * form was opened - from a team's page, an organization's page, or the "Where
   * does this idea belong?" dialog.
   *
   * It is applied to the form's own values and the context controls are then
   * closed, because the question has been answered: showing a picker that
   * silently ignores the answer would let a reader file a team idea for a
   * different team without any indication that they had.
   *
   * **Presentation, not authority.** The same context, team and organization are
   * sent with the idea and `ideas.services._resolve_context` proves membership
   * again; a hand-written request naming another team is refused there.
   */
  lockedContext?: LockedContext | null
  /**
   * The idea being edited: a draft, or (S3-005) an idea a reviewer sent back
   * with `CHANGES_REQUESTED`. `null` means "create a new one".
   */
  idea?: Idea | null
  /**
   * The idea was saved. `failedUploads` names any supporting document that
   * could not be attached to a newly created idea - the idea itself was
   * saved, so the caller should say so and point at what is missing.
   */
  onSaved: (idea: Idea, failedUploads?: string[]) => void
  onCancel?: () => void
  /**
   * Offer "Submit for review" next to "Save draft" when filing a new idea:
   * the idea is created, its documents attached, and then submitted in one
   * go. Ignored while editing, where submitting stays the list's own action.
   */
  onSubmitted?: (outcome: SubmitOutcome) => void
}

/**
 * What came of "Submit for review". Creating and submitting are separate
 * requests, so the second can fail after the first succeeded: the idea then
 * exists as a draft, and the caller must say so rather than report a
 * submission. `failedUploads` is as for `onSaved`.
 */
export type SubmitOutcome =
  | { submitted: true; idea: Idea; failedUploads?: string[] }
  | { submitted: false; idea: Idea; message: string; failedUploads?: string[] }

/**
 * Who the idea belongs to, decided by the caller.
 *
 * `context` and the tenant it names travel together because the server needs
 * both: an individual filing names neither, a team filing names a team, and an
 * organization filing names an organization. `ownerName` is for the banner and
 * is never sent.
 */
export interface LockedContext {
  context: SubmissionContext
  teamId?: string | null
  organizationId?: string | null
  /** For the banner. Falls back to a neutral phrase when a caller has no name. */
  ownerName?: string
}

const CONTEXT_TITLES: Record<SubmissionContext, string> = {
  INDIVIDUAL: 'Individual Idea',
  TEAM: 'Team Idea',
  ORGANIZATION: 'Organization Idea',
}

/**
 * "Creating a Team Idea for Automation Team" - on every step, not just the one
 * where ownership is chosen.
 *
 * The single most repeated sentence in this feature's requirements, and the
 * reason it is a banner rather than a field: the intake form is eight steps
 * long, and by the last of them a reader who chose their team three screens ago
 * has no way to tell whether the question still stands. A banner that is on
 * every step costs one line and removes the doubt entirely.
 */
export function IdeaContextBanner({ context }: { context: LockedContext }) {
  const title = CONTEXT_TITLES[context.context]
  const owner =
    context.context === 'INDIVIDUAL'
      ? 'yourself'
      : context.ownerName && context.ownerName !== ''
        ? context.ownerName
        : context.context === 'TEAM'
          ? 'your team'
          : 'your organization'

  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-sm text-brand-900">
      <span className="font-semibold">Creating {aOrAn(title)}</span>
      <span className="text-brand-800">for</span>
      <span className="font-semibold">{owner}</span>
    </p>
  )
}

function aOrAn(title: string): string {
  return `${/^[AEIOU]/.test(title) ? 'an' : 'a'} ${title}`
}

/** A list of everyday examples under a question. */
function Examples({ items }: { items: readonly string[] }) {
  return (
    <>
      For example:
      <ul className="mt-1 list-disc space-y-0.5 pl-5">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </>
  )
}

export function IdeaForm({
  organizationId,
  lockedContext = null,
  idea = null,
  onSaved,
  onCancel,
  onSubmitted,
}: IdeaFormProps) {
  const isEditing = idea !== null
  // A pre-answered ownership question closes the controls that would ask it
  // again. Editing never closes them on this account: an existing idea's owner
  // comes from the row, not from a choice.
  const contextLocked = !isEditing && lockedContext !== null
  const canSubmitHere = !isEditing && onSubmitted !== undefined
  // Sent back by a reviewer (S3-005): the same form, answering the feedback.
  const isRevising = idea?.status === 'CHANGES_REQUESTED'

  const [values, setValues] = useState<FormValues>(() =>
    initialValues(idea, lockedContext ?? undefined),
  )
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  // How each held file's upload is going, by position, while the form attaches them.
  const [uploadStatuses, setUploadStatuses] = useState<UploadStatus[]>([])
  const [step, setStep] = useState(0)
  const [categories, setCategories] = useState<IdeaCategory[]>([])
  const [errors, setErrors] = useState<FieldErrors>({})
  // Which button was last pressed: live validation checks the same rules.
  const [attempted, setAttempted] = useState<SaveMode | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [pending, setPending] = useState<SaveMode | null>(null)
  const [progress, setProgress] = useState<string | null>(null)
  const isSubmitting = pending !== null
  const [isLoadingCategories, setIsLoadingCategories] = useState(true)
  const [categoriesFailed, setCategoriesFailed] = useState(false)

  const categoryIdForLabel = useId()
  const teamIdForLabel = useId()
  const stepHeadingId = useId()
  // Only the `TEAM` context can name a team, so this is the only step that
  // needs the roster; loading it here rather than at mount keeps a form that
  // never offers the choice from ever asking.
  const { teams } = useMyTeams()
  const stepHeadingRef = useRef<HTMLHeadingElement>(null)

  /*
    The three ways to file, with the two that need a tenant marked unavailable
    when the caller has none. Computed rather than written out per context
    because "can this person choose this" is a fact about the session, not a
    property of the context: the same three options are offered to everybody,
    and the two that could not be filed are drawn disabled with the reason.
  */
  const contextOptions = useMemo(
    () =>
      [
        {
          value: 'INDIVIDUAL' as const,
          label: 'Individual level',
          unavailable: false,
          unavailableReason: '',
        },
        {
          value: 'TEAM' as const,
          label: 'Team level',
          unavailable: teams.length === 0,
          unavailableReason: 'You are not in a team yet.',
        },
        {
          value: 'ORGANIZATION' as const,
          label: 'Organization level',
          unavailable: organizationId === null,
          unavailableReason: 'You are not in an organization yet.',
        },
      ] as const,
    [teams.length, organizationId],
  )

  // Focus follows a step change the author made, never the first render: a
  // page that grabs focus on load is a page that fights its reader. Holds the
  // step whose heading should take focus once it is on screen.
  const focusStep = useRef<number | null>(null)

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
        // leaves a usable form that simply cannot classify the idea yet - but
        // it is said, because submitting now needs a category.
        if (!cancelled) {
          setCategories([])
          setCategoriesFailed(true)
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingCategories(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (focusStep.current === step) {
      focusStep.current = null
      stepHeadingRef.current?.focus()
    }
  }, [step])

  function goTo(next: number) {
    const target = Math.min(Math.max(next, 0), LAST_STEP)
    focusStep.current = target
    setStep(target)
  }

  function setField<K extends keyof FormValues>(field: K, value: FormValues[K]) {
    const next = { ...values, [field]: value }
    setValues(next)
    if (attempted) setErrors(validate(next, attempted))
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    // Enter moves on rather than saving: "Next" is the form's submit button,
    // so a stray Enter in a one-line answer never files a half-written idea.
    event.preventDefault()
    if (step < LAST_STEP) goTo(step + 1)
  }

  function showErrors(nextErrors: FieldErrors) {
    setErrors(nextErrors)
    const errorStep = firstStepWithError(nextErrors)
    if (errorStep !== null && errorStep !== step) goTo(errorStep)
  }

  async function save(mode: SaveMode) {
    setAttempted(mode)
    setFormError(null)

    const nextErrors = validate(values, mode)
    if (hasErrors(nextErrors)) {
      showErrors(nextErrors)
      return
    }
    setErrors({})

    setPending(mode)
    try {
      const input = toInput(values)
      // The tenant is only needed to *create*; an update writes content to an
      // idea that already has one, so there is nothing to resolve in that case.
      let result: IdeaMutationResult
      if (isEditing) {
        result = await updateIdeaRequest(idea.id, input)
      } else {
        // A new idea cannot be filed without a tenant, so there is no way to
        // send this request yet. `validate` stops a *submission* for it; a
        // draft still has to be saveable, so it is refused as a whole-form
        // problem here rather than silently filed for the wrong tenant.
        // A pre-chosen organization outranks the header's switcher: the reader
        // said which organization this idea is for, and the header is a
        // workspace preference that happens to also name an organization.
        const target = toTarget(values, lockedContext?.organizationId ?? organizationId)
        if (target === null) {
          setFormError('Choose who is filing this before saving it.')
          return
        }
        result = await createIdeaRequest(target, input)
      }

      if (result.success && result.idea) {
        const failedUploads = isEditing ? [] : await uploadPendingFiles(result.idea)
        if (mode === 'submit' && onSubmitted) {
          const outcome = await submitCreated(result.idea)
          onSubmitted(failedUploads.length > 0 ? { ...outcome, failedUploads } : outcome)
        } else if (failedUploads.length > 0) {
          onSaved(result.idea, failedUploads)
        } else {
          onSaved(result.idea)
        }
        return
      }

      // A `field` failure belongs next to the input that caused it; anything
      // else is a whole-form problem and goes at the top.
      const field = fieldFromServer(result.field)
      if (field) showErrors({ [field]: result.message })
      else setFormError(result.message)
    } catch {
      // The request never reached a decision. Deliberately not the backend's
      // wording: it never answered.
      setFormError('We could not reach the server. Please try again.')
    } finally {
      setPending(null)
      setProgress(null)
    }
  }

  /**
   * Attach the documents chosen on the last step to the idea just created,
   * through the ordinary upload endpoint - so every server-side check applies
   * exactly as it does anywhere else. One at a time, and never throws: the
   * idea already exists, so a refused or lost upload is reported by name
   * rather than failing the save.
   */
  async function uploadPendingFiles(created: Idea): Promise<string[]> {
    const failed: string[] = []
    const statuses: UploadStatus[] = pendingFiles.map(() => 'ready')
    const report = (index: number, status: UploadStatus) => {
      statuses[index] = status
      setUploadStatuses([...statuses])
    }
    for (const [index, file] of pendingFiles.entries()) {
      setProgress(`Attaching file ${index + 1} of ${pendingFiles.length}: ${file.name}`)
      report(index, 'uploading')
      try {
        const result = await uploadAttachmentRequest(created.id, file)
        report(index, result.success ? 'done' : 'failed')
        if (!result.success) failed.push(file.name)
      } catch {
        report(index, 'failed')
        failed.push(file.name)
      }
    }
    return failed
  }

  /**
   * The second half of "Submit for review". Never throws: the draft already
   * exists, so staying on this form to retry would file it a second time. A
   * refusal or a lost connection is handed back as an unsubmitted draft.
   */
  async function submitCreated(created: Idea): Promise<SubmitOutcome> {
    try {
      const result = await submitIdeaRequest(created.id)
      if (result.success && result.idea) return { submitted: true, idea: result.idea }
      return { submitted: false, idea: created, message: result.message }
    } catch {
      return {
        submitted: false,
        idea: created,
        message: 'We could not reach the server to submit it.',
      }
    }
  }

  const stepAnswered = STEPS.map(
    (candidate, index) =>
      stepHasAnswers(values, candidate) || (index === LAST_STEP && pendingFiles.length > 0),
  )
  const stepStates: StepState[] = STEPS.map((candidate, index) => {
    if (index === step) return 'current'
    if (candidate.fields.some((field) => errors[field])) return 'error'
    return stepAnswered[index] ? 'answered' : 'untouched'
  })

  const current = STEPS[step]
  const disabled = isSubmitting

  function renderStep(): ReactNode {
    switch (step) {
      case 0:
        return (
          <>
            <TextQuestion
              label="What problem would you like to solve?"
              help="Give your problem a short and clear name."
              example="Students wait too long to receive hostel allocation"
              value={values.title}
              onChange={(value) => setField('title', value)}
              error={errors.title}
              required
              large
              disabled={disabled}
              maxLength={MAX_TITLE_LENGTH}
            />
            <TextQuestion
              label="Tell us about the problem"
              help="Describe what is happening, what makes it difficult, and why you would like it to improve. Explain it in your own words — you do not need to use technical language."
              value={values.description}
              onChange={(value) => setField('description', value)}
              error={errors.description}
              required
              large
              rows={8}
              disabled={disabled}
              maxLength={STORY_LIMITS.longAnswer}
              note={
                isRevising
                  ? `Your revised description must contain at least ${MIN_DESCRIPTION_LENGTH} characters.`
                  : `A draft can be incomplete, but submitting needs at least ${MIN_DESCRIPTION_LENGTH} characters.`
              }
            />
          </>
        )
      case 1:
        return (
          <>
            <TextQuestion
              label="How do you currently handle this?"
              help="Tell us what normally happens from the beginning to the end. Describe the steps in your own words."
              example="Students fill out a form, submit it to the office, staff check the information, and then the information is entered into another document before the student is informed."
              value={values.currentProcess}
              onChange={(value) => setField('currentProcess', value)}
              error={errors.currentProcess}
              rows={6}
              disabled={disabled}
              maxLength={STORY_LIMITS.longAnswer}
            />
            <CheckboxQuestion
              legend="What do you currently use to handle this work?"
              help="Choose all that apply."
              options={CURRENT_TOOL_OPTIONS}
              value={values.currentTools}
              onChange={(value) => setField('currentTools', value)}
              error={errors.currentTools}
              disabled={disabled}
            />
            {values.currentTools.includes('OTHER') && (
              <TextQuestion
                label="What else do you use?"
                value={values.currentToolsOther}
                onChange={(value) => setField('currentToolsOther', value)}
                error={errors.currentToolsOther}
                disabled={disabled}
                maxLength={STORY_LIMITS.currentToolsOther}
              />
            )}
          </>
        )
      case 2:
        return (
          <>
            <TextQuestion
              label="Who is usually responsible for doing this?"
              help="For example, staff members, teachers, students, managers, customers, administrators, or other people."
              value={values.performedBy}
              onChange={(value) => setField('performedBy', value)}
              error={errors.performedBy}
              disabled={disabled}
              maxLength={STORY_LIMITS.shortAnswer}
            />
            <TextQuestion
              label="Who is affected when this problem happens?"
              value={values.affectedPeople}
              onChange={(value) => setField('affectedPeople', value)}
              error={errors.affectedPeople}
              disabled={disabled}
              maxLength={STORY_LIMITS.shortAnswer}
            />
            <RadioQuestion
              legend="How often does this happen?"
              options={FREQUENCY_OPTIONS}
              value={values.frequency}
              onChange={(value) => setField('frequency', value)}
              error={errors.frequency}
              disabled={disabled}
            />
            <div>
              <TextQuestion
                label="Approximately how much time does this work take?"
                help="A rough answer is fine."
                value={values.timeRequired}
                onChange={(value) => setField('timeRequired', value)}
                error={errors.timeRequired}
                disabled={disabled}
                maxLength={STORY_LIMITS.timeRequired}
              />
              <Suggestions
                label="Common answers for how much time it takes"
                suggestions={TIME_SUGGESTIONS}
                onPick={(value) => setField('timeRequired', value)}
                disabled={disabled}
              />
            </div>
            <TextQuestion
              label="Approximately how many people are involved in this process?"
              help="A rough number is fine."
              value={values.peopleInvolved}
              onChange={(value) => setField('peopleInvolved', value)}
              error={errors.peopleInvolved}
              disabled={disabled}
              inputMode="numeric"
              maxLength={7}
            />
            <CheckboxQuestion
              legend="What happens because of the problem?"
              help="Choose all that apply."
              options={IMPACT_OPTIONS}
              value={values.impacts}
              onChange={(value) => setField('impacts', value)}
              error={errors.impacts}
              disabled={disabled}
            />
            <TextQuestion
              label="Tell us anything else that happens because of this problem."
              value={values.impactDetails}
              onChange={(value) => setField('impactDetails', value)}
              error={errors.impactDetails}
              rows={4}
              disabled={disabled}
              maxLength={STORY_LIMITS.longAnswer}
            />
          </>
        )
      case 3:
        return (
          <>
            <TextQuestion
              label="What would you like to be different?"
              help="Tell us what you would like to become easier, faster, clearer, or more reliable."
              value={values.improvementGoal}
              onChange={(value) => setField('improvementGoal', value)}
              error={errors.improvementGoal}
              rows={4}
              disabled={disabled}
              maxLength={STORY_LIMITS.longAnswer}
            />
            <TextQuestion
              label="If the problem were solved, what would you like to happen?"
              example="I would like students to submit their information once and receive their room allocation without having to visit the office several times."
              value={values.desiredOutcome}
              onChange={(value) => setField('desiredOutcome', value)}
              error={errors.desiredOutcome}
              rows={4}
              disabled={disabled}
              maxLength={STORY_LIMITS.longAnswer}
            />
            <TextQuestion
              label="What should people be able to do more easily after this problem is solved?"
              value={values.easierForPeople}
              onChange={(value) => setField('easierForPeople', value)}
              error={errors.easierForPeople}
              rows={3}
              disabled={disabled}
              maxLength={STORY_LIMITS.longAnswer}
            />
          </>
        )
      case 4:
        return (
          <TextQuestion
            label="How would you know that this problem has been solved?"
            help={
              <>
                What would be better after the problem is solved?{' '}
                <Examples
                  items={[
                    'People should spend less time doing this work.',
                    'Customers should receive a response faster.',
                  ]}
                />
              </>
            }
            value={values.expectedBenefit}
            onChange={(value) => setField('expectedBenefit', value)}
            error={errors.expectedBenefit}
            rows={5}
            disabled={disabled}
            maxLength={STORY_LIMITS.longAnswer}
          />
        )
      case 5:
        return (
          <TextQuestion
            label="Tell us about anything that should be considered when trying to improve this process."
            help={
              <Examples
                items={['Some information is private', 'A manager must approve something']}
              />
            }
            value={values.importantConsiderations}
            onChange={(value) => setField('importantConsiderations', value)}
            error={errors.importantConsiderations}
            rows={5}
            disabled={disabled}
            maxLength={STORY_LIMITS.longAnswer}
          />
        )
      case 6:
        return (
          <>
            {/*
              The context first, because it decides two things below it: which
              visibilities can be offered at all, and what the idea's next step
              will be. Fixed while editing - an idea's tenant is settled when it
              is created, and `updateIdea` writes no context.
            */}
            <fieldset disabled={disabled || isRevising || contextLocked}>
              <legend className={labelClasses}>Who is putting this forward?</legend>
              <div className="grid gap-2 @xl:grid-cols-3">
                {contextOptions.map((option) => (
                  <label key={option.value} className={choiceClasses}>
                    <input
                      type="radio"
                      name="submission-context"
                      value={option.value}
                      checked={values.submissionContext === option.value}
                      disabled={disabled || isRevising || contextLocked || option.unavailable}
                      onChange={() => setField('submissionContext', option.value)}
                      className="h-4 w-4 shrink-0 accent-brand-600"
                    />
                    <span>
                      {option.label}
                      {option.unavailable && (
                        <span className="block text-xs font-normal text-gray-500">
                          {option.unavailableReason}
                        </span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
              <p className="mt-1.5 text-sm leading-6 text-gray-500">
                {contextDescription(values.submissionContext)}
              </p>
              {isRevising && (
                <p className="mt-1.5 text-sm text-gray-500">
                  Who is filing this is fixed now that it has been submitted.
                </p>
              )}
              {contextLocked && (
                <p className="mt-1.5 text-sm text-gray-500">
                  You chose this before you started writing, so it is not a question here.
                </p>
              )}
            </fieldset>

            {values.submissionContext === 'TEAM' && (
              <div>
                <label className={labelClasses} htmlFor={teamIdForLabel}>
                  Which team?
                </label>
                {teams.length === 0 ? (
                  <p className="text-sm leading-6 text-gray-500">
                    You are not in a team yet. Create one from your workspace, or file this on your
                    own instead.
                  </p>
                ) : (
                  <select
                    id={teamIdForLabel}
                    className={`${inputClasses(Boolean(errors.teamId))} w-full`}
                    value={values.teamId}
                    disabled={disabled || isRevising || contextLocked}
                    onChange={(event) => setField('teamId', event.target.value)}
                  >
                    <option value="">Choose a team</option>
                    {teams.map((team) => (
                      <option key={team.id} value={team.id}>
                        {team.name}
                      </option>
                    ))}
                  </select>
                )}
                {errors.teamId && <p className={fieldErrorClasses}>{errors.teamId}</p>}
              </div>
            )}

            <div>
              <label className={labelClasses} htmlFor={categoryIdForLabel}>
                Category
              </label>
              <p className="mb-2 text-sm leading-6 text-gray-500">
                Choose the area that best describes your problem.
              </p>
              <select
                id={categoryIdForLabel}
                className={`${inputClasses(Boolean(errors.categoryId))} w-full`}
                value={values.categoryId}
                disabled={disabled || isLoadingCategories}
                onChange={(event) => setField('categoryId', event.target.value)}
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
                control honest, and the saved payload is unaffected either way.
              */}
                {isLoadingCategories && values.categoryId && idea?.category && (
                  <option value={values.categoryId}>{idea.category.name}</option>
                )}
                {categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </select>
              {errors.categoryId ? (
                <p className={fieldErrorClasses}>{errors.categoryId}</p>
              ) : categoriesFailed ? (
                <p role="alert" className="mt-1.5 text-sm text-amber-700">
                  Categories could not be loaded. You can still save a draft; reload the page to
                  choose a category before submitting.
                </p>
              ) : !isLoadingCategories && categories.length === 0 ? (
                <p className="mt-1.5 text-sm text-amber-700">
                  No categories are available yet. You can still save a draft; a category is needed
                  before the idea can be submitted.
                </p>
              ) : (
                <p className="mt-1.5 text-sm text-gray-500">
                  {isRevising
                    ? 'Category remains optional while revising, but it is required to submit.'
                    : 'Optional while drafting; you will need one to submit.'}
                </p>
              )}
            </div>

            {/* No audience picker: who can see an idea follows the level it is filed
                at, so this says what that is rather than asking. */}
            <section
              aria-label="Who will see this idea"
              className="rounded-lg border border-gray-200 bg-gray-50 p-4"
            >
              <p className={labelClasses}>Who will see this idea</p>
              <p className="text-sm leading-6 text-gray-600">
                {audienceDescription(values.submissionContext)}
              </p>
              <p className="mt-2 text-sm leading-6 text-gray-500">
                While it is a draft, only you can see it.
              </p>
            </section>
          </>
        )
      default:
        return (
          <div>
            <p className={labelClasses}>
              Do you have anything that can help us understand the problem?
            </p>
            <p className="mb-4 text-sm leading-6 text-gray-500">
              You can upload documents, screenshots, photos, spreadsheets, reports, forms, or other
              files that show or explain the problem - existing forms, sample documents, process
              diagrams, anything that helps.
            </p>
            {isEditing && idea ? (
              <IdeaEvidenceUploader idea={idea} />
            ) : (
              <PendingFilesPicker
                files={pendingFiles}
                onChange={setPendingFiles}
                statuses={uploadStatuses}
                disabled={disabled}
              />
            )}
          </div>
        )
    }
  }

  const saveLabel = isRevising ? 'Save changes' : 'Save draft'

  return (
    <form
      // `lg:flex-1` + `lg:flex-col`: the form fills the page wrapper, which is
      // exactly as tall as the space under the app chrome (a container query
      // cannot style the element that declares the container, hence `lg:`), so the page never scrolls here. Everything that
      // does not fit goes into the questions pane instead - see the grid below.
      // Below `@3xl` none of this applies and the page scrolls as it always did.
      className="@container overflow-hidden rounded-xl border border-gray-200 bg-white p-5 pt-0 shadow-sm sm:p-6 sm:pt-0 lg:flex lg:min-h-0 lg:flex-1 lg:flex-col"

      onSubmit={handleSubmit}
      noValidate
    >
      {isRevising && idea && (
        // The feedback being answered, above the questions it is about. Read
        // from the server's history, unchanged by anything saved here.
        <section
          aria-label="Reviewer feedback"
          className="mt-4 shrink-0 rounded-lg border border-amber-200 bg-amber-50 p-3"
        >
          <h3 className="text-sm font-semibold text-amber-900">Reviewer feedback</h3>
          <div className="mt-2">
            <ReviewHistory ideaId={idea.id} viewerId={null} />
          </div>
        </section>
      )}

      {formError && (
        <p
          role="alert"
          className="mt-4 shrink-0 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700"
        >
          {formError}
        </p>
      )}

      {/*
        The step list and the questions are two panes, divided by a full-height
        rule when side by side and a full-width one when stacked, so the menu
        never reads as part of the form.
      */}
      <div className="grid @3xl:min-h-0 @3xl:flex-1 @3xl:grid-cols-[21rem_minmax(0,1fr)]">
        <div
          // Bled to the card's edge (its padding is p-5, and p-6 at the widths
          // where the panes sit side by side) so the pane reads as a pane.
          // `overflow-clip` rather than `overflow-hidden` for the glow, and
          // `min-h-0` so this grid item may be shorter than its content without
          // pushing the card past the height it is capped at.
          className="relative isolate -mx-5 overflow-clip border-b-2 border-gray-300 bg-linear-to-br from-brand-50 via-slate-50 to-sky-50 px-4 py-5 sm:-mx-6 sm:px-5 @3xl:mx-0 @3xl:-ml-6 @3xl:min-h-0 @3xl:border-r-2 @3xl:border-b-0 @3xl:py-5 @3xl:pr-5 @3xl:pl-5"
        >
          {/* Soft colour behind the glass, so the blur has something to blur. */}
          <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
            <div className="absolute -top-16 -left-16 h-56 w-56 rounded-full bg-brand-200/70 blur-3xl" />
            <div className="absolute top-1/3 -right-20 h-64 w-64 rounded-full bg-sky-200/70 blur-3xl" />
            <div className="absolute -bottom-16 left-1/4 h-56 w-56 rounded-full bg-emerald-100/80 blur-3xl" />
          </div>
          {/*
            No `sticky` and no scrolling, deliberately. The page does not scroll
            here at all - the form is capped to the space under the chrome - so a
            sticky sidebar had nothing to stick to and could only be taller than
            the space it was given. It is a flex column so the step list below
            takes the height that is left over.
          */}
          <div className="flex flex-col rounded-2xl border border-white/80 bg-white/45 p-3 shadow-lg shadow-slate-900/5 ring-1 ring-slate-900/5 backdrop-blur-xl">
            <StepProgress
              steps={STEPS}
              current={step}
              states={stepStates}
              answered={stepAnswered}
              onSelect={goTo}
              disabled={disabled}
            />
          </div>
        </div>

        {/*
          The one scrollable region on this screen, and the only reason the form
          is capped above: the questions run to any length, the step list does
          not. `overscroll-contain` so reaching the end of the questions does not
          then start scrolling the page behind them.
        */}
        <section
          aria-labelledby={stepHeadingId}
          className="min-w-0 pt-6 @3xl:min-h-0 @3xl:overflow-y-auto @3xl:overscroll-contain @3xl:pt-5 @3xl:pr-2 @3xl:pl-10"
        >
          {/*
            The form's name and what it is for, at the top of the pane that
            scrolls rather than above the two panes. It was a fixed band across
            the whole card, and a fixed band is height the step list cannot have:
            eight steps have to be on screen at once without scrolling, and every
            pixel above the rule was a pixel off the bottom of the list. Here it
            costs the step list nothing and scrolls away with the questions it
            introduces.
          */}
          <div className="mb-6 border-b border-gray-200 pb-5">
            <h2 className="text-base font-semibold text-gray-900">
              {isRevising ? 'Revise this idea' : isEditing ? 'Edit this draft' : 'Share a problem'}
            </h2>
            <p className="mt-1 text-sm leading-6 text-gray-600">
              {isRevising
                ? 'A reviewer asked for changes. Update your answers and save, then use Submit again on the idea to put it forward for the next review.'
                : isEditing
                  ? 'A draft can be saved half-finished. Submitting puts it forward for review; it does not change who can see it.'
                  : 'Tell us about a problem in your own words. Only the name is needed to save a draft, so you can stop at any step and come back later.'}
            </p>
          </div>
          <h3
            id={stepHeadingId}
            ref={stepHeadingRef}
            tabIndex={-1}
            className="text-lg font-semibold text-gray-900 focus:outline-none"
          >
            {current.title}
          </h3>
          <p className="mt-1 text-sm leading-6 text-gray-500">{current.hint}</p>
          <div className="mt-5 space-y-6">{renderStep()}</div>
        </section>
      </div>

      <p aria-live="polite" className="@3xl:shrink-0 mt-4 text-sm text-gray-600">
        {progress}
      </p>

      <div className="@3xl:shrink-0 mt-2 flex flex-wrap items-center justify-between gap-3 border-t-2 border-gray-300 pt-5">
        <div className="flex flex-wrap items-center gap-2">
          {step > 0 && (
            <button
              type="button"
              onClick={() => goTo(step - 1)}
              disabled={disabled}
              className={quietButtonClasses}
            >
              Back
            </button>
          )}
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              disabled={disabled}
              className={quietButtonClasses}
            >
              Cancel
            </button>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => void save('draft')}
            disabled={disabled}
            className={
              step === LAST_STEP && !canSubmitHere ? primaryButtonClasses : secondaryButtonClasses
            }
          >
            {pending === 'draft' && <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />}
            <span>{pending === 'draft' ? 'Saving…' : saveLabel}</span>
          </button>
          {step < LAST_STEP ? (
            <button type="submit" disabled={disabled} className={primaryButtonClasses}>
              Next
            </button>
          ) : (
            canSubmitHere && (
              <button
                type="button"
                onClick={() => void save('submit')}
                disabled={disabled}
                className={primaryButtonClasses}
              >
                {pending === 'submit' && (
                  <SpinnerIcon className="h-4 w-4 motion-safe:animate-spin" />
                )}
                <span>{pending === 'submit' ? 'Submitting…' : 'Submit for review'}</span>
              </button>
            )
          )}
        </div>
      </div>
    </form>
  )
}
