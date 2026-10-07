import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { makeApprovedIdea, makeIdea } from '../../../test/idea'
import { useAuth } from '../../identity/auth/AuthContext'
import { IdeaDetailPage } from './IdeaDetailPage'

vi.mock('../../ideas/api/ideasApi', async (importOriginal) => ({
  ...(await importOriginal()),
  ideaRequest: vi.fn(),
}))
vi.mock('../../identity/auth/AuthContext', () => ({ useAuth: vi.fn() }))
// The two panels are stubbed at the *hook* rather than at the component, so
// their real code - headings, empty states - is still rendered and asserted
// against. `importOriginal` keeps the modules' constants, which the components
// read for their limits.
vi.mock('../../ideas/hooks/useComments', async (importOriginal) => ({
  ...(await importOriginal()),
  useComments: () => ({
    comments: [],
    loading: false,
    error: null,
    reload: vi.fn(),
    post: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    // The composer's own error slot, which the component reads unconditionally.
    writeError: null,
    clearWriteError: vi.fn(),
  }),
}))
vi.mock('../../ideas/hooks/useAttachments', async (importOriginal) => ({
  ...(await importOriginal()),
  useAttachments: () => ({
    attachments: [],
    loading: false,
    error: null,
    reload: vi.fn(),
    upload: vi.fn(),
    remove: vi.fn(),
    // The uploader's own error slot, read unconditionally by the component.
    writeError: null,
    clearWriteError: vi.fn(),
  }),
}))
vi.mock('../../reviews/api/reviewsApi', () => ({
  ideaReviewsRequest: vi.fn(async () => []),
}))
vi.mock('../../notifications/hooks/useNotifications', () => ({
  useUnreadNotifications: () => 0,
}))
vi.mock('../../messaging/hooks/useMessages', () => ({
  useUnreadMessageThreads: () => 0,
}))

const { ideaRequest } = await import('../../ideas/api/ideasApi')

function renderPage(ideaId = '1') {
  return render(
    <RouterProvider
      router={createMemoryRouter([{ path: '/app/ideas/:ideaId', element: <IdeaDetailPage /> }], {
        initialEntries: [`/app/ideas/${ideaId}`],
      })}
    />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuth).mockReturnValue({
    user: { id: '7', email: 'ada@example.com' },
  } as unknown as ReturnType<typeof useAuth>)
})

/**
 * The page's job is to answer four questions before anything else: what is this,
 * **who owns it**, who created it, and where is it. Each test below asks one of
 * them, because a page that answered three of the four would pass a test that
 * checked one.
 */
describe('IdeaDetailPage', () => {
  it('says who owns it and who can see it, as two separate facts', async () => {
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({
        title: 'Automate the invoice run',
        submissionContext: 'ORGANIZATION',
        organizationId: '3',
        tenantName: 'MUNA',
        visibility: 'ORGANIZATION',
      }),
    )
    renderPage()

    expect(
      await screen.findByRole('heading', { name: 'Automate the invoice run' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Owned by')).toBeInTheDocument()
    expect(screen.getByText('MUNA')).toBeInTheDocument()
    expect(screen.getByText('Organization only')).toBeInTheDocument()
    expect(screen.getByText('Organization Idea')).toBeInTheDocument()
  })

  it('keeps the owner visible when the audience is the whole platform', async () => {
    // The case that must never collapse: public audience, organization owner.
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({
        submissionContext: 'ORGANIZATION',
        organizationId: '3',
        tenantName: 'MUNA',
        visibility: 'PUBLIC',
      }),
    )
    renderPage()

    expect(await screen.findByText('MUNA')).toBeInTheDocument()
    expect(screen.getByText('Public')).toBeInTheDocument()
  })

  it('says an individual idea is the reader’s own', async () => {
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({
        submissionContext: 'INDIVIDUAL',
        authorId: '7',
        visibility: 'PRIVATE',
      }),
    )
    renderPage()

    expect(await screen.findByText('Individual Idea')).toBeInTheDocument()
    expect(screen.getByText('Owned by')).toBeInTheDocument()
    expect(screen.getAllByText('you').length).toBeGreaterThan(0)
    expect(screen.getByText('Private')).toBeInTheDocument()
  })

  it('says when an idea belongs to somebody else', async () => {
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({
        submissionContext: 'INDIVIDUAL',
        authorId: '8',
        visibility: 'PUBLIC',
      }),
    )
    renderPage()

    expect(await screen.findByText('the person who filed it')).toBeInTheDocument()
    expect(screen.getByText('Another member of this platform')).toBeInTheDocument()
  })

  it('offers Edit only when the server says this viewer may edit', async () => {
    // Author-only, from `viewerCanEdit` - the field the backend computes by
    // running `updateIdea`'s own checks.
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({ authorId: '7', status: 'DRAFT', viewerCanEdit: true }),
    )
    renderPage()

    expect(await screen.findByRole('link', { name: 'Edit draft' })).toHaveAttribute(
      'href',
      '/app/ideas/1/edit',
    )
  })

  it('offers no Edit when the server says this viewer may not', async () => {
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({ authorId: '8', status: 'DRAFT', viewerCanEdit: false }),
    )
    renderPage()

    await screen.findByRole('heading', { name: /Automate/ })
    expect(screen.queryByRole('link', { name: 'Edit draft' })).not.toBeInTheDocument()
  })

  it('labels a resubmission as a revision, not an edit', async () => {
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({
        authorId: '7',
        status: 'CHANGES_REQUESTED',
        viewerCanEdit: true,
      }),
    )
    renderPage()

    expect(await screen.findByRole('link', { name: 'Revise idea' })).toBeInTheDocument()
  })

  it('says an unavailable idea without choosing between the two reasons', async () => {
    // `null` is the server's answer for "may not read" and for "does not exist",
    // and this page must not guess which - an id must not reveal what exists.
    vi.mocked(ideaRequest).mockResolvedValue(null)
    renderPage()

    expect(
      await screen.findByText(/It may not exist, or it may not be one you are allowed to read/),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to your ideas' })).toHaveAttribute(
      'href',
      '/app/ideas',
    )
  })

  it('reports a failed load as a failure and offers a retry', async () => {
    vi.mocked(ideaRequest).mockRejectedValue(new Error('offline'))
    renderPage()

    expect(await screen.findByRole('button', { name: 'Try again' })).toBeInTheDocument()

    vi.mocked(ideaRequest).mockResolvedValue(makeApprovedIdea({ title: 'Second try' }))
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('heading', { name: 'Second try' })).toBeInTheDocument()
  })

  it('shows the lifecycle position, with what the state means', async () => {
    vi.mocked(ideaRequest).mockResolvedValue(
      makeIdea({
        status: 'UNDER_REVIEW',
        state: { ...makeIdea().state, label: 'Under review' },
      }),
    )
    renderPage()

    expect(await screen.findByText('Status')).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText('Under review').length).toBeGreaterThan(0))
  })
})
