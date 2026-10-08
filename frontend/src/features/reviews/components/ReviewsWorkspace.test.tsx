import { makeIdea } from '../../../test/idea'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { IdeaPage } from '../../ideas/api/ideasApi'
import { useAuth } from '../../identity/auth/AuthContext'
import { useOrganization } from '../../organizations/context/useOrganization'
import { pageOf } from '../../../test/ideaPage'
import type { Review } from '../api/reviewsApi'
import { PlatformReviewWorkspace, ReviewsWorkspace } from './ReviewsWorkspace'

vi.mock('../api/reviewsApi', () => ({
  viewerCanReviewInRequest: vi.fn(),
  reviewQueueRequest: vi.fn(),
  ideaReviewsRequest: vi.fn(),
  startReviewRequest: vi.fn(),
  completeReviewRequest: vi.fn(),
  platformReviewQueueRequest: vi.fn(),
}))
vi.mock('../../ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  attachmentsRequest: vi.fn(async () => pageOf([])),
  ideaRequest: vi.fn(),
}))
vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
vi.mock('../../organizations/context/useOrganization', () => ({ useOrganization: vi.fn() }))

const {
  viewerCanReviewInRequest,
  reviewQueueRequest,
  ideaReviewsRequest,
  startReviewRequest,
  platformReviewQueueRequest,
} = await import('../api/reviewsApi')
const { ideaRequest } = await import('../../ideas/api/ideasApi')
const canReviewMock = vi.mocked(viewerCanReviewInRequest)
const queueMock = vi.mocked(reviewQueueRequest)
const historyMock = vi.mocked(ideaReviewsRequest)
const startMock = vi.mocked(startReviewRequest)

function review(overrides: Partial<Review> = {}): Review {
  return {
    id: '9',
    ideaId: '1',
    scope: 'PLATFORM',
    round: 1,
    reviewerId: '7',
    decision: 'CHANGES_REQUESTED',
    feedback: 'Add the monthly volume.',
    assessments: [{ criterion: 'EVIDENCE', rating: 'DOES_NOT_MEET', note: '' }],
    createdAt: '2026-01-03T00:00:00.000Z',
    completedAt: '2026-01-04T00:00:00.000Z',
    submissionSnapshot: { title: 'Automate the invoice run' },
    ...overrides,
  }
}

const GLOBEX = { id: '4', name: 'Globex' }
const INITECH = { id: '5', name: 'Initech' }

function mockContext(
  organization: { id: string; name: string } | null = { id: '3', name: 'Acme' },
) {
  vi.mocked(useAuth).mockReturnValue({
    user: { id: '7', email: 'reviewer@acme.example' },
  } as unknown as ReturnType<typeof useAuth>)
  vi.mocked(useOrganization).mockReturnValue({
    activeOrganization: organization,
    status: 'ready',
  } as unknown as ReturnType<typeof useOrganization>)
}

/**
 * A fresh element on every call, deliberately: rerendering the *same* element
 * reference lets React bail out of the subtree, which would quietly make the
 * organization switch below a no-op and the test pass for the wrong reason.
 */
const workspace = () => (
  <MemoryRouter>
    <ReviewsWorkspace />
  </MemoryRouter>
)

function renderWorkspace() {
  const view = render(workspace())
  return {
    ...view,
    /**
     * What the organization switcher does: the context answers with a different
     * organization and the same tree is rendered again.
     */
    switchOrganization(organization: { id: string; name: string }) {
      mockContext(organization)
      view.rerender(workspace())
    },
  }
}

const queueList = () => screen.findByRole('list', { name: 'Ideas waiting for review' })

describe('ReviewsWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockContext()
    canReviewMock.mockResolvedValue(true)
    // A queue is ideas this reviewer may take, so the default subject is one they
    // can start a review of. The one test about the server declining states that
    // with its own fixture.
    queueMock.mockResolvedValue(pageOf([makeIdea({ viewerCanStartReview: true })]))
    historyMock.mockResolvedValue([])
  })

  it('asks for a queue only once it knows the viewer reviews here', async () => {
    renderWorkspace()

    expect(
      await within(await queueList()).findByText('Automate the invoice run'),
    ).toBeInTheDocument()
    expect(canReviewMock).toHaveBeenCalledWith('3')
    expect(queueMock).toHaveBeenCalledWith('3', { offset: 0 })
  })

  it('tells a non-reviewer why, and never asks for the queue', async () => {
    canReviewMock.mockResolvedValue(false)

    renderWorkspace()

    expect(await screen.findByText(/You do not review ideas in Acme/)).toBeInTheDocument()
    expect(queueMock).not.toHaveBeenCalled()
  })

  it('asks the viewer to pick an organization when none is active', () => {
    mockContext(null)

    renderWorkspace()

    expect(screen.getByText(/Choose an organization/)).toBeInTheDocument()
    expect(canReviewMock).not.toHaveBeenCalled()
  })

  it('shows a loading state, then the empty queue', async () => {
    let resolve: (page: IdeaPage) => void = () => {}
    queueMock.mockReturnValue(new Promise((r) => (resolve = r)))

    renderWorkspace()

    expect(await screen.findByText('Loading the review queue…')).toBeInTheDocument()
    await act(async () => resolve(pageOf([])))
    expect(screen.getByText('No ideas are waiting for your review.')).toBeInTheDocument()
  })

  // --- the empty state must not claim to know more than it does -----------

  it('says whose queue is empty, and why their own ideas are not in it', async () => {
    queueMock.mockResolvedValue(pageOf([]))

    renderWorkspace()

    expect(await screen.findByText('No ideas are waiting for your review.')).toBeInTheDocument()
    expect(
      screen.getByText('Ideas you submitted yourself are not eligible for your own review.'),
    ).toBeInTheDocument()
  })

  it('makes no claim about the organization’s submissions', async () => {
    queueMock.mockResolvedValue(pageOf([]))

    renderWorkspace()
    await screen.findByText('No ideas are waiting for your review.')

    // The server does not say why a queue is empty - it returns the same empty
    // page whether there is nothing waiting or the only ideas are the reviewer's
    // own - so every one of these would be a guess. One of them would also be the
    // answer to "does this organization have ideas?", which is the thing the
    // empty page exists to withhold.
    const panel = screen.getByText('No ideas are waiting for your review.').closest('div')
    expect(panel).not.toHaveTextContent(/no one has submitted/i)
    expect(panel).not.toHaveTextContent(/there are no submitted ideas/i)
    expect(panel).not.toHaveTextContent(/nobody has submitted/i)
    expect(panel).not.toHaveTextContent(/other reviewers/i)
    // A count would be the same leak in a different shape.
    expect(panel).not.toHaveTextContent(/\\d/)
  })

  it('asks nothing beyond the queue and the review check', async () => {
    queueMock.mockResolvedValue(pageOf([]))

    renderWorkspace()
    await screen.findByText('No ideas are waiting for your review.')

    // The security test, in the only form a client can honour it: the empty
    // state is rendered from the answer to the one request the page already made,
    // so there is no second question whose answer would distinguish "nothing
    // eligible" from "only my own ideas".
    expect(queueMock).toHaveBeenCalledTimes(1)
    expect(canReviewMock).toHaveBeenCalledTimes(1)
  })

  it('shows the same empty state whichever organization is selected', async () => {
    queueMock.mockResolvedValue(pageOf([]))
    const view = renderWorkspace()
    await screen.findByText('No ideas are waiting for your review.')

    view.switchOrganization(GLOBEX)

    expect(await screen.findByText('No ideas are waiting for your review.')).toBeInTheDocument()
    expect(
      screen.getByText('Ideas you submitted yourself are not eligible for your own review.'),
    ).toBeInTheDocument()
  })

  it('reports a failed request as a failure, not as an empty queue', async () => {
    queueMock.mockRejectedValue(new Error('offline'))

    renderWorkspace()

    expect(await screen.findByRole('alert')).toHaveTextContent('could not load the review queue')
    expect(screen.queryByText(/No ideas are waiting/)).not.toBeInTheDocument()
  })

  it('pages by the server’s offsets', async () => {
    queueMock.mockImplementation(async (_organizationId, page = {}) =>
      pageOf(
        [makeIdea({ id: String((page.offset ?? 0) + 1), title: `Idea at ${page.offset ?? 0}` })],
        {
          offset: page.offset ?? 0,
          limit: 20,
          totalCount: 45,
          hasNextPage: (page.offset ?? 0) + 20 < 45,
          hasPreviousPage: (page.offset ?? 0) > 0,
        },
      ),
    )

    renderWorkspace()
    await screen.findByText('Idea at 0')

    fireEvent.click(screen.getByRole('button', { name: /next/i }))

    expect(await screen.findByText('Idea at 20')).toBeInTheDocument()
    expect(queueMock).toHaveBeenLastCalledWith('3', { offset: 20 })
  })

  it('discards a superseded answer that arrives late', async () => {
    const resolvers: Array<(page: IdeaPage) => void> = []
    queueMock.mockImplementation(() => new Promise<IdeaPage>((resolve) => resolvers.push(resolve)))

    renderWorkspace()
    await waitFor(() => expect(resolvers).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(resolvers).toHaveLength(2))

    await act(async () => resolvers[1](pageOf([makeIdea({ title: 'Current answer' })])))
    await act(async () => resolvers[0](pageOf([makeIdea({ title: 'Stale answer' })])))

    expect(screen.getByText('Current answer')).toBeInTheDocument()
    expect(screen.queryByText('Stale answer')).not.toBeInTheDocument()
  })

  // --- switching organization ----------------------------------------------

  it('starts the new organization at the first page, not where the last one was', async () => {
    queueMock.mockImplementation(async (organizationId, page = {}) =>
      pageOf(
        [
          makeIdea({
            id: `${organizationId}-${page.offset ?? 0}`,
            title: `Idea for ${organizationId} at ${page.offset ?? 0}`,
          }),
        ],
        {
          offset: page.offset ?? 0,
          limit: 20,
          totalCount: 45,
          hasNextPage: (page.offset ?? 0) + 20 < 45,
          hasPreviousPage: (page.offset ?? 0) > 0,
        },
      ),
    )

    const view = renderWorkspace()
    await screen.findByText('Idea for 3 at 0')
    fireEvent.click(screen.getByRole('button', { name: /next/i }))
    await screen.findByText('Idea for 3 at 20')
    expect(queueMock).toHaveBeenLastCalledWith('3', { offset: 20 })

    view.switchOrganization(GLOBEX)

    await screen.findByText('Idea for 4 at 0')
    expect(queueMock).toHaveBeenLastCalledWith('4', { offset: 0 })
    // Not even once: an offset carried across is a page of somebody else's
    // results, so the new organization must never be asked for one.
    expect(queueMock).not.toHaveBeenCalledWith('4', { offset: 20 })
  })

  it('shows the new organization loading instead of the old organization’s rows', async () => {
    // Acme answers; Globex never does, so the switch is caught mid-flight.
    queueMock.mockImplementation(async (organizationId) =>
      organizationId === '3'
        ? pageOf([makeIdea({ title: 'Acme idea' })])
        : new Promise<IdeaPage>(() => {}),
    )

    const view = renderWorkspace()
    expect(await screen.findByText('Acme idea')).toBeInTheDocument()

    view.switchOrganization(GLOBEX)

    expect(await screen.findByText('Loading the review queue…')).toBeInTheDocument()
    // The previous organization's rows are the previous organization's rows.
    expect(screen.queryByText('Acme idea')).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Ideas waiting for review' })).not.toBeInTheDocument()
  })

  it('shows the new organization’s own empty queue, not the old organization’s rows', async () => {
    queueMock.mockImplementation(async (organizationId) =>
      organizationId === '3' ? pageOf([makeIdea({ title: 'Idea for Acme' })]) : pageOf([]),
    )

    const view = renderWorkspace()
    expect(await screen.findByText('Idea for Acme')).toBeInTheDocument()

    view.switchOrganization(GLOBEX)

    // Globex's own answer, empty - not Acme's rows left over, and not Acme's
    // pageInfo still claiming there is a page two to turn to.
    expect(await screen.findByText('No ideas are waiting for your review.')).toBeInTheDocument()
    expect(screen.queryByText('Idea for Acme')).not.toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: /page/i })).not.toBeInTheDocument()
  })

  it('drops a late answer from an organization that is no longer selected', async () => {
    const resolvers: Record<string, Array<(page: IdeaPage) => void>> = {}
    queueMock.mockImplementation(
      (organizationId) =>
        new Promise<IdeaPage>((resolve) => {
          resolvers[organizationId] = [...(resolvers[organizationId] ?? []), resolve]
        }),
    )

    const view = renderWorkspace()
    await waitFor(() => expect(resolvers['3']).toHaveLength(1))

    view.switchOrganization(GLOBEX)
    await waitFor(() => expect(resolvers['4']).toHaveLength(1))

    view.switchOrganization(INITECH)
    await waitFor(() => expect(resolvers['5']).toHaveLength(1))

    // Initech answers, and only then does the abandoned Acme request settle.
    await act(async () => resolvers['5'][0](pageOf([makeIdea({ title: 'Initech idea' })])))
    expect(await screen.findByText('Initech idea')).toBeInTheDocument()
    await act(async () => resolvers['3'][0](pageOf([makeIdea({ title: 'Acme idea' })])))

    expect(screen.getByText('Initech idea')).toBeInTheDocument()
    expect(screen.queryByText('Acme idea')).not.toBeInTheDocument()
  })

  it('closes the review context of an idea from the previous organization', async () => {
    queueMock.mockImplementation(async (organizationId) =>
      pageOf([makeIdea({ title: `Idea for ${organizationId}` })]),
    )

    const view = renderWorkspace()
    fireEvent.click(await within(await queueList()).findByRole('button', { name: /Idea for 3/ }))
    expect(await screen.findByRole('article')).toBeInTheDocument()

    view.switchOrganization(GLOBEX)

    // Wait for Globex to have arrived first: asserted during the capability
    // check the panel is not there for the reason this test is about, but
    // because the whole section is replaced while access is re-checked.
    await screen.findByText('Idea for 4')
    // One organization's idea must not stay on screen under another's name.
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('opens an idea’s review context with its history', async () => {
    historyMock.mockResolvedValue([
      review(),
      review({
        id: '10',
        round: 2,
        decision: null,
        completedAt: null,
        feedback: '',
        assessments: [],
      }),
    ])

    renderWorkspace()
    fireEvent.click(
      await within(await queueList()).findByRole('button', { name: /Automate the invoice run/ }),
    )

    const context = screen.getByRole('article')
    expect(within(context).getByText('A description long enough.')).toBeInTheDocument()
    expect(within(context).getByRole('button', { name: 'Start review' })).toBeInTheDocument()
    const history = await within(context).findByRole('list', { name: 'Review history' })
    expect(within(history).getByText('Add the monthly volume.')).toBeInTheDocument()
    expect(within(history).getByText('Changes requested')).toBeInTheDocument()
    expect(within(history).getByText('In progress')).toBeInTheDocument()
    expect(within(history).getByText('Does not meet')).toBeInTheDocument()
    expect(historyMock).toHaveBeenCalledWith('1')
  })

  it('shows the reviewer the author’s problem story, and only the answered parts', async () => {
    queueMock.mockResolvedValue(
      pageOf([
        makeIdea({
          currentProcess: 'Invoices arrive by email and are typed into Excel.',
          currentTools: ['EMAIL', 'EXCEL'],
          frequency: 'DAILY',
          peopleInvolved: 3,
          impacts: ['MISTAKES'],
          expectedBenefit: 'Fewer typing mistakes.',
        }),
      ]),
    )

    renderWorkspace()
    fireEvent.click(
      await within(await queueList()).findByRole('button', { name: /Automate the invoice run/ }),
    )

    const story = within(screen.getByRole('article')).getByRole('region', {
      name: 'The problem in detail',
    })
    expect(
      within(story).getByText('Invoices arrive by email and are typed into Excel.'),
    ).toBeInTheDocument()
    expect(within(story).getByText('Email, Excel')).toBeInTheDocument()
    expect(within(story).getByText('Daily')).toBeInTheDocument()
    expect(within(story).getByText('About 3')).toBeInTheDocument()
    expect(within(story).getByText('Mistakes happen')).toBeInTheDocument()
    expect(within(story).getByText('Fewer typing mistakes.')).toBeInTheDocument()
    // Nothing was said about who is affected, so nothing is shown for it.
    expect(within(story).queryByText('Who is affected')).not.toBeInTheDocument()
  })

  it('offers no Start review, and no decision form, where the server says not', async () => {
    queueMock.mockResolvedValue(pageOf([makeIdea({ viewerCanStartReview: false })]))

    renderWorkspace()
    fireEvent.click(await within(await queueList()).findByRole('button', { name: /Automate/ }))

    expect(screen.queryByRole('button', { name: 'Start review' })).not.toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Review decision' })).not.toBeInTheDocument()
  })

  it('starts a review and shows the server’s idea with the decision form', async () => {
    startMock.mockResolvedValue({
      success: true,
      message: 'Review started.',
      field: null,
      review: review({ id: '12', decision: null, completedAt: null }),
      idea: makeIdea({
        status: 'UNDER_REVIEW',
        viewerCanStartReview: false,
        viewerActiveReviewId: '12',
      }),
    })

    renderWorkspace()
    fireEvent.click(await within(await queueList()).findByRole('button', { name: /Automate/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Start review' }))

    expect(await screen.findByRole('form', { name: 'Review decision' })).toBeInTheDocument()
    expect(screen.getByText('Review started.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start review' })).not.toBeInTheDocument()
    expect(startMock).toHaveBeenCalledWith('1')
    // The idea is no longer waiting, so the queue is asked again.
    await waitFor(() => expect(queueMock.mock.calls.length).toBeGreaterThan(1))
  })

  it('shows the server’s refusal when somebody else started first', async () => {
    startMock.mockResolvedValue({
      success: false,
      message: 'This idea is not waiting for review.',
      field: null,
      review: null,
      idea: null,
    })

    renderWorkspace()
    fireEvent.click(await within(await queueList()).findByRole('button', { name: /Automate/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Start review' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('not waiting for review')
    expect(screen.queryByRole('form', { name: 'Review decision' })).not.toBeInTheDocument()
  })

  it('reports a failed start as a transport failure', async () => {
    startMock.mockRejectedValue(new Error('offline'))

    renderWorkspace()
    fireEvent.click(await within(await queueList()).findByRole('button', { name: /Automate/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Start review' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('review was not started')
  })

  it('reopens an in-progress review from ?idea=', async () => {
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({
        id: '5',
        title: 'Being reviewed',
        status: 'UNDER_REVIEW',
        viewerCanStartReview: false,
        viewerActiveReviewId: '12',
      }),
    )

    render(
      <MemoryRouter initialEntries={['/app/reviews?idea=5']}>
        <ReviewsWorkspace />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Being reviewed' })).toBeInTheDocument()
    expect(screen.getByRole('form', { name: 'Review decision' })).toBeInTheDocument()
    expect(ideaRequest).toHaveBeenCalledWith('5')
  })

  it('offers to take over a stalled review from ?idea=, then shows the new round', async () => {
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({
        id: '5',
        title: 'Stalled',
        status: 'UNDER_REVIEW',
        viewerCanStartReview: true,
        viewerActiveReviewId: null,
      }),
    )
    startMock.mockResolvedValue({
      success: true,
      message: 'Review started.',
      field: null,
      review: review({ id: '13', round: 2, decision: null, completedAt: null }),
      idea: makeIdea({
        id: '5',
        title: 'Stalled',
        status: 'UNDER_REVIEW',
        viewerCanStartReview: false,
        viewerActiveReviewId: '13',
      }),
    })
    historyMock.mockResolvedValue([
      review({ reviewerId: '6', decision: 'WITHDRAWN', feedback: '', assessments: [] }),
    ])

    render(
      <MemoryRouter initialEntries={['/app/reviews?idea=5']}>
        <ReviewsWorkspace />
      </MemoryRouter>,
    )

    expect(
      await screen.findByText('The reviewer who started this review can no longer review it.'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start review' })).not.toBeInTheDocument()
    expect(await screen.findByText('Withdrawn')).toBeInTheDocument()
    expect(screen.getByText(/another reviewer took it over/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Take over review' }))

    expect(await screen.findByRole('form', { name: 'Review decision' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Take over review' })).not.toBeInTheDocument()
    expect(startMock).toHaveBeenCalledWith('5')
  })
})

describe('PlatformReviewWorkspace', () => {
  beforeEach(() => {
    vi.mocked(platformReviewQueueRequest).mockReset()
    historyMock.mockReset()
    historyMock.mockResolvedValue([])
    mockContext(null)
  })

  it('lists the platform queue without needing an organization, and opens a submission', async () => {
    vi.mocked(platformReviewQueueRequest).mockResolvedValue([
      makeIdea({
        id: '4',
        title: 'Tax collection',
        status: 'SUBMITTED',
        viewerCanStartReview: true,
      }),
    ])
    render(
      <MemoryRouter>
        <PlatformReviewWorkspace />
      </MemoryRouter>,
    )

    const queue = await screen.findByRole('list', {
      name: 'Submissions waiting for platform review',
    })
    // Status as a pill above the details, then the details themselves.
    expect(within(queue).getByText('Waiting for review')).toBeVisible()
    expect(within(queue).getByText('Uncategorised', { exact: false })).toBeVisible()
    fireEvent.click(within(queue).getByRole('button', { name: /Tax collection/ }))

    expect(await screen.findByRole('button', { name: 'Start review' })).toBeInTheDocument()
  })

  it('hides the details and keeps them hidden for the next idea, with the review still there', async () => {
    vi.mocked(platformReviewQueueRequest).mockResolvedValue([
      makeIdea({
        id: '4',
        title: 'Tax collection',
        description: 'Taxes are collected on paper.',
        status: 'SUBMITTED',
        viewerCanStartReview: true,
      }),
      makeIdea({
        id: '5',
        title: 'Hostel payments',
        description: 'Payments are tracked by hand.',
        status: 'SUBMITTED',
        viewerCanStartReview: true,
      }),
    ])
    render(
      <MemoryRouter>
        <PlatformReviewWorkspace />
      </MemoryRouter>,
    )
    const queue = await screen.findByRole('list', {
      name: 'Submissions waiting for platform review',
    })
    fireEvent.click(within(queue).getByRole('button', { name: /Tax collection/ }))

    const hide = await screen.findByRole('button', { name: 'Hide details' })
    expect(hide).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(hide)

    const show = screen.getByRole('button', { name: 'Show details' })
    expect(show).toHaveAttribute('aria-expanded', 'false')
    // The supporting evidence goes with the details; the review controls stay.
    expect(screen.queryByRole('button', { name: /Supporting evidence/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Start review' })).toBeInTheDocument()

    fireEvent.click(within(queue).getByRole('button', { name: /Hostel payments/ }))
    expect(await screen.findByRole('heading', { name: 'Hostel payments' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show details' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Show details' }))
    expect(screen.getByRole('button', { name: /Supporting evidence/ })).toBeInTheDocument()
  })

  it('closes the selected idea and goes back to the queue', async () => {
    vi.mocked(platformReviewQueueRequest).mockResolvedValue([
      makeIdea({ id: '4', title: 'Tax collection', status: 'SUBMITTED' }),
    ])
    render(
      <MemoryRouter>
        <PlatformReviewWorkspace />
      </MemoryRouter>,
    )
    const queue = await screen.findByRole('list', {
      name: 'Submissions waiting for platform review',
    })
    fireEvent.click(within(queue).getByRole('button', { name: /Tax collection/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }))

    expect(screen.queryByRole('heading', { name: 'Tax collection' })).toBeNull()
    expect(screen.getByText(/Select a submission/)).toBeInTheDocument()
  })

  it('says so when nothing is waiting', async () => {
    vi.mocked(platformReviewQueueRequest).mockResolvedValue([])
    render(
      <MemoryRouter>
        <PlatformReviewWorkspace />
      </MemoryRouter>,
    )

    expect(
      await screen.findByText('No submissions are waiting for platform review.'),
    ).toBeInTheDocument()
  })
})

describe('ReviewsWorkspace beside a platform queue', () => {
  it('stays out of the way of a platform reviewer who reviews in no organization', async () => {
    mockContext(null)
    const { container } = render(
      <MemoryRouter>
        <ReviewsWorkspace quiet />
      </MemoryRouter>,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
