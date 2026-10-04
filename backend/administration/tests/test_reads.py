"""
What an administrator can see, and what stays redacted.

Users, organizations, ideas, reviews and approvals through the admin GraphQL
fields, with the content gate checked on every path content can travel:
titles in lists, the idea detail, comments, evidence, review feedback and
snapshots, the activity feed and search. Then the privacy rules that hold
whatever the permission (no credential ever leaves), and the query counts
that keep list pages from degrading into N+1.
"""

import json
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from administration.tests.conftest import PRIVATE_TITLE, make_user, submitted_idea
from graphql_api.schema import schema
from ideas.models import Idea
from organizations.services import CreateOrganizationInput, create_organization_for_user
from reviews import services as review_services
from reviews.models import Review

pytestmark = pytest.mark.django_db

USERS = """
query($filters: AdminUserFiltersInput, $offset: Int, $limit: Int) {
  adminUsers(filters: $filters, offset: $offset, limit: $limit) {
    items {
      id email firstName lastName isActive isVerified isPlatformAdmin isSuperuser
      createdAt lastActiveAt
      memberships { id status organization { id name } roles { id name slug isSystem } }
    }
    pageInfo { offset limit totalCount hasNextPage hasPreviousPage }
  }
}
"""

USER = """
query($id: ID!) {
  adminUser(id: $id) {
    id email phoneNumber signInMethods ideaCount reviewCount commentCount isPlatformAdmin
    memberships { organization { name } roles { slug } }
    auditEntries { action result actor { email } targetLabel metadata }
  }
}
"""

ORGANIZATIONS = """
query($search: String) {
  adminOrganizations(search: $search) {
    items { id name slug memberCount ideaCount ownerCount reviewerCount }
    pageInfo { totalCount }
  }
}
"""

ORGANIZATION = """
query($id: ID!) {
  adminOrganization(id: $id) {
    id name memberCount ownerCount reviewerCount ideaCount
    roles { id name slug isSystem permissions holderCount }
    ideasByStatus { status count }
  }
  adminOrganizationMembers(organizationId: $id) {
    items { id status user { email name } userIsActive roles { slug } }
    pageInfo { totalCount }
  }
}
"""

IDEAS = """
query($filters: AdminIdeaFiltersInput) {
  adminIdeas(filters: $filters) {
    items {
      id title contentRestricted status visibility categoryName
      organization { name } author { email } voteCount commentCount attachmentCount
      createdAt updatedAt submittedAt
    }
    pageInfo { totalCount }
  }
}
"""

IDEA = """
query($id: ID!) {
  adminIdea(id: $id) {
    id title contentRestricted canInspectContent status visibility
    content { description currentProcess frequency impacts currentTools }
    attachments { id filename contentType size downloadPath uploadedBy { email } }
    comments { items { content author { email } } pageInfo { totalCount } }
    reviews { round decision feedback contentRestricted reviewer { email }
              assessments { criterion rating note } }
    transitions { fromStatus toStatus actor { email } }
  }
}
"""

REVIEWS = """
query($filters: AdminReviewFiltersInput) {
  adminReviews(filters: $filters) {
    items {
      id round decision isCompleted createdAt completedAt contentRestricted feedback
      idea { id title status organization { name } }
      reviewer { email }
      assessments { criterion rating }
    }
    pageInfo { totalCount }
  }
}
"""

REVIEW = """
query($id: ID!) {
  adminReview(id: $id) {
    id scope round decision isCompleted isStalled feedback submissionSnapshot
    history { scope round decision isStalled }
  }
}
"""


def _ids(page):
    return {item['id'] for item in page['items']}


def _platform_round(rounds):
    """
    The platform review out of a list of rounds.

    An idea that reached the platform has at least two: its organization's
    confirmation and the platform round. Only the platform one carries the
    platform's feedback and assessments, so a test about those has to say which
    it means rather than take the first row.
    """
    platform_decisions = ('APPROVED', 'CHANGES_REQUESTED', 'REJECTED')
    matches = [item for item in rounds if item['decision'] in platform_decisions]
    assert len(matches) == 1, rounds
    return matches[0]


def reviewer_can_only_open_the_console(gql, reviewer):
    """A platform reviewer has the console gate and none of the capabilities."""
    capabilities = gql(
        'query { adminCapabilities { canAccessConsole canInspectIdeaContent '
        'canManageUserAccounts canManageOrganizationRoles canManageCategories } }',
        user=reviewer,
    )['adminCapabilities']
    assert capabilities == {
        'canAccessConsole': True,
        'canInspectIdeaContent': False,
        'canManageUserAccounts': False,
        'canManageOrganizationRoles': False,
        'canManageCategories': False,
    }
    return capabilities


class TestUsers:
    def test_the_administrator_lists_every_account_with_memberships_and_roles(self, gql, world):
        page = gql(USERS, user=world['admin'])['adminUsers']

        assert page['pageInfo']['totalCount'] == 8
        by_email = {item['email']: item for item in page['items']}
        owner = by_email['owner@acme.example']
        assert owner['memberships'][0]['organization']['name'] == 'Acme'
        assert [role['slug'] for role in owner['memberships'][0]['roles']] == ['owner']
        assert by_email['admin@platform.example']['isPlatformAdmin'] is True
        assert by_email['support@platform.example']['isPlatformAdmin'] is True
        assert by_email['root@platform.example']['isPlatformAdmin'] is True
        assert by_email['root@platform.example']['isSuperuser'] is True
        assert by_email['owner@acme.example']['isPlatformAdmin'] is False

    def test_search_and_filters_are_applied_by_the_server(self, gql, world):
        def emails(filters):
            page = gql(USERS, {'filters': filters}, user=world['admin'])['adminUsers']
            return {item['email'] for item in page['items']}

        assert emails({'search': 'REVIEWER@acme'}) == {'reviewer@acme.example'}
        assert emails({'search': 'Rae'}) == {'reviewer@acme.example'}
        assert emails({'organizationId': world['globex'].pk}) == {'owner@globex.example'}
        # This filter is "who can open the console", and a **platform reviewer**
        # can: the review queue is a console surface, so the permission that grants
        # it comes with `access_console`. Being able to open the console is not
        # being an administrator, and the distinction is asserted below rather
        # than left to the reader - an administrator holds the capabilities, a
        # reviewer holds the gate.
        assert emails({'platformAdminsOnly': True}) == {
            'admin@platform.example',
            'support@platform.example',
            'root@platform.example',
            'reviewer@acme.example',
        }
        assert reviewer_can_only_open_the_console(gql, world['reviewer'])
        world['member'].is_active = False
        world['member'].save()
        assert emails({'isActive': False}) == {'member@acme.example'}

    def test_the_list_is_paged_by_the_server(self, gql, world):
        page = gql(USERS, {'offset': 2, 'limit': 3}, user=world['admin'])['adminUsers']

        assert len(page['items']) == 3
        assert page['pageInfo'] == {
            'offset': 2,
            'limit': 3,
            'totalCount': 8,
            'hasNextPage': True,
            'hasPreviousPage': True,
        }

    def test_the_administrator_inspects_one_account(self, gql, world):
        detail = gql(USER, {'id': world['author'].pk}, user=world['admin'])['adminUser']

        assert detail['email'] == 'author@acme.example'
        assert detail['signInMethods'] == ['password']
        assert detail['ideaCount'] == 4
        assert detail['reviewCount'] == 0
        assert detail['commentCount'] == 0

        # Six, not three: this reviewer confirmed three of Acme's ideas as its
        # organization's Reviewer and then reviewed all three at the platform.
        # Both tracks are real rounds with a real reviewer, and the console counts
        # rows rather than people.
        reviewer = gql(USER, {'id': world['reviewer'].pk}, user=world['admin'])['adminUser']
        assert reviewer['reviewCount'] == 6

    def test_an_unknown_or_malformed_id_is_null(self, gql, world):
        assert gql(USER, {'id': 999999}, user=world['admin'])['adminUser'] is None
        assert gql(USER, {'id': 'nope'}, user=world['admin'])['adminUser'] is None


class TestOrganizations:
    def test_the_administrator_lists_every_organization_with_counts(self, gql, world):
        page = gql(ORGANIZATIONS, user=world['admin'])['adminOrganizations']

        by_name = {item['name']: item for item in page['items']}
        assert set(by_name) == {'Acme', 'Globex'}
        assert by_name['Acme']['memberCount'] == 4
        assert by_name['Acme']['ideaCount'] == 4
        assert by_name['Acme']['ownerCount'] == 1
        assert by_name['Acme']['reviewerCount'] == 1
        assert by_name['Globex']['ideaCount'] == 0

    def test_search(self, gql, world):
        page = gql(ORGANIZATIONS, {'search': 'glob'}, user=world['admin'])['adminOrganizations']
        assert [item['name'] for item in page['items']] == ['Globex']

    def test_the_administrator_inspects_members_owners_and_reviewers(self, gql, world):
        data = gql(ORGANIZATION, {'id': world['acme'].pk}, user=world['admin'])

        roles = {role['slug']: role for role in data['adminOrganization']['roles']}
        assert roles['owner']['holderCount'] == 1
        assert roles['owner']['isSystem'] is True
        assert 'idea.review' in roles['reviewer']['permissions']
        members = {
            item['user']['email']: [role['slug'] for role in item['roles']]
            for item in data['adminOrganizationMembers']['items']
        }
        assert members == {
            'author@acme.example': [],
            'member@acme.example': [],
            'owner@acme.example': ['owner'],
            'reviewer@acme.example': ['reviewer'],
        }
        by_status = {
            item['status']: item['count'] for item in data['adminOrganization']['ideasByStatus']
        }
        assert by_status['APPROVED'] == 1

    def test_inactive_memberships_are_listed_too(self, gql, world):
        membership = world['member'].memberships.get()
        membership.status = 'inactive'
        membership.save()
        data = gql(ORGANIZATION, {'id': world['acme'].pk}, user=world['admin'])
        statuses = {
            item['user']['email']: item['status']
            for item in data['adminOrganizationMembers']['items']
        }
        assert statuses['member@acme.example'] == 'inactive'
        assert data['adminOrganization']['memberCount'] == 3


class TestIdeas:
    def test_the_administrator_sees_every_idea_in_every_organization(self, gql, world):
        page = gql(IDEAS, user=world['admin'])['adminIdeas']

        assert page['pageInfo']['totalCount'] == 4
        titles = {item['title'] for item in page['items']}
        assert PRIVATE_TITLE in titles
        org_idea = next(item for item in page['items'] if item['title'] == 'Organization payroll')
        assert org_idea['attachmentCount'] == 1
        assert org_idea['commentCount'] == 1
        assert org_idea['categoryName'] == 'Finance'
        assert org_idea['author']['email'] == 'author@acme.example'

    @pytest.mark.parametrize(
        ('filters', 'expected'),
        [
            ({'status': 'APPROVED'}, {'Public invoice run'}),
            ({'visibility': 'PRIVATE'}, {PRIVATE_TITLE}),
            ({'search': 'payroll'}, {'Organization payroll'}),
            ({'search': 'author@acme'}, None),  # every idea: author email matches
        ],
    )
    def test_filters(self, gql, world, filters, expected):
        page = gql(IDEAS, {'filters': filters}, user=world['admin'])['adminIdeas']
        titles = {item['title'] for item in page['items']}
        assert titles == (expected if expected is not None else titles)
        if expected is None:
            assert page['pageInfo']['totalCount'] == 4

    def test_filters_by_organization_category_author_and_date(self, gql, world):
        def count(filters):
            return gql(IDEAS, {'filters': filters}, user=world['admin'])['adminIdeas']['pageInfo'][
                'totalCount'
            ]

        assert count({'organizationId': world['globex'].pk}) == 0
        assert count({'organizationId': world['acme'].pk}) == 4
        assert count({'categoryId': world['finance'].pk}) == 3
        assert count({'authorId': world['member'].pk}) == 0
        today = world['org_idea'].created_at.date().isoformat()
        assert count({'createdFrom': today, 'createdTo': today}) == 4
        assert count({'createdFrom': '2999-01-01'}) == 0

    def test_the_administrator_inspects_the_whole_idea(self, gql, world):
        detail = gql(IDEA, {'id': world['org_idea'].pk}, user=world['admin'])['adminIdea']

        assert detail['contentRestricted'] is False
        assert detail['content']['currentProcess'] == 'We copy numbers by hand.'
        assert detail['attachments'][0]['filename'] == 'evidence.pdf'
        assert detail['attachments'][0]['downloadPath'].startswith('/administration/attachments/')
        assert detail['comments']['items'][0]['content'] == 'We have this problem too.'
        # Both tracks, newest first: the platform's changes request and the
        # organization's confirmation are separate rounds with separate
        # reviewers' words, so the round carrying the feedback is picked by
        # decision rather than by position.
        assert len(detail['reviews']) == 2
        platform_round = _platform_round(detail['reviews'])
        assert platform_round['decision'] == 'CHANGES_REQUESTED'
        assert platform_round['feedback'] == 'Add the monthly volumes.'
        assert len(platform_round['assessments']) == 5
        # Five moves: to the organization, confirmed by it, on to the platform,
        # claimed by a reviewer, and sent back. The organization stage is not a
        # detail of the platform one - it is three of the five transitions.
        assert [(t['fromStatus'], t['toStatus']) for t in detail['transitions']] == [
            ('DRAFT', 'SUBMITTED_TO_ORGANIZATION'),
            ('SUBMITTED_TO_ORGANIZATION', 'ORGANIZATION_CONFIRMED'),
            ('ORGANIZATION_CONFIRMED', 'SUBMITTED'),
            ('SUBMITTED', 'UNDER_REVIEW'),
            ('UNDER_REVIEW', 'CHANGES_REQUESTED'),
        ]

    def test_a_private_draft_is_readable_with_the_inspection_permission(self, gql, world):
        detail = gql(IDEA, {'id': world['private_idea'].pk}, user=world['admin'])['adminIdea']
        assert detail['title'] == PRIVATE_TITLE
        assert detail['content']['description'] == 'Very private words.'


class TestPrivateContentIsRedacted:
    """An administrator with ACCESS_CONSOLE alone sees metadata, never non-public content."""

    def test_titles_of_non_public_ideas_are_withheld_in_the_list(self, gql, world):
        page = gql(IDEAS, user=world['limited_admin'])['adminIdeas']

        by_visibility = {item['visibility']: item for item in page['items']}
        assert page['pageInfo']['totalCount'] == 4
        assert by_visibility['PUBLIC']['title'] == 'Public invoice run'
        assert by_visibility['PUBLIC']['contentRestricted'] is False
        assert by_visibility['PRIVATE']['title'] is None
        assert by_visibility['PRIVATE']['contentRestricted'] is True
        assert by_visibility['ORGANIZATION']['title'] is None

    def test_search_cannot_confirm_a_restricted_title(self, gql, world):
        page = gql(IDEAS, {'filters': {'search': 'salary'}}, user=world['limited_admin'])
        assert page['adminIdeas']['pageInfo']['totalCount'] == 0

        reviews = gql(REVIEWS, {'filters': {'search': 'payroll'}}, user=world['limited_admin'])
        assert reviews['adminReviews']['pageInfo']['totalCount'] == 0

    def test_the_detail_carries_metadata_but_no_content(self, gql, world):
        detail = gql(IDEA, {'id': world['org_idea'].pk}, user=world['limited_admin'])['adminIdea']

        assert detail['title'] is None
        assert detail['contentRestricted'] is True
        assert detail['canInspectContent'] is False
        assert detail['content'] is None
        assert detail['attachments'] == []
        assert detail['comments'] == {'items': [], 'pageInfo': {'totalCount': 0}}
        restricted = _platform_round(detail['reviews'])
        assert restricted['decision'] == 'CHANGES_REQUESTED'
        assert restricted['feedback'] is None
        assert restricted['assessments'] is None
        assert restricted['contentRestricted'] is True
        assert len(detail['transitions']) == 5

    def test_a_public_idea_is_readable_but_its_evidence_and_feedback_are_not(self, gql, world):
        detail = gql(IDEA, {'id': world['public_idea'].pk}, user=world['limited_admin'])[
            'adminIdea'
        ]
        assert detail['title'] == 'Public invoice run'
        assert detail['content']['description']
        assert detail['attachments'] == []
        assert _platform_round(detail['reviews'])['feedback'] is None

    def test_the_activity_feed_withholds_restricted_titles(self, gql, world):
        overview = gql(
            'query { adminOverview { recentActivity { ideaId ideaTitle } } }',
            user=world['limited_admin'],
        )['adminOverview']
        titles = {item['ideaId']: item['ideaTitle'] for item in overview['recentActivity']}
        assert titles[str(world['open_idea'].pk)] is None
        assert titles[str(world['public_idea'].pk)] == 'Public invoice run'

    def test_review_snapshots_are_withheld(self, gql, world):
        # `.get()` would be ambiguous now: an idea this old has both an
        # organization confirmation and a platform round, and the snapshot being
        # withheld is the platform one - the submission the platform actually
        # received, which is what an inspector without the content permission may
        # not read.
        review = world['public_idea'].reviews.get(scope=Review.Scope.PLATFORM)
        detail = gql(REVIEW, {'id': review.pk}, user=world['limited_admin'])['adminReview']
        assert detail['submissionSnapshot'] is None
        assert detail['feedback'] is None


class TestReviewsAndApprovals:
    def test_the_administrator_sees_every_round(self, gql, world):
        page = gql(REVIEWS, user=world['admin'])['adminReviews']

        # Six: this world has three ideas that reached the platform, each with an
        # organization confirmation and a platform round, and one of those rounds
        # is still open.
        assert page['pageInfo']['totalCount'] == 6
        open_round = next(item for item in page['items'] if not item['isCompleted'])
        assert open_round['decision'] is None
        assert open_round['completedAt'] is None
        assert open_round['idea']['status'] == 'UNDER_REVIEW'
        assert open_round['reviewer']['email'] == 'reviewer@acme.example'

    def test_state_filters(self, gql, world):
        def count(filters):
            return gql(REVIEWS, {'filters': filters}, user=world['admin'])['adminReviews'][
                'pageInfo'
            ]['totalCount']

        # One open round and five completed, across both tracks: the three
        # organization confirmations are completed rounds too. A filter that
        # counted only the platform's would silently hide the stage the platform
        # depends on.
        assert count({'state': 'OPEN'}) == 1
        assert count({'state': 'COMPLETED'}) == 5
        assert count({'organizationId': world['globex'].pk}) == 0
        assert count({'reviewerId': world['reviewer'].pk}) == 6

    def test_the_approvals_view_is_completed_decisions_with_the_idea_status(self, gql, world):
        page = gql(
            REVIEWS,
            {'filters': {'state': 'COMPLETED', 'decisions': ['APPROVED']}},
            user=world['admin'],
        )['adminReviews']

        assert page['pageInfo']['totalCount'] == 1
        approval = page['items'][0]
        assert approval['decision'] == 'APPROVED'
        assert approval['idea']['status'] == 'APPROVED'
        assert approval['idea']['organization']['name'] == 'Acme'
        assert approval['feedback'] == 'Clear and worth doing.'

    def test_the_review_detail_carries_the_idea_history_and_stall_state(self, gql, world):
        detail = gql(REVIEW, {'id': world['open_review'].pk}, user=world['admin'])['adminReview']

        assert detail['isCompleted'] is False
        assert detail['isStalled'] is False
        assert detail['submissionSnapshot']['title'] == 'Open procurement'
        # Both tracks, one round each, and **both numbered 1**: rounds are
        # counted per scope, which is what lets the history say "platform review
        # round 2" and mean it rather than counting the organization's rounds in.
        # The scope is what tells the two apart.
        assert [(item['scope'], item['round']) for item in detail['history']] == [
            ('ORGANIZATION', 1),
            ('PLATFORM', 1),
        ]

        world['reviewer'].is_active = False
        world['reviewer'].save()
        detail = gql(REVIEW, {'id': world['open_review'].pk}, user=world['admin'])['adminReview']
        assert detail['isStalled'] is True

    def test_a_completed_review_is_never_stalled(self, gql, world):
        review = world['public_idea'].reviews.get(scope=Review.Scope.PLATFORM)
        detail = gql(REVIEW, {'id': review.pk}, user=world['admin'])['adminReview']
        assert detail['isStalled'] is False


class TestNothingSensitiveLeaves:
    FORBIDDEN = ('password', 'secret', 'token', 'credential', 'hash', 'providersubject', 'session')

    def test_no_admin_type_declares_a_sensitive_field(self):
        sdl = str(schema)
        blocks = re.findall(r'type (Admin\w+) (?:implements \w+ )?\{(.*?)\n\}', sdl, re.S)
        assert len(blocks) > 20
        for type_name, body in blocks:
            names = re.findall(r'^\s+(\w+)[(:]', body, re.M)
            for name in names:
                assert not any(fragment in name.lower() for fragment in self.FORBIDDEN), (
                    f'{type_name}.{name}'
                )

    def test_a_password_hash_never_appears_in_any_response(self, gql, world):
        user = world['author']
        responses = [
            gql(USERS, user=world['admin']),
            gql(USER, {'id': user.pk}, user=world['admin']),
            gql(ORGANIZATION, {'id': world['acme'].pk}, user=world['admin']),
            gql(IDEA, {'id': world['org_idea'].pk}, user=world['admin']),
        ]
        dumped = json.dumps(responses)
        assert user.password not in dumped
        assert 'md5$' not in dumped
        assert 'pbkdf2' not in dumped


class TestQueryCounts:
    """A page costs the same number of queries whatever its size."""

    @staticmethod
    def _queries(gql, query, user, variables=None):
        with CaptureQueriesContext(connection) as context:
            gql(query, variables, user=user)
        return len(context.captured_queries)

    def test_the_user_list(self, gql, world):
        before = self._queries(gql, USERS, world['admin'])
        for index in range(10):
            make_user(f'extra{index}@example.com')
        assert self._queries(gql, USERS, world['admin']) == before

    def test_the_idea_list(self, gql, world):
        before = self._queries(gql, IDEAS, world['admin'])
        for index in range(10):
            Idea.objects.create(
                organization=world['globex'],
                author=world['globex_owner'],
                title=f'Extra {index}',
                visibility=Idea.Visibility.PRIVATE,
            )
        assert self._queries(gql, IDEAS, world['admin']) == before

    def test_the_organization_list(self, gql, world):
        before = self._queries(gql, ORGANIZATIONS, world['admin'])
        for index in range(5):
            create_organization_for_user(
                make_user(f'founder{index}@example.com'),
                CreateOrganizationInput(name=f'Extra {index}'),
            )
        assert self._queries(gql, ORGANIZATIONS, world['admin']) == before

    def test_the_review_list(self, gql, world):
        before = self._queries(gql, REVIEWS, world['admin'])
        for index in range(4):
            idea = submitted_idea(
                world['author'], world['acme'], world['finance'], f'More {index}', 'organization'
            )
            review_services.start_review(world['reviewer'], idea.pk)
        assert self._queries(gql, REVIEWS, world['admin']) == before


class TestUnknownIdsAndTheAuditTrail:
    @pytest.mark.parametrize(
        'query',
        [
            'query($id: ID!) { adminOrganization(id: $id) { id } }',
            'query($id: ID!) { adminIdea(id: $id) { id } }',
            'query($id: ID!) { adminReview(id: $id) { id } }',
        ],
    )
    @pytest.mark.parametrize('value', ['999999', 'not-a-number'])
    def test_an_unknown_or_malformed_id_is_null(self, gql, world, query, value):
        data = gql(query, {'id': value}, user=world['admin'])
        assert next(iter(data.values())) is None

    def test_members_of_a_malformed_organization_id_is_an_empty_page(self, gql, world):
        page = gql(
            'query { adminOrganizationMembers(organizationId: "x") { pageInfo { totalCount } } }',
            user=world['admin'],
        )['adminOrganizationMembers']
        assert page['pageInfo']['totalCount'] == 0

    def test_member_search(self, gql, world):
        page = gql(
            'query($id: ID!) { adminOrganizationMembers(organizationId: $id, search: "rae") '
            '{ items { user { email } } } }',
            {'id': world['acme'].pk},
            user=world['admin'],
        )['adminOrganizationMembers']
        assert page['items'] == [{'user': {'email': 'reviewer@acme.example'}}]

    def test_comments_of_an_unknown_idea_are_an_empty_page(self, world):
        from administration import selectors

        assert selectors.list_idea_comments(world['admin'], 999999).total_count == 0

    def test_the_audit_trail_filters_by_target(self, gql, world):
        query = """
        query($type: String, $id: String) {
          adminAuditEntries(targetType: $type, targetId: $id) {
            items { action targetType targetId actor { email } }
            pageInfo { totalCount }
          }
        }
        """
        everything = gql(query, user=world['admin'])['adminAuditEntries']
        assert everything['pageInfo']['totalCount'] == 1
        assert everything['items'][0]['actor'] is None

        one = gql(query, {'type': 'user', 'id': str(world['admin'].pk)}, user=world['admin'])[
            'adminAuditEntries'
        ]
        assert one['pageInfo']['totalCount'] == 1
        other = gql(query, {'type': 'category'}, user=world['admin'])['adminAuditEntries']
        assert other['pageInfo']['totalCount'] == 0
