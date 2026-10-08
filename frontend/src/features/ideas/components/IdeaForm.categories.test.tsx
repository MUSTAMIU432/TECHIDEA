import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { categoriesRequest, createIdeaRequest, type IdeaCategory } from '../api/ideasApi'
import { IdeaForm } from './IdeaForm'
import {
  CLASSIFY_STEP,
  EVIDENCE_STEP,
  contextChoice,
  fillProblem,
  goToStep,
} from '../../../test/ideaForm'

/** The form's own source, and every module it is built from. */
const formSources = import.meta.glob<string>(['./IdeaForm.tsx', './intake/*.{ts,tsx}'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

/**
 * The category picker renders whatever the backend's `categories` query
 * returns, and nothing else. The development seed
 * (`backend/ideas/dev_categories.py`) is temporary data, so these tests use
 * made-up categories on purpose: a form that only worked with the seeded
 * names would fail here.
 */

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(),
  createIdeaRequest: vi.fn(),
  submitIdeaRequest: vi.fn(),
}))

const categoriesMock = vi.mocked(categoriesRequest)
const createMock = vi.mocked(createIdeaRequest)

const FROM_THE_SERVER: IdeaCategory[] = [
  { id: '901', name: 'Zeta Widgets', slug: 'zeta-widgets', description: '' },
  {
    id: '902',
    name: 'Alpha Gadgets',
    slug: 'alpha-gadgets',
    description: 'Gadgets.',
  },
]

function optionLabels(): string[] {
  return within(screen.getByLabelText('Category'))
    .getAllByRole('option')
    .map((option) => option.textContent ?? '')
}

/** Render the form and open the step the category picker is on. */
function renderAtCategory(props: Partial<React.ComponentProps<typeof IdeaForm>> = {}) {
  render(<IdeaForm organizationId="3" onSaved={vi.fn()} {...props} />)
  fillProblem()
  goToStep(CLASSIFY_STEP)
}

describe('IdeaForm — categories come from the backend', () => {
  afterEach(() => {
    vi.resetAllMocks()
  })

  it('asks the backend for the categories once', async () => {
    categoriesMock.mockResolvedValue(FROM_THE_SERVER)
    renderAtCategory()

    await screen.findByRole('option', { name: 'Zeta Widgets' })
    expect(categoriesMock).toHaveBeenCalledOnce()
  })

  it('offers exactly the returned categories, in the order returned', async () => {
    categoriesMock.mockResolvedValue(FROM_THE_SERVER)
    renderAtCategory()

    await screen.findByRole('option', { name: 'Zeta Widgets' })
    expect(optionLabels()).toEqual(['Not classified yet', 'Zeta Widgets', 'Alpha Gadgets'])
  })

  it('has no category list of its own', async () => {
    // Nothing returned, nothing offered beyond "unclassified".
    categoriesMock.mockResolvedValue([])
    renderAtCategory()

    await waitFor(() => expect(screen.getByLabelText('Category')).toBeEnabled())
    expect(optionLabels()).toEqual(['Not classified yet'])
    expect(screen.getByText(/No categories are available yet/)).toBeInTheDocument()

    // And no module the form is built from names any development category.
    expect(Object.keys(formSources).length).toBeGreaterThan(1)
    for (const source of Object.values(formSources)) {
      for (const name of ['Human Resources', 'Procurement', 'Healthcare', 'Logistics']) {
        expect(source).not.toContain(name)
      }
    }
  })

  it('sends the chosen category by its backend id', async () => {
    categoriesMock.mockResolvedValue(FROM_THE_SERVER)
    createMock.mockResolvedValue({
      success: false,
      message: 'stop',
      field: null,
      idea: null,
    })
    renderAtCategory()
    await screen.findByRole('option', { name: 'Alpha Gadgets' })

    fireEvent.change(screen.getByLabelText('Category'), {
      target: { value: '902' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        { context: 'INDIVIDUAL' },
        expect.objectContaining({ categoryId: '902' }),
      ),
    )
  })

  it('disables the picker and says so while categories load', async () => {
    let resolve: (categories: IdeaCategory[]) => void = () => {}
    categoriesMock.mockReturnValue(new Promise((done) => (resolve = done)))
    renderAtCategory()

    expect(screen.getByLabelText('Category')).toBeDisabled()
    expect(optionLabels()).toEqual(['Loading categories…'])

    resolve(FROM_THE_SERVER)
    await waitFor(() => expect(screen.getByLabelText('Category')).toBeEnabled())
    expect(optionLabels()).toContain('Zeta Widgets')
  })

  it('says when categories could not be loaded, and still saves a draft', async () => {
    categoriesMock.mockRejectedValue(new Error('offline'))
    createMock.mockResolvedValue({
      success: false,
      message: 'stop',
      field: null,
      idea: null,
    })
    renderAtCategory()

    expect(await screen.findByText(/Categories could not be loaded/)).toBeInTheDocument()
    expect(screen.getByLabelText('Category')).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))
    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        { context: 'INDIVIDUAL' },
        expect.objectContaining({ categoryId: null }),
      ),
    )
  })

  it('requires a category to submit for review', async () => {
    categoriesMock.mockResolvedValue(FROM_THE_SERVER)
    renderAtCategory({ onSubmitted: vi.fn() })
    await screen.findByRole('option', { name: 'Zeta Widgets' })

    fireEvent.click(contextChoice('Organization level'))
    goToStep(EVIDENCE_STEP)
    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    expect(screen.getByText('Choose a category before submitting this idea.')).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })
})
