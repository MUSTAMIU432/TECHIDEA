import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { makeIdea } from '../../../test/idea'
import { renderWithRouter } from '../../../test/renderWithRouter'
import { statusLabel } from '../utils/lifecycle'
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
vi.mock('../../organizations/context/useOrganization', () => ({
  useOrganization: vi.fn(),
}))

const { organizationIdeasRequest, transitionIdeaRequest } = await import('../api/ideasApi')
const listMock = vi.mocked(organizationIdeasRequest)
const transitionMock = vi.mocked(transitionIdeaRequest)

const SIGNED_IN = { id: '7', email: 'ada@example.com' }
const REVIEWER = { id: '8', email: 'reviewer@example.com' }

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
    // Every status in the enum, both tracks' worth: an idea may be waiting on
    // its own organization or on the platform, and a reader who cannot tell
    // "waiting for my organization" from "waiting for the platform" has learned
    // nothing from the card.
    const states: IdeaStatus[] = [
      'DRAFT',
      'SUBMITTED_TO_ORGANIZATION',
      'ORGANIZATION_CHANGES_REQUESTED',
      'ORGANIZATION_CONFIRMED',
      'SUBMITTED',
      'UNDER_REVIEW',
      'CHANGES_REQUESTED',
      'REJECTED',
      'APPROVED',
      'READY_FOR_IMPLEMENTATION',
      'AUTOMATION_PROPOSAL',
    ]
    mockContext({
      ideas: states.map((status, index) =>
        makeIdea({ id: String(index), status, title: `Idea in ${status}` }),
      ),
    })
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Idea in DRAFT')
    // Human labels, not the raw enum names a client would otherwise have to
    // carry its own table for.
    //
    // **Once per card now, not twice.** The details row used to repeat the
    // status under a "Status" heading beside the badge that already said it -
    // two renderings of one fact, on the same card, where a reader could be
    // left wondering whether they disagreed. The badge is the one.
    //
    // Read from within the list: the status filter above the list offers
    // options with these same words, and a filter named "Draft" is exactly what
    // it should be called, so the assertion is about the rows.
    const list = within(screen.getByRole('list'))
    const labels = states.map((status) => [
      statusLabel(status),
      list.getAllByText(statusLabel(status)).length,
    ])
    expect(labels).toEqual(states.map((status) => [statusLabel(status), 1]))
  })

  it('does not call a platform-approved idea approved, because the author still owes a decision', async () => {
    mockContext({ ideas: [makeIdea({ status: 'APPROVED' })] })
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    // Platform approval and the author's go-ahead are two different facts; the
    // second is still outstanding, so the badge says so. Read from the list: the
    // status filter above it offers an "Approved" option, and a filter named
    // "Approved" is exactly what it should be called.
    const list = within(screen.getByRole('list'))
    expect(list.queryByText('Approved')).not.toBeInTheDocument()
    expect(list.getAllByText(/your confirmation needed/).length).toBeGreaterThan(0)
    // ...and says what the author actually has to do.
    expect(screen.getByText(/decide whether to give the go-ahead/)).toBeInTheDocument()
  })

  it('explains what a changes-requested idea needs from its author', async () => {
    mockContext({
      ideas: [makeIdea({ status: 'CHANGES_REQUESTED', authorId: '7' })],
    })
    renderWithRouter(<IdeasWorkspace />)

    // The state whose next step belongs to the reader, so it is worth saying
    // in words rather than leaving them to infer it from a badge colour.
    expect(await screen.findByText(/Sent back to you with changes to make/)).toBeInTheDocument()
  })

  it('does not explain a draft, which is already obvious', async () => {
    mockContext({
      ideas: [
        makeIdea({
          status: 'DRAFT',
          submittedAt: null,
          authorId: '7',
          availableTransitions: ['SUBMITTED'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByText(/Sent back to you/)).not.toBeInTheDocument()
  })

  // --- visibility rendering -------------------------------------------------

  // The audience badge's wording, which is also `visibilityLabel`'s: one fact,
  // one set of words. "Organization only" rather than "This organization"
  // because a bare "Organization" on a card reads as the kind of idea.
  it.each([
    ['PUBLIC', 'Public'],
    ['ORGANIZATION', 'Organization only'],
    ['TEAM', 'Team only'],
    ['PRIVATE', 'Private'],
  ])('renders %s as "%s"', async (visibility, label) => {
    mockContext({
      ideas: [makeIdea({ visibility: visibility as Idea['visibility'] })],
    })
    renderWithRouter(<IdeasWorkspace />)

    // Scoped to the list: the audience filter above the list offers options with
    // these same words, which is not a collision to design around - a filter
    // labelled "Public" is what it should be called. The assertion is about the
    // rows.
    const list = await screen.findByRole('list', { name: 'Ideas' })
    expect(await within(list).findByText(label)).toBeInTheDocument()
  })

  // --- what is offered ------------------------------------------------------

  it('never offers a review move as a plain status change', async () => {
    // The server listed the review move for this reviewer, and the card still
    // draws nothing: starting and deciding a review have to leave a `Review`
    // row, which `transitionIdea` refuses. Those moves belong to the review
    // workspace, so offering them here would be offering a button the server
    // rejects - and a client-side copy of the review rules is exactly the
    // second place for them to drift.
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [makeIdea({ availableTransitions: ['UNDER_REVIEW'] })],
    })
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: 'Start review' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Take over review' })).not.toBeInTheDocument()
  })

  it('never offers a decision as a plain status change', async () => {
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [
        makeIdea({
          status: 'UNDER_REVIEW',
          availableTransitions: ['CHANGES_REQUESTED', 'REJECTED', 'APPROVED'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    for (const label of ['Request changes', 'Reject', 'Approve']) {
      expect(screen.queryByRole('button', { name: label })).not.toBeInTheDocument()
    }
  })

  it('offers an ordinary member nothing on somebody else submitted idea', async () => {
    mockContext({
      viewer: SIGNED_IN,
      systemRole: false,
      ideas: [makeIdea({ availableTransitions: [] })],
    })
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: 'Start review' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Send to my organization' }),
    ).not.toBeInTheDocument()
  })

  it('offers the author a re-submission after changes are requested', async () => {
    mockContext({
      viewer: SIGNED_IN,
      ideas: [
        makeIdea({
          status: 'CHANGES_REQUESTED',
          authorId: '7',
          availableTransitions: ['SUBMITTED'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    expect(await screen.findByRole('button', { name: 'Submit again' })).toBeInTheDocument()
  })

  it('never offers the automation hand-off, which leads nowhere yet', async () => {
    // The transition exists so the lifecycle is complete, and nothing acts on an
    // idea once it reaches that state. A button that goes nowhere would be
    // worse than none.
    mockContext({
      viewer: REVIEWER,
      systemRole: true,
      ideas: [
        makeIdea({
          status: 'APPROVED',
          availableTransitions: ['AUTOMATION_PROPOSAL'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Automate the invoice run')
    expect(screen.queryByRole('button', { name: /Hand off/ })).not.toBeInTheDocument()
  })

  // --- performing a transition ----------------------------------------------

  it('performs the move the server listed and reports its message', async () => {
    transitionMock.mockResolvedValue({
      success: true,
      message: 'Idea moved to Waiting for your organization.',
      field: null,
      idea: makeIdea({
        status: 'SUBMITTED_TO_ORGANIZATION',
        availableTransitions: [],
      }),
    })
    mockContext({
      viewer: SIGNED_IN,
      ideas: [
        makeIdea({
          authorId: '7',
          availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Send to my organization' }))

    expect(transitionMock).toHaveBeenCalledWith('1', 'SUBMITTED_TO_ORGANIZATION')
    expect(
      await screen.findByText('Idea moved to Waiting for your organization.'),
    ).toBeInTheDocument()
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
      viewer: SIGNED_IN,
      ideas: [
        makeIdea({
          authorId: '7',
          availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Send to my organization' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('You are not allowed to make that change to this idea.')
  })

  it('reports a transport failure without claiming the move happened', async () => {
    transitionMock.mockRejectedValue(new Error('Failed to fetch'))
    mockContext({
      viewer: SIGNED_IN,
      ideas: [
        makeIdea({
          authorId: '7',
          availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Send to my organization' }))

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
      viewer: SIGNED_IN,
      ideas: [
        makeIdea({
          authorId: '7',
          availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    fireEvent.click(await screen.findByRole('button', { name: 'Send to my organization' }))

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Send to my organization' })).toBeDisabled(),
    )
    release({
      success: true,
      message: 'Idea moved to Waiting for your organization.',
      field: null,
      idea: makeIdea({ status: 'SUBMITTED_TO_ORGANIZATION' }),
    })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Send to my organization' })).not.toBeDisabled(),
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
      viewer: SIGNED_IN,
      ideas: [
        makeIdea({
          id: '1',
          authorId: '7',
          availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
        }),
        makeIdea({
          id: '2',
          title: 'Another idea',
          authorId: '7',
          status: 'ORGANIZATION_CHANGES_REQUESTED',
          availableTransitions: ['SUBMITTED_TO_ORGANIZATION'],
        }),
      ],
    })
    renderWithRouter(<IdeasWorkspace />)

    await screen.findByText('Another idea')
    const cards = within(screen.getByRole('list')).getAllByRole('listitem')
    fireEvent.click(within(cards[0]).getByRole('button', { name: 'Send to my organization' }))

    await waitFor(() =>
      expect(
        within(cards[0]).getByRole('button', {
          name: 'Send to my organization',
        }),
      ).toBeDisabled(),
    )
    // The other idea's button is untouched: one move in flight does not lock
    // the whole page.
    expect(within(cards[1]).getByRole('button', { name: 'Submit again' })).not.toBeDisabled()
    release({ success: true, message: 'ok', field: null, idea: makeIdea() })
  })

  // --- protected ideas ------------------------------------------------------

  it('does not display an idea the server did not return', async () => {
    // A `PRIVATE` idea of somebody else is not in the response at all, so
    // there is nothing to hide client-side - which is the point: the filter
    // that removed it ran on the server, in the same query the UI reads.
    mockContext({ ideas: [] })
    renderWithRouter(<IdeasWorkspace />)

    expect(await screen.findByText('No ideas here yet')).toBeInTheDocument()
    expect(screen.queryByText('Automate the invoice run')).not.toBeInTheDocument()
  })

  it('does not offer a transition on an idea the viewer cannot see', async () => {
    mockContext({
      ideas: [makeIdea({ visibility: 'PRIVATE', availableTransitions: [] })],
    })
    renderWithRouter(<IdeasWorkspace />)

    // Scoped to the idea's own card: the page's "File a new idea" link lives
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
      'Send to my organization',
      'Start review',
      'Send back',
      'Approve',
      'Reject',
      'Hand off for automation',
    ]) {
      expect(within(card as HTMLElement).queryByRole('button', { name: label })).toBeNull()
    }
  })
})
