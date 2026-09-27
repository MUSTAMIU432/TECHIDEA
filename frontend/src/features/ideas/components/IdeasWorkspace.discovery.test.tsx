import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeasWorkspace } from './IdeasWorkspace'
import { page, pagedPage } from '../../../test/ideaPage'
import type { Idea, IdeaFilters, IdeaPage } from '../api/ideasApi'

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

const { categoriesRequest, organizationIdeasRequest, transitionIdeaRequest } =
  await import('../api/ideasApi')
const listMock = vi.mocked(organizationIdeasRequest)
const categoriesMock = vi.mocked(categoriesRequest)
const transitionMock = vi.mocked(transitionIdeaRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }
const CATEGORIES = [
  { id: '4', name: 'Customer support', slug: 'customer-support', description: '' },
  { id: '5', name: 'Finance', slug: 'finance', description: '' },
]

function idea(overrides: Partial<Idea> = {}): Idea {
  return {
    id: '1',
    title: 'Automate the invoice run',
    description: 'A description long enough.',
    status: 'DRAFT',
    visibility: 'PRIVATE',
    submittedAt: null,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    authorId: '7',
    organizationId: '3',
    category: null,
    availableTransitions: [],
    discussionOpen: true,
    ...overrides,
  }
}

/**
 * A server that answers for the window it was asked for.
 *
 * A fixed answer is enough to test the first page and useless for the rest:
 * paging is a conversation, and a mock that always replies with page 3 leaves
 * the buttons reading an offset the reader is not at.
 */
function echoPage(total: number, rows: Idea[] = [idea()]) {
  listMock.mockImplementation(async (_organizationId, filters = {}) => {
    const offset = filters.offset ?? 0
    const limit = filters.limit ?? 20
    return {
      items: rows,
      pageInfo: {
        offset,
        limit,
        totalCount: total,
        hasNextPage: offset + limit < total,
        hasPreviousPage: offset > 0,
      },
    }
  })
}

/** The filters the most recent request was made with. */
function lastFilters(): IdeaFilters {
  const call = listMock.mock.calls.at(-1)
  if (call === undefined) throw new Error('no request was made')
  return call[1] ?? {}
}

function mockContext(answer: IdeaPage = page([idea()])) {
  vi.mocked(useAuth).mockReturnValue({
    user: SIGNED_IN,
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: { id: '3', name: 'Acme Labs' },
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(answer)
  categoriesMock.mockResolvedValue(CATEGORIES)
}

/**
 * The pagination summary as one string.
 *
 * Read as text rather than as three `getByText` calls because the numbers are
 * separate elements: a range is a single sentence to a reader, and asserting
 * on its pieces would let "Showing 1- of 137" pass.
 */
function pageSummary(): string {
  const nav = screen.getByRole('navigation', { name: 'Ideas pagination' })
  const summary = nav.querySelector('p')
  return (summary?.textContent ?? '').replace(/\s+/g, ' ').trim()
}

const searchBox = () => screen.getByLabelText('Search')
const categorySelect = () => screen.getByLabelText('Category')
const statusSelect = () => screen.getByLabelText('Status')

/**
 * Discovery (S2-004), asserted through the workspace.
 *
 * Three claims are under test, and they are the claims the feature rests on:
 *
 * - **The controls reach the server, and reach it as filters.** A category, a
 *   status and a search each become an argument on the request. None of them
 *   is applied to the rows on screen, because that is the server's rule — so
 *   the assertions are about what was *asked for*, not about what is
 *   displayed.
 * - **A narrowing filter returns to the first page.** Held in one place, and
 *   the reason it is tested here rather than trusted: it is the one rule that
 *   can produce "showing 40-60 of 12" if it is ever dropped.
 * - **Paging is arithmetic on the server's numbers**, and the buttons move by
 *   the page size the server applied rather than a page index this client
 *   invented.
 */
describe('IdeasWorkspace discovery (S2-004)', () => {
  beforeEach(() => {
    mockContext()
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  // --- the controls reach the server ---------------------------------------

  it('offers the categories the server listed, and no others', async () => {
    render(<IdeasWorkspace />)

    const select = (await screen.findByLabelText('Category')) as HTMLSelectElement
    const options = within(select)
      .getAllByRole('option')
      .map((option) => option.textContent)

    // "All categories" is the absence of a filter, and the server's two active
    // categories are the rest. A retired category is not sent and so cannot
    // be offered - the picker cannot know about what the server does not list.
    expect(options).toEqual(['All categories', 'Customer support', 'Finance'])
  })

  it('sends a category filter when one is chosen', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.change(categorySelect(), { target: { value: '4' } })

    await waitFor(() => expect(lastFilters().categoryId).toBe('4'))
  })

  it('sends a status filter when one is chosen', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.change(statusSelect(), { target: { value: 'SUBMITTED' } })

    await waitFor(() => expect(lastFilters().status).toBe('SUBMITTED'))
  })

  it('composes category, status and search into one request', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    fireEvent.change(categorySelect(), { target: { value: '4' } })
    fireEvent.change(statusSelect(), { target: { value: 'SUBMITTED' } })
    await waitFor(() => expect(lastFilters().categoryId).toBe('4'))
    fireEvent.change(searchBox(), { target: { value: 'invoice' } })
    await waitFor(() => expect(lastFilters().search).toBe('invoice'))

    expect(lastFilters()).toMatchObject({
      categoryId: '4',
      status: 'SUBMITTED',
      search: 'invoice',
      offset: 0,
    })
  })

  it('drops the filter again when the control is put back to all', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')
    fireEvent.change(categorySelect(), { target: { value: '4' } })
    await waitFor(() => expect(lastFilters().categoryId).toBe('4'))

    fireEvent.change(categorySelect(), { target: { value: '' } })

    await waitFor(() => expect(lastFilters().categoryId).toBeNull())
  })

  it('clears every filter at once, and empties the search box', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')
    fireEvent.change(categorySelect(), { target: { value: '4' } })
    fireEvent.change(statusSelect(), { target: { value: 'REJECTED' } })
    await waitFor(() => expect(lastFilters().categoryId).toBe('4'))
    fireEvent.change(searchBox(), { target: { value: 'invoice' } })
    await waitFor(() => expect(lastFilters().search).toBe('invoice'))

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))

    await waitFor(() =>
      expect(lastFilters()).toMatchObject({
        search: null,
        categoryId: null,
        status: null,
        offset: 0,
      }),
    )
    expect(searchBox()).toHaveValue('')
  })

  it('asks for a search only once the typing pauses', async () => {
    vi.useFakeTimers()
    render(<IdeasWorkspace />)
    // Let the mount fetch settle before counting requests, or the first
    // render's call is counted as a search.
    await act(async () => {})

    const before = listMock.mock.calls.length
    fireEvent.change(searchBox(), { target: { value: 'i' } })
    fireEvent.change(searchBox(), { target: { value: 'in' } })
    fireEvent.change(searchBox(), { target: { value: 'inv' } })

    // Three keystrokes, no requests: a reader typing a word should not send a
    // query per letter.
    expect(listMock.mock.calls.length).toBe(before)

    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    expect(listMock.mock.calls.length).toBe(before + 1)
    expect(lastFilters().search).toBe('inv')
  })

  it('never searches for a term that was typed and then replaced', async () => {
    vi.useFakeTimers()
    render(<IdeasWorkspace />)
    await act(async () => {})

    fireEvent.change(searchBox(), { target: { value: 'invoice' } })
    fireEvent.change(searchBox(), { target: { value: 'invoicing' } })
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    // The superseded term never reaches the server at all, rather than
    // reaching it and being overwritten — which is what would leave the list
    // showing results for a word that is no longer in the box if the two
    // responses arrived out of order.
    const searches = listMock.mock.calls.map((call) => (call[1] ?? {}).search)
    expect(searches.filter(Boolean)).toEqual(['invoicing'])
  })

  it('trims a search before sending it', async () => {
    vi.useFakeTimers()
    render(<IdeasWorkspace />)
    await act(async () => {})

    fireEvent.change(searchBox(), { target: { value: '   ' } })
    await act(async () => {
      vi.advanceTimersByTime(300)
    })

    // Whitespace is not a search term. Sending it would ask the server for
    // everything and look, in the box, like a filter is applied.
    expect(lastFilters().search).toBeNull()
  })

  // --- narrowing returns to the first page ---------------------------------

  it('returns to the first page when a filter narrows the result', async () => {
    mockContext(pagedPage([idea()], 40, { offset: 20, limit: 20 }))
    render(<IdeasWorkspace />)
    await screen.findByRole('navigation', { name: 'Ideas pagination' })

    fireEvent.change(categorySelect(), { target: { value: '4' } })

    // Filtered from page 2, and the filter matches a different set of rows, so
    // staying on page 2 would ask for a window of a result that may not have
    // one.
    await waitFor(() => expect(lastFilters().offset).toBe(0))
  })

  it('returns to the first page when a search narrows the result', async () => {
    mockContext(pagedPage([idea()], 40, { offset: 20, limit: 20 }))
    render(<IdeasWorkspace />)
    await screen.findByRole('navigation', { name: 'Ideas pagination' })

    fireEvent.change(searchBox(), { target: { value: 'invoice' } })
    await waitFor(() => expect(lastFilters().offset).toBe(0))
  })

  // --- paging ---------------------------------------------------------------

  it('reports the window the server applied, not the rows it returned', async () => {
    mockContext(pagedPage([idea({ id: '1' })], 137, { offset: 40, limit: 20 }))
    // Answered as though the reader asked for page 3, which is the state the
    // fixture describes.
    render(<IdeasWorkspace />)

    // One idea on screen, and the reader is told there are 137. The count is
    // the server's because the client has never seen the other 136.
    expect(await screen.findByRole('navigation', { name: 'Ideas pagination' })).toBeInTheDocument()
    expect(pageSummary()).toBe('Showing 41-60 of 137')
  })

  it('does not print a range past the end on the last page', async () => {
    mockContext()
    echoPage(25)
    render(<IdeasWorkspace />)
    await waitFor(() => expect(lastFilters().offset).toBe(0))

    fireEvent.click(await screen.findByRole('button', { name: 'Next' }))

    // 21-25, not 21-40: the end of the range is clamped to what exists, so the
    // reader is not told there are 40 of something there are 25 of.
    await waitFor(() => expect(pageSummary()).toBe('Showing 21-25 of 25'))
  })

  it('moves forward by the page size the server applied', async () => {
    mockContext()
    echoPage(137)
    render(<IdeasWorkspace />)
    await screen.findByRole('navigation', { name: 'Ideas pagination' })

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    // A page *number* would be 20 here too, but only because this page size
    // is the default. The applied `limit` is what the buttons use, so a server
    // that clamped 1000 to 50 is paged by 50.
    await waitFor(() => expect(lastFilters().offset).toBe(20))
  })

  it('moves back by the applied page size, and never below zero', async () => {
    mockContext()
    echoPage(137)
    render(<IdeasWorkspace />)
    fireEvent.click(await screen.findByRole('button', { name: 'Next' }))
    await waitFor(() => expect(lastFilters().offset).toBe(20))
    expect(pageSummary()).toBe('Showing 21-40 of 137')

    fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
    await waitFor(() => expect(lastFilters().offset).toBe(0))
    expect(pageSummary()).toBe('Showing 1-20 of 137')

    // Already at the start, and the button says so rather than sending a
    // negative offset and being clamped back to zero.
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
  })

  it('disables the buttons at the ends and only there', async () => {
    mockContext()
    echoPage(137)
    render(<IdeasWorkspace />)
    await screen.findByRole('navigation', { name: 'Ideas pagination' })

    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()
  })

  it('paging keeps the filters that got us here', async () => {
    mockContext()
    echoPage(137)
    render(<IdeasWorkspace />)
    await screen.findByRole('navigation', { name: 'Ideas pagination' })
    fireEvent.change(categorySelect(), { target: { value: '4' } })
    await waitFor(() => expect(lastFilters().categoryId).toBe('4'))

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    // Page 2 of the *filtered* result, which is the only page 2 that means
    // anything to the reader.
    await waitFor(() => expect(lastFilters().offset).toBe(20))
    expect(lastFilters().categoryId).toBe('4')
  })

  it('offers no pager at all when everything fits on one page', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    // "Showing 1-1 of 1" with both buttons disabled is a control that cannot
    // be used, telling the reader nothing they cannot see. It appears when
    // there is a second page to go to.
    expect(screen.queryByRole('navigation', { name: 'Ideas pagination' })).not.toBeInTheDocument()
  })

  // --- the three empties ----------------------------------------------------

  it('distinguishes "nothing here" from "nothing matches"', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    listMock.mockResolvedValue(page([]))
    fireEvent.change(categorySelect(), { target: { value: '4' } })

    // A filter that matched nothing is the filter working, not an absence, and
    // the message says which — so the reader does not go looking for a bug.
    expect(await screen.findByText('Nothing matches those filters')).toBeInTheDocument()
    expect(screen.queryByText('No ideas here yet')).not.toBeInTheDocument()
  })

  it('offers a way back from a page that is now past the end', async () => {
    mockContext()
    echoPage(40)
    render(<IdeasWorkspace />)
    await waitFor(() => expect(lastFilters().offset).toBe(0))

    // The reader is on page 2, and the rows on page 3 are gone: ideas were
    // removed, or a filter narrowed elsewhere, between paging and reading. The
    // count still says 40, so this is not "there is nothing here" and it must
    // not be rendered as though it were.
    listMock.mockResolvedValue(page([], { totalCount: 40 }))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(await screen.findByText('Nothing on this page')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Back to the first page' }))
    await waitFor(() => expect(lastFilters().offset).toBe(0))
  })

  // --- failures and races ---------------------------------------------------

  it('keeps the previous page on screen while a new filter is in flight', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    let release: (() => void) | null = null
    listMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = () => resolve(page([idea({ id: '2', title: 'A second idea' })]))
        }),
    )
    fireEvent.change(categorySelect(), { target: { value: '4' } })

    // The rows for the *old* filter are still there, marked busy and dimmed.
    // Replacing them with a spinner on every keystroke would make typing
    // unusable; showing them at full strength would claim they are the results
    // for the filter now chosen.
    const busy = document.querySelector('[aria-busy="true"]')
    expect(busy).not.toBeNull()
    expect(screen.getByText('Automate the invoice run')).toBeInTheDocument()

    await act(async () => {
      release?.()
    })
    expect(await screen.findByText('A second idea')).toBeInTheDocument()
  })

  it('shows the results for the newest query when two arrive out of order', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    const releases: (() => void)[] = []
    listMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          releases.push(() => resolve(page([idea({ id: '9', title: 'Resolved second' })])))
        }),
    )

    fireEvent.change(categorySelect(), { target: { value: '4' } })
    fireEvent.change(statusSelect(), { target: { value: 'REJECTED' } })
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThanOrEqual(3))

    // Settle the newest request first, then the older one that is now stale.
    // A typeahead makes this the normal case rather than an exotic one, and
    // the loser must not overwrite the winner.
    await act(async () => {
      releases.at(-1)?.()
    })
    expect(await screen.findByText('Resolved second')).toBeInTheDocument()

    await act(async () => {
      releases[0]?.()
    })
    expect(screen.getByText('Resolved second')).toBeInTheDocument()
  })

  it('reports a failed page as a failure, not as an empty result', async () => {
    mockContext(pagedPage([idea()], 137))
    render(<IdeasWorkspace />)
    await screen.findByRole('navigation', { name: 'Ideas pagination' })

    listMock.mockRejectedValue(new Error('Failed to fetch'))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
  })

  // --- a write does not disturb the discovery state -------------------------

  it('keeps the filters and the page when a write reloads the list', async () => {
    mockContext(
      pagedPage(
        [idea({ id: '2', title: 'A submitted idea', availableTransitions: ['SUBMITTED'] })],
        137,
        { offset: 20 },
      ),
    )
    render(<IdeasWorkspace />)
    await screen.findByRole('navigation', { name: 'Ideas pagination' })
    fireEvent.change(categorySelect(), { target: { value: '4' } })
    await waitFor(() => expect(lastFilters().categoryId).toBe('4'))

    transitionMock.mockResolvedValue({
      success: true,
      message: 'Idea submitted.',
      field: null,
      idea: idea({ id: '2', status: 'SUBMITTED', availableTransitions: [] }),
    })
    fireEvent.click(screen.getByRole('button', { name: 'Submit for review' }))

    // The write re-asks, and the answer keeps the reader where they were. A
    // remount here would drop them on page 1 with no filters, which is the
    // kind of thing that loses somebody's place.
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThanOrEqual(3))
    expect(pageSummary()).toBe('Showing 21-40 of 137')
    expect(categorySelect()).toHaveValue('4')
  })
})
