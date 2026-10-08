import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  AdminInvitation,
  AdminMessage,
  AdminMessageThread,
  AdminMessageThreadDetail,
  AdminNotification,
  AdminTeam,
  AdminTeamDetail,
} from '../api/administrationApi'
import {
  EMPTY_PAGE_INFO,
  pageOfItems,
  READ_ONLY_ADMIN,
  renderAdminPage,
} from '../../../test/renderAdmin'
import { AdminInvitationsPage } from './AdminInvitationsPage'
import { AdminMessageDetailPage } from './AdminMessageDetailPage'
import { AdminMessagesPage } from './AdminMessagesPage'
import { AdminNotificationsPage } from './AdminNotificationsPage'
import { AdminTeamDetailPage } from './AdminTeamDetailPage'
import { AdminTeamsPage } from './AdminTeamsPage'

vi.mock('../api/administrationApi', async (importOriginal) => ({
  ...(await importOriginal()),
  adminTeamsRequest: vi.fn(),
  adminTeamRequest: vi.fn(),
  adminTeamInvitationsRequest: vi.fn(),
  adminInvitationsRequest: vi.fn(),
  adminMessageThreadsRequest: vi.fn(),
  adminMessageThreadRequest: vi.fn(),
  adminThreadMessagesRequest: vi.fn(),
  adminNotificationsRequest: vi.fn(),
}))

const api = await import('../api/administrationApi')
const teamsMock = vi.mocked(api.adminTeamsRequest)
const teamMock = vi.mocked(api.adminTeamRequest)
const teamInvitationsMock = vi.mocked(api.adminTeamInvitationsRequest)
const invitationsMock = vi.mocked(api.adminInvitationsRequest)
const threadsMock = vi.mocked(api.adminMessageThreadsRequest)
const threadMock = vi.mocked(api.adminMessageThreadRequest)
const messagesMock = vi.mocked(api.adminThreadMessagesRequest)
const notificationsMock = vi.mocked(api.adminNotificationsRequest)

const TEAM: AdminTeam = {
  id: '9',
  name: 'Registrar',
  slug: 'registrar',
  description: 'The people who allocate rooms.',
  owner: { id: '7', email: 'ada@example.com', name: 'Ada Author' },
  memberCount: 1,
  inactiveMemberCount: 1,
  ideaCount: 2,
  invitationCount: 1,
  createdAt: '2026-01-01T00:00:00Z',
}

const TEAM_DETAIL: AdminTeamDetail = {
  ...TEAM,
  roles: [
    {
      id: '1',
      name: 'Owner',
      slug: 'owner',
      description: 'Can manage the team.',
      isSystem: true,
      permissions: ['team.view', 'team.members.manage', 'team.ideas.submit'],
      holderCount: 1,
    },
    {
      id: '2',
      name: 'Member',
      slug: 'member',
      description: 'Can file ideas for it.',
      isSystem: true,
      permissions: ['team.view'],
      holderCount: 1,
    },
  ],
  members: [
    {
      id: '20',
      status: 'active',
      joinedAt: '2026-01-01T00:00:00Z',
      user: { id: '7', email: 'ada@example.com', name: 'Ada Author' },
      userIsActive: true,
      roles: [{ id: '1', name: 'Owner', slug: 'owner', isSystem: true }],
    },
    {
      id: '21',
      status: 'inactive',
      joinedAt: '2026-01-02T00:00:00Z',
      user: { id: '8', email: 'rae@example.com', name: 'Rae Rivera' },
      userIsActive: false,
      roles: [{ id: '2', name: 'Member', slug: 'member', isSystem: true }],
    },
  ],
  ideasByStatus: [
    { status: 'APPROVED', count: 2 },
    { status: 'DRAFT', count: 0 },
  ],
}

function invitation(overrides: Partial<AdminInvitation> = {}): AdminInvitation {
  return {
    id: '30',
    scope: 'team',
    status: 'pending',
    isOpen: true,
    email: 'newcomer@teams.example',
    roleSlug: 'member',
    tenantId: '9',
    tenantName: 'Registrar',
    invitedBy: { id: '7', email: 'ada@example.com', name: 'Ada Author' },
    acceptedBy: null,
    createdAt: '2026-02-01T00:00:00Z',
    expiresAt: '2026-02-08T00:00:00Z',
    acceptedAt: null,
    ...overrides,
  }
}

function thread(overrides: Partial<AdminMessageThread> = {}): AdminMessageThread {
  return {
    id: '40',
    subject: 'Automate the invoice run',
    ideaId: '1',
    ideaTitle: 'Automate the invoice run',
    startedBy: { id: '7', email: 'ada@example.com', name: 'Ada Author' },
    participantCount: 2,
    messageCount: 2,
    staleParticipantCount: 1,
    latestMessageAt: '2026-02-02T00:00:00Z',
    createdAt: '2026-02-01T00:00:00Z',
    contentRestricted: false,
    ...overrides,
  }
}

function message(overrides: Partial<AdminMessage> = {}): AdminMessage {
  return {
    id: '50',
    sender: { id: '7', email: 'ada@example.com', name: 'Ada Author' },
    body: 'Can you send the monthly volume?',
    createdAt: '2026-02-01T00:00:00Z',
    ...overrides,
  }
}

function notification(overrides: Partial<AdminNotification> = {}): AdminNotification {
  return {
    id: '60',
    kind: 'idea.platform_approved',
    title: 'Platform review completed',
    body: 'Your idea is ready. Read the report and decide.',
    recipient: { id: '8', email: 'rae@example.com', name: 'Rae Rivera' },
    isRead: false,
    ideaId: '1',
    ideaTitle: 'Automate the invoice run',
    reportId: '70',
    createdAt: '2026-02-03T00:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  for (const mock of [
    teamsMock,
    teamMock,
    teamInvitationsMock,
    invitationsMock,
    threadsMock,
    threadMock,
    messagesMock,
    notificationsMock,
  ]) {
    mock.mockReset()
  }
})

describe('AdminTeamsPage', () => {
  it('lists teams with active members, former members, ideas and invitations', async () => {
    teamsMock.mockResolvedValue(pageOfItems([TEAM]))
    renderAdminPage(<AdminTeamsPage />)

    const row = within(await screen.findByRole('table', { name: 'Teams' })).getAllByRole('row')[1]
    expect(row).toHaveTextContent('Registrar')
    expect(row).toHaveTextContent('/registrar')
    expect(row).toHaveTextContent('ada@example.com')
    // The two membership numbers are both shown: a team keeps the row of
    // somebody who left, so "one member, one former" is the honest summary.
    expect(within(row).getAllByRole('cell')[1]).toHaveTextContent('1')
    expect(within(row).getAllByRole('cell')[2]).toHaveTextContent('1')
    expect(within(row).getByRole('link', { name: 'Registrar' })).toHaveAttribute(
      'href',
      '/app/admin/teams/9',
    )
  })

  it('says a team is not an organization, on the page', () => {
    teamsMock.mockResolvedValue(pageOfItems([TEAM]))
    renderAdminPage(<AdminTeamsPage />)

    // The distinction is the reason this section exists beside Organizations,
    // so the page states it rather than relying on the reader knowing.
    expect(screen.getByText(/A team is not an organization/u)).toBeInTheDocument()
  })

  it('shows an empty state and an error state', async () => {
    teamsMock.mockResolvedValueOnce(pageOfItems([]))
    const { unmount } = renderAdminPage(<AdminTeamsPage />)
    expect(await screen.findByText('No teams match.')).toBeInTheDocument()
    unmount()

    teamsMock.mockRejectedValueOnce(new Error('down'))
    renderAdminPage(<AdminTeamsPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('We could not load the teams.')
  })
})

describe('AdminTeamDetailPage', () => {
  beforeEach(() => {
    teamMock.mockResolvedValue(TEAM_DETAIL)
    teamInvitationsMock.mockResolvedValue(pageOfItems([invitation()]))
  })

  function renderDetail() {
    return renderAdminPage(<AdminTeamDetailPage />, {
      path: '/teams/9',
      pattern: '/teams/:teamId',
    })
  }

  it('shows the roles with their permission codes, and the membership', async () => {
    renderDetail()

    expect(await screen.findByRole('heading', { name: 'Registrar' })).toBeInTheDocument()
    // The codes are listed rather than summarized: a team role holding a review
    // or approval code would be the platform's boundary failing.
    expect(screen.getByText('team.members.manage')).toBeInTheDocument()
    const members = await screen.findByRole('table', { name: 'Team members' })
    expect(members).toHaveTextContent('Ada Author')
    expect(members).toHaveTextContent('Rae Rivera')
    // "Left" and "account deactivated" are two different facts, so they are two
    // different words.
    expect(members).toHaveTextContent('Left')
    expect(members).toHaveTextContent('account deactivated')
  })

  it('offers no way to change the team, because there is no such operation', async () => {
    renderDetail()
    await screen.findByRole('heading', { name: 'Registrar' })

    // Membership is created by accepting an invitation, so an administrator has
    // nothing to press here.
    expect(screen.queryByRole('button', { name: /Remove|Add member|Deactivate/ })).toBeNull()
  })

  it('shows the invitations it has sent, with whether they could still be accepted', async () => {
    renderDetail()

    const table = await screen.findByRole('table', { name: 'Team invitations' })
    expect(table).toHaveTextContent('newcomer@teams.example')
    expect(table).toHaveTextContent('Waiting to be accepted')
    // And says plainly that an invitation is not a membership.
    expect(screen.getByText(/creates one only when the recipient/u)).toBeInTheDocument()
  })

  it('reports a failed load', async () => {
    teamMock.mockRejectedValueOnce(new Error('down'))
    renderDetail()

    expect(await screen.findByRole('alert')).toHaveTextContent('We could not load this team.')
  })
})

describe('AdminInvitationsPage', () => {
  beforeEach(() => invitationsMock.mockResolvedValue(pageOfItems([invitation()])))

  it('lists invitations for both kinds of tenant', async () => {
    invitationsMock.mockResolvedValue(
      pageOfItems([
        invitation(),
        invitation({
          id: '31',
          scope: 'organization',
          tenantId: '3',
          tenantName: 'Acme',
          email: 'newcomer@acme.example',
        }),
      ]),
    )
    renderAdminPage(<AdminInvitationsPage />)

    const table = await screen.findByRole('table', { name: 'Invitations' })
    expect(table).toHaveTextContent('newcomer@teams.example')
    expect(table).toHaveTextContent('newcomer@acme.example')
    // A team invitation links to the team; the organization one is named.
    expect(within(table).getByRole('link', { name: 'Registrar' })).toHaveAttribute(
      'href',
      '/app/admin/teams/9',
    )
  })

  it('sends the filters to the server rather than filtering in the browser', async () => {
    renderAdminPage(<AdminInvitationsPage />)
    await screen.findByRole('table', { name: 'Invitations' })

    fireEvent.change(screen.getByLabelText('Tenant'), { target: { value: 'organization' } })
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'accepted' } })

    await waitFor(() =>
      expect(invitationsMock).toHaveBeenLastCalledWith(
        { scope: 'organization', status: 'accepted' },
        { offset: 0 },
      ),
    )
  })

  it('shows an expired invitation as expired rather than as pending', async () => {
    invitationsMock.mockResolvedValue(
      pageOfItems([
        invitation({ status: 'pending', isOpen: false, expiresAt: '2026-01-01T00:00:00Z' }),
      ]),
    )
    renderAdminPage(<AdminInvitationsPage />)

    // `isOpen` rather than `status`: a pending invitation past its expiry is
    // still "pending" in the database and cannot be accepted.
    expect(await screen.findByText('Expired')).toBeInTheDocument()
  })
})

describe('AdminMessagesPage', () => {
  it('reports the shape of a conversation and no content', async () => {
    threadsMock.mockResolvedValue(pageOfItems([thread()]))
    renderAdminPage(<AdminMessagesPage />)

    const row = within(await screen.findByRole('table', { name: 'Conversations' })).getAllByRole(
      'row',
    )[1]
    expect(row).toHaveTextContent('Automate the invoice run')
    expect(row).toHaveTextContent('started by ada@example.com')
    // Two links carry that title: the conversation, and the idea it is about.
    expect(within(row).getAllByRole('link')[0]).toHaveAttribute('href', '/app/admin/messages/40')
    // "Not read by", not "unread": unread is a position per reader and this
    // console has no reader.
    expect(within(row).getAllByRole('cell')[4]).toHaveTextContent('1')
  })

  it('says a withheld subject is restricted rather than showing a blank cell', async () => {
    threadsMock.mockResolvedValue(
      pageOfItems([thread({ subject: null, ideaTitle: null, contentRestricted: true })]),
    )
    renderAdminPage(<AdminMessagesPage />, { capabilities: READ_ONLY_ADMIN })

    // A thread anchored to an idea takes its subject from that idea's title, so
    // a null subject means "you may not read this", not "unnamed".
    expect(await screen.findByRole('link', { name: 'Restricted' })).toBeInTheDocument()
    expect(screen.getAllByText('An idea you may not read').length).toBeGreaterThan(0)
  })

  it('sends the search and the anchored filter to the server', async () => {
    threadsMock.mockResolvedValue(pageOfItems([thread()]))
    renderAdminPage(<AdminMessagesPage />)
    await screen.findByRole('table', { name: 'Conversations' })

    fireEvent.change(screen.getByLabelText('About an idea'), { target: { value: 'true' } })

    await waitFor(() =>
      expect(threadsMock).toHaveBeenLastCalledWith({ search: '', anchored: true }, { offset: 0 }),
    )
  })

  it('explains why the words are not here', () => {
    threadsMock.mockResolvedValue(pageOfItems([thread()]))
    renderAdminPage(<AdminMessagesPage />)

    expect(screen.getByText(/not visible to anybody who can read one/u)).toBeInTheDocument()
  })
})

describe('AdminMessageDetailPage', () => {
  function renderDetail(capabilities?: typeof READ_ONLY_ADMIN) {
    return renderAdminPage(<AdminMessageDetailPage />, {
      path: '/messages/40',
      pattern: '/messages/:threadId',
      ...(capabilities ? { capabilities } : {}),
    })
  }

  beforeEach(() => {
    threadMock.mockResolvedValue({
      ...thread(),
      participants: [
        { id: '7', email: 'ada@example.com', name: 'Ada Author' },
        { id: '8', email: 'rae@example.com', name: 'Rae Rivera' },
      ],
    } satisfies AdminMessageThreadDetail)
  })

  it('shows the participants - the access rule, made visible', async () => {
    messagesMock.mockResolvedValue({
      ...pageOfItems([message()]),
      contentRestricted: false,
    })
    renderDetail()

    expect(
      await screen.findByRole('heading', { name: 'Automate the invoice run' }),
    ).toBeInTheDocument()
    const participants = screen.getByRole('heading', { name: 'Participants' }).closest('section')!
    expect(participants).toHaveTextContent('Ada Author')
    expect(participants).toHaveTextContent('rae@example.com')
  })

  it('shows the messages to an administrator who may inspect content', async () => {
    messagesMock.mockResolvedValue({
      ...pageOfItems([message()]),
      contentRestricted: false,
    })
    renderDetail()

    expect(await screen.findByText('Can you send the monthly volume?')).toBeInTheDocument()
  })

  it('withholds every body, and says why, without the content permission', async () => {
    // The thread says so too: the page takes its "restricted" answer from the
    // thread, and the message page only agrees with it.
    threadMock.mockResolvedValue({
      ...thread({ contentRestricted: true }),
      participants: [
        { id: '7', email: 'ada@example.com', name: 'Ada Author' },
        { id: '8', email: 'rae@example.com', name: 'Rae Rivera' },
      ],
    } satisfies AdminMessageThreadDetail)
    messagesMock.mockResolvedValue({
      items: [
        message({ body: null }),
        message({
          id: '51',
          sender: { id: '8', email: 'rae@example.com', name: 'Rae Rivera' },
          body: null,
        }),
      ],
      pageInfo: EMPTY_PAGE_INFO,
      contentRestricted: true,
    })
    renderDetail(READ_ONLY_ADMIN)

    expect(await screen.findByText(/cannot read message bodies/u)).toBeInTheDocument()
    // Each message says it was withheld. An empty bubble would read as
    // "nothing was said".
    expect(screen.getAllByText('Message withheld')).toHaveLength(2)
    expect(screen.queryByText('Can you send the monthly volume?')).toBeNull()
  })

  it('offers nothing that could change the conversation', async () => {
    messagesMock.mockResolvedValue({ ...pageOfItems([message()]), contentRestricted: false })
    renderDetail()
    await screen.findByText('Can you send the monthly volume?')

    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('AdminNotificationsPage', () => {
  beforeEach(() => notificationsMock.mockResolvedValue(pageOfItems([notification()])))

  it('lists what the platform told each person', async () => {
    renderAdminPage(<AdminNotificationsPage />)

    const row = within(await screen.findByRole('table', { name: 'Notifications' })).getAllByRole(
      'row',
    )[1]
    expect(row).toHaveTextContent('rae@example.com')
    expect(row).toHaveTextContent('Platform review completed')
    expect(row).toHaveTextContent('Unread')
    // The notification's own words are metadata; the idea it points at is gated
    // like everywhere else.
    expect(within(row).getByRole('link', { name: 'Automate the invoice run' })).toHaveAttribute(
      'href',
      '/app/admin/ideas/1',
    )
  })

  it('withholds the idea title from an administrator who may not read it', async () => {
    notificationsMock.mockResolvedValue(pageOfItems([notification({ ideaTitle: null })]))
    renderAdminPage(<AdminNotificationsPage />, { capabilities: READ_ONLY_ADMIN })

    expect(await screen.findByText('An idea you may not read')).toBeInTheDocument()
  })

  it('sends the kind filter and the unread toggle to the server', async () => {
    renderAdminPage(<AdminNotificationsPage />)
    await screen.findByRole('table', { name: 'Notifications' })

    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'review.assigned' } })
    await waitFor(() =>
      expect(notificationsMock).toHaveBeenLastCalledWith(
        { kind: 'review.assigned' },
        { offset: 0 },
      ),
    )

    fireEvent.click(screen.getByLabelText('Unread only'))
    await waitFor(() =>
      expect(notificationsMock).toHaveBeenLastCalledWith(
        { kind: 'review.assigned', unreadOnly: true },
        { offset: 0 },
      ),
    )
  })
})
