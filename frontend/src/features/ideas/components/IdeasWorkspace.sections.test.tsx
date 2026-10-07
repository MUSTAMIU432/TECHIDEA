import { makeIdea } from '../../../test/idea'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithRouter } from '../../../test/renderWithRouter'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeasWorkspace } from './IdeasWorkspace'
import { page, pageOf } from '../../../test/ideaPage'
import type { Idea } from '../api/ideasApi'

vi.mock('../api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  categoriesRequest: vi.fn(async () => []),
  createIdeaRequest: vi.fn(),
  updateIdeaRequest: vi.fn(),
  transitionIdeaRequest: vi.fn(),
  organizationIdeasRequest: vi.fn(),
  commentsRequest: vi.fn(async () => pageOf([])),
  attachmentsRequest: vi.fn(async () => pageOf([])),
}))

vi.mock('../../reviews/api/reviewsApi', async (importOriginal) => ({
  ...(await importOriginal()),
  ideaReviewsRequest: vi.fn(async () => []),
}))

vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({
  useOrganization: vi.fn(),
}))

const { organizationIdeasRequest, commentsRequest, attachmentsRequest } =
  await import('../api/ideasApi')
const listMock = vi.mocked(organizationIdeasRequest)
const commentsMock = vi.mocked(commentsRequest)
const attachmentsMock = vi.mocked(attachmentsRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }

const FIRST = makeIdea({ id: '1', title: 'First idea' })
const SECOND = makeIdea({ id: '2', title: 'Second idea' })
const THIRD = makeIdea({ id: '3', title: 'Third idea' })

function mockContext(ideas: Idea[] = [FIRST, SECOND, THIRD]) {
  vi.mocked(useAuth).mockReturnValue({
    user: SIGNED_IN,
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: { id: '3', name: 'Acme Labs' },
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(page(ideas))
  commentsMock.mockResolvedValue(pageOf([]))
  attachmentsMock.mockResolvedValue(pageOf([]))
}

/** One idea's card, so an assertion cannot accidentally read a neighbour's. */
async function card(title: string) {
  return (await screen.findByText(title)).closest('li') as HTMLElement
}

/** Press one of a card's three disclosures, by the name its cell carries. */
async function press(target: HTMLElement, name: 'Discussion' | 'Supporting evidence' | 'Review') {
  await act(async () => {})
  fireEvent.click(within(target).getByRole('button', { name }))
  await act(async () => {})
}

function expanded(target: HTMLElement, name: string) {
  return within(target).getByRole('button', { name }).getAttribute('aria-expanded')
}

/**
 * The card's three sections, and the rule that connects them: **at most one is
 * open on the page.**
 *
 * Each section is a request keyed on being open - `useComments`,
 * `useAttachments`, `IdeaReviewSection`'s history - so a list of twenty ideas
 * with three open sections each is sixty fetches for content nobody asked for,
 * and a page taller than the content on it warrants. The rule is deliberately
 * tested on a list of three cards rather than on one: it is the comparison
 * between cards that is the point, and a single card cannot show it.
 */
describe('Idea card sections (one open at a time)', () => {
  beforeEach(() => {
    mockContext()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('starts with every section closed', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const first = await card('First idea')

    expect(expanded(first, 'Discussion')).toBe('false')
    expect(expanded(first, 'Supporting evidence')).toBe('false')
    // No discussion and no evidence fetch for content nobody opened.
    expect(commentsMock).not.toHaveBeenCalled()
    expect(attachmentsMock).not.toHaveBeenCalled()
  })

  it('closes the others when a second section on the same card is opened', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const first = await card('First idea')

    await press(first, 'Discussion')
    expect(expanded(first, 'Discussion')).toBe('true')

    await press(first, 'Supporting evidence')

    // Evidence replaced the discussion rather than joining it: the row has one
    // thing open, so the reader is never choosing between two.
    expect(expanded(first, 'Supporting evidence')).toBe('true')
    expect(expanded(first, 'Discussion')).toBe('false')
  })

  it('closes the first card when a section of another card is opened', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const first = await card('First idea')
    await press(first, 'Discussion')

    const second = await card('Second idea')
    await press(second, 'Discussion')

    // A list is compared, not read top to bottom: opening the next card's thread
    // is the reader moving on, and the previous one should not still be open
    // three screens above.
    expect(expanded(second, 'Discussion')).toBe('true')
    expect(expanded(first, 'Discussion')).toBe('false')
  })

  it('leaves every other card closed however many the reader opens', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const first = await card('First idea')
    await press(first, 'Discussion')
    const second = await card('Second idea')
    await press(second, 'Supporting evidence')
    const third = await card('Third idea')
    await press(third, 'Discussion')

    for (const [target, name] of [
      [first, 'Discussion'],
      [first, 'Supporting evidence'],
      [second, 'Discussion'],
      [second, 'Supporting evidence'],
      [third, 'Supporting evidence'],
    ] as const) {
      expect(expanded(target, name)).toBe('false')
    }
  })

  it('closes the section when its own cell is pressed again', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const first = await card('First idea')

    await press(first, 'Discussion')
    await press(first, 'Discussion')

    // A control that reports `aria-expanded` has to be able to report false.
    expect(expanded(first, 'Discussion')).toBe('false')
    await waitFor(() => expect(screen.queryByLabelText('Add a comment')).not.toBeInTheDocument())
  })

  it('fetches only the section that is open', async () => {
    renderWithRouter(<IdeasWorkspace />)
    const first = await card('First idea')

    await press(first, 'Supporting evidence')
    expect(attachmentsMock).toHaveBeenCalledTimes(1)
    expect(commentsMock).not.toHaveBeenCalled()

    await press(first, 'Discussion')
    expect(commentsMock).toHaveBeenCalledTimes(1)
    // Still the one evidence request: opening a second section is not a reason
    // to re-ask for the first one.
    expect(attachmentsMock).toHaveBeenCalledTimes(1)
  })
})
