import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { categoriesRequest, createIdeaRequest, submitIdeaRequest } from '../api/ideasApi'
import type { Idea } from '../api/ideasApi'
import { IdeaForm } from './IdeaForm'
import { EMPTY_PROBLEM_STORY } from '../utils/problemStory'
import { makeIdea } from '../../../test/idea'
import {
  CLASSIFY_STEP,
  EVIDENCE_STEP,
  contextChoice,
  descriptionField,
  fillProblem,
  goToStep,
  titleField,
} from '../../../test/ideaForm'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(),
  createIdeaRequest: vi.fn(),
  submitIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
}))

const categoriesMock = vi.mocked(categoriesRequest)
const createMock = vi.mocked(createIdeaRequest)
const submitMock = vi.mocked(submitIdeaRequest)

const CATEGORY = {
  id: '10',
  name: 'Finance',
  slug: 'finance',
  description: '',
}

const CREATED: Idea = makeIdea({
  id: '42',
  title: 'Automate the invoice run',
  description: 'We key invoices in by hand every month.',
  visibility: 'ORGANIZATION',
  category: CATEGORY,
  availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
})

const SUBMITTED: Idea = {
  ...CREATED,
  status: 'SUBMITTED',
  submittedAt: '2026-01-02T00:00:00.000Z',
  availableTransitions: [],
}

async function renderForm(props: Partial<React.ComponentProps<typeof IdeaForm>> = {}) {
  const onSaved = vi.fn()
  const onSubmitted = vi.fn()
  render(<IdeaForm organizationId="3" onSaved={onSaved} onSubmitted={onSubmitted} {...props} />)
  await waitFor(() => expect(categoriesMock).toHaveBeenCalled())
  return { onSaved, onSubmitted }
}

/** Answer what submitting needs: the problem, a category. */
async function fillReviewable() {
  fillProblem({ title: CREATED.title, description: CREATED.description })
  goToStep(CLASSIFY_STEP)
  await screen.findByRole('option', { name: 'Finance' })
  fireEvent.change(screen.getByLabelText('Category'), {
    target: { value: CATEGORY.id },
  })
  // The organization context, because an organization idea is reviewed by its
  // own organization first - which is also what makes `ORGANIZATION`
  // visibility reviewable.
  fireEvent.click(contextChoice('Organization level'))
}

/** "Submit for review" is the last step's button, so get there first. */
function submitForReview() {
  goToStep(EVIDENCE_STEP)
  fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))
}

describe('IdeaForm — submit for review on create', () => {
  beforeEach(() => {
    categoriesMock.mockResolvedValue([CATEGORY])
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it('offers Submit for review only when the caller handles it and the idea is new', async () => {
    const { unmount } = render(<IdeaForm organizationId="3" onSaved={vi.fn()} />)

    goToStep(EVIDENCE_STEP)
    expect(screen.queryByRole('button', { name: 'Submit for review' })).not.toBeInTheDocument()
    unmount()

    render(<IdeaForm organizationId="3" idea={CREATED} onSaved={vi.fn()} onSubmitted={vi.fn()} />)
    goToStep(CLASSIFY_STEP)
    expect(screen.queryByRole('button', { name: 'Submit for review' })).not.toBeInTheDocument()
    await screen.findByRole('option', { name: 'Finance' })
  })

  it('creates the idea, submits it, and reports the submitted idea', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: CREATED,
    })
    submitMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: SUBMITTED,
    })
    const { onSaved, onSubmitted } = await renderForm()

    await fillReviewable()
    submitForReview()

    await waitFor(() =>
      expect(onSubmitted).toHaveBeenCalledWith({
        submitted: true,
        idea: SUBMITTED,
      }),
    )
    expect(createMock).toHaveBeenCalledWith(
      { context: 'ORGANIZATION', organizationId: '3' },
      {
        ...EMPTY_PROBLEM_STORY,
        title: CREATED.title,
        description: CREATED.description,
        categoryId: CATEGORY.id,
      },
    )
    expect(submitMock).toHaveBeenCalledWith('42')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('asks for a category and a reviewable visibility before sending anything', async () => {
    await renderForm()
    fillProblem({ title: CREATED.title, description: CREATED.description })

    submitForReview()

    // Taken back to the step that needs them.
    expect(screen.getByRole('button', { name: /^Step 7:/ })).toHaveAttribute('aria-current', 'step')
    expect(screen.getByText('Choose a category before submitting this idea.')).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()

    // Fixing the fields clears their messages.
    await screen.findByRole('option', { name: 'Finance' })
    fireEvent.change(screen.getByLabelText('Category'), {
      target: { value: CATEGORY.id },
    })
    expect(screen.queryByText('Choose a category before submitting this idea.')).toBeNull()
  })

  it('lets an organization idea be submitted with its own audience, My organization', async () => {
    await renderForm()
    fillProblem({ title: CREATED.title, description: CREATED.description })
    goToStep(CLASSIFY_STEP)
    await screen.findByRole('option', { name: 'Finance' })

    fireEvent.click(contextChoice('Organization level'))
    fireEvent.change(screen.getByLabelText('Category'), {
      target: { value: CATEGORY.id },
    })
    submitForReview()

    expect(screen.queryByText(/A private idea cannot be reviewed/)).toBeNull()
  })

  it('asks for a description long enough to understand before submitting', async () => {
    await renderForm()
    fireEvent.change(titleField(), { target: { value: CREATED.title } })
    fireEvent.change(descriptionField(), { target: { value: 'too short' } })

    submitForReview()

    expect(
      screen.getByText('Describe the problem in at least 20 characters before submitting.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Step 1:/ })).toHaveAttribute('aria-current', 'step')
    expect(createMock).not.toHaveBeenCalled()
  })

  it('does not require any of the helpful questions to submit', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: CREATED,
    })
    submitMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: SUBMITTED,
    })
    const { onSubmitted } = await renderForm()

    // Only the problem, a category and a visibility: every other step skipped.
    await fillReviewable()
    submitForReview()

    await waitFor(() =>
      expect(onSubmitted).toHaveBeenCalledWith({
        submitted: true,
        idea: SUBMITTED,
      }),
    )
  })

  it('still saves a private, unclassified draft with Save draft', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: CREATED,
    })
    const { onSaved, onSubmitted } = await renderForm()
    fillProblem({ title: CREATED.title, description: CREATED.description })

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED))
    expect(submitMock).not.toHaveBeenCalled()
    expect(onSubmitted).not.toHaveBeenCalled()
  })

  it('hands back the draft with the reason when submitting is refused', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: CREATED,
    })
    submitMock.mockResolvedValue({
      success: false,
      message: 'Choose a category before submitting this idea.',
      field: null,
      idea: null,
    })
    const { onSubmitted } = await renderForm()

    await fillReviewable()
    submitForReview()

    await waitFor(() =>
      expect(onSubmitted).toHaveBeenCalledWith({
        submitted: false,
        idea: CREATED,
        message: 'Choose a category before submitting this idea.',
      }),
    )
  })

  it('hands back the draft when the submit request never arrives', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: CREATED,
    })
    submitMock.mockRejectedValue(new Error('offline'))
    const { onSubmitted } = await renderForm()

    await fillReviewable()
    submitForReview()

    await waitFor(() =>
      expect(onSubmitted).toHaveBeenCalledWith({
        submitted: false,
        idea: CREATED,
        message: 'We could not reach the server to submit it.',
      }),
    )
  })

  it('stays on the form, submitting nothing, when creating is refused', async () => {
    createMock.mockResolvedValue({
      success: false,
      message: 'Choose a valid category.',
      field: 'category',
      idea: null,
    })
    const { onSubmitted } = await renderForm()

    await fillReviewable()
    submitForReview()

    expect(await screen.findByText('Choose a valid category.')).toBeInTheDocument()
    expect(submitMock).not.toHaveBeenCalled()
    expect(onSubmitted).not.toHaveBeenCalled()
  })
})
