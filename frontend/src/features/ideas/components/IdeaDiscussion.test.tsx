import { makeIdea } from '../../../test/idea'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithRouter } from '../../../test/renderWithRouter'
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
  ideasRequest: vi.fn(),
  commentsRequest: vi.fn(),
  createCommentRequest: vi.fn(),
  updateCommentRequest: vi.fn(),
  deleteCommentRequest: vi.fn(),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({
  useOrganization: vi.fn(),
}))

const {
  ideasRequest,
  commentsRequest,
  createCommentRequest,
  updateCommentRequest,
  deleteCommentRequest,
} = await import('../api/ideasApi')
const listMock = vi.mocked(ideasRequest)
const commentsMock = vi.mocked(commentsRequest)
const createMock = vi.mocked(createCommentRequest)
const updateMock = vi.mocked(updateCommentRequest)
const deleteMock = vi.mocked(deleteCommentRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }
const SOMEBODY_ELSE = '9'

function comment(
  overrides: Partial<Awaited<ReturnType<typeof commentsRequest>>['items'][number]> = {},
) {
  return {
    id: 'c1',
    ideaId: '1',
    authorId: SIGNED_IN.id,
    content: 'We do this by hand every month.',
    parentId: null,
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
  // The action bar's cell, which is what opens the thread. Named for the
  // section rather than the action, because the composer's own submit button
  // is already called "Comment".
  fireEvent.click(within(target).getByRole('button', { name: 'Discussion' }))
  return target as HTMLElement
}

/**
 * A comment's own author reaches Edit and Delete through the overflow on that
 * comment's row, so a test that wants one opens the menu first - the way a
 * reader does, rather than reaching past the disclosure.
 */
function openCommentMenu(target: HTMLElement) {
  fireEvent.click(
    within(target).getByRole('button', {
      name: 'More actions for your comment',
    }),
  )
}

function composer(target: HTMLElement) {
  return within(target).getByLabelText('Add a comment') as HTMLTextAreaElement
}

/**
 * A comment's "Reply" disclosure, which is not the reply box's own submit
 * button - both are called "Reply", deliberately, because one opens the other
 * and a reader who cannot tell them apart by name can still tell by position.
 * This reaches the toggle, so a test does not depend on which one it clicked.
 */
function replyToggle(target: HTMLElement) {
  const toggle = within(target)
    .getAllByRole('button', { name: 'Reply' })
    .find((button) => button.getAttribute('type') === 'button')
  if (toggle === undefined) throw new Error('No Reply disclosure on screen.')
  return toggle
}

/** The reply box, and the form around it - whose submit button is also "Reply". */
async function replyBox(target: HTMLElement) {
  const box = (await within(target).findByLabelText('Reply to this comment')) as HTMLTextAreaElement
  return { box, form: box.closest('form') as HTMLElement }
}

/**
 * Opens a collapsed run of comments. A thread with more than one comment shows
 * only its first, so a test that wants a later one presses the disclosure
 * rather than reaching past the component.
 */
function seeMore(target: HTMLElement) {
  const [button] = within(target).queryAllByRole('button', {
    name: /^See \d+ more/,
  })
  if (button === undefined) throw new Error('No "See more" disclosure on screen.')
  fireEvent.click(button)
  return button
}

function postReply(box: HTMLTextAreaElement, form: HTMLElement, content: string) {
  fireEvent.change(box, { target: { value: content } })
  fireEvent.click(within(form).getByRole('button', { name: 'Reply' }))
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
 * - **A reply is written where it is answered.** The box opens inside the
 *   comment being replied to, posts with that comment's id, and lands under it
 *   in the thread - which is the whole difference between a conversation and a
 *   list of things that happen to be near each other.
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
    renderWithRouter(<IdeasWorkspace />)
    await screen.findByText('Automate the invoice run')

    // One request for the ideas, and none for their discussions.
    expect(listMock).toHaveBeenCalledTimes(1)
    expect(commentsMock).not.toHaveBeenCalled()
  })

  it('fetches the discussion when it is opened, and only for that idea', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    await screen.findByText('We do this by hand every month.')
    expect(commentsMock).toHaveBeenCalledTimes(1)
    expect(commentsMock).toHaveBeenCalledWith('1')
    // One name whichever way the cell points; `aria-expanded` is what says the
    // thread is open, so the label cannot drift between the two states.
    expect(within(target).getByRole('button', { name: 'Discussion' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })

  it('only ever has one discussion open', async () => {
    mockContext([
      makeIdea({ id: '1', title: 'First idea' }),
      makeIdea({ id: '2', title: 'Second idea' }),
    ])
    renderWithRouter(<IdeasWorkspace />)

    const first = await openDiscussion('First idea')
    await waitFor(() => expect(commentsMock).toHaveBeenCalledTimes(1))
    fireEvent.click(within(first).getByRole('button', { name: 'Discussion' }))

    const second = await openDiscussion('Second idea')
    await waitFor(() => expect(commentsMock).toHaveBeenCalledTimes(2))
    // Opening the second closed the first: two threads on one page is two
    // things to read and a page that grew without the reader asking.
    expect(within(second).getByRole('button', { name: 'Discussion' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    expect(within(first).getByRole('button', { name: 'Discussion' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  // --- the three states -----------------------------------------------------

  it('shows a loading state before the discussion arrives', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    expect(within(target).getByText('Loading the discussion…')).toBeInTheDocument()
  })

  it('shows an empty state when nobody has commented', async () => {
    commentsMock.mockResolvedValue(commentPage([]))
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    expect(
      await within(target).findByText('No comments yet. Be the first to say something.'),
    ).toBeInTheDocument()
  })

  it('reports a failed read as itself, not as an empty discussion', async () => {
    commentsMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)
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
        comment({
          id: 'c2',
          authorId: SOMEBODY_ELSE,
          content: 'Second comment.',
        }),
      ]),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    await within(target).findByText('First comment.')
    seeMore(target)
    const rendered = within(target)
      .getAllByRole('listitem')
      .map((item) => item.textContent ?? '')
    expect(rendered[0]).toContain('First comment.')
    expect(rendered[1]).toContain('Second comment.')
  })

  it('shows who wrote it and when', async () => {
    renderWithRouter(<IdeasWorkspace />)
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
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    expect(await within(target).findByText('· edited')).toBeInTheDocument()
  })

  // --- reading comments as text ---------------------------------------------

  it('renders comment content as text, never as markup', async () => {
    commentsMock.mockResolvedValue(commentPage([comment({ content: '<b>bold</b> & "quoted"' })]))
    renderWithRouter(<IdeasWorkspace />)
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
    renderWithRouter(<IdeasWorkspace />)
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
    renderWithRouter(<IdeasWorkspace />)
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
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    fireEvent.change(composer(target), { target: { value: 'A new comment.' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))

    await waitFor(() => expect(composer(target)).toHaveValue(''))
  })

  it('will not post an empty or whitespace-only comment', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    const button = within(target).getByRole('button', { name: 'Comment' })

    expect(button).toBeDisabled()

    fireEvent.change(composer(target), { target: { value: '    ' } })
    expect(button).toBeDisabled()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('refuses an over-long comment before sending it', async () => {
    renderWithRouter(<IdeasWorkspace />)
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
    renderWithRouter(<IdeasWorkspace />)
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
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    fireEvent.change(composer(target), {
      target: { value: 'One more thought' },
    })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))

    expect(
      await within(target).findByText('This idea is no longer open for discussion.'),
    ).toBeInTheDocument()
  })

  it('reports a failed post as itself and clears the spinner', async () => {
    createMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)
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
    renderWithRouter(<IdeasWorkspace />)
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
    mockContext([makeIdea({ status: 'REJECTED', discussionOpen: false })])
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    expect(
      await within(target).findByText('This idea is closed for discussion.'),
    ).toBeInTheDocument()
    // Follows the server's `discussionOpen`, rather than re-deriving "is this
    // rejected?" here and carrying a second copy of a lifecycle rule.
    expect(within(target).queryByLabelText('Add a comment')).not.toBeInTheDocument()
  })

  it('offers no voting or attachment control inside the discussion', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    // Scoped to the discussion region rather than the card: S2-006 added a
    // vote control to the card, which is correct and lives outside the thread.
    // What must stay true is that a *discussion* is a discussion - no voting,
    // no attachments (S2-007).
    const thread = within(target).getByRole('region', { name: /^Discussion:/ })
    const text = thread.textContent ?? ''
    for (const word of ['Vote', 'Upvote', 'Like', 'Attach', 'Upload', 'attachment']) {
      expect(text).not.toContain(word)
    }
    expect(within(thread).queryByRole('button', { name: /vote|attach|upload/i })).toBeNull()
  })

  // --- editing --------------------------------------------------------------

  it('offers Edit and Delete only on the reader’s own comment', async () => {
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', authorId: SIGNED_IN.id }),
        comment({
          id: 'c2',
          authorId: SOMEBODY_ELSE,
          content: 'Somebody else.',
        }),
      ]),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    const mine = (await within(target).findByText('We do this by hand every month.')).closest('li')
    seeMore(target)
    const theirs = (await within(target).findByText('Somebody else.')).closest('li')

    openCommentMenu(mine as HTMLElement)
    expect(within(mine as HTMLElement).getByRole('menuitem', { name: 'Edit' })).toBeInTheDocument()
    expect(
      within(mine as HTMLElement).getByRole('menuitem', { name: 'Delete' }),
    ).toBeInTheDocument()
    // An offer, not a control: the server refuses either way, so drawing these
    // on somebody else's comment would be a button guaranteed to fail - and
    // here not even a menu to open.
    expect(
      within(theirs as HTMLElement).queryByRole('button', {
        name: 'More actions for your comment',
      }),
    ).toBeNull()
  })

  it('saves an edit and shows the new text without re-fetching', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    const before = commentsMock.mock.calls.length

    openCommentMenu(target)
    fireEvent.click(within(target).getByRole('menuitem', { name: 'Edit' }))
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
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    openCommentMenu(target)
    fireEvent.click(within(target).getByRole('menuitem', { name: 'Edit' }))
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
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    openCommentMenu(target)
    fireEvent.click(within(target).getByRole('menuitem', { name: 'Edit' }))
    fireEvent.change(within(target).getByLabelText('Edit your comment'), {
      target: { value: 'Never mind' },
    })
    fireEvent.click(within(target).getByRole('button', { name: 'Cancel' }))

    expect(within(target).queryByLabelText('Edit your comment')).not.toBeInTheDocument()
    expect(within(target).getByText('We do this by hand every month.')).toBeInTheDocument()
    expect(updateMock).not.toHaveBeenCalled()
  })

  it('will not save an emptied comment', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    openCommentMenu(target)
    fireEvent.click(within(target).getByRole('menuitem', { name: 'Edit' }))
    fireEvent.change(within(target).getByLabelText('Edit your comment'), {
      target: { value: '  ' },
    })

    expect(within(target).getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(updateMock).not.toHaveBeenCalled()
  })

  // --- deleting -------------------------------------------------------------

  it('deletes the reader’s own comment and removes it from the thread', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    const before = commentsMock.mock.calls.length

    openCommentMenu(target)
    fireEvent.click(within(target).getByRole('menuitem', { name: 'Delete' }))

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
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    openCommentMenu(target)
    fireEvent.click(within(target).getByRole('menuitem', { name: 'Delete' }))

    expect(await within(target).findByText('Comment is unavailable.')).toBeInTheDocument()
    expect(within(target).getByText('We do this by hand every month.')).toBeInTheDocument()
  })

  it('reports a failed delete as itself', async () => {
    deleteMock.mockRejectedValue(new Error('Failed to fetch'))
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    openCommentMenu(target)
    fireEvent.click(within(target).getByRole('menuitem', { name: 'Delete' }))

    expect(
      await within(target).findByText('We could not reach the server. Please try again.'),
    ).toBeInTheDocument()
    expect(within(target).getByText('We do this by hand every month.')).toBeInTheDocument()
  })

  it('shows the empty state again once the last comment is deleted', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    openCommentMenu(target)
    fireEvent.click(within(target).getByRole('menuitem', { name: 'Delete' }))

    expect(
      await within(target).findByText('No comments yet. Be the first to say something.'),
    ).toBeInTheDocument()
  })

  // --- the discovery context is untouched ------------------------------------

  it('keeps the idea list, its filters and its page through a discussion', async () => {
    renderWithRouter(<IdeasWorkspace />)
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
    mockContext([
      makeIdea({ id: '1', title: 'First idea' }),
      makeIdea({ id: '2', title: 'Second idea' }),
    ])
    const releases: (() => void)[] = []
    commentsMock.mockImplementation(
      (_ideaId) =>
        new Promise((resolve) => {
          releases.push(() => resolve(commentPage([comment({ id: 'c9', content: 'Too late.' })])))
        }),
    )
    renderWithRouter(<IdeasWorkspace />)
    const first = await openDiscussion('First idea')
    // Close the first thread, open the second idea's, and only then let the
    // first answer arrive. Two discussions are open at different moments, and
    // the late one must not be painted under the second - which is why the
    // answer is tagged with the idea it belongs to.
    fireEvent.click(within(first).getByRole('button', { name: 'Discussion' }))
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

  // --- replying --------------------------------------------------------------

  it('opens the reply box inside the comment being answered', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    // Nothing to type into yet: the thread's own composer is not a reply, and
    // there is no second box waiting somewhere on the page.
    expect(within(target).queryByLabelText('Reply to this comment')).not.toBeInTheDocument()
    fireEvent.click(replyToggle(target))

    // Inside that comment, not below the thread.
    const row = (await within(target).findByText('We do this by hand every month.')).closest(
      'li',
    ) as HTMLElement
    expect(within(row).getByLabelText('Reply to this comment')).toBeInTheDocument()
  })

  it('posts a reply with the id of the comment it answers', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'Reply posted.',
      field: null,
      comment: comment({
        id: 'c2',
        parentId: 'c1',
        content: 'We automate it in batches.',
      }),
    })
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    fireEvent.click(replyToggle(target))

    const { box, form } = await replyBox(target)
    postReply(box, form, 'We automate it in batches.')

    // The parent is the one thing the client names about the request, so it is
    // the thing worth asserting: sent as the id of the comment being answered.
    expect(createMock).toHaveBeenCalledWith('1', 'We automate it in batches.', 'c1')
  })

  it('shows a posted reply under the comment it answers', async () => {
    commentsMock.mockResolvedValue(
      commentPage([comment({ id: 'c1', content: 'How is it done now?' })]),
    )
    createMock.mockResolvedValue({
      success: true,
      message: 'Reply posted.',
      field: null,
      comment: comment({
        id: 'c2',
        parentId: 'c1',
        content: 'By hand, in a spreadsheet.',
      }),
    })
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('How is it done now?')
    fireEvent.click(replyToggle(target))

    const { box, form } = await replyBox(target)
    postReply(box, form, 'By hand, in a spreadsheet.')

    // The reply is inside the same list item as the comment it answers, one
    // level in - rather than a second card sitting below the first as though it
    // were a separate conversation. Scoped to `p` because the reply box's own
    // textarea holds the same text until it is posted.
    const reply = await within(target).findByText('By hand, in a spreadsheet.', { selector: 'p' })
    const threadItem = (await within(target).findByText('How is it done now?')).closest('li')
    expect(threadItem).toContainElement(reply)
    expect(reply.closest('li')).not.toBe(threadItem)
  })

  it('closes the reply box once the reply is accepted', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'Reply posted.',
      field: null,
      comment: comment({ id: 'c2', parentId: 'c1', content: 'An answer.' }),
    })
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    fireEvent.click(replyToggle(target))
    const { box, form } = await replyBox(target)
    postReply(box, form, 'An answer.')

    // Nothing is left to do with a box whose reply is posted, and the thread's
    // own composer is untouched - it was never where the reply was written.
    await waitFor(() =>
      expect(within(target).queryByLabelText('Reply to this comment')).not.toBeInTheDocument(),
    )
    expect(composer(target)).toHaveValue('')
  })

  it('keeps the box open, with the words in it, when the reply is refused', async () => {
    createMock.mockResolvedValue({
      success: false,
      message: 'You can only reply to a top-level comment.',
      field: 'parentId',
      comment: null,
    })
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    fireEvent.click(replyToggle(target))
    const { box, form } = await replyBox(target)
    postReply(box, form, 'An answer.')

    // A refusal is not a reason to throw away what somebody wrote.
    expect(await within(target).findByRole('alert')).toHaveTextContent(
      'You can only reply to a top-level comment.',
    )
    expect(within(target).getByLabelText('Reply to this comment')).toHaveValue('An answer.')
  })

  it('toggles the reply box closed when Reply is pressed again', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    fireEvent.click(replyToggle(target))
    await within(target).findByLabelText('Reply to this comment')
    fireEvent.click(replyToggle(target))

    expect(within(target).queryByLabelText('Reply to this comment')).not.toBeInTheDocument()
  })

  it('posts the reply on Enter, the same as pressing Reply', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'Reply posted.',
      field: null,
      comment: comment({ id: 'c2', parentId: 'c1', content: 'Yes, it is me.' }),
    })
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    fireEvent.click(replyToggle(target))

    const { box, form } = await replyBox(target)
    fireEvent.change(box, { target: { value: 'Yes, it is me.' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    // The same request the button makes, not a second path to the same place.
    expect(createMock).toHaveBeenCalledWith('1', 'Yes, it is me.', 'c1')
    expect(within(form).getByRole('button', { name: 'Reply' })).toHaveAttribute('type', 'submit')
  })

  it('keeps the line break for Shift+Enter, which is the way back to a newline', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    fireEvent.click(replyToggle(target))

    const { box } = await replyBox(target)
    fireEvent.change(box, { target: { value: 'Yes, it is me.' } })
    const notCancelled = fireEvent.keyDown(box, {
      key: 'Enter',
      shiftKey: true,
    })

    // No post, and the key was left to the textarea - so the break lands where
    // the reader pressed it instead of the key looking broken.
    expect(createMock).not.toHaveBeenCalled()
    expect(notCancelled).toBe(true)
  })

  it('does not post on the Enter that is choosing a character', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    fireEvent.click(replyToggle(target))

    const { box } = await replyBox(target)
    fireEvent.change(box, { target: { value: 'はい' } })
    // `isComposing` is what an IME sets while a reader is still picking the
    // character: the Enter confirms the kana and posts a half-typed word
    // otherwise.
    fireEvent.keyDown(box, { key: 'Enter', isComposing: true })

    expect(createMock).not.toHaveBeenCalled()
  })

  it('does not post on Enter while the box has nothing to send', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    fireEvent.click(replyToggle(target))

    const { box } = await replyBox(target)
    fireEvent.change(box, { target: { value: '   ' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    // Nothing to post, so the key is a newline rather than a no-op: the reader
    // gets what they asked for instead of a keypress that did nothing at all.
    expect(createMock).not.toHaveBeenCalled()
  })

  it('posts once on Enter even when it is pressed twice', async () => {
    let release: (() => void) | undefined
    createMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () =>
            resolve({
              success: true,
              message: 'Reply posted.',
              field: null,
              comment: comment({
                id: 'c2',
                parentId: 'c1',
                content: 'Yes, it is me.',
              }),
            })
        }),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')
    fireEvent.click(replyToggle(target))

    const { box } = await replyBox(target)
    fireEvent.change(box, { target: { value: 'Yes, it is me.' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    // The impatient second press, before the first has been answered. The box
    // still holds the words for that moment, so the same guard has to hold.
    fireEvent.keyDown(box, { key: 'Enter' })

    expect(createMock).toHaveBeenCalledTimes(1)
    await act(async () => {
      release?.()
    })
    expect(await within(target).findByText('Yes, it is me.', { selector: 'p' })).toBeInTheDocument()
  })

  // --- a long thread --------------------------------------------------------

  it('shows one comment and leaves the rest behind "See more"', async () => {
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', content: 'First comment.' }),
        comment({ id: 'c2', content: 'Second comment.' }),
        comment({ id: 'c3', content: 'Third comment.' }),
      ]),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()

    await within(target).findByText('First comment.')
    expect(within(target).queryByText('Second comment.')).not.toBeInTheDocument()
    // The count is spelled out rather than reduced to an ellipsis, so a reader
    // can decide whether it is worth opening.
    expect(within(target).getByRole('button', { name: 'See 2 more comments' })).toBeInTheDocument()
  })

  it('reveals the rest of the thread when "See more" is pressed, and hides them again', async () => {
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', content: 'First comment.' }),
        comment({ id: 'c2', content: 'Second comment.' }),
      ]),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('First comment.')

    seeMore(target)
    expect(await within(target).findByText('Second comment.')).toBeInTheDocument()

    fireEvent.click(within(target).getByRole('button', { name: 'See fewer' }))
    expect(within(target).queryByText('Second comment.')).not.toBeInTheDocument()
  })

  it('draws no "See more" for a thread of one', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('We do this by hand every month.')

    // A disclosure with nothing behind it is a control that only says no.
    expect(within(target).queryByRole('button', { name: /^See \d+ more/ })).not.toBeInTheDocument()
  })

  it('collapses the replies under a comment, and opens them on request', async () => {
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', content: 'How is it done now?' }),
        comment({ id: 'c2', parentId: 'c1', content: 'First answer.' }),
        comment({ id: 'c3', parentId: 'c1', content: 'Second answer.' }),
      ]),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('How is it done now?')

    // The reply run collapses on its own terms - the comment it hangs off is
    // the only thing on screen, and a thread of answers is not what the reader
    // came for.
    expect(within(target).queryByText('Second answer.')).not.toBeInTheDocument()
    fireEvent.click(within(target).getByRole('button', { name: 'See 1 more reply' }))
    expect(await within(target).findByText('Second answer.')).toBeInTheDocument()
  })

  it('does not collapse a run while the reader is editing a comment inside it', async () => {
    commentsMock.mockResolvedValue(
      commentPage([
        comment({
          id: 'c1',
          authorId: SOMEBODY_ELSE,
          content: 'How is it done now?',
        }),
        comment({
          id: 'c2',
          parentId: 'c1',
          authorId: SIGNED_IN.id,
          content: 'First answer.',
        }),
        comment({ id: 'c3', parentId: 'c1', content: 'Second answer.' }),
      ]),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('How is it done now?')
    const reply = (await within(target).findByText('First answer.')).closest('li') as HTMLElement

    openCommentMenu(reply)
    fireEvent.click(within(reply).getByRole('menuitem', { name: 'Edit' }))

    // Somebody is working in this run. Collapsing it would take the rest of the
    // thread - the context the edit is made in - out from under them.
    expect(await within(target).findByText('Second answer.')).toBeInTheDocument()
  })

  it('never collapses the comment the reader has just posted', async () => {
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', content: 'First comment.' }),
        comment({ id: 'c2', content: 'Second comment.' }),
      ]),
    )
    createMock.mockResolvedValue({
      success: true,
      message: 'Comment posted.',
      field: null,
      comment: comment({ id: 'c3', content: 'Third comment.' }),
    })
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('First comment.')

    fireEvent.change(composer(target), { target: { value: 'Third comment.' } })
    fireEvent.click(within(target).getByRole('button', { name: 'Comment' }))

    // A comment nobody can find after posting it is the one comment that must
    // not be hidden - so posting opens the run rather than burying the words.
    expect(await within(target).findByText('Third comment.')).toBeInTheDocument()
  })

  it('moves the open box to another comment rather than stacking two', async () => {
    /*
      One box, always. Two open reply boxes is two half-written answers and two
      places for a reader's attention to be, on a card that is already holding a
      conversation.
    */
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', content: 'How is it done now?' }),
        comment({ id: 'c2', content: 'And who signs it off?' }),
      ]),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    await within(target).findByText('How is it done now?')

    fireEvent.click(replyToggle(target))
    await within(target).findByLabelText('Reply to this comment')
    const second = within(target)
      .getAllByRole('button', { name: 'Reply' })
      .filter((button) => button.getAttribute('type') === 'button')[1] as HTMLElement
    fireEvent.click(second)

    await waitFor(() =>
      expect(within(target).getAllByLabelText('Reply to this comment')).toHaveLength(1),
    )
    const row = (await within(target).findByText('And who signs it off?')).closest(
      'li',
    ) as HTMLElement
    expect(within(row).getByLabelText('Reply to this comment')).toBeInTheDocument()
  })

  it('offers Reply on a comment but never on a reply', async () => {
    /*
      The server refuses a reply to a reply, so a button there would be offering
      something guaranteed to fail. A reader who wants to answer a reply answers
      the comment it belongs to.
    */
    commentsMock.mockResolvedValue(
      commentPage([
        comment({ id: 'c1', content: 'How is it done now?' }),
        comment({ id: 'c2', parentId: 'c1', content: 'By hand.' }),
      ]),
    )
    renderWithRouter(<IdeasWorkspace />)
    const target = await openDiscussion()
    const reply = await within(target).findByText('By hand.')

    expect(
      within(reply.closest('li') as HTMLElement).queryByRole('button', {
        name: 'Reply',
      }),
    ).toBe(null)
    const row = (await within(target).findByText('How is it done now?')).closest(
      'li',
    ) as HTMLElement
    expect(within(row).getAllByRole('button', { name: 'Reply' })[0]).toBeInTheDocument()
  })
})
