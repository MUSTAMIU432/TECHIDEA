import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { categoriesRequest, createIdeaRequest, updateIdeaRequest } from '../api/ideasApi'
import { IdeaForm, MIN_DESCRIPTION_LENGTH } from './IdeaForm'
import type { Idea, IdeaMutationResult } from '../api/ideasApi'
import { makeIdea, makeTeam } from '../../../test/idea'
import {
  CLASSIFY_STEP,
  EVIDENCE_STEP,
  contextChoice,
  descriptionField,
  fillProblem,
  goToStep,
  stepButton,
  titleField,
} from '../../../test/ideaForm'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
}))

// The form asks for the author's teams only so the `TEAM` context can name one.
// Stubbed to empty, which is the session these tests describe: somebody with an
// organization and no team. `TEAMS` overrides it for the one test that is about
// having a team.
vi.mock('../../teams/api/teamsApi', async (importOriginal) => ({
  ...(await importOriginal()),
  teamsRequest: vi.fn(async () => []),
}))

const { teamsRequest } = await import('../../teams/api/teamsApi')
const teamsMock = vi.mocked(teamsRequest)

const categoriesMock = vi.mocked(categoriesRequest)
const createMock = vi.mocked(createIdeaRequest)
const updateMock = vi.mocked(updateIdeaRequest)

const CATEGORIES = [
  { id: '10', name: 'Customer support', slug: 'customer-support', description: '' },
  { id: '11', name: 'Finance', slug: 'finance', description: '' },
]

const DRAFT: Idea = makeIdea({
  title: 'An existing draft',
  description: 'A description long enough to be usable.',
  category: CATEGORIES[0],
  availableTransitions: [],
})

const SAVED: IdeaMutationResult = { success: true, message: 'ok', field: null, idea: DRAFT }

function renderForm(props: Partial<React.ComponentProps<typeof IdeaForm>> = {}) {
  const onSaved = props.onSaved ?? vi.fn()
  render(<IdeaForm organizationId="3" onSaved={onSaved} {...props} />)
  return { onSaved }
}

function saveDraft() {
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))
}

function next() {
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
}

/** The current step's own heading: the last level-3 heading on the page. */
function stepHeading() {
  return screen.getAllByRole('heading', { level: 3 }).at(-1)
}

/**
 * The tenant a new idea is filed for, per context.
 *
 * The form's default context is `INDIVIDUAL`, which names no tenant at all -
 * that is what makes an organization optional - so a test that never touches
 * the picker sends these. A test about the *content* of the request should say
 * which context it means rather than depend on the default.
 */
const INDIVIDUAL_TARGET = { context: 'INDIVIDUAL' }
const ORGANIZATION_TARGET = { context: 'ORGANIZATION', organizationId: '3' }

describe('IdeaForm', () => {
  afterEach(() => {
    categoriesMock.mockReset()
    createMock.mockReset()
    updateMock.mockReset()
  })

  // --- the guided flow --------------------------------------------------------

  it('starts with the problem, and asks for it in plain words', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    expect(screen.getByRole('heading', { name: 'Share a problem' })).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 8')).toBeInTheDocument()
    expect(stepHeading()).toHaveTextContent('Tell us about the problem')
    expect(titleField()).toBeInTheDocument()
    expect(descriptionField()).toBeInTheDocument()
    expect(screen.getByText('Give your problem a short and clear name.')).toBeInTheDocument()
    expect(screen.getByText(/you do not need to use technical language/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeInTheDocument()
    // Nothing to go back to yet.
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
    await waitFor(() => expect(categoriesMock).toHaveBeenCalledOnce())
  })

  it('lists all eight steps in order and marks the current one', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    const progress = screen.getByRole('navigation', { name: 'Form progress' })
    const steps = within(progress).getAllByRole('button')
    expect(steps.map((step) => step.getAttribute('aria-label'))).toEqual([
      'Step 1: Tell us about the problem',
      'Step 2: How does this happen today?',
      'Step 3: Tell us about the impact',
      'Step 4: What would you like to improve?',
      'Step 5: How will you know the problem is solved?',
      'Step 6: Is there anything important we should know?',
      'Step 7: Who is filing this, and who can see it',
      'Step 8: Supporting documents',
    ])
    expect(steps[0]).toHaveAttribute('aria-current', 'step')
  })

  it('moves forward and back, and focuses each step as it arrives', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    next()
    expect(screen.getByText('Step 2 of 8')).toBeInTheDocument()
    expect(stepHeading()).toHaveTextContent('How does this happen today?')
    expect(stepHeading()).toHaveFocus()

    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(stepHeading()).toHaveTextContent('Tell us about the problem')
    expect(stepHeading()).toHaveFocus()
  })

  it('keeps every answer when moving between steps', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    fillProblem({ title: 'Kept title' })

    next()
    fireEvent.change(screen.getByLabelText('How do you currently handle this?'), {
      target: { value: 'Paper forms go to the office.' },
    })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Excel' }))
    goToStep(CLASSIFY_STEP)
    goToStep('How does this happen today?')

    expect(screen.getByLabelText('How do you currently handle this?')).toHaveValue(
      'Paper forms go to the office.',
    )
    expect(screen.getByRole('checkbox', { name: 'Excel' })).toBeChecked()
    goToStep('Tell us about the problem')
    expect(titleField()).toHaveValue('Kept title')
  })

  it('marks a step as started once something on it is answered', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    fillProblem()

    next()

    expect(stepButton('Tell us about the problem')).toHaveAccessibleName(
      'Step 1: Tell us about the problem (started)',
    )
  })

  it('moves on, rather than saving, when Enter submits the form', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    fillProblem()

    fireEvent.submit(titleField())

    expect(stepHeading()).toHaveTextContent('How does this happen today?')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('never asks a technical question', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    const technical =
      /\b(API|APIs|database|backend|frontend|programming|framework|architecture|integrations?|authentication|deployment|hosting|infrastructure|technical requirements?)\b/i
    for (let step = 1; step <= 8; step++) {
      fireEvent.click(screen.getByRole('button', { name: new RegExp(`^Step ${step}:`) }))
      // "you do not need to use technical language" is reassurance, not a question.
      const text = (document.body.textContent ?? '').replace('technical language', '')
      expect(text).not.toMatch(technical)
    }
    await waitFor(() => expect(categoriesMock).toHaveBeenCalledOnce())
  })

  // --- the story questions ------------------------------------------------------

  it('asks what happens today, including what is used for it', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep('How does this happen today?')

    expect(screen.getByLabelText('How do you currently handle this?')).toBeInTheDocument()
    const tools = screen.getByRole('group', {
      name: 'What do you currently use to handle this work?',
    })
    for (const tool of ['Paper forms', 'Excel', 'Google Sheets', 'Email', 'WhatsApp', 'Other']) {
      expect(within(tools).getByRole('checkbox', { name: tool })).toBeInTheDocument()
    }
    // "Other" asks what, and only once it is chosen.
    expect(screen.queryByLabelText('What else do you use?')).not.toBeInTheDocument()
    fireEvent.click(within(tools).getByRole('checkbox', { name: 'Other' }))
    expect(screen.getByLabelText('What else do you use?')).toBeInTheDocument()
  })

  it('asks about the impact with simple options', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep('Tell us about the impact')

    expect(screen.getByLabelText('Who is usually responsible for doing this?')).toBeInTheDocument()
    expect(screen.getByLabelText('Who is affected when this problem happens?')).toBeInTheDocument()
    const frequency = screen.getByRole('group', { name: 'How often does this happen?' })
    expect(
      within(frequency)
        .getAllByRole('radio')
        .map((radio) => radio.parentElement?.textContent),
    ).toEqual([
      'Several times a day',
      'Daily',
      'Several times a week',
      'Weekly',
      'Monthly',
      'Occasionally',
      'Other',
    ])
    const impacts = screen.getByRole('group', { name: 'What happens because of the problem?' })
    expect(within(impacts).getByRole('checkbox', { name: 'Mistakes happen' })).toBeInTheDocument()
    expect(
      within(impacts).getByRole('checkbox', { name: 'It increases costs' }),
    ).toBeInTheDocument()
  })

  it('fills the time question from a suggestion, leaving it editable', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep('Tell us about the impact')

    fireEvent.click(screen.getByRole('button', { name: 'Several hours' }))

    const time = screen.getByLabelText(/^Approximately how much time does this work take\?/)
    expect(time).toHaveValue('Several hours')
    expect(time).toBeEnabled()
  })

  it('sends the whole story with the idea', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()

    goToStep('How does this happen today?')
    fireEvent.change(screen.getByLabelText('How do you currently handle this?'), {
      target: { value: '  Forms, then Excel.  ' },
    })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Excel' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Paper forms' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('What else do you use?'), {
      target: { value: 'A notice board' },
    })

    goToStep('Tell us about the impact')
    fireEvent.change(screen.getByLabelText('Who is usually responsible for doing this?'), {
      target: { value: 'Office staff' },
    })
    fireEvent.change(screen.getByLabelText('Who is affected when this problem happens?'), {
      target: { value: 'Students' },
    })
    fireEvent.click(screen.getByRole('radio', { name: 'Monthly' }))
    fireEvent.click(screen.getByRole('button', { name: 'Several days' }))
    fireEvent.change(
      screen.getByLabelText(/^Approximately how many people are involved in this process\?/),
      { target: { value: '12' } },
    )
    fireEvent.click(screen.getByRole('checkbox', { name: 'People have to wait' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Mistakes happen' }))
    fireEvent.change(
      screen.getByLabelText('Tell us anything else that happens because of this problem.'),
      { target: { value: 'Students come back twice.' } },
    )

    goToStep('What would you like to improve?')
    fireEvent.change(screen.getByLabelText('What would you like to be different?'), {
      target: { value: 'Faster.' },
    })
    fireEvent.change(
      screen.getByLabelText('If the problem were solved, what would you like to happen?'),
      { target: { value: 'Submit once.' } },
    )
    fireEvent.change(
      screen.getByLabelText(
        'What should people be able to do more easily after this problem is solved?',
      ),
      { target: { value: 'Check progress.' } },
    )

    goToStep('How will you know the problem is solved?')
    fireEvent.change(
      screen.getByLabelText('How would you know that this problem has been solved?'),
      { target: { value: 'No repeat visits.' } },
    )

    goToStep('Is there anything important we should know?')
    fireEvent.change(
      screen.getByLabelText(
        'Tell us about anything that should be considered when trying to improve this process.',
      ),
      { target: { value: 'Payment details are private.' } },
    )

    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(INDIVIDUAL_TARGET, {
        title: 'A new idea',
        description: 'A description long enough.',
        categoryId: null,
        visibility: 'PRIVATE',
        currentProcess: 'Forms, then Excel.',
        // In the options' own order, whatever order they were ticked in.
        currentTools: ['PAPER_FORMS', 'EXCEL', 'OTHER'],
        currentToolsOther: 'A notice board',
        performedBy: 'Office staff',
        affectedPeople: 'Students',
        frequency: 'MONTHLY',
        timeRequired: 'Several days',
        peopleInvolved: 12,
        impacts: ['MISTAKES', 'WAITING'],
        impactDetails: 'Students come back twice.',
        improvementGoal: 'Faster.',
        desiredOutcome: 'Submit once.',
        easierForPeople: 'Check progress.',
        expectedBenefit: 'No repeat visits.',
        importantConsiderations: 'Payment details are private.',
      }),
    )
  })

  it('sends an unanswered story as blanks', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()

    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        INDIVIDUAL_TARGET,
        expect.objectContaining({
          currentTools: [],
          impacts: [],
          frequency: null,
          peopleInvolved: null,
          currentProcess: '',
        }),
      ),
    )
  })

  it('drops the "other" answer once Other is unticked', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()
    goToStep('How does this happen today?')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Other' }))
    fireEvent.change(screen.getByLabelText('What else do you use?'), {
      target: { value: 'Carrier pigeon' },
    })

    fireEvent.click(screen.getByRole('checkbox', { name: 'Other' }))
    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        INDIVIDUAL_TARGET,
        expect.objectContaining({ currentTools: [], currentToolsOther: '' }),
      ),
    )
  })

  it('lets a frequency answer be cleared', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep('Tell us about the impact')
    fireEvent.click(screen.getByRole('radio', { name: 'Weekly' }))

    fireEvent.click(screen.getByRole('button', { name: 'Clear answer' }))

    expect(screen.getByRole('radio', { name: 'Weekly' })).not.toBeChecked()
  })

  // --- an existing idea -----------------------------------------------------------

  it('shows the values of the draft being edited', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm({
      idea: {
        ...DRAFT,
        currentProcess: 'We fill in forms.',
        frequency: 'DAILY',
        peopleInvolved: 4,
        impacts: ['DELAYS'],
      },
    })

    expect(screen.getByRole('heading', { name: 'Edit this draft' })).toBeInTheDocument()
    expect(titleField()).toHaveValue('An existing draft')
    expect(descriptionField()).toHaveValue('A description long enough to be usable.')

    goToStep('How does this happen today?')
    expect(screen.getByLabelText('How do you currently handle this?')).toHaveValue(
      'We fill in forms.',
    )
    goToStep('Tell us about the impact')
    expect(screen.getByRole('radio', { name: 'Daily' })).toBeChecked()
    expect(
      screen.getByLabelText(/^Approximately how many people are involved in this process\?/),
    ).toHaveValue('4')
    expect(screen.getByRole('checkbox', { name: 'Work gets delayed' })).toBeChecked()

    // The draft's own category is shown while the list loads, so an
    // already-classified draft never reads as unclassified.
    goToStep(CLASSIFY_STEP)
    expect(screen.getByLabelText('Category')).toHaveValue('10')
    await waitFor(() => expect(screen.getByRole('option', { name: 'Finance' })).toBeInTheDocument())
    expect(screen.getByLabelText('Category')).toHaveValue('10')
  })

  // --- context, category and visibility ------------------------------------------

  it('offers the three ways to file, and marks the two this author cannot use', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm({ organizationId: null })
    goToStep(CLASSIFY_STEP)

    expect(screen.getByRole('group', { name: 'Who is putting this forward?' })).toBeInTheDocument()
    // This form was rendered with no organization, so "my organization" is
    // drawn disabled rather than offered and then refused by the server.
    expect(contextChoice('Just me')).toBeEnabled()
    expect(contextChoice('My team')).toBeDisabled()
    expect(contextChoice('My organization')).toBeDisabled()
    expect(screen.getByText('You are not in an organization yet.')).toBeInTheDocument()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('offers every context when the author has an organization', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep(CLASSIFY_STEP)

    expect(contextChoice('My organization')).toBeEnabled()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('files on its own by default, which is why no organization is needed', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm({ organizationId: null })
    fillProblem()

    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({ context: 'INDIVIDUAL' }, expect.anything()),
    )
  })

  it('files for the organization when that context is chosen', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()
    goToStep(CLASSIFY_STEP)
    await screen.findByRole('option', { name: 'Finance' })

    fireEvent.click(contextChoice('My organization'))
    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(ORGANIZATION_TARGET, expect.anything()),
    )
  })

  it('does not offer a team the author is not in', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep(CLASSIFY_STEP)

    // Drawn with its reason rather than omitted: "my team" is one of the three
    // ways to file, and a reader with no team should be told why it is closed
    // instead of finding it missing.
    expect(contextChoice('My team')).toBeDisabled()
    expect(screen.getByText('You are not in a team yet.')).toBeInTheDocument()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('names the team once the author is in one', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    teamsMock.mockResolvedValue([makeTeam()])
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()
    goToStep(CLASSIFY_STEP)
    await screen.findByRole('option', { name: 'Finance' })

    fireEvent.click(contextChoice('My team'))
    fireEvent.change(screen.getByLabelText('Which team?'), { target: { value: '9' } })
    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith({ context: 'TEAM', teamId: '9' }, expect.anything()),
    )
  })

  it('refuses to submit a team idea before a team is chosen', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    teamsMock.mockResolvedValue([makeTeam()])
    renderForm({ onSubmitted: vi.fn() })
    fillProblem()
    goToStep(CLASSIFY_STEP)
    await screen.findByRole('option', { name: 'Finance' })

    fireEvent.click(contextChoice('My team'))
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: '11' } })
    fireEvent.click(screen.getByRole('radio', { name: /Everyone/ }))
    goToStep(EVIDENCE_STEP)
    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    expect(screen.getByText('Choose which team is putting this forward.')).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('offers the visibilities that context can actually be reviewed under', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep(CLASSIFY_STEP)
    await screen.findByRole('option', { name: 'Finance' })

    // Filing alone, which is the default: there is no organization to widen to,
    // and platform reviewers are in none of the author's.
    expect(
      screen.queryByRole('radio', { name: /People in your organization/ }),
    ).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Everyone/ })).toBeInTheDocument()

    // Organization context: a reviewer is in the organization, so the
    // organization's own visibility reaches one.
    fireEvent.click(contextChoice('My organization'))
    expect(
      screen.getByRole('radio', {
        name: /My organization.*People in your organization who have permission can see it/,
      }),
    ).toBeInTheDocument()
  })

  it('offers the three visibilities a person may choose, and not department', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep(CLASSIFY_STEP)
    fireEvent.click(contextChoice('My organization'))

    // `DEPARTMENT` is reserved vocabulary with no tier behind it: the backend
    // would refuse it, so the picker does not offer it. Matched by role and a
    // partial name because each radio's accessible name is its label *and* its
    // hint, which is the point of the hint.
    expect(
      screen.getByRole('group', { name: 'Who should be able to see this?' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('radio', { name: /Only me.*Only you can see this idea/ }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('radio', {
        name: /My organization.*People in your organization who have permission can see it/,
      }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('radio', { name: /Everyone.*Other users can discover this idea/ }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /department/i })).not.toBeInTheDocument()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('defaults a new idea to private, matching the server default', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep(CLASSIFY_STEP)
    fireEvent.click(contextChoice('My organization'))

    expect(screen.getByRole('radio', { name: /Only me/ })).toBeChecked()
    // ...and says, before anybody clicks Submit, that private cannot be reviewed.
    expect(screen.getByText(/reviewers can only review what they can see/)).toBeInTheDocument()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('says why a private idea cannot be reviewed when filing alone', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep(CLASSIFY_STEP)

    // The default context. There is no organization to widen to, so the
    // message must not tell this author to share with one they do not have.
    expect(contextChoice('Just me')).toBeChecked()
    expect(screen.getByText(/the platform reviews this idea directly/)).toBeInTheDocument()
    expect(
      screen.queryByText(/reviewers can only review what they can see/),
    ).not.toBeInTheDocument()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('keeps category and visibility together on one step', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep(CLASSIFY_STEP)

    const step = screen.getByRole('region', { name: CLASSIFY_STEP })
    expect(within(step).getByLabelText('Category')).toBeInTheDocument()
    expect(
      within(step).getByRole('group', { name: 'Who should be able to see this?' }),
    ).toBeInTheDocument()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('sends the chosen category', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()
    goToStep(CLASSIFY_STEP)
    await waitFor(() => expect(screen.getByRole('option', { name: 'Finance' })).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText('Category'), { target: { value: '11' } })
    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        INDIVIDUAL_TARGET,
        expect.objectContaining({ categoryId: '11' }),
      ),
    )
  })

  it('sends a null category when none is chosen', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()

    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        INDIVIDUAL_TARGET,
        expect.objectContaining({ categoryId: null }),
      ),
    )
  })

  it('sends the chosen visibility', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()
    goToStep(CLASSIFY_STEP)

    fireEvent.click(screen.getByRole('radio', { name: /Everyone/ }))
    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        INDIVIDUAL_TARGET,
        expect.objectContaining({ visibility: 'PUBLIC' }),
      ),
    )
  })

  it('stays usable when the category list cannot be loaded', async () => {
    // The category is optional on a draft, so a failed lookup leaves a working
    // form that simply cannot classify the idea yet.
    categoriesMock.mockRejectedValue(new Error('offline'))
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()
    goToStep(CLASSIFY_STEP)

    await waitFor(() => expect(screen.getByLabelText('Category')).toBeEnabled())
    saveDraft()

    await waitFor(() => expect(createMock).toHaveBeenCalled())
  })

  // --- validation -------------------------------------------------------------

  it('requires a title', () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    fireEvent.change(descriptionField(), { target: { value: 'Long enough.' } })

    saveDraft()

    expect(screen.getByText('Give your problem a short name.')).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('returns to the problem step when the title is missing', () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    goToStep('Is there anything important we should know?')

    saveDraft()

    expect(stepHeading()).toHaveTextContent('Tell us about the problem')
    expect(screen.getByText('Give your problem a short name.')).toBeInTheDocument()
  })

  it('saves a draft whose description is still short - drafts may be incomplete', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem({ description: 'too short' })

    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        INDIVIDUAL_TARGET,
        expect.objectContaining({ description: 'too short' }),
      ),
    )
  })

  it('says the description minimum before the author has tried anything', async () => {
    // The point of a draft is that it can be incomplete, so the rule is
    // visible up front rather than only as a rejection.
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    expect(
      screen.getByText(
        `A draft can be incomplete, but submitting needs at least ${MIN_DESCRIPTION_LENGTH} characters.`,
      ),
    ).toBeInTheDocument()
  })

  it('refuses a number of people that is not a whole number, on its own step', () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    fillProblem()
    goToStep('Tell us about the impact')
    fireEvent.change(
      screen.getByLabelText(/^Approximately how many people are involved in this process\?/),
      { target: { value: 'about ten' } },
    )
    goToStep('Tell us about the problem')

    saveDraft()

    expect(stepHeading()).toHaveTextContent('Tell us about the impact')
    expect(screen.getByText('Enter a whole number, like 5.')).toBeInTheDocument()
    expect(stepButton('Tell us about the impact')).toHaveAttribute('aria-current', 'step')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('re-validates a field once it has been touched', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    saveDraft()
    expect(screen.getByText('Give your problem a short name.')).toBeInTheDocument()

    fireEvent.change(titleField(), { target: { value: 'Now titled' } })

    expect(screen.queryByText('Give your problem a short name.')).not.toBeInTheDocument()
  })

  // --- saving -----------------------------------------------------------------

  it('saves a draft without naming a tenant, because it was filed alone', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()

    saveDraft()

    // The tenant is an input to a server-side decision, not a value the client
    // writes onto the idea - and an idea filed alone names none.
    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(INDIVIDUAL_TARGET, expect.anything()),
    )
  })

  it('can save a draft from any step', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem()
    goToStep('What would you like to improve?')

    saveDraft()

    await waitFor(() => expect(createMock).toHaveBeenCalledOnce())
  })

  it('trims the content it sends', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    renderForm()
    fillProblem({ title: '  Padded  ', description: '  Because it costs us real time.  ' })

    saveDraft()

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        INDIVIDUAL_TARGET,
        expect.objectContaining({ title: 'Padded', description: 'Because it costs us real time.' }),
      ),
    )
  })

  it('edits an existing draft through the update mutation', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    updateMock.mockResolvedValue(SAVED)
    renderForm({ idea: DRAFT })

    fireEvent.change(titleField(), { target: { value: 'A sharper title' } })
    saveDraft()

    await waitFor(() =>
      expect(updateMock).toHaveBeenCalledWith(
        '1',
        expect.objectContaining({ title: 'A sharper title' }),
      ),
    )
    expect(createMock).not.toHaveBeenCalled()
  })

  it('hands the saved idea to its caller', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue(SAVED)
    const { onSaved } = renderForm()
    fillProblem()

    saveDraft()

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(DRAFT))
  })

  it('shows a loading state while saving', async () => {
    let release: (value: IdeaMutationResult) => void = () => {}
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    renderForm()
    fillProblem()

    saveDraft()

    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    release(SAVED)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save draft' })).toBeEnabled())
  })

  // --- failures ---------------------------------------------------------------

  it('shows a backend field error next to the input it belongs to', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({
      success: false,
      message: 'The title must be 200 characters or fewer.',
      field: 'title',
      idea: null,
    })
    renderForm()
    fillProblem()

    saveDraft()

    expect(
      await screen.findByText('The title must be 200 characters or fewer.'),
    ).toBeInTheDocument()
    expect(titleField()).toHaveAccessibleDescription(/The title must be 200 characters or fewer/)
  })

  it('takes the author to a story answer the server refused', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({
      success: false,
      message: 'This answer must be 300 characters or fewer.',
      field: 'performed_by',
      idea: null,
    })
    renderForm()
    fillProblem()

    saveDraft()

    expect(
      await screen.findByText('This answer must be 300 characters or fewer.'),
    ).toBeInTheDocument()
    expect(stepHeading()).toHaveTextContent('Tell us about the impact')
    expect(
      screen.getByLabelText('Who is usually responsible for doing this?'),
    ).toHaveAccessibleDescription(/This answer must be 300 characters or fewer/)
  })

  it('shows a category refusal next to the category', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({
      success: false,
      message: 'Choose a valid category.',
      field: 'category',
      idea: null,
    })
    renderForm()
    fillProblem()

    saveDraft()

    expect(await screen.findByText('Choose a valid category.')).toBeInTheDocument()
    expect(stepHeading()).toHaveTextContent(CLASSIFY_STEP)
  })

  it('shows a refusal with no field as a whole-form error', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({
      success: false,
      message: 'You must be an active member of this organization to file ideas here.',
      field: null,
      idea: null,
    })
    renderForm()
    fillProblem()

    saveDraft()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('You must be an active member of this organization')
  })

  it('reports an unreachable server without claiming the idea was saved', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockRejectedValue(new Error('Failed to fetch'))
    const { onSaved } = renderForm()
    fillProblem()

    saveDraft()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('keeps what the author typed when the save fails', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockRejectedValue(new Error('Failed to fetch'))
    renderForm()
    fillProblem({ title: 'Worth keeping' })

    saveDraft()

    await screen.findByRole('alert')
    expect(titleField()).toHaveValue('Worth keeping')
  })

  it('offers a cancel only when the caller supplied one', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    const onCancel = vi.fn()
    const { unmount } = render(
      <IdeaForm organizationId="3" onSaved={vi.fn()} onCancel={onCancel} />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledOnce()
    unmount()

    render(<IdeaForm organizationId="3" onSaved={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Cancel' })).not.toBeInTheDocument()
  })
})
