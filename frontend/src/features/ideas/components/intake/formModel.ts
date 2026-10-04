import {
  SELECTABLE_VISIBILITIES,
  type Idea,
  type IdeaCurrentTool,
  type IdeaDraftInput,
  type IdeaFrequency,
  type IdeaImpact,
  type IdeaVisibility,
  type SubmissionContext,
  type SubmissionTarget,
} from '../../api/ideasApi'
import { STORY_LIMITS } from '../../utils/problemStory'

/**
 * The intake form's data: its values, its steps, and its rules - kept apart
 * from the rendering so the "what must be filled in" logic reads in one
 * place.
 *
 * Validation is deliberately duplicated client-side even though the server
 * validates everything: the client copy gives immediate feedback, and the
 * server copy is the one that counts. The two are kept in step - the minimum
 * description length mirrors `ideas.services.MIN_DESCRIPTION_LENGTH`, and the
 * submission rules mirror `ideas.services._validate_for_submission`, with the
 * same wording.
 */

export const MIN_DESCRIPTION_LENGTH = 20
export const MAX_TITLE_LENGTH = 200

export type SaveMode = 'draft' | 'submit'

/** The reviewable visibilities; mirrors `ideas.services.REVIEWABLE_VISIBILITIES`. */
export const REVIEWABLE_VISIBILITIES: readonly IdeaVisibility[] = ['ORGANIZATION', 'PUBLIC']

export const PRIVATE_CANNOT_BE_REVIEWED =
  'A private idea cannot be reviewed. Share it with your organization or make it public before submitting.'

/**
 * The other half of that rule: an idea nobody's organization is behind is read
 * by platform reviewers, who are not in any organization of the author's, so
 * the only visibility that reaches them is `PUBLIC`. Mirrors
 * `ideas.services.DIRECT_CONTEXT_VISIBILITY_MESSAGE`.
 */
export const DIRECT_CONTEXT_CANNOT_BE_REVIEWED =
  'An idea you submit on your own, or for a team, is reviewed by the platform. Make it public so platform reviewers can read it before submitting.'

/**
 * The visibilities a submission can carry, per context.
 *
 * A *draft* may be private in any context - the author's own draft is nobody
 * else's business until they put it forward - so this narrows what the form
 * asks for on submit, not what it lets somebody save. The organization
 * context keeps `ORGANIZATION` because its reviewers are in the organization;
 * the two direct contexts have no organization to widen to.
 */
export function reviewableVisibilities(context: SubmissionContext): readonly IdeaVisibility[] {
  return context === 'ORGANIZATION' ? REVIEWABLE_VISIBILITIES : ['PUBLIC']
}

/** Everything the form edits, as the controls hold it. */
export interface FormValues {
  title: string
  description: string
  categoryId: string
  visibility: IdeaVisibility
  /**
   * Who is putting this forward. Not part of the idea *content*, which is why
   * `toInput` leaves it out: it names the tenant `createIdea` files the idea
   * for, and the server proves the caller belongs to it.
   */
  submissionContext: SubmissionContext
  /** Only meaningful for the `TEAM` context; `''` means "not chosen yet". */
  teamId: string
  currentProcess: string
  currentTools: IdeaCurrentTool[]
  currentToolsOther: string
  performedBy: string
  affectedPeople: string
  /** `''` means not answered. */
  frequency: IdeaFrequency | ''
  timeRequired: string
  /** As typed: a number input's value is text until it is sent. */
  peopleInvolved: string
  impacts: IdeaImpact[]
  impactDetails: string
  improvementGoal: string
  desiredOutcome: string
  easierForPeople: string
  expectedBenefit: string
  importantConsiderations: string
}

export type FieldName = keyof FormValues
export type FieldErrors = Partial<Record<FieldName, string>>

export function initialValues(idea: Idea | null): FormValues {
  return {
    title: idea?.title ?? '',
    description: idea?.description ?? '',
    categoryId: idea?.category?.id ?? '',
    visibility: idea?.visibility ?? 'PRIVATE',
    submissionContext: idea?.submissionContext ?? 'INDIVIDUAL',
    teamId: idea?.teamId ?? '',
    currentProcess: idea?.currentProcess ?? '',
    currentTools: idea?.currentTools ?? [],
    currentToolsOther: idea?.currentToolsOther ?? '',
    performedBy: idea?.performedBy ?? '',
    affectedPeople: idea?.affectedPeople ?? '',
    frequency: idea?.frequency ?? '',
    timeRequired: idea?.timeRequired ?? '',
    peopleInvolved: idea?.peopleInvolved == null ? '' : String(idea.peopleInvolved),
    impacts: idea?.impacts ?? [],
    impactDetails: idea?.impactDetails ?? '',
    improvementGoal: idea?.improvementGoal ?? '',
    desiredOutcome: idea?.desiredOutcome ?? '',
    easierForPeople: idea?.easierForPeople ?? '',
    expectedBenefit: idea?.expectedBenefit ?? '',
    importantConsiderations: idea?.importantConsiderations ?? '',
  }
}

/**
 * The visibilities the picker offers for a context.
 *
 * A draft may be private whatever its context - it is nobody else's business
 * until somebody puts it forward - so this is not the submission rule. What it
 * avoids is offering "My organization" to somebody filing on their own or for
 * a team, which names a tenant their idea does not belong to and which they
 * could never submit with anyway.
 */
export function visibilitiesForContext(
  context: SubmissionContext,
): ReadonlyArray<{ value: IdeaVisibility; label: string; hint: string }> {
  return context === 'ORGANIZATION'
    ? SELECTABLE_VISIBILITIES
    : SELECTABLE_VISIBILITIES.filter((option) => option.value !== 'ORGANIZATION')
}

/** The whole input, trimmed: an update writes every field, so every field is sent. */
export function toInput(values: FormValues): IdeaDraftInput {
  const people = values.peopleInvolved.trim()
  return {
    title: values.title.trim(),
    description: values.description.trim(),
    categoryId: values.categoryId || null,
    visibility: values.visibility,
    currentProcess: values.currentProcess.trim(),
    currentTools: values.currentTools,
    // Only meaningful alongside "Other"; dropped rather than sent orphaned.
    currentToolsOther: values.currentTools.includes('OTHER') ? values.currentToolsOther.trim() : '',
    performedBy: values.performedBy.trim(),
    affectedPeople: values.affectedPeople.trim(),
    frequency: values.frequency || null,
    timeRequired: values.timeRequired.trim(),
    peopleInvolved: people === '' ? null : Number(people),
    impacts: values.impacts,
    impactDetails: values.impactDetails.trim(),
    improvementGoal: values.improvementGoal.trim(),
    desiredOutcome: values.desiredOutcome.trim(),
    easierForPeople: values.easierForPeople.trim(),
    expectedBenefit: values.expectedBenefit.trim(),
    importantConsiderations: values.importantConsiderations.trim(),
  }
}

/**
 * The tenant a new idea is filed for, from the context the author chose.
 *
 * Separate from `toInput` because it is a different kind of value: this one
 * names *who is putting the idea forward*, and the server proves the caller
 * belongs to whatever it names. The three shapes are not independent choices -
 * an individual filing names nothing, a team filing names a team and no
 * organization, an organization filing names an organization and no team -
 * which is why the ids are dropped here rather than sent as nulls.
 *
 * `null` for a team that has not been chosen yet, because there is no
 * `SubmissionTarget` that could mean it; `validate` is what stops that from
 * reaching the server.
 */
export function toTarget(
  values: FormValues,
  activeOrganizationId: string | null,
): SubmissionTarget | null {
  if (values.submissionContext === 'INDIVIDUAL') return { context: 'INDIVIDUAL' }
  if (values.submissionContext === 'TEAM') {
    return values.teamId ? { context: 'TEAM', teamId: values.teamId } : null
  }
  return activeOrganizationId
    ? { context: 'ORGANIZATION', organizationId: activeOrganizationId }
    : null
}

/**
 * A draft needs a title and nothing else - drafts may be incomplete, which
 * is the server's rule too. Submitting also needs what the server's
 * submission rule asks for: a description long enough to understand, a
 * category, and a visibility whoever will review it can read. Every other
 * question is there to help, and none of them blocks anything.
 */
export function validate(values: FormValues, mode: SaveMode): FieldErrors {
  const errors: FieldErrors = {}
  if (!values.title.trim()) errors.title = 'Give your problem a short name.'

  const people = values.peopleInvolved.trim()
  if (people !== '') {
    const count = Number(people)
    if (!/^\d+$/.test(people) || count > STORY_LIMITS.maxPeopleInvolved) {
      errors.peopleInvolved = 'Enter a whole number, like 5.'
    }
  }

  if (mode === 'submit') {
    if (values.description.trim().length < MIN_DESCRIPTION_LENGTH) {
      errors.description = `Describe the problem in at least ${MIN_DESCRIPTION_LENGTH} characters before submitting.`
    }
    if (!values.categoryId) errors.categoryId = 'Choose a category before submitting this idea.'
    if (values.submissionContext === 'TEAM' && !values.teamId) {
      errors.teamId = 'Choose which team is putting this forward.'
    }
    // Which visibility a submission needs depends on who reviews it, so the
    // message does too: telling somebody filing alone that their idea must be
    // shared with "your organization" would name a tenant they do not have.
    if (!reviewableVisibilities(values.submissionContext).includes(values.visibility)) {
      errors.visibility =
        values.submissionContext === 'ORGANIZATION'
          ? PRIVATE_CANNOT_BE_REVIEWED
          : DIRECT_CONTEXT_CANNOT_BE_REVIEWED
    }
  }
  return errors
}

export function hasErrors(errors: FieldErrors): boolean {
  return Object.values(errors).some(Boolean)
}

/** One step of the guided flow. */
export interface Step {
  title: string
  hint: string
  fields: readonly FieldName[]
}

export const STEPS: readonly Step[] = [
  {
    title: 'Tell us about the problem',
    hint: 'Name it and describe it in your own words. You do not need any technical language.',
    fields: ['title', 'description'],
  },
  {
    title: 'How does this happen today?',
    hint: 'Walk us through what normally happens, and what you use to do it.',
    fields: ['currentProcess', 'currentTools', 'currentToolsOther'],
  },
  {
    title: 'Tell us about the impact',
    hint: 'Who does this work, who it affects, and what it costs them. Rough answers are fine.',
    fields: [
      'performedBy',
      'affectedPeople',
      'frequency',
      'timeRequired',
      'peopleInvolved',
      'impacts',
      'impactDetails',
    ],
  },
  {
    title: 'What would you like to improve?',
    hint: 'Describe the result you would like - not how it should be built.',
    fields: ['improvementGoal', 'desiredOutcome', 'easierForPeople'],
  },
  {
    title: 'How will you know the problem is solved?',
    hint: 'What would be better afterwards. No measurements needed.',
    fields: ['expectedBenefit'],
  },
  {
    title: 'Is there anything important we should know?',
    hint: 'Rules, approvals, private information, or anything else to keep in mind.',
    fields: ['importantConsiderations'],
  },
  {
    title: 'Who is filing this, and who can see it',
    hint: 'Whether this is yours alone, your team’s or your organization’s — and who should be able to read it.',
    fields: ['submissionContext', 'teamId', 'categoryId', 'visibility'],
  },
  {
    title: 'Supporting documents',
    hint: 'Optional: anything that shows or explains the problem.',
    fields: [],
  },
]

export const LAST_STEP = STEPS.length - 1

/** The first step holding one of `errors`, or `null`. */
export function firstStepWithError(errors: FieldErrors): number | null {
  const index = STEPS.findIndex((step) => step.fields.some((field) => errors[field]))
  return index === -1 ? null : index
}

/** Whether the author has answered anything on `step` yet. */
export function stepHasAnswers(values: FormValues, step: Step): boolean {
  return step.fields.some((field) => {
    // Neither is an *answer*: the context always has a value and the
    // visibility defaults to private, so counting them would mark this step
    // answered on a form nobody has touched.
    if (field === 'visibility' || field === 'submissionContext') return false
    const value = values[field]
    return Array.isArray(value) ? value.length > 0 : value.trim() !== ''
  })
}

/**
 * The form field a server refusal names. The service reports its own
 * (snake_case) field names; `category` is the one that differs in more than
 * case.
 */
export function fieldFromServer(field: string | null): FieldName | null {
  if (field === null) return null
  if (field === 'category') return 'categoryId'
  const camel = field.replace(/_([a-z])/g, (_, letter: string) => letter.toUpperCase())
  return camel in initialValues(null) ? (camel as FieldName) : null
}
