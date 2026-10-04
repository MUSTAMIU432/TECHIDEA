"""
Who may use the administration console, asked at the GraphQL boundary.

The console is a platform permission, not an organization one. These tests
check that every admin field answers an administrator and gives everybody
else - anonymous, a plain member, an organization Owner, a Reviewer, a
staff flag on its own - the same empty answer; and that holding the
permission changes nothing in the member-facing API (no bypass).
"""

import pytest

from administration import authorization
from administration.tests.conftest import grant, make_user
from reviews.models import Review

pytestmark = pytest.mark.django_db

CAPABILITIES = """
query { adminCapabilities {
  canAccessConsole canInspectIdeaContent canManageUserAccounts
  canManageOrganizationRoles canManageCategories
} }
"""

OVERVIEW = """
query { adminOverview {
  userCount activeUserCount organizationCount ideaCount
  ideasByStatus { status count }
  openReviewCount completedReviewCount
  recentActivity { ideaId ideaTitle fromStatus toStatus actor { email } organization { name } }
  recentAdminActions { action result targetLabel actor { email } }
} }
"""

ALL_READS = {
    'adminOverview': 'query { adminOverview { userCount } }',
    'adminUsers': 'query { adminUsers { items { id } pageInfo { totalCount } } }',
    'adminUser': 'query($id: ID!) { adminUser(id: $id) { id } }',
    'adminOrganizations': 'query { adminOrganizations { items { id } pageInfo { totalCount } } }',
    'adminOrganization': 'query($id: ID!) { adminOrganization(id: $id) { id } }',
    'adminOrganizationMembers': (
        'query($id: ID!) { adminOrganizationMembers(organizationId: $id) '
        '{ items { id } pageInfo { totalCount } } }'
    ),
    'adminIdeas': 'query { adminIdeas { items { id } pageInfo { totalCount } } }',
    'adminIdea': 'query($id: ID!) { adminIdea(id: $id) { id } }',
    'adminReviews': 'query { adminReviews { items { id } pageInfo { totalCount } } }',
    'adminReview': 'query($id: ID!) { adminReview(id: $id) { id } }',
    'adminCategories': 'query { adminCategories { id } }',
    'adminAuditEntries': 'query { adminAuditEntries { items { id } pageInfo { totalCount } } }',
}


def _read_all(gql, world, user):
    """Every admin read, as `user`. The id is a real row of the right kind each time."""
    ids = {
        'adminUser': world['author'].pk,
        'adminOrganization': world['acme'].pk,
        'adminOrganizationMembers': world['acme'].pk,
        'adminIdea': world['org_idea'].pk,
        'adminReview': world['open_review'].pk,
    }
    return {
        name: gql(query, {'id': ids[name]} if name in ids else None, user=user)[name]
        for name, query in ALL_READS.items()
    }


#: The same read `test_reads.py` uses, narrowed to what this file asserts: the
#: redaction of an idea and its rounds for somebody who can open the console but
#: cannot inspect content.
REVIEWER_IDEA_QUERY = """
query($id: ID!) {
  adminIdea(id: $id) {
    id title contentRestricted content { description }
    attachments { id }
    comments { items { content } pageInfo { totalCount } }
    reviews { scope decision feedback contentRestricted }
  }
}
"""


def _is_empty(value):
    if value is None or value == []:
        return True
    return value.get('items') == [] and value['pageInfo']['totalCount'] == 0


class TestWhoIsAnAdministrator:
    def test_the_administrator_can_read_the_dashboard(self, gql, world):
        overview = gql(OVERVIEW, user=world['admin'])['adminOverview']

        assert overview['userCount'] == 8
        assert overview['activeUserCount'] == 8
        assert overview['organizationCount'] == 2
        assert overview['ideaCount'] == 4
        by_status = {item['status']: item['count'] for item in overview['ideasByStatus']}
        assert by_status['APPROVED'] == 1
        assert by_status['CHANGES_REQUESTED'] == 1
        assert by_status['UNDER_REVIEW'] == 1
        assert by_status['DRAFT'] == 1
        assert by_status['REJECTED'] == 0
        assert overview['openReviewCount'] == 1
        # Five, across both tracks: three ideas confirmed by Acme and then
        # reviewed by the platform, so each contributed two completed rounds and
        # the one still open has contributed none. Counting only the platform's
        # would report 2 and would hide the stage the platform depends on.
        assert overview['completedReviewCount'] == 5
        assert overview['recentActivity'][0]['toStatus'] == 'UNDER_REVIEW'
        assert overview['recentAdminActions'][0]['action'] == 'PLATFORM_ADMIN_GRANTED'

    def test_a_superuser_is_an_administrator(self, gql, world):
        capabilities = gql(CAPABILITIES, user=world['superuser'])['adminCapabilities']
        assert all(capabilities.values())

    def test_the_group_grants_every_capability(self, gql, world):
        capabilities = gql(CAPABILITIES, user=world['admin'])['adminCapabilities']
        assert all(capabilities.values())

    def test_a_limited_administrator_has_only_console_access(self, gql, world):
        capabilities = gql(CAPABILITIES, user=world['limited_admin'])['adminCapabilities']
        assert capabilities == {
            'canAccessConsole': True,
            'canInspectIdeaContent': False,
            'canManageUserAccounts': False,
            'canManageOrganizationRoles': False,
            'canManageCategories': False,
        }

    @pytest.mark.parametrize('who', ['member', 'author', 'acme_owner', 'globex_owner'])
    def test_no_organization_role_makes_anyone_an_administrator(self, gql, world, who):
        user = world[who]
        capabilities = gql(CAPABILITIES, user=user)['adminCapabilities']
        assert not any(capabilities.values())
        for name, value in _read_all(gql, world, user).items():
            assert _is_empty(value), name

    def test_a_platform_reviewer_is_not_an_administrator(self, gql, world):
        """
        The one member of this world who *can* open the console, and holds none of
        its capabilities.

        Separated out from the parametrized case above because they are the
        interesting one: an organization role never grants console access, so for
        everybody else that test would be checking something trivially true.

        What the console gate actually buys is metadata - counts, listings, and
        the *shape* of an idea or a review - and what the capabilities buy is the
        content inside them. So this asserts the three things that are actually
        guaranteed:

        - no capability is granted by holding the gate alone;
        - content is redacted everywhere it appears, including in the activity
          feed, so a reviewer who cannot inspect content cannot learn an idea's
          title by looking at the list;
        - and nothing is writable, which is asserted in `test_mutations.py`
          (`test_every_mutation_is_refused_without_its_permission` lists this
          reviewer, because every console write *is* one of the capabilities).
        """
        reviewer = world['reviewer']
        capabilities = gql(CAPABILITIES, user=reviewer)['adminCapabilities']
        assert capabilities['canAccessConsole'] is True
        assert not any(value for name, value in capabilities.items() if name != 'canAccessConsole')

        idea = gql(REVIEWER_IDEA_QUERY, {'id': world['org_idea'].pk}, user=reviewer)['adminIdea']
        assert idea['contentRestricted'] is True
        assert idea['title'] is None
        assert idea['content'] is None
        assert idea['comments'] == {'items': [], 'pageInfo': {'totalCount': 0}}
        assert idea['attachments'] == []
        assert all(item['feedback'] is None for item in idea['reviews'])

        review = world['public_idea'].reviews.get(scope=Review.Scope.PLATFORM)
        detail = gql(
            'query($id: ID!) { adminReview(id: $id) { feedback submissionSnapshot } }',
            {'id': review.pk},
            user=reviewer,
        )['adminReview']
        assert detail['feedback'] is None
        assert detail['submissionSnapshot'] is None

        # And in the activity feed, where a title is withheld for anything that
        # was not PUBLIC - a public idea's title is shown to any console user,
        # because a signed-in member could read it anyway and redaction would be
        # theatre. Asserted against the specific ideas rather than "all null",
        # because that is the rule.
        overview = gql(OVERVIEW, user=reviewer)['adminOverview']
        titles = {item['ideaId']: item['ideaTitle'] for item in overview['recentActivity']}
        assert titles[str(world['org_idea'].pk)] is None
        assert titles[str(world['public_idea'].pk)] == 'Public invoice run'

    def test_anonymous_requests_get_nothing(self, gql, world):
        assert not any(gql(CAPABILITIES)['adminCapabilities'].values())
        for name, value in _read_all(gql, world, None).items():
            assert _is_empty(value), name

    def test_the_staff_flag_alone_grants_nothing(self, gql, world):
        staff = make_user('staff@platform.example', is_staff=True)
        assert not any(gql(CAPABILITIES, user=staff)['adminCapabilities'].values())
        assert gql(OVERVIEW, user=staff)['adminOverview'] is None

    def test_an_extra_permission_without_console_access_is_not_honoured(self, gql, world):
        user = grant(make_user('half@platform.example'), 'inspect_idea_content')
        assert not any(gql(CAPABILITIES, user=user)['adminCapabilities'].values())
        assert (
            gql(ALL_READS['adminIdea'], {'id': world['private_idea'].pk}, user=user)['adminIdea']
            is None
        )

    def test_a_deactivated_administrator_loses_the_console(self, gql, world):
        admin = world['admin']
        admin.is_active = False
        admin.save()
        assert not authorization.is_platform_admin(admin)
        # The token itself is refused: an inactive user is not authenticated at all.
        assert gql(OVERVIEW, user=admin)['adminOverview'] is None


class TestNoBypass:
    """Platform administration opens the console and nothing else."""

    def test_the_member_facing_api_still_hides_other_tenants_ideas(self, gql, world):
        admin = world['admin']
        query = 'query($id: ID!) { idea(id: $id) { id } }'

        assert gql(query, {'id': world['org_idea'].pk}, user=admin)['idea'] is None
        assert gql(query, {'id': world['private_idea'].pk}, user=admin)['idea'] is None

    def test_an_administrator_cannot_review_without_being_a_reviewer(self, gql, world):
        query = """
        mutation($id: ID!) { startReview(ideaId: $id) { success message } }
        """
        idea = world['open_idea']
        payload = gql(query, {'id': idea.pk}, user=world['superuser'])['startReview']

        assert payload['success'] is False

    def test_the_member_facing_review_history_stays_closed(self, gql, world):
        query = 'query($id: ID!) { ideaReviews(ideaId: $id) { id } }'
        assert gql(query, {'id': world['public_idea'].pk}, user=world['admin'])['ideaReviews'] == []

    def test_an_administrator_has_no_organization_membership_rights(self, gql, world):
        query = 'query($id: ID!) { organizationMembers(organizationId: $id) { id } }'
        assert (
            gql(query, {'id': world['acme'].pk}, user=world['admin'])['organizationMembers'] == []
        )
