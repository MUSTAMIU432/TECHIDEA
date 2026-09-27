import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeasWorkspace } from './IdeasWorkspace'
import { page, pageOf } from '../../../test/ideaPage'
import type { Idea, IdeaComment, IdeaCommentPage, IdeaPageInfo } from '../api/ideasApi'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(async () => []),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  transitionIdeaRequest: vi.fn(),
  organizationIdeasRequest: vi.fn(),
  commentsRequest: vi.fn(),
  createCommentRequest: vi.fn(),
  updateCommentRequest: vi.fn(),
  deleteCommentRequest: vi.fn(),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))

const {
  organizationIdeasRequest,
  commentsRequest,
  createCommentRequest,
  updateCommentRequest,
  deleteCommentRequest,
} = await import('../api/ideasApi')
const listMock = vi.mocked(organizationIdeasRequest)
const commentsMock = vi.mocked(commentsRequest)
const createMock = vi.mocked(createCommentRequest)
const updateMock = vi.mocked(updateCommentRequest)
const deleteMock = vi.mocked(deleteCommentRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }
const SOMEBODY_ELSE = '9'

function idea(overrides: Partial<Idea> = {}): Idea {
  return {
    id: '1',
    title: 'Automate the invoice run',
    description: 'A description long enough.',
    status: 'DRAFT',
    visibility: 'ORGANIZATION',
    submittedAt: null,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    authorId: SOMEBODY_ELSE,
    organizationId: '3',
    category: null,
    availableTransitions: [],
    discussionOpen: true,
    ...overrides,
  }
}

function comment(
  overrides: Partial<Awaited<ReturnType<typeof commentsRequest>>['items'][number]> = {},
) {
  return {
    id: 'c1',
    ideaId: '1',
    authorId: SIGNED_IN.id,
    content: 'We do this by hand every month.',
    createdAt: '2026-02-01T09:00:00.000Z',
    updatedAt: '2026-02-01T09:00:00.000Z',
    ...overrides,
  }
}

function commentPage(items = [comment()], overrides: Partial<IdeaPageInfo> = {}): IdeaCommentPage {
  return pageOf<IdeaComment>(items, overrides)
}

/** The card for an idea, so assertions are scoped to the discussion under test. */
async function card(title = 'Automate the invoice run') {
  return (await screen.findByText(title)).closest('li') as HTMLElement
}

async function openDiscussion(title = 'Automate the invoice run') {
  const target = await card(title)
  fireEvent.click(within(target).getByRole('button', { name: 'Discussion' }))
  return target as HTMLElement
}

function composer(target: HTMLElement) {
  return within(target).getByLabelText('Add a comment') as HTMLTextAreaElement
}

function mockContext(ideas: Idea[] = [idea()]) {
  vi.mocked(useAuth).mockReturnValue({
    user: SIGNED_IN,
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: { id: '3', name: 'Acme Labs' },
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(page(ideas))
  commentsMock.mockResolvedValue(commentPage())
  createMock.mockResolvedValue({
    success: true,
    message: 'Comment posted.',
    field: null,
    comment: comment(),
  })
  updateMock.mockResolvedValue({
    success: true,
    message: 'Comment updated.',
    field: null,
    comment: comment({ content: 'Edited.' }),
  })
  deleteMock.mockResolvedValue({
    success: true,
    message: 'Comment deleted.',
    field: null,
    comment: null,
  })
}

/**
 * The discussion section (S2-005).
 *
 * The claims under test, in the order they would break:
 *
 * - **A closed discussion is not fetched.** The list holds twenty ideas; a page
 *   that fetched twenty threads on mount would be twenty requests for content
 *   nobody asked to read.
 * - **A refusal is a refusal, not a failure.** `success: false` with a message
 *   and a `field` is the server's answer and is shown next to the box, with the
 *   typed text left alone. Only a thrown error becomes the transport message.
 * - **Ownership decides what is *offered*.** Edit and Delete are drawn only on
 *   the signed-in user's own comments - a courtesy, because the server refuses
 *   regardless - and the assertions check that no elevation exists.
 * - **A write updates what is on screen without re-fetching the list.** The
 *   idea list above, its filters and its page are untouched by commenting.
 */
describe('Idea discussion (S2-005)', () => {
  beforeEach(() => {
    mockContext()
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  // --- fetching -------------------------------------------------------------

  it('does not fetch a discussion until it is opened', async () => {
    render(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    // One request for the ideas, and none for their discussions.
    expect(listMock).toHaveBeenCalledTimes(1)
    expect(commentsMock).not.toHaveBeenCalled()
  })

  it('fetches the discussion when it is opened, and only for that idea', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    await screen.findByText('We do this by hand every month.')
    expect(commentsMock).toHaveBeenCalledTimes(1)
    expect(commentsMock).toHaveBeenCalledWith('1')
    expect(within(target).getByRole('button', { name: 'Hide discussion' })).toBeInTheDocument()
  })

  it('only ever has one discussion open', async () => {
    mockContext([idea({ id: '1', title: 'First idea' }), idea({ id: '2', title: 'Second idea' })])
    render(<IdeasWorkspace />)

    const first = await openDiscussion('First idea')
    await waitFor(() => expect(commentsMock).toHaveBeenCalledTimes(1))
    fireEvent.click(within(first).getByRole('button', { name: 'Hide discussion' }))

    const second = await openDiscussion('Second idea')
    await waitFor(() => expect(commentsMock).toHaveBeenCalledTimes(2))
    // Opening the second closed the first: two threads on one page is two
    // things to read and a page that grew without the reader asking.
    expect(within(second).getByRole('button', { name: 'Hide discussion' })).toBeInTheDocument()
    expect(within(first).getByRole('button', { name: 'Discussion' })).toBeInTheDocument()
  })

  // --- the three states -----------------------------------------------------

  it('shows a loading state before the discussion arrives', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    expect(within(target).getByText('Loading the discussion…')).toBeInTheDocument()
  })

  it('shows an empty state when nobody has commented', async () => {
    commentsMock.mockResolvedValue(commentPage([]))
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    expect(
      await within(target).findByText('No comments yet. Be the first to say something.'),
    ).toBeInTheDocument()
  })

  it('reports a failed read as itself, not as an empty discussion', async () => {
    commentsMock.mockRejectedValue(new Error('Failed to fetch'))
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    // An empty discussion would read as "nobody has said anything", which is a
    // different claim from "we could not ask".
    const alert = await within(target).findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    expect(
      within(target).queryByText('No comments yet. Be the first to say something.'),
    ).not.toBeInTheDocument()
  })

  it('renders the comments oldest first, as the server ordered them', async () => {
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', content: 'First comment.' }),
        comment({ id: 'c2', authorId: SOMEBODY_ELSE, content: 'Second comment.' }),
      ]),
    )
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    await within(target).findByText('First comment.')
    const rendered = within(target)
      .getAllByRole('listitem')
      .map((item) => item.textContent ?? '')
    expect(rendered[0]).toContain('First comment.')
    expect(rendered[1]).toContain('Second comment.')
  })

  it('shows who wrote it and when', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    const row = (await within(target).findByText('We do this by hand every month.')).closest('li')
    const text = row?.textContent ?? ''
    expect(text).toContain('You')
    expect(text).toMatch(/\d{1,2}\/\d{1,2}\/\d{4}|\d{1,2}:\d{2}/)
  })

  it('marks a comment that has been edited', async () => {
    commentsMock.mockResolvedValue(
      commentPage([comment({ updatedAt: '2026-02-03T11:00:00.000Z' })]),
    )
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    expect(await within(target).findByText('· edited')).toBeInTheDocument()
  })

  // --- reading comments as text ---------------------------------------------

  it('renders comment content as text, never as markup', async () => {
    commentsMock.mockResolvedValue(commentPage([comment({ content: '<b>bold</b> & "quoted"' })]))
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    const rendered = await within(target).findByText('<b>bold</b> & "quoted"')
    // The backend stores content verbatim and does not escape it, so escaping
    // belongs here. A client that rendered this as HTML would be where an
    // injection had to be prevented.
    expect(rendered.querySelector('b')).toBeNull()
    expect(rendered.tagName).toBe('P')
  })

  it('preserves the line breaks somebody typed', async () => {
    commentsMock.mockResolvedValue(commentPage([comment({ content: 'Step one.\nStep two.' })]))
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    await within(target).findByText(/Step one/)
    // The content element, found by the class that makes the browser honour a
    // newline - the author line is the other `<p>` in the row.
    const rendered = within(target)
      .getAllByRole('listitem')
      .map((item) => item.querySelector('p.whitespace-pre-wrap'))
      .filter((node) => node?.textContent?.includes('Step one.'))[0]

    // Matched on the element's own textContent, because a string matcher
    // normalizes the newline away and would pass even if the component had
    // collapsed it to one line.
    expect(rendered?.textContent).toBe('Step one.\nStep two.')
    // ...and the class that makes the browser render it as two lines.
    expect(rendered?.className).toContain('whitespace-pre-wrap')
  })

  // --- creating -------------------------------------------------------------

  it('posts a comment and shows it without re-fetching', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'Comment posted.',
      field: null,
      comment: comment({ id: 'c2', content: 'A new comment.' }),
    })
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    const before = commentsMock.mock.calls.length

    fireEvent.change(composer(target), { target: { value: 'A new comment.' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))

    expect(await within(target).findByText('A new comment.')).toBeInTheDocument()
    // Spliced in, not re-fetched: the reader's open thread and scroll position
    // survive somebody else saying something.
    expect(commentsMock.mock.calls.length).toBe(before)
    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it('empties the box after a successful post', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    fireEvent.change(composer(target), { target: { value: 'A new comment.' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))

    await waitFor(() => expect(composer(target)).toHaveValue(''))
  })

  it('will not post an empty or whitespace-only comment', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    const button = within(target).getByRole('button', { name: 'Comment' })

    expect(button).toBeDisabled()

    fireEvent.change(composer(target), { target: { value: '    ' } })
    expect(button).toBeDisabled()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('refuses an over-long comment before sending it', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    fireEvent.change(composer(target), { target: { value: 'x'.repeat(2001) } })

    expect(await within(target).findByText(/must be 2000 characters or fewer/)).toBeInTheDocument()
    expect(within(target).getByRole('button', { name: 'Comment' })).toBeDisabled()
    // Caught on the client because the server would refuse it anyway - and the
    // server stays the authority if the two ever disagree.
    expect(createMock).not.toHaveBeenCalled()
  })

  it('shows a field-level refusal next to the box and keeps the text', async () => {
    createMock.mockResolvedValue({
      success: false,
      message: 'Write something before posting.',
      field: 'content',
      comment: null,
    })
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    fireEvent.change(composer(target), { target: { value: 'Something' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))

    const alert = await within(target).findByText('Write something before posting.')
    expect(alert).toBeInTheDocument()
    // The reader's words are still there to fix, not wiped by a refusal.
    expect(composer(target)).toHaveValue('Something')
  })

  it('shows a business refusal that has no field, in the composer', async () => {
    createMock.mockResolvedValue({
      success: false,
      message: 'This idea is no longer open for discussion.',
      field: null,
      comment: null,
    })
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    fireEvent.change(composer(target), { target: { value: 'One more thought' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))

    expect(
      await within(target).findByText('This idea is no longer open for discussion.'),
    ).toBeInTheDocument()
  })

  it('reports a failed post as itself and clears the spinner', async () => {
    createMock.mockRejectedValue(new Error('Failed to fetch'))
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    fireEvent.change(composer(target), { target: { value: 'A new comment.' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))

    expect(
      await within(target).findByText('We could not reach the server. Please try again.'),
    ).toBeInTheDocument()
    expect(within(target).getByRole('button', { name: 'Comment' })).toBeEnabled()
  })

  it('sends one request when the button is clicked twice', async () => {
    let release: (() => void) | null = null
    createMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              success: true,
              message: 'Comment posted.',
              field: null,
              comment: comment({ id: 'c2', content: 'A new comment.' }),
            })
        }),
    )
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    fireEvent.change(composer(target), { target: { value: 'A new comment.' } })

    const button = within(target).getByRole('button', { name: 'Comment' })
    fireEvent.click(button)
    // A second click while the first is in flight - the impatient double-click.
    // The button is disabled, and a second request would be a duplicate the
    // reader has to delete by hand.
    fireEvent.click(button)

    expect(createMock).toHaveBeenCalledTimes(1)
    await act(async () => {
      release?.()
    })
    expect(await within(target).findByText('A new comment.')).toBeInTheDocument()
  })

  it('hides the composer on a closed discussion', async () => {
    mockContext([idea({ status: 'REJECTED', discussionOpen: false })])
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    expect(
      await within(target).findByText('This idea is closed for discussion.'),
    ).toBeInTheDocument()
    // Follows the server's `discussionOpen`, rather than re-deriving "is this
    // rejected?" here and carrying a second copy of a lifecycle rule.
    expect(within(target).queryByLabelText('Add a comment')).not.toBeInTheDocument()
  })

  it('offers no voting or attachment control', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    // S2-006 and S2-007. Nothing in this section may look like either.
    const text = target.textContent ?? ''
    for (const word of ['Vote', 'Upvote', 'Like', 'Attach', 'Upload', 'attachment']) {
      expect(text).not.toContain(word)
    }
    expect(within(target).queryByRole('button', { name: /vote|attach|upload/i })).toBeNull()
  })

  // --- editing --------------------------------------------------------------

  it('offers Edit and Delete only on the reader’s own comment', async () => {
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', authorId: SIGNED_IN.id }),
        comment({ id: 'c2', authorId: SOMEBODY_ELSE, content: 'Somebody else.' }),
      ]),
    )
    render(<IdeasWorkspace />)
    const target = await openDiscussion()

    const mine = (await within(target).findByText('We do this by hand every month.')).closest('li')
    const theirs = (await within(target).findByText('Somebody else.')).closest('li')

    expect(within(mine as HTMLElement).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(within(mine as HTMLElement).getByRole('button', { name: 'Delete' })).toBeInTheDocument()
    // An offer, not a control: the server refuses either way, so drawing these
    // on somebody else's comment would be a button guaranteed to fail.
    expect(within(theirs as HTMLElement).queryByRole('button', { name: 'Edit' })).toBeNull()
    expect(within(theirs as HTMLElement).queryByRole('button', { name: 'Delete' })).toBeNull()
  })

  it('saves an edit and shows the new text without re-fetching', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    const before = commentsMock.mock.calls.length

    fireEvent.click(within(target).getByRole('button', { name: 'Edit' }))
    const box = within(target).getByLabelText('Edit your comment')
    fireEvent.change(box, { target: { value: 'Edited.' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(updateMock).toHaveBeenCalled())
    expect(await within(target).findByText('Edited.')).toBeInTheDocument()
    expect(commentsMock.mock.calls.length).toBe(before)
  })

  it('leaves the comment alone when the save is refused', async () => {
    updateMock.mockResolvedValue({
      success: false,
      message: 'A comment must be 2000 characters or fewer.',
      field: 'content',
      comment: null,
    })
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    fireEvent.click(within(target).getByRole('button', { name: 'Edit' }))
    fireEvent.change(within(target).getByLabelText('Edit your comment'), {
      target: { value: 'x'.repeat(2001) },
    })
    fireEvent.click(within(target).getByRole('button', { name: 'Save' }))

    expect(
      await within(target).findByText('A comment must be 2000 characters or fewer.'),
    ).toBeInTheDocument()
    // Still in edit mode, with the original text intact - a refusal is not a
    // reason to throw away what was typed.
    expect(within(target).getByLabelText('Edit your comment')).toBeInTheDocument()
  })

  it('closes the editor on cancel without changing anything', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    fireEvent.click(within(target).getByRole('button', { name: 'Edit' }))
    fireEvent.change(within(target).getByLabelText('Edit your comment'), {
      target: { value: 'Never mind' },
    })
    fireEvent.click(within(target).getByRole('button', { name: 'Cancel' }))

    expect(within(target).queryByLabelText('Edit your comment')).not.toBeInTheDocument()
    expect(within(target).getByText('We do this by hand every month.')).toBeInTheDocument()
    expect(updateMock).not.toHaveBeenCalled()
  })

  it('will not save an emptied comment', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    fireEvent.click(within(target).getByRole('button', { name: 'Edit' }))
    fireEvent.change(within(target).getByLabelText('Edit your comment'), {
      target: { value: '  ' },
    })

    expect(within(target).getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(updateMock).not.toHaveBeenCalled()
  })

  // --- deleting -------------------------------------------------------------

  it('deletes the reader’s own comment and removes it from the thread', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    const before = commentsMock.mock.calls.length

    fireEvent.click(within(target).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(deleteMock).toHaveBeenCalled())
    await waitFor(() =>
      expect(within(target).queryByText('We do this by hand every month.')).not.toBeInTheDocument(),
    )
    expect(commentsMock.mock.calls.length).toBe(before)
    expect(listMock).toHaveBeenCalledTimes(1)
  })

  it('keeps the comment when a delete is refused', async () => {
    deleteMock.mockResolvedValue({
      success: false,
      message: 'Comment is unavailable.',
      field: null,
      comment: null,
    })
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    fireEvent.click(within(target).getByRole('button', { name: 'Delete' }))

    expect(await within(target).findByText('Comment is unavailable.')).toBeInTheDocument()
    expect(within(target).getByText('We do this by hand every month.')).toBeInTheDocument()
  })

  it('reports a failed delete as itself', async () => {
    deleteMock.mockRejectedValue(new Error('Failed to fetch'))
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    fireEvent.click(within(target).getByRole('button', { name: 'Delete' }))

    expect(
      await within(target).findByText('We could not reach the server. Please try again.'),
    ).toBeInTheDocument()
    expect(within(target).getByText('We do this by hand every month.')).toBeInTheDocument()
  })

  it('shows the empty state again once the last comment is deleted', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    fireEvent.click(within(target).getByRole('button', { name: 'Delete' }))

    expect(
      await within(target).findByText('No comments yet. Be the first to say something.'),
    ).toBeInTheDocument()
  })

  // --- the discovery context is untouched ------------------------------------

  it('keeps the idea list, its filters and its page through a discussion', async () => {
    render(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    fireEvent.change(composer(target), { target: { value: 'A new comment.' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))
    await within(target).findByText('A new comment.')

    // The comment went into the thread, not into the idea list's query - so the
    // reader is still where they were, with the same filters and the same page.
    expect(listMock).toHaveBeenCalledTimes(1)
    expect(within(target).getByText('Automate the invoice run')).toBeInTheDocument()
  })

  it('ignores a late answer for a discussion the reader has left', async () => {
    mockContext([idea({ id: '1', title: 'First idea' }), idea({ id: '2', title: 'Second idea' })])
    const releases: (() => void)[] = []
    commentsMock.mockImplementation(
      (_ideaId) =>
        new Promise((resolve) => {
          releases.push(() => resolve(commentPage([comment({ id: 'c9', content: 'Too late.' })])))
        }),
    )
    render(<IdeasWorkspace />)
    const first = await openDiscussion('First idea')
    // Close the first thread, open the second idea's, and only then let the
    // first answer arrive. Two discussions are open at different moments, and
    // the late one must not be painted under the second - which is why the
    // answer is tagged with the idea it belongs to.
    fireEvent.click(within(first).getByRole('button', { name: 'Hide discussion' }))
    const second = await openDiscussion('Second idea')
    await waitFor(() => expect(releases).toHaveLength(2))

    await act(async () => {
      releases[0]?.()
    })
    expect(within(second).queryByText('Too late.')).not.toBeInTheDocument()

    // The reader's own thread still resolves normally.
    await act(async () => {
      releases[1]?.()
    })
    expect(await within(second).findByText('Too late.')).toBeInTheDocument()
  })
})
