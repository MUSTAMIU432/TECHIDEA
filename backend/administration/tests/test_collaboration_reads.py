"""
The collaboration set, as the console sees it: teams, invitations, private
messages and notifications.

Separate from `test_reads.py` because these four surfaces exist for one reason -
they are the records the member-facing API cannot show anybody but their own
participants - and they carry one rule the rest of the console does not: **a
message body is content**. Everything else here is metadata about the platform
and is visible under `ACCESS_CONSOLE` alone.

Every fixture row is written through the owning domain's own service, so a team
here is a team a real user could have created and an invitation is one somebody
could have accepted. Nothing writes a membership row directly.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from administration.tests.conftest import make_user
from graphql_api.schema import schema
from invitations import services as invitation_services
from messaging import services as message_services
from notifications import services as notification_services
from teams import services as team_services
from teams.models import TeamMembership

pytestmark = pytest.mark.django_db

TEAMS = """
query($search: String) {
  adminTeams(search: $search) {
    items {
      id name slug description memberCount inactiveMemberCount ideaCount invitationCount
      owner { email }
    }
    pageInfo { totalCount }
  }
}
"""

TEAM = """
query($id: ID!) {
  adminTeam(id: $id) {
    id name memberCount inactiveMemberCount
    roles { name slug isSystem permissions holderCount }
    ideasByStatus { status count }
  }
  adminTeamInvitations(teamId: $id) {
    items { id scope status isOpen email roleSlug tenantName }
    pageInfo { totalCount }
  }
}
"""

INVITATIONS = """
query($scope: String, $status: String, $teamId: ID) {
  adminInvitations(scope: $scope, status: $status, teamId: $teamId) {
    items {
      id scope status isOpen email roleSlug tenantId tenantName
      invitedBy { email } acceptedBy { email } createdAt expiresAt acceptedAt
    }
    pageInfo { totalCount }
  }
}
"""

THREADS = """
query($search: String, $anchored: Boolean) {
  adminMessageThreads(search: $search, anchored: $anchored) {
    items {
      id subject ideaId ideaTitle startedBy { email }
      participantCount messageCount staleParticipantCount contentRestricted
    }
    pageInfo { totalCount }
  }
}
"""

THREAD = """
query($id: ID!) {
  adminMessageThread(id: $id) {
    id subject contentRestricted participants { email name }
  }
  adminThreadMessages(threadId: $id) {
    items { id body sender { email } createdAt }
    pageInfo { totalCount }
    contentRestricted
  }
}
"""

NOTIFICATIONS = """
query($kind: String, $unreadOnly: Boolean) {
  adminNotifications(kind: $kind, unreadOnly: $unreadOnly) {
    items {
      id kind title body isRead ideaId ideaTitle reportId recipient { email } createdAt
    }
    pageInfo { totalCount }
  }
}
"""


@pytest.fixture
def collaboration(world, django_capture_on_commit_callbacks):
    """
    A team with two members and one who left, a pending invitation, a private
    conversation anchored to a PRIVATE idea, and a notification about it.

    The private idea matters: it is how the redaction tests tell "the console
    cannot show this" apart from "there was nothing to show".
    """
    author = world['author']
    member = world['member']

    team = team_services.create_team(
        author, team_services.TeamInput(name='Registrar', description='Who allocate rooms.')
    )
    teammate = make_user('rae@teams.example', first_name='Rae', last_name='Rivera')
    team_services.add_existing_member(author, team.pk, teammate)
    # A membership that ended. The row is kept so the team does not re-invite
    # somebody who declined, and the console is where that is visible.
    TeamMembership.objects.filter(team=team, user=teammate).update(
        status=TeamMembership.Status.INACTIVE
    )

    invitation_services.invite_to_organization(
        world['acme_owner'], world['acme'], 'newcomer@acme.example'
    )
    invitation_services.invite_to_team(author, team, 'newcomer@teams.example')

    thread = message_services.start_thread(
        author,
        message_services.StartThreadInput(
            recipient_ids=(member.pk,),
            body='Can you send the monthly volume?',
            idea_id=world['private_idea'].pk,
        ),
    )
    message_services.post_message(
        member,
        message_services.PostMessageInput(thread_id=thread.pk, body='Here it is.'),
    )

    # `deliver` writes on commit, so inside the test transaction the row only
    # exists once the callbacks are executed - the same arrangement
    # `messaging/tests` uses. Nothing here is asserting about email.
    with django_capture_on_commit_callbacks(execute=True):
        notification_services.deliver(
            recipients=member,
            kind='idea.platform_approved',
            title='Platform review completed',
            body='Your idea is ready. Read the report and decide.',
            idea=world['private_idea'],
            send_email=False,
        )

    return {
        **world,
        'team': team,
        'teammate': teammate,
        'thread': thread,
    }


class TestTeams:
    def test_the_administrator_lists_every_team_with_its_counts(self, gql, collaboration):
        data = gql(TEAMS, user=collaboration['admin'])['adminTeams']

        assert data['pageInfo']['totalCount'] == 1
        row = data['items'][0]
        assert row['name'] == 'Registrar'
        assert row['slug'] == 'registrar'
        assert row['memberCount'] == 1
        # The person who left is still a row, and the console is where that is
        # visible; a team that re-invited them would be the bug this prevents.
        assert row['inactiveMemberCount'] == 1
        assert row['ideaCount'] == 0
        assert row['invitationCount'] == 1
        assert row['owner']['email'] == 'author@acme.example'

    def test_search_matches_the_name_the_slug_and_the_owner(self, gql, collaboration):
        assert (
            gql(TEAMS, {'search': 'regist'}, user=collaboration['admin'])['adminTeams']['pageInfo'][
                'totalCount'
            ]
            == 1
        )
        assert (
            gql(TEAMS, {'search': 'author@'}, user=collaboration['admin'])['adminTeams'][
                'pageInfo'
            ]['totalCount']
            == 1
        )
        assert (
            gql(TEAMS, {'search': 'nothing'}, user=collaboration['admin'])['adminTeams'][
                'pageInfo'
            ]['totalCount']
            == 0
        )

    def test_the_detail_carries_roles_and_the_full_membership(self, gql, collaboration):
        team = collaboration['team']
        data = gql(TEAM, {'id': team.pk}, user=collaboration['admin'])

        detail = data['adminTeam']
        slugs = {role['slug'] for role in detail['roles']}
        assert slugs == {'owner', 'member', 'reviewer'}
        owner_role = next(role for role in detail['roles'] if role['slug'] == 'owner')
        # A team role holds the platform's permission codes and nothing else:
        # there is no review or approval code for it to hold.
        assert 'team.members.manage' in owner_role['permissions']
        assert not any(
            code.startswith(('idea.review', 'administration.'))
            for code in owner_role['permissions']
        )
        assert owner_role['holderCount'] == 1

        invitations = data['adminTeamInvitations']
        assert invitations['pageInfo']['totalCount'] == 1
        assert invitations['items'][0]['email'] == 'newcomer@teams.example'
        assert invitations['items'][0]['scope'] == 'team'

    def test_an_unknown_or_malformed_team_id_is_null(self, gql, collaboration):
        for value in ('999999', 'not-an-id'):
            data = gql(TEAM, {'id': value}, user=collaboration['admin'])
            assert data['adminTeam'] is None
            assert data['adminTeamInvitations']['pageInfo']['totalCount'] == 0


class TestInvitations:
    def test_the_administrator_lists_every_invitation_with_its_tenant(self, gql, collaboration):
        data = gql(INVITATIONS, user=collaboration['admin'])['adminInvitations']

        assert data['pageInfo']['totalCount'] == 2
        scopes = {row['scope'] for row in data['items']}
        assert scopes == {'organization', 'team'}
        organization_row = next(row for row in data['items'] if row['scope'] == 'organization')
        assert organization_row['tenantName'] == 'Acme'
        assert organization_row['tenantId'] == str(collaboration['acme'].pk)
        assert organization_row['isOpen'] is True
        assert organization_row['status'] == 'pending'
        # Nothing has been accepted, so there is no accepter to name.
        assert organization_row['acceptedBy'] is None

    def test_filters_narrow_by_scope_and_status(self, gql, collaboration):
        assert (
            gql(INVITATIONS, {'scope': 'team'}, user=collaboration['admin'])['adminInvitations'][
                'pageInfo'
            ]['totalCount']
            == 1
        )
        assert (
            gql(INVITATIONS, {'status': 'accepted'}, user=collaboration['admin'])[
                'adminInvitations'
            ]['pageInfo']['totalCount']
            == 0
        )
        assert (
            gql(
                INVITATIONS,
                {'teamId': collaboration['team'].pk},
                user=collaboration['admin'],
            )['adminInvitations']['pageInfo']['totalCount']
            == 1
        )

    def test_no_invitation_token_is_ever_exposed(self, gql, collaboration):
        """
        The plaintext token exists only in the email; the row stores a digest.
        So this is not a redaction rule that could be forgotten - there is
        nothing to redact.
        """
        sdl = str(schema)
        assert 'tokenDigest' not in sdl
        assert 'token_digest' not in sdl
        invitation_type = sdl.split('type AdminInvitationType')[1].split('}')[0]
        assert 'oken' not in invitation_type

        data = gql(INVITATIONS, user=collaboration['admin'])
        assert 'token' not in str(data).lower()


class TestMessageThreads:
    def test_metadata_is_visible_to_any_administrator(self, gql, collaboration):
        data = gql(THREADS, user=collaboration['limited_admin'])['adminMessageThreads']

        assert data['pageInfo']['totalCount'] == 1
        row = data['items'][0]
        assert row['messageCount'] == 2
        assert row['participantCount'] == 2
        assert row['startedBy']['email'] == 'author@acme.example'
        # The idea it is anchored to is PRIVATE, so even the metadata view does
        # not confirm its title - and `start_thread` defaults the thread's
        # subject to that title, so the subject is withheld with it rather than
        # being the way round.
        assert row['ideaTitle'] is None
        assert row['subject'] is None
        assert row['contentRestricted'] is True

    def test_message_bodies_are_withheld_without_the_content_permission(self, gql, collaboration):
        data = gql(THREAD, {'id': collaboration['thread'].pk}, user=collaboration['limited_admin'])

        thread = data['adminMessageThread']
        assert [person['email'] for person in thread['participants']] == [
            'author@acme.example',
            'member@acme.example',
        ]
        messages = data['adminThreadMessages']
        assert messages['contentRestricted'] is True
        assert messages['pageInfo']['totalCount'] == 2
        # Every body is null - never an empty string, which would read as an
        # empty message rather than a withheld one.
        assert [item['body'] for item in messages['items']] == [None, None]
        # Who said it and when is still platform metadata.
        assert messages['items'][0]['sender']['email'] == 'author@acme.example'

    def test_bodies_are_returned_to_an_administrator_who_may_inspect_content(
        self, gql, collaboration
    ):
        data = gql(THREAD, {'id': collaboration['thread'].pk}, user=collaboration['admin'])

        assert data['adminMessageThread']['contentRestricted'] is False
        messages = data['adminThreadMessages']
        assert messages['contentRestricted'] is False
        assert [item['body'] for item in messages['items']] == [
            'Can you send the monthly volume?',
            'Here it is.',
        ]

    def test_an_unread_participant_is_counted_without_pretending_to_be_an_unread_count(
        self, gql, collaboration
    ):
        """
        Unread is per reader. The console has no reader, so it reports how many
        participants have not opened the thread - which is a fact - rather than
        an unread total, which would be a fiction.
        """
        row = gql(THREADS, user=collaboration['admin'])['adminMessageThreads']['items'][0]
        assert row['staleParticipantCount'] == 1

    def test_the_anchored_filter_narrows_to_ideas_and_plain_conversations(self, gql, collaboration):
        assert (
            gql(THREADS, {'anchored': True}, user=collaboration['admin'])['adminMessageThreads'][
                'pageInfo'
            ]['totalCount']
            == 1
        )
        assert (
            gql(THREADS, {'anchored': False}, user=collaboration['admin'])['adminMessageThreads'][
                'pageInfo'
            ]['totalCount']
            == 0
        )

    def test_search_cannot_confirm_a_restricted_idea_title(self, gql, collaboration):
        # The title is "Secret salary spreadsheet" on the PRIVATE idea, which
        # the thread is anchored to.
        assert (
            gql(THREADS, {'search': 'salary'}, user=collaboration['limited_admin'])[
                'adminMessageThreads'
            ]['pageInfo']['totalCount']
            == 0
        )
        # The starter's address is metadata, so it is searchable either way.
        assert (
            gql(THREADS, {'search': 'author@'}, user=collaboration['limited_admin'])[
                'adminMessageThreads'
            ]['pageInfo']['totalCount']
            == 1
        )

    def test_messages_of_an_unknown_thread_are_an_empty_page(self, gql, collaboration):
        data = gql(THREAD, {'id': '999999'}, user=collaboration['admin'])
        assert data['adminMessageThread'] is None
        assert data['adminThreadMessages']['pageInfo']['totalCount'] == 0


class TestNotifications:
    def test_the_administrator_lists_what_the_platform_told_somebody(self, gql, collaboration):
        row = gql(NOTIFICATIONS, user=collaboration['admin'])['adminNotifications']['items'][0]

        assert row['kind'] == 'idea.platform_approved'
        assert row['title'] == 'Platform review completed'
        # A notification is the platform's own words, so its body is metadata -
        # it is bounded to 500 characters precisely because it never carries
        # report contents.
        assert row['body'] == 'Your idea is ready. Read the report and decide.'
        assert row['recipient']['email'] == 'member@acme.example'
        assert row['isRead'] is False
        # This administrator holds the content permission, so the title of the
        # PRIVATE idea it points at is visible - the same rule as every other
        # idea reference in the console.
        assert row['ideaTitle'] == 'Secret salary spreadsheet'

    def test_the_idea_a_notification_points_at_is_withheld_without_the_permission(
        self, gql, collaboration
    ):
        row = gql(NOTIFICATIONS, user=collaboration['limited_admin'])['adminNotifications'][
            'items'
        ][0]
        assert row['kind'] == 'idea.platform_approved'
        assert row['ideaId'] == str(collaboration['private_idea'].pk)
        assert row['ideaTitle'] is None

    def test_filters_narrow_by_kind_and_unread(self, gql, collaboration):
        assert (
            gql(NOTIFICATIONS, {'kind': 'idea.platform_approved'}, user=collaboration['admin'])[
                'adminNotifications'
            ]['pageInfo']['totalCount']
            == 1
        )
        assert (
            gql(NOTIFICATIONS, {'kind': 'review.assigned'}, user=collaboration['admin'])[
                'adminNotifications'
            ]['pageInfo']['totalCount']
            == 0
        )
        assert (
            gql(NOTIFICATIONS, {'unreadOnly': True}, user=collaboration['admin'])[
                'adminNotifications'
            ]['pageInfo']['totalCount']
            == 1
        )


class TestNobodyElseSeesAnyOfIt:
    @pytest.mark.parametrize(
        ('key', 'query'),
        [
            ('adminTeams', TEAMS),
            ('adminInvitations', INVITATIONS),
            ('adminMessageThreads', THREADS),
            ('adminNotifications', NOTIFICATIONS),
        ],
    )
    def test_an_ordinary_member_gets_nothing(self, gql, collaboration, key, query):
        """A member of an organization is not an administrator, whatever they can read."""
        page = gql(query, user=collaboration['member'])[key]
        assert page['pageInfo']['totalCount'] == 0

    def test_a_single_team_is_null_for_a_non_administrator(self, gql, collaboration):
        data = gql(TEAM, {'id': collaboration['team'].pk}, user=collaboration['member'])
        assert data['adminTeam'] is None
        assert data['adminTeamInvitations']['pageInfo']['totalCount'] == 0

    def test_a_single_thread_is_null_for_a_non_administrator(self, gql, collaboration):
        data = gql(THREAD, {'id': collaboration['thread'].pk}, user=collaboration['member'])
        assert data['adminMessageThread'] is None
        assert data['adminThreadMessages']['pageInfo']['totalCount'] == 0


class TestQueryCounts:
    """A page costs the same number of queries whatever its size."""

    @staticmethod
    def _queries(gql, query, user, variables=None):
        with CaptureQueriesContext(connection) as context:
            gql(query, variables, user=user)
        return len(context.captured_queries)

    def test_the_team_list(self, gql, collaboration):
        before = self._queries(gql, TEAMS, collaboration['admin'])
        for index in range(5):
            team_services.create_team(
                collaboration['author'],
                team_services.TeamInput(name=f'Extra {index}'),
            )
        assert self._queries(gql, TEAMS, collaboration['admin']) == before

    def test_the_invitation_list(self, gql, collaboration):
        before = self._queries(gql, INVITATIONS, collaboration['admin'])
        for index in range(5):
            invitation_services.invite_to_team(
                collaboration['author'],
                collaboration['team'],
                f'extra{index}@teams.example',
            )
        assert self._queries(gql, INVITATIONS, collaboration['admin']) == before

    def test_the_thread_list(self, gql, collaboration):
        before = self._queries(gql, THREADS, collaboration['admin'])
        for index in range(5):
            message_services.start_thread(
                collaboration['author'],
                message_services.StartThreadInput(
                    recipient_ids=(collaboration['member'].pk,),
                    body=f'Conversation {index}',
                ),
            )
        assert self._queries(gql, THREADS, collaboration['admin']) == before

    def test_the_notification_list(self, gql, collaboration):
        before = self._queries(gql, NOTIFICATIONS, collaboration['admin'])
        for index in range(5):
            notification_services.deliver(
                recipients=collaboration['member'],
                kind='review.assigned',
                title=f'Assigned {index}',
                body='An idea is waiting for you.',
                send_email=False,
            )
        assert self._queries(gql, NOTIFICATIONS, collaboration['admin']) == before

    def test_one_threads_messages(self, gql, collaboration):
        before = self._queries(
            gql, THREAD, collaboration['admin'], {'id': collaboration['thread'].pk}
        )
        for index in range(5):
            message_services.post_message(
                collaboration['author'],
                message_services.PostMessageInput(
                    thread_id=collaboration['thread'].pk, body=f'Follow-up {index}'
                ),
            )
        assert (
            self._queries(gql, THREAD, collaboration['admin'], {'id': collaboration['thread'].pk})
            == before
        )
