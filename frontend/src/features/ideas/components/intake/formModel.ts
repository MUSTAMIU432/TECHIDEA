import {
  type Idea,
  type IdeaCurrentTool,
  type IdeaDraftInput,
  type IdeaFrequency,
  type IdeaImpact,
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

/** Everything the form edits, as the controls hold it. */
export interface FormValues {
  title: string
  description: string
  categoryId: string
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

export function initialValues(
  idea: Idea | null,
  /**
   * An ownership the reader already chose before the form opened - from a
   * team's page, an organization's page, or the "Where does this idea belong?"
   * dialog. Seeded here rather than defaulted later so the very first render
   * shows the chosen context, not a form that flickers from "Just me" to the
   * answer.
   */
  locked?: { context: SubmissionContext; teamId?: string | null },
): FormValues {
  return {
    title: idea?.title ?? '',
    description: idea?.description ?? '',
    categoryId: idea?.category?.id ?? '',
    submissionContext: idea?.submissionContext ?? locked?.context ?? 'INDIVIDUAL',
    teamId: idea?.teamId ?? locked?.teamId ?? '',
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

/** The whole input, trimmed: an update writes every field, so every field is sent. */
export function toInput(values: FormValues): IdeaDraftInput {
  const people = values.peopleInvolved.trim()
  return {
    title: values.title.trim(),
    description: values.description.trim(),
    categoryId: values.categoryId || null,
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
 * category. Every other
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
    title: 'At which level are you filing this?',
    hint: 'Individual level, team level or organization level. It decides who can see the idea and who checks it before the platform does.',
    fields: ['submissionContext', 'teamId', 'categoryId'],
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
    // The level always has a value, so counting it would mark this step
    // answered on a form nobody has touched.
    if (field === 'submissionContext') return false
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
