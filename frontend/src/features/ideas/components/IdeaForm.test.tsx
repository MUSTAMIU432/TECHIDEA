import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { categoriesRequest, createIdeaRequest, updateIdeaRequest } from '../api/ideasApi'
import { IdeaForm, MIN_DESCRIPTION_LENGTH } from './IdeaForm'
import type { Idea, IdeaMutationResult } from '../api/ideasApi'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
}))

const categoriesMock = vi.mocked(categoriesRequest)
const createMock = vi.mocked(createIdeaRequest)
const updateMock = vi.mocked(updateIdeaRequest)

const CATEGORIES = [
  { id: '10', name: 'Customer support', slug: 'customer-support', description: '' },
  { id: '11', name: 'Finance', slug: 'finance', description: '' },
]

const DRAFT: Idea = {
  id: '1',
  title: 'An existing draft',
  description: 'A description long enough to be usable.',
  status: 'DRAFT',
  visibility: 'PRIVATE',
  submittedAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  updatedAt: '2026-01-01T00:00:00.000Z',
  authorId: '7',
  organizationId: '3',
  category: CATEGORIES[0],
  availableTransitions: [],
  discussionOpen: true,
}

function renderForm(props: Partial<React.ComponentProps<typeof IdeaForm>> = {}) {
  const onSaved = props.onSaved ?? vi.fn()
  render(<IdeaForm organizationId="3" onSaved={onSaved} {...props} />)
  return { onSaved }
}

function fillForm({ title = 'A new idea', description = 'A description long enough.' } = {}) {
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: title } })
  fireEvent.change(screen.getByLabelText('Description'), { target: { value: description } })
}

describe('IdeaForm', () => {
  afterEach(() => {
    categoriesMock.mockReset()
    createMock.mockReset()
    updateMock.mockReset()
  })

  // --- rendering ----------------------------------------------------------

  it('renders every field the flow needs', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    expect(screen.getByRole('heading', { name: 'File a new idea' })).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toBeInTheDocument()
    expect(screen.getByLabelText('Description')).toBeInTheDocument()
    expect(screen.getByLabelText('Category')).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Who can see this' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeInTheDocument()

    await waitFor(() => expect(screen.getByRole('option', { name: 'Finance' })).toBeInTheDocument())
  })

  it('offers the three visibilities a person may choose, and not department', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    // `DEPARTMENT` is reserved vocabulary with no tier behind it: the backend
    // would refuse it, so the picker does not offer it. Matched by role and a
    // partial name because each radio's accessible name is its label *and* its
    // hint, which is the point of the hint.
    expect(screen.getByRole('radio', { name: /Only me/ })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /My organization/ })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /Everyone on the platform/ })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /department/i })).not.toBeInTheDocument()
  })

  it('defaults a new idea to private, matching the server default', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    expect(screen.getByRole('radio', { name: /Only me/ })).toBeChecked()
  })

  it('shows the values of the draft being edited', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm({ idea: DRAFT })

    expect(screen.getByRole('heading', { name: 'Edit this draft' })).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toHaveValue('An existing draft')
    expect(screen.getByLabelText('Description')).toHaveValue(
      'A description long enough to be usable.',
    )

    // The draft's own category is shown while the list loads, so an
    // already-classified draft never reads as unclassified.
    expect(screen.getByLabelText('Category')).toHaveValue('10')
    await waitFor(() => expect(screen.getByRole('option', { name: 'Finance' })).toBeInTheDocument())
    expect(screen.getByLabelText('Category')).toHaveValue('10')
  })

  // --- validation ---------------------------------------------------------

  it('requires a title', () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'Long enough.' } })

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    expect(screen.getByText('Give the idea a title.')).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('requires a description long enough to act on', () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    fillForm({ description: 'too short' })

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    expect(
      screen.getByText(`Describe the problem in at least ${MIN_DESCRIPTION_LENGTH} characters.`),
    ).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('says the minimum before the author has tried anything', async () => {
    // The point of a draft is that it can be incomplete, so the rule is
    // visible up front rather than only as a rejection.
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()

    expect(screen.getByText(/submitting needs at least/i)).toBeInTheDocument()
  })

  it('re-validates a field once it has been touched', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    renderForm()
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    expect(screen.getByText('Give the idea a title.')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Now titled' } })

    expect(screen.queryByText('Give the idea a title.')).not.toBeInTheDocument()
  })

  // --- category and visibility selection -----------------------------------

  it('sends the chosen category', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    renderForm()
    await waitFor(() => expect(screen.getByRole('option', { name: 'Finance' })).toBeInTheDocument())
    fillForm()

    fireEvent.change(screen.getByLabelText('Category'), { target: { value: '11' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith('3', expect.objectContaining({ categoryId: '11' })),
    )
  })

  it('sends a null category when none is chosen', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    renderForm()
    fillForm()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith('3', expect.objectContaining({ categoryId: null })),
    )
  })

  it('sends the chosen visibility', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    renderForm()
    fillForm()

    fireEvent.click(screen.getByRole('radio', { name: /Everyone on the platform/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({ visibility: 'PUBLIC' }),
      ),
    )
  })

  it('stays usable when the category list cannot be loaded', async () => {
    // The category is optional on a draft, so a failed lookup leaves a working
    // form that simply cannot classify the idea yet.
    categoriesMock.mockRejectedValue(new Error('offline'))
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    renderForm()
    fillForm()

    await waitFor(() => expect(screen.getByLabelText('Category')).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() => expect(createMock).toHaveBeenCalled())
  })

  // --- saving -------------------------------------------------------------

  it('saves a draft into the organization it was given', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    renderForm()
    fillForm()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    // The organization is an input to a server-side decision, not a value the
    // client writes onto the idea.
    await waitFor(() => expect(createMock).toHaveBeenCalledWith('3', expect.anything()))
  })

  it('trims the content it sends', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    renderForm()
    // Long enough to clear the minimum before trimming, which is the point:
    // the length check runs on what the author typed, and the payload carries
    // what was typed minus the padding.
    fillForm({ title: '  Padded  ', description: '  Because it costs us real time.  ' })

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        '3',
        expect.objectContaining({ title: 'Padded', description: 'Because it costs us real time.' }),
      ),
    )
  })

  it('edits an existing draft through the update mutation', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    updateMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    renderForm({ idea: DRAFT })

    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'A sharper title' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

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
    createMock.mockResolvedValue({ success: true, message: 'ok', field: null, idea: DRAFT })
    const { onSaved } = renderForm()
    fillForm()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

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
    fillForm()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
    release({ success: true, message: 'ok', field: null, idea: DRAFT })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save draft' })).toBeEnabled())
  })

  // --- failures -----------------------------------------------------------

  it('shows a backend field error next to the input it belongs to', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockResolvedValue({
      success: false,
      message: 'The title must be 200 characters or fewer.',
      field: 'title',
      idea: null,
    })
    renderForm()
    fillForm()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    expect(
      await screen.findByText('The title must be 200 characters or fewer.'),
    ).toBeInTheDocument()
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
    fillForm()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    expect(await screen.findByText('Choose a valid category.')).toBeInTheDocument()
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
    fillForm()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('You must be an active member of this organization')
  })

  it('reports an unreachable server without claiming the idea was saved', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockRejectedValue(new Error('Failed to fetch'))
    const { onSaved } = renderForm()
    fillForm()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('keeps what the author typed when the save fails', async () => {
    categoriesMock.mockResolvedValue(CATEGORIES)
    createMock.mockRejectedValue(new Error('Failed to fetch'))
    renderForm()
    fillForm({ title: 'Worth keeping' })

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await screen.findByRole('alert')
    expect(screen.getByLabelText('Title')).toHaveValue('Worth keeping')
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
  })
})
