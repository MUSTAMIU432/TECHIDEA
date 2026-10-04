import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createMemoryRouter, MemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithRouter } from '../../../test/renderWithRouter'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeasWorkspace } from './IdeasWorkspace'
import { page } from '../../../test/ideaPage'
import type { Idea, IdeaMutationResult } from '../api/ideasApi'
import { makeIdea } from '../../../test/idea'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(async () => []),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  transitionIdeaRequest: vi.fn(),
  organizationIdeasRequest: vi.fn(),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))

const { organizationIdeasRequest, transitionIdeaRequest } = await import('../api/ideasApi')
const listMock = vi.mocked(organizationIdeasRequest)
const transitionMock = vi.mocked(transitionIdeaRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }

/**
 * The button that puts an organization draft forward. Its wording is the one
 * `TRANSITION_LABELS` gives that move, and it is spelled here rather than
 * imported because this file is asserting the *card* offers it: if the label
 * changed, these tests should fail rather than follow.
 */
const SUBMIT_LABEL = 'Send to my organization'

/**
 * An organization-context draft, offered one move.
 *
 * `availableTransitions` is what the *server* says this viewer may do with it,
 * so these fixtures state it explicitly - which is the stronger assertion: a
 * button appears here because the server listed the move, not because the
 * component decided to draw one. An organization idea's first submission goes
 * to its own organization, which is why the move is `SUBMITTED_TO_ORGANIZATION`
 * and the button reads "Send to my organization".
 */
const DRAFT: Idea = makeIdea({
  availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
})

const SOMEONE_ELSES: typeof DRAFT = {
  ...DRAFT,
  id: '2',
  title: 'Their idea',
  authorId: '9',
  visibility: 'ORGANIZATION',
  // An ordinary reader, and not the author: the server offers them nothing.
  availableTransitions: [],
}

function mockContext(
  ideas: (typeof DRAFT)[] = [DRAFT],
  activeOrganization: unknown = { id: '3', name: 'Acme Labs' },
) {
  vi.mocked(useAuth).mockReturnValue({
    user: SIGNED_IN,
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization,
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(page(ideas))
}

describe('IdeasWorkspace', () => {
  beforeEach(() => {
    mockContext()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // --- the list -----------------------------------------------------------

  it('reads the active organization feed, not a list the client filtered', async () => {
    renderWithRouter(<IdeasWorkspace />)

    // The tenant and visibility rules are the server's. This component asks
    // for one organization's ideas by id and renders whatever it is given.
    //
    // The second argument is asserted key by key rather than as a literal,
    // because it is the filter and it is meant to grow: what matters is that
    // it names no tenant, no author and no visibility, since a filter that
    // could widen a result is the one thing this client must not be able to
    // ask for.
    await waitFor(() => expect(listMock).toHaveBeenCalled())
    const [organizationId, filters = {}] = listMock.mock.calls[0]
    expect(organizationId).toBe('3')
    expect(Object.keys(filters).sort()).toEqual([
      'categoryId',
      'limit',
      'offset',
      'search',
      'status',
    ])
  })

  it('shows a loading state before the ideas arrive', () => {
    renderWithRouter(<IdeasWorkspace />)

    expect(screen.getByText('Loading ideas…')).toBeInTheDocument()
  })

  it('renders the ideas it is given', async () => {
    mockContext([DRAFT, SOMEONE_ELSES])
    renderWithRouter(<IdeasWorkspace />)

    expect(await screen.findByText('Automate the invoice run')).toBeInTheDocument()
    expect(screen.getByText('Their idea')).toBeInTheDocument()
  })

  it('shows an empty state when the organization has no ideas', async () => {
    mockContext([])
    renderWithRouter(<IdeasWorkspace />)

    expect(await screen.findByText('No ideas here yet')).toBeInTheDocument()
  })

  it('shows an empty state, not an error, when there is no organization', async () => {
    mockContext([], null)
    renderWithRouter(<IdeasWorkspace />)

    expect(await screen.findByText('No organization selected')).toBeInTheDocument()
  })

  it('reports a transport failure as itself rather than as an empty list', async () => {
    // An empty list would read as "this organization has no ideas", which is a
    // different claim from "we could not ask".
    listMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
  })

  it('labels a draft and a submitted idea differently', async () => {
    mockContext([
      DRAFT,
      {
        ...SOMEONE_ELSES,
        status: 'SUBMITTED',
        submittedAt: '2026-02-01T00:00:00.000Z',
        availableTransitions: [],
      },
    ])
    renderWithRouter(<IdeasWorkspace />)

    // Each state appears as its badge *and* in the details row, so the count
    // is asserted rather than assumed: one badge plus one row per idea.
    //
    // Scoped to the list, because the status filter above offers options with
    // the same words in them. That is not a collision to design around - a
    // filter labelled "Draft" is what it should be called - so the assertion
    // asks about the rows rather than about the whole page.
    await screen.findByText('Their idea')
    const list = within(screen.getByRole('list'))
    expect(list.getAllByText('Draft')).toHaveLength(2)
    expect(list.getAllByText('Submitted')).toHaveLength(2)
  })

  it('shows who each idea is shared with', async () => {
    mockContext([{ ...DRAFT, visibility: 'ORGANIZATION' }])
    renderWithRouter(<IdeasWorkspace />)

    expect(await screen.findByText('This organization')).toBeInTheDocument()
  })

  // --- what is offered ----------------------------------------------------

  it('offers edit and submit only on the signed-in user own draft', async () => {
    mockContext([DRAFT, SOMEONE_ELSES])
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    // One of each: the other idea belongs to somebody else, and offering to
    // edit it would be offering something the server would refuse.
    expect(screen.getAllByRole('button', { name: 'Edit draft' })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: SUBMIT_LABEL })).toHaveLength(1)
  })

  it('offers no edit or submit on somebody elses idea', async () => {
    mockContext([SOMEONE_ELSES])
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Their idea')
    expect(screen.queryByRole('button', { name: 'Edit draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: SUBMIT_LABEL })).not.toBeInTheDocument()
  })

  it('offers no edit or submit once an idea is submitted', async () => {
    // A submitted idea has no further move available to its author, so the
    // server reports none - which is why the fixture says so rather than
    // inheriting the draft's `['SUBMITTED']`.
    mockContext([
      {
        ...DRAFT,
        status: 'SUBMITTED',
        submittedAt: '2026-02-01T00:00:00.000Z',
        availableTransitions: [],
      },
    ])
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: 'Edit draft' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: SUBMIT_LABEL })).not.toBeInTheDocument()
  })

  // --- creating -----------------------------------------------------------

  it('links to the create page rather than opening a form inline', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    expect(screen.getByRole('link', { name: /File a new idea/ })).toHaveAttribute(
      'href',
      '/app/ideas/new',
    )
    expect(screen.queryByRole('heading', { name: 'File a new idea' })).not.toBeInTheDocument()
  })

  it('offers no create link without an active organization', async () => {
    mockContext([], null)
    renderWithRouter(<IdeasWorkspace />)

    await waitFor(() =>
      expect(screen.queryByRole('link', { name: /File a new idea/ })).not.toBeInTheDocument(),
    )
  })

  it('marks the idea the create page just filed', async () => {
    const notice = 'Your idea "Automate the invoice run" was created and submitted for review.'
    render(
      <MemoryRouter
        initialEntries={[
          { pathname: '/app/ideas', state: { notice, tone: 'success', ideaId: '1' } },
        ]}
      >
        <IdeasWorkspace />
      </MemoryRouter>,
    )

    expect(await screen.findByText(notice)).toBeInTheDocument()
    const card = screen.getByText('Automate the invoice run').closest('li')
    expect(card).toHaveAttribute('aria-current', 'true')
    expect(within(card as HTMLElement).getByText('Just filed')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText(notice)).not.toBeInTheDocument()
  })

  it('shows a draft that could not be submitted as a warning', async () => {
    const notice =
      'Your idea "Automate the invoice run" was saved as a draft, but it was not submitted: offline'
    render(
      <MemoryRouter
        initialEntries={[
          { pathname: '/app/ideas', state: { notice, tone: 'warning', ideaId: '1' } },
        ]}
      >
        <IdeasWorkspace />
      </MemoryRouter>,
    )

    expect(await screen.findByText(notice)).toBeInTheDocument()
    expect(screen.getByText(notice).closest('output')).toHaveClass('bg-amber-50')
  })

  it('shows the confirmation carried by the redirect from the create page', async () => {
    const notice = 'Draft saved. Only you can see it.'
    render(
      <MemoryRouter initialEntries={[{ pathname: '/app/ideas', state: { notice } }]}>
        <IdeasWorkspace />
      </MemoryRouter>,
    )

    expect(await screen.findByText(notice)).toBeInTheDocument()
  })

  // --- editing ------------------------------------------------------------

  it('opens the draft in the guided form on its own page', async () => {
    const router = createMemoryRouter(
      [
        { path: '/app/ideas', element: <IdeasWorkspace /> },
        { path: '/app/ideas/:ideaId/edit', element: <p>Edit page</p> },
      ],
      { initialEntries: ['/app/ideas'] },
    )
    render(<RouterProvider router={router} />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))

    expect(await screen.findByText('Edit page')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/app/ideas/1/edit')
  })

  it('has no editor of its own beside the list', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }))

    expect(screen.queryByRole('heading', { name: 'Edit this draft' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('textbox', { name: 'What problem would you like to solve?' }),
    ).toBeNull()
  })

  // --- submitting ---------------------------------------------------------

  it('submits a draft and reports what the backend said happened', async () => {
    transitionMock.mockResolvedValue({
      success: true,
      message: 'Idea moved to Submitted.',
      field: null,
      idea: { ...DRAFT, status: 'SUBMITTED', submittedAt: '2026-02-01T00:00:00.000Z' },
    })
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

    // The backend's wording, not a local string: it is the layer that knows
    // what the transition actually did.
    expect(await screen.findByText('Idea moved to Submitted.')).toBeInTheDocument()
    expect(transitionMock).toHaveBeenCalledWith('1', 'SUBMITTED_TO_ORGANIZATION')
  })

  it('shows a business refusal from a submission as the backend worded it', async () => {
    transitionMock.mockResolvedValue({
      success: false,
      message: 'Choose a category before submitting this idea.',
      field: null,
      idea: null,
    })
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Choose a category before submitting this idea.')
  })

  it('reports a submission transport failure without claiming it went through', async () => {
    transitionMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    expect(screen.queryByText(/Idea moved to/)).not.toBeInTheDocument()
  })

  it('spins only on the idea being submitted', async () => {
    let release: (value: IdeaMutationResult) => void = () => {}
    transitionMock.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    mockContext([DRAFT, SOMEONE_ELSES])
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.click(screen.getByRole('button', { name: SUBMIT_LABEL }))

    await waitFor(() => expect(screen.getByRole('button', { name: SUBMIT_LABEL })).toBeDisabled())
    release({ success: true, message: 'ok', field: null, idea: DRAFT })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: SUBMIT_LABEL })).not.toBeDisabled(),
    )
  })
})
