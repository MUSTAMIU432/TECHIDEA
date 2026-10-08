import { makeIdea } from '../../../test/idea'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithRouter } from '../../../test/renderWithRouter'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeasWorkspace } from './IdeasWorkspace'
import { page } from '../../../test/ideaPage'
import type { Idea } from '../api/ideasApi'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(async () => [
    {
      id: '4',
      name: 'Customer support',
      slug: 'customer-support',
      description: '',
    },
  ]),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  transitionIdeaRequest: vi.fn(),
  ideasRequest: vi.fn(),
  commentsRequest: vi.fn(async () => ({
    items: [],
    pageInfo: page([]).pageInfo,
  })),
  voteIdeaRequest: vi.fn(),
  removeVoteRequest: vi.fn(),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({
  useOrganization: vi.fn(),
}))

const { ideasRequest, voteIdeaRequest, removeVoteRequest } = await import('../api/ideasApi')
const listMock = vi.mocked(ideasRequest)
const voteMock = vi.mocked(voteIdeaRequest)
const removeMock = vi.mocked(removeVoteRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }

function state(voteCount: number, viewerHasVoted: boolean) {
  return {
    success: true,
    message: 'Vote recorded.',
    field: null,
    voteState: { ideaId: '1', voteCount, viewerHasVoted },
  }
}

function mockContext(ideas: Idea[] = [makeIdea()]) {
  vi.mocked(useAuth).mockReturnValue({
    user: SIGNED_IN,
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: { id: '3', name: 'Acme Labs' },
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(page(ideas))
  voteMock.mockResolvedValue(state(1, true))
  removeMock.mockResolvedValue({
    success: true,
    message: 'Vote withdrawn.',
    field: null,
    voteState: { ideaId: '1', voteCount: 0, viewerHasVoted: false },
  })
}

/**
 * The vote toggle on one idea's card, found by its accessible name.
 *
 * Flushes, then queries synchronously, and the order matters: an async
 * `findBy` can resolve in the same tick React commits a re-render, so the node
 * it hands back may be one about to be replaced - and a click on a detached
 * node is silently a no-op, which is a maddening test failure to chase. A
 * settled flush followed by a synchronous query returns a live element, and
 * the caller clicks it in the same tick.
 */
async function voteToggle(title = 'Automate the invoice run') {
  await screen.findByText(title)
  await act(async () => {})

  const card = screen.getByText(title).closest('li') as HTMLElement
  return within(card).getByRole('button', {
    name: /vote for this idea|remove your vote/i,
  })
}

/**
 * Voting in the Ideas UI (S2-006).
 *
 * The claims under test, in the order they would break:
 *
 * - **The displayed count is the server's.** The list arrives with
 *   `voteCount`/`viewerHasVoted` and a vote updates from the mutation's
 *   `voteState`, so there is no local arithmetic to be wrong and nothing to
 *   roll back when a request fails.
 * - **A failure never corrupts what is on screen.** A refused vote and a
 *   transport error both leave the count and the flag exactly as they were,
 *   and both are reported as themselves.
 * - **Two rapid clicks send one request.** The toggle is disabled while a
 *   request is in flight, and the guard is a ref rather than state, because
 *   state cannot be read synchronously from a click handler.
 * - **One idea's response cannot touch another's.** The control state is keyed
 *   by idea id, which is the only reason a page of many controls is safe.
 */
describe('Idea voting (S2-006)', () => {
  beforeEach(() => {
    mockContext()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // --- rendering the state the server sent ----------------------------------

  it('renders the count the list arrived with, without asking again', async () => {
    mockContext([makeIdea({ voteCount: 7, viewerHasVoted: false })])
    renderWithRouter(<IdeasWorkspace />)

    const toggle = await voteToggle()
    expect(toggle).toHaveTextContent('7')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    // The count came with the list: one request for the page, none for votes.
    expect(listMock).toHaveBeenCalledTimes(1)
    expect(voteMock).not.toHaveBeenCalled()
  })

  it('renders an unvoted idea as unvoted and at zero', async () => {
    renderWithRouter(<IdeasWorkspace />)

    const toggle = await voteToggle()
    expect(toggle).toHaveTextContent('0')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
  })

  it('renders a voted idea as voted', async () => {
    mockContext([makeIdea({ voteCount: 3, viewerHasVoted: true })])
    renderWithRouter(<IdeasWorkspace />)

    const toggle = await voteToggle()
    expect(toggle).toHaveTextContent('3')
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
  })

  it('gives every idea on the page its own control', async () => {
    mockContext([
      makeIdea({
        id: '1',
        title: 'First idea',
        voteCount: 2,
        viewerHasVoted: true,
      }),
      makeIdea({
        id: '2',
        title: 'Second idea',
        voteCount: 5,
        viewerHasVoted: false,
      }),
    ])
    renderWithRouter(<IdeasWorkspace />)

    const first = within((await screen.findByText('First idea')).closest('li') as HTMLElement)
    const second = within((await screen.findByText('Second idea')).closest('li') as HTMLElement)

    expect(first.getByRole('button', { name: /remove your vote/i })).toHaveTextContent('2')
    expect(second.getByRole('button', { name: /vote for this idea/i })).toHaveTextContent('5')
  })

  // --- voting ---------------------------------------------------------------

  it('votes and shows the count the server reported', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await voteToggle()

    fireEvent.click(await voteToggle())

    await waitFor(() => expect(voteMock).toHaveBeenCalledWith('1'))
    // The server said 1, not "0 plus one". Same number here, but the number
    // that arrives is the one the database holds.
    expect(await screen.findByText('1')).toBeInTheDocument()
  })

  it('marks the idea as voted after a successful vote', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await voteToggle()

    fireEvent.click(await voteToggle())

    // Re-queried rather than asserting on a captured node: the list re-renders
    // around the control, and a reference held across that render can be a
    // detached node which passes or fails depending on timing.
    await waitFor(async () => expect(await voteToggle()).toHaveAttribute('aria-pressed', 'true'))
  })

  it('sends only the idea id, never a voter', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await voteToggle()

    fireEvent.click(await voteToggle())

    await waitFor(() => expect(voteMock).toHaveBeenCalledWith('1'))
    // The signed-in user is the voter; there is no argument that could say
    // otherwise, so this client cannot express voting for somebody else.
    expect(voteMock.mock.calls[0]).toHaveLength(1)
  })

  it('takes the count the server sent even when it is not the expected one', async () => {
    // Somebody else voted between the list arriving and this click, so the
    // count is not `previous + 1`. Local arithmetic would render 1.
    voteMock.mockResolvedValue(state(9, true))
    renderWithRouter(<IdeasWorkspace />)
    await voteToggle()

    fireEvent.click(await voteToggle())

    expect(await screen.findByText('9')).toBeInTheDocument()
  })

  // --- withdrawing ----------------------------------------------------------

  it('removes a vote and shows the new count', async () => {
    mockContext([makeIdea({ voteCount: 3, viewerHasVoted: true })])
    renderWithRouter(<IdeasWorkspace />)

    fireEvent.click(await voteToggle())

    await waitFor(() => expect(removeMock).toHaveBeenCalledWith('1'))
    const after = await voteToggle()
    expect(after).toHaveAttribute('aria-pressed', 'false')
    expect(after).toHaveTextContent('0')
  })

  it('the same toggle votes and un-votes', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const toggle = await voteToggle()

    fireEvent.click(toggle)
    await waitFor(() => expect(toggle).toHaveAttribute('aria-pressed', 'true'))

    fireEvent.click(toggle)
    await waitFor(() => expect(toggle).toHaveAttribute('aria-pressed', 'false'))

    expect(voteMock).toHaveBeenCalledTimes(1)
    expect(removeMock).toHaveBeenCalledTimes(1)
  })

  // --- pending and duplicate clicks ----------------------------------------

  it('disables the toggle while a vote is in flight', async () => {
    let release: (() => void) | null = null
    voteMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve(state(1, true))
        }),
    )
    renderWithRouter(<IdeasWorkspace />)
    const toggle = await voteToggle()

    fireEvent.click(toggle)

    await waitFor(() => expect(toggle).toBeDisabled())
    await act(async () => {
      release?.()
    })
    await waitFor(() => expect(toggle).toBeEnabled())
  })

  it('sends one request when the toggle is clicked twice in a row', async () => {
    let release: (() => void) | null = null
    voteMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve(state(1, true))
        }),
    )
    renderWithRouter(<IdeasWorkspace />)
    const toggle = await voteToggle()

    fireEvent.click(toggle)
    // The impatient double-click. The server is idempotent so a second request
    // would be harmless, but this client should not send one.
    fireEvent.click(toggle)

    expect(voteMock).toHaveBeenCalledTimes(1)
    await act(async () => {
      release?.()
    })
    expect(await screen.findByText('1')).toBeInTheDocument()
  })

  it('does not send a second request while the first is still pending', async () => {
    let release: (() => void) | null = null
    voteMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve(state(1, true))
        }),
    )
    renderWithRouter(<IdeasWorkspace />)
    const toggle = await voteToggle()

    fireEvent.click(toggle)
    await waitFor(() => expect(toggle).toBeDisabled())
    fireEvent.click(toggle)

    expect(voteMock).toHaveBeenCalledTimes(1)
    await act(async () => {
      release?.()
    })
  })

  // --- failures -------------------------------------------------------------

  it('reports a refused vote and leaves the count alone', async () => {
    voteMock.mockResolvedValue({
      success: false,
      message: 'Idea is unavailable.',
      field: null,
      voteState: null,
    })
    renderWithRouter(<IdeasWorkspace />)

    fireEvent.click(await voteToggle())

    expect(await screen.findByText('Idea is unavailable.')).toBeInTheDocument()
    // Nothing guessed, so nothing to corrupt: the count and the flag are as
    // the list delivered them.
    const after = await voteToggle()
    expect(after).toHaveTextContent('0')
    expect(after).toHaveAttribute('aria-pressed', 'false')
  })

  it('reports a transport failure as itself', async () => {
    voteMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)

    fireEvent.click(await voteToggle())

    expect(
      await screen.findByText('We could not reach the server. Please try again.'),
    ).toBeInTheDocument()
    expect(await voteToggle()).toHaveTextContent('0')
  })

  it('reports a failed removal and leaves the vote in place', async () => {
    mockContext([makeIdea({ voteCount: 3, viewerHasVoted: true })])
    removeMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)

    fireEvent.click(await voteToggle())

    expect(
      await screen.findByText('We could not reach the server. Please try again.'),
    ).toBeInTheDocument()
    // The reader's vote is still shown as cast, because it is.
    const after = await voteToggle()
    expect(after).toHaveAttribute('aria-pressed', 'true')
    expect(after).toHaveTextContent('3')
  })

  it('lets the reader dismiss an error and try again', async () => {
    voteMock.mockRejectedValueOnce(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)
    fireEvent.click(await voteToggle())
    await screen.findByText('We could not reach the server. Please try again.')

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(
      screen.queryByText('We could not reach the server. Please try again.'),
    ).not.toBeInTheDocument()
    // Usable again rather than stuck disabled by a failed request.
    expect(await voteToggle()).toBeEnabled()
  })

  it('recovers on a retry after a failure', async () => {
    voteMock.mockRejectedValueOnce(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)
    fireEvent.click(await voteToggle())
    await screen.findByText('We could not reach the server. Please try again.')

    fireEvent.click(await voteToggle())

    await waitFor(async () => expect(await voteToggle()).toHaveAttribute('aria-pressed', 'true'))
  })

  // --- one idea's response cannot touch another's --------------------------

  it("a late vote response updates only its own idea's control", async () => {
    mockContext([
      makeIdea({ id: '1', title: 'First idea' }),
      makeIdea({ id: '2', title: 'Second idea' }),
    ])
    const releases = new Map<string, () => void>()
    voteMock.mockImplementation(
      (id: string) =>
        new Promise((resolve) => {
          releases.set(id, () =>
            resolve({
              success: true,
              message: 'Vote recorded.',
              field: null,
              voteState: { ideaId: id, voteCount: 5, viewerHasVoted: true },
            }),
          )
        }),
    )
    renderWithRouter(<IdeasWorkspace />)

    const firstToggle = within((await screen.findByText('First idea')).closest('li') as HTMLElement)
    const secondToggle = within(
      (await screen.findByText('Second idea')).closest('li') as HTMLElement,
    )

    // Two controls in flight at once, which is the normal state of a page.
    fireEvent.click(firstToggle.getByRole('button', { name: /vote for this idea/i }))
    fireEvent.click(secondToggle.getByRole('button', { name: /vote for this idea/i }))

    // The *second* idea's response lands first.
    await act(async () => {
      releases.get('2')?.()
    })
    await waitFor(() =>
      expect(secondToggle.getByRole('button', { name: /remove your vote/i })).toHaveTextContent(
        '5',
      ),
    )
    // ...and the first idea's own response then lands, updating only it.
    await act(async () => {
      releases.get('1')?.()
    })
    await waitFor(() =>
      expect(firstToggle.getByRole('button', { name: /remove your vote/i })).toHaveTextContent('5'),
    )
  })

  it('an idea keeps its own state when another is voted on', async () => {
    mockContext([
      makeIdea({
        id: '1',
        title: 'First idea',
        voteCount: 1,
        viewerHasVoted: true,
      }),
      makeIdea({
        id: '2',
        title: 'Second idea',
        voteCount: 0,
        viewerHasVoted: false,
      }),
    ])
    renderWithRouter(<IdeasWorkspace />)
    const secondToggle = within(
      (await screen.findByText('Second idea')).closest('li') as HTMLElement,
    ).getByRole('button', { name: /vote for this idea/i })
    const firstToggle = await voteToggle('First idea')

    fireEvent.click(secondToggle)
    await waitFor(() => expect(secondToggle).toHaveAttribute('aria-pressed', 'true'))

    // The other idea is untouched: the state is keyed by idea id.
    expect(firstToggle).toHaveAttribute('aria-pressed', 'true')
    expect(firstToggle).toHaveTextContent('1')
  })

  // --- the discovery context survives ---------------------------------------

  it('does not re-fetch the list when a vote is cast', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await voteToggle()

    fireEvent.click(await voteToggle())

    await waitFor(() => expect(voteMock).toHaveBeenCalled())
    // One request for the ideas, and no reload: voting is not a change to the
    // ideas, so the reader's filters and page are untouched by construction.
    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it('preserves the filters and the page through a vote', async () => {
    renderWithRouter(<IdeasWorkspace />)
    await voteToggle()
    const category = screen.getByLabelText('Category')

    fireEvent.change(category, { target: { value: '4' } })
    expect(category).toHaveValue('4')
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThanOrEqual(2))
    const callsBeforeVote = listMock.mock.calls.length

    fireEvent.click(await voteToggle())

    await waitFor(() => expect(voteMock).toHaveBeenCalled())
    // The filter is still applied, and voting did not send the reader back to
    // page one or drop the filter.
    expect(category).toHaveValue('4')
    expect(listMock.mock.calls.length).toBe(callsBeforeVote)
  })

  it('shows vote controls on a page of ideas without a request per idea', async () => {
    mockContext([
      makeIdea({ id: '1', title: 'One', voteCount: 1 }),
      makeIdea({ id: '2', title: 'Two', voteCount: 2 }),
      makeIdea({ id: '3', title: 'Three', voteCount: 3 }),
    ])
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('One')

    expect(listMock).toHaveBeenCalledTimes(1)
    expect(voteMock).not.toHaveBeenCalled()
  })

  // --- nothing adjacent ----------------------------------------------------

  it('offers no attachment, review or proposal control', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const toggle = await voteToggle()
    const card = (await screen.findByText('Automate the invoice run')).closest('li') as HTMLElement

    // S2-007 and later sprints. A card carries votes and nothing adjacent.
    expect(within(card).queryByRole('button', { name: /attach|upload|file/i })).toBeNull()
    expect(within(card).queryByRole('button', { name: /proposal|developer|match/i })).toBeNull()
    expect(toggle).toBeInTheDocument()
  })
})
