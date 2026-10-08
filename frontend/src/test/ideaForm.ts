import { fireEvent, screen } from '@testing-library/react'

/**
 * Driving the guided idea form (`IdeaForm`) from a test.
 *
 * The form asks its questions over several steps, so a test that wants the
 * category picker has to get to it the way an author would. These helpers
 * name the questions once, in the words the form uses, so a test reads as
 * the author's path through the form rather than as a list of selectors.
 */

// By role and accessible name: "Tell us about the problem" is also the first
// step's heading, which names the step's region, so a label query would find
// both. The required marker is `aria-hidden`, so it is not part of the name.
export function titleField(): HTMLElement {
  return screen.getByRole('textbox', { name: 'What problem would you like to solve?' })
}

export function descriptionField(): HTMLElement {
  return screen.getByRole('textbox', { name: 'Tell us about the problem' })
}

/** Fill the first step's two questions. */
export function fillProblem({
  title = 'A new idea',
  description = 'A description long enough.',
}: { title?: string; description?: string } = {}) {
  fireEvent.change(titleField(), { target: { value: title } })
  fireEvent.change(descriptionField(), { target: { value: description } })
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** The progress control's button for the step titled `title`. */
export function stepButton(title: string): HTMLElement {
  return screen.getByRole('button', { name: new RegExp(`^Step \\d+: ${escape(title)}`) })
}

/** Jump straight to the step titled `title`, as the progress list allows. */
export function goToStep(title: string) {
  fireEvent.click(stepButton(title))
}

/** The step that decides who is filing the idea and who may read it. */
export const CLASSIFY_STEP = 'At which level are you filing this?'
export const EVIDENCE_STEP = 'Supporting documents'

/**
 * The context picker's radio for one option, by its visible label.
 *
 * Separate from `screen.getByRole('radio', { name: /My organization/ })` because
 * "My organization" now names *two* controls on that step — the filing context
 * and the visibility — and the regex would find whichever came first.
 */
export function contextChoice(label: string): HTMLElement {
  return screen.getByRole('radio', { name: new RegExp(`^${label}`) })
}

/** The visibility picker's radio for one option. */
export function visibilityChoice(label: string): HTMLElement {
  return screen.getByRole('radio', { name: new RegExp(`^${label}`) })
}
