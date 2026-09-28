import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { IdeasWorkspace } from './IdeasWorkspace'
import { page } from '../../../test/ideaPage'
import type { Idea, IdeaMutationResult, IdeaStatus } from '../api/ideasApi'

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

const { createIdeaRequest, organizationIdeasRequest, transitionIdeaRequest } =
  await import('../api/ideasApi')
const createMock = vi.mocked(createIdeaRequest)
const listMock = vi.mocked(organizationIdeasRequest)
const transitionMock = vi.mocked(transitionIdeaRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }
const REVIEWER = { id: '8', email: 'reviewer@example.com' }

function idea(overrides: Partial<Idea> = {}): Idea {
  return {
    id: '1',
    title: 'Automate the invoice run',
    description: 'A description long enough.',
    status: 'SUBMITTED',
    visibility: 'ORGANIZATION',
    submittedAt: '2026-02-01T00:00:00.000Z',
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-02T00:00:00.000Z',
    authorId: '9',
    organizationId: '3',
    category: null,
    availableTransitions: [],
    discussionOpen: true,
    voteCount: 0,
    viewerHasVoted: false,
    viewerCanStartReview: false,
    viewerActiveReviewId: null,
    ...overrides,
  }
}

/**
 * `viewer` decides who is signed in and what the organization context says they
 * hold. The UI takes *both* of its cues from the server - `availableTransitions`
 * on the idea, and the membership's roles - so the fixtures below vary those
 * rather than varying any client-side rule, which is the thing S2-003 removed.
 */
function mockContext({
  viewer = SIGNED_IN,
  ideas = [],
  systemRole = false,
}: {
  viewer?: { id: string; email: string }
  ideas?: Idea[]
  systemRole?: boolean
} = {}) {
  vi.mocked(useAuth).mockReturnValue({ user: viewer } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: { id: '3', name: 'Acme Labs' },
    status: 'ready',
    activeMembership: {
      id: '5',
      status: 'active',
      roles: systemRole
        ? [
            {
              id: '1',
              name: 'Owner',
              slug: 'owner',
              description: '',
              isSystem: true,
              createdAt: '',
              updatedAt: '',
              organization: { id: '3' },
              permissions: [],
            },
          ]
        : [
            {
              id: '2',
              name: 'Contributor',
              slug: 'contributor',
              description: '',
              isSystem: false,
              createdAt: '',
              updatedAt: '',
              organization: { id: '3' },
              permissions: [],
            },
          ],
    },
  } as unknown as ReturnType<typeof useOrganization>)
  listMock.mockResolvedValue(page(ideas))
}

describe('IdeasWorkspace lifecycle (S2-003)', () => {
  beforeEach(() => {
    mockContext()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  // --- status rendering ----------------------------------------------------

  it('renders every lifecycle state the backend can report', async () => {
    const states: IdeaStatus[] = [
      'DRAFT',
      'SUBMITTED',
      'UNDER_REVIEW',
      'CHANGES_REQUESTED',
      'REJECTED',
      'APPROVED',
      'AUTOMATION_PROPOSAL',
    ]
    mockContext({
      ideas: states.map((status, index) =>
        idea({ id: String(index), status, title: `Idea in ${status}` }),
      ),
    })
    render(<IdeasWorkspace />)

    await screen.findByText('Idea in DRAFT')
    // Human labels, not the raw enum names a client would otherwise have to
    // carry its own table for. Each appears twice — once as the badge, once in
    // the details row — which is asserted rather than assumed.
    //
    // Read from within the list: the status filter above the list offers
    // options with these same seven words, and a filter named "Draft" is
    // exactly what it should be called, so the assertion is about the rows.
    const list = within(screen.getByRole('list'))
    const counts = [
      'Draft',
      'Submitted',
      'Under review',
      'Changes requested',
      'Rejected',
      'Approved',
      'Automation proposal',
    ].map((label) => [label, list.getAllByText(label).length])
    expect(counts).toEqual([
      ['Draft', 2],
      ['Submitted', 2],
      ['Under review', 2],
      ['Changes requested', 2],
      ['Rejected', 2],
      ['Approved', 2],
      ['Automation proposal', 2],
    ])
  })

  it('explains what a changes-requested idea needs from its author', async () => {
    mockContext({ ideas: [idea({ status: 'CHANGES_REQUESTED', authorId: '7' })] })
    render(<IdeasWorkspace />)

    // The state whose next step belongs to the reader, so it is worth saying
    // in words rather than leaving them to infer it from a badge colour.
    expect(await screen.findByText(/Sent back to you with changes to make/)).toBeInTheDocument()
  })

  it('does not explain a draft, which is already obvious', async () => {
    mockContext({
      ideas: [
        idea({
          status: 'DRAFT',
          submittedAt: null,
          authorId: '7',
          availableTransitions: ['SUBMITTED'],
        }),
      ],
    })
    render(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByText(/Sent back to you/)).not.toBeInTheDocument()
  })

  // --- visibility rendering -------------------------------------------------

  it.each([
    ['PUBLIC', 'Everyone on the platform'],
    ['ORGANIZATION', 'This organization'],
    ['PRIVATE', 'Only you'],
  ])('renders %s as "%s"', async (visibility, label) => {
    mockContext({ ideas: [idea({ visibility: visibility as Idea['visibility'] })] })
    render(<IdeasWorkspace />)

    expect(await screen.findByText(label)).toBeInTheDocument()
  })

  // --- what is offered ------------------------------------------------------

  it('offers the review move the backend says this viewer may make', async () => {
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [idea({ availableTransitions: ['UNDER_REVIEW'] })],
    })
    render(<IdeasWorkspace />)

    expect(await screen.findByRole('button', { name: 'Start review' })).toBeInTheDocument()
  })

  it('offers the three review outcomes on an idea under review', async () => {
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [
        idea({
          status: 'UNDER_REVIEW',
          availableTransitions: ['CHANGES_REQUESTED', 'REJECTED', 'APPROVED'],
        }),
      ],
    })
    render(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.getByRole('button', { name: 'Request changes' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
  })

  it('offers an author nothing on their own submitted idea, even holding the Owner role', async () => {
    // The self-review rule, seen from the client. The viewer here is the author
    // *and* holds a system role; the server declined to list any move, and the
    // UI draws no button because there is nothing to draw.
    mockContext({
      viewer: SIGNED_IN,
      systemRole: true,
      ideas: [idea({ authorId: '7', availableTransitions: [] })],
    })
    render(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: 'Start review' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit draft' })).not.toBeInTheDocument()
  })

  it('offers an ordinary member nothing on somebody else submitted idea', async () => {
    mockContext({
      viewer: SIGNED_IN,
      systemRole: false,
      ideas: [idea({ availableTransitions: [] })],
    })
    render(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: 'Start review' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Submit for review' })).not.toBeInTheDocument()
  })

  it('offers the author a re-submission after changes are requested', async () => {
    mockContext({
      viewer: SIGNED_IN,
      ideas: [
        idea({ status: 'CHANGES_REQUESTED', authorId: '7', availableTransitions: ['SUBMITTED'] }),
      ],
    })
    render(<IdeasWorkspace />)

    expect(await screen.findByRole('button', { name: 'Submit for review' })).toBeInTheDocument()
  })

  it('never offers the automation hand-off, which leads nowhere yet', async () => {
    // The transition exists so the lifecycle is complete, and nothing acts on an
    // idea once it reaches that state. A button that goes nowhere would be
    // worse than none.
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [idea({ status: 'APPROVED', availableTransitions: ['AUTOMATION_PROPOSAL'] })],
    })
    render(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: /Hand off/ })).not.toBeInTheDocument()
  })

  // --- performing a transition ----------------------------------------------

  it('performs the move the server listed and reports its message', async () => {
    transitionMock.mockResolvedValue({
      success: true,
      message: 'Idea moved to Under review.',
      field: null,
      idea: idea({
        status: 'UNDER_REVIEW',
        availableTransitions: ['CHANGES_REQUESTED', 'REJECTED', 'APPROVED'],
      }),
    })
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [idea({ availableTransitions: ['UNDER_REVIEW'] })],
    })
    render(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))

    expect(transitionMock).toHaveBeenCalledWith('1', 'UNDER_REVIEW')
    expect(await screen.findByText('Idea moved to Under review.')).toBeInTheDocument()
    // The list is re-read, because the card the move was made from has changed.
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThan(1))
  })

  it('shows a business refusal with the backends wording', async () => {
    transitionMock.mockResolvedValue({
      success: false,
      message: 'You are not allowed to make that change to this idea.',
      field: null,
      idea: null,
    })
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [idea({ availableTransitions: ['UNDER_REVIEW'] })],
    })
    render(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('You are not allowed to make that change to this idea.')
  })

  it('reports a transport failure without claiming the move happened', async () => {
    transitionMock.mockRejectedValue(new Error('Failed to fetch'))
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [idea({ availableTransitions: ['UNDER_REVIEW'] })],
    })
    render(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('We could not reach the server. Please try again.')
    expect(screen.queryByText(/Idea moved to/)).not.toBeInTheDocument()
  })

  it('disables the button while the move is in flight and re-enables it after', async () => {
    let release: (value: IdeaMutationResult) => void = () => {}
    transitionMock.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [idea({ availableTransitions: ['UNDER_REVIEW'] })],
    })
    render(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Start review' })).toBeDisabled())
    release({
      success: true,
      message: 'Idea moved to Under review.',
      field: null,
      idea: idea({ status: 'UNDER_REVIEW' }),
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Start review' })).not.toBeDisabled(),
    )
  })

  it('spins only on the button whose move is in flight', async () => {
    let release: (value: IdeaMutationResult) => void = () => {}
    transitionMock.mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [
        idea({ id: '1', availableTransitions: ['UNDER_REVIEW'] }),
        idea({
          id: '2',
          title: 'Another idea',
          status: 'UNDER_REVIEW',
          availableTransitions: ['CHANGES_REQUESTED', 'REJECTED', 'APPROVED'],
        }),
      ],
    })
    render(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))

    await waitFor(() => expect(screen.getByRole('button', { name: 'Start review' })).toBeDisabled())
    // The other idea's buttons are untouched: one move in flight does not lock
    // the whole page.
    expect(screen.getByRole('button', { name: 'Approve' })).not.toBeDisabled()
    release({ success: true, message: 'ok', field: null, idea: idea() })
  })

  // --- protected ideas ------------------------------------------------------

  it('does not display an idea the server did not return', async () => {
    // A `PRIVATE` idea of somebody else is not in the response at all, so
    // there is nothing to hide client-side - which is the point: the filter
    // that removed it ran on the server, in the same query the UI reads.
    mockContext({ ideas: [] })
    render(<IdeasWorkspace />)

    expect(await screen.findByText('No ideas here yet')).toBeInTheDocument()
    expect(screen.queryByText('Automate the invoice run')).not.toBeInTheDocument()
  })

  it('does not offer a transition on an idea the viewer cannot see', async () => {
    mockContext({ ideas: [idea({ visibility: 'PRIVATE', availableTransitions: [] })] })
    render(<IdeasWorkspace />)

    // Scoped to the idea's own card: the page's "File a new idea" button lives
    // outside it and is not what this is about. `findByText` because the list
    // is fetched on mount.
    const card = (await screen.findByText('Automate the invoice run')).closest('li')
    expect(card).not.toBeNull()

    // Asserted against the transition labels rather than as "no buttons at
    // all", which is how this was first written. That phrasing was true until
    // S2-005 added a discussion toggle to every card, and it was narrower than
    // the test meant: what must be absent is a *lifecycle* offer. The server
    // computed an empty `availableTransitions` for this viewer, so the UI has
    // nothing to draw and no client-side rule to fall back on.
    for (const label of [
      'Submit for review',
      'Start review',
      'Send back',
      'Approve',
      'Reject',
      'Hand off for automation',
    ]) {
      expect(within(card as HTMLElement).queryByRole('button', { name: label })).toBeNull()
    }
  })

  // --- the form still works alongside ---------------------------------------

  it('still creates a draft, whose only available move is submitting it', async () => {
    createMock.mockResolvedValue({
      success: true,
      message: 'ok',
      field: null,
      idea: idea({ status: 'DRAFT', submittedAt: null }),
    })
    mockContext({ viewer: SIGNED_IN, ideas: [] })
    render(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'File a new idea' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'A new idea' } })
    fireEvent.change(screen.getByLabelText('Description'), {
      target: { value: 'A description long enough.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() => expect(createMock).toHaveBeenCalledWith('3', expect.anything()))
  })
})
