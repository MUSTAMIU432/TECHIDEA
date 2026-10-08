"""
Review reads (S3-003): `reviews.selectors`.

Three reads, each tested for what it shows and - more importantly - for whom
it shows nothing:

- the **queue**: one organization's `SUBMITTED` ideas, for a reviewer there,
  never the caller's own and never an idea they cannot read; empty for every
  other caller, indistinguishably from an empty queue;
- **history**: every round for a reviewer of the idea's organization, completed
  rounds for the author, nothing for anybody else - `PUBLIC` readers included;
- the viewer's **active review**: only their own, only while still eligible.

Reads never write: listing the queue creates no review and moves no status.
"""

import pytest
from django.utils import timezone

from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import eligibility, selectors
from reviews.models import Review
from reviews.tests.platform import (
    build_idea,
    grant_platform_reviewer,
    revoke_platform_reviewer,
)

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password=VALID_PASSWORD,
    )


def make_organization(name, owner):
    return create_organization_for_user(owner, CreateOrganizationInput(name=name)).organization


def add_member(organization, user, *, reviewer=False, status=Membership.Status.ACTIVE):
    membership = Membership.objects.create(user=user, organization=organization, status=status)
    if reviewer:
        MembershipRole.objects.create(
            membership=membership,
            role=Role.objects.get(organization=organization, slug=REVIEWER_ROLE_SLUG),
        )
        # Platform review is authorized by a platform-scoped permission and by
        # nothing else, so a reviewer built here is a *platform* reviewer too.
        # The organization Reviewer role above still governs the organization
        # review queue, which is a separate track with separate rules.
        grant_platform_reviewer(user)
    return membership


def make_idea(
    organization, author, *, status=Idea.Status.SUBMITTED_TO_ORGANIZATION, visibility=None, **extra
):
    """
    An idea in `status`, defaulted to the one the **organization** queue holds.

    `review_queue` is the organization's queue, so `SUBMITTED_TO_ORGANIZATION` is
    what "waiting" means here; the platform queue has its own tests below with
    their own default.
    """
    return build_idea(
        status=status,
        submitted_at=extra.pop('submitted_at', None),
        organization=organization,
        author=author,
        title=extra.pop('title', 'An idea'),
        description=DESCRIPTION,
        category=Category.objects.create(name=f'Cat {Category.objects.count() + 1}'),
        visibility=visibility or Idea.Visibility.ORGANIZATION,
        **extra,
    )


def make_review(idea, reviewer, *, round=1, decision=None):
    return Review.objects.create(
        idea=idea,
        reviewer=reviewer,
        round=round,
        submission_snapshot={'title': idea.title},
        decision=decision,
        feedback='Please add numbers.' if decision else '',
        completed_at=timezone.now() if decision else None,
    )


@pytest.fixture
def world():
    acme_owner = make_user('owner@acme.example')
    acme = make_organization('Acme', acme_owner)
    author = make_user('author@acme.example')
    add_member(acme, author)
    reviewer = make_user('reviewer@acme.example')
    add_member(acme, reviewer, reviewer=True)
    member = make_user('member@acme.example')
    add_member(acme, member)

    globex_owner = make_user('owner@globex.example')
    globex = make_organization('Globex', globex_owner)
    globex_reviewer = make_user('reviewer@globex.example')
    add_member(globex, globex_reviewer, reviewer=True)

    return {
        'acme': acme,
        'acme_owner': acme_owner,
        'author': author,
        'reviewer': reviewer,
        'member': member,
        'globex': globex,
        'globex_owner': globex_owner,
        'globex_reviewer': globex_reviewer,
        'outsider': make_user('outsider@example.com'),
    }


def fresh(user):
    """`user` again from the database, so its permission cache is empty."""
    return User.objects.get(pk=user.pk)


def queue_ids(user, organization, **kwargs):
    return [idea.pk for idea in selectors.review_queue(user, organization.pk, **kwargs).items]


@pytest.mark.django_db
class TestQueueContents:
    def test_a_reviewer_sees_submitted_ideas(self, world):
        idea = make_idea(world['acme'], world['author'])

        assert queue_ids(world['reviewer'], world['acme']) == [idea.pk]
        assert queue_ids(world['acme_owner'], world['acme']) == [idea.pk]

    @pytest.mark.parametrize(
        'status',
        [s for s in Idea.Status.values if s != Idea.Status.SUBMITTED_TO_ORGANIZATION],
    )
    def test_every_other_status_is_excluded(self, world, status):
        """
        Only `SUBMITTED_TO_ORGANIZATION` is in the organization's queue.

        Including `SUBMITTED` - an idea already with the platform is not this
        organization's to confirm any more - and including `ORGANIZATION_CONFIRMED`,
        which has *left* the tenant. And `AUTOMATION_PROPOSAL`/`READY_FOR_IMPLEMENTATION`,
        which are finished.
        """
        make_idea(world['acme'], world['author'], status=status)

        assert queue_ids(world['reviewer'], world['acme']) == []

    def test_the_callers_own_ideas_are_excluded(self, world):
        own = make_idea(world['acme'], world['acme_owner'])
        other = make_idea(world['acme'], world['author'])

        assert queue_ids(world['acme_owner'], world['acme']) == [other.pk]
        assert own.pk in queue_ids(world['reviewer'], world['acme'])

    @pytest.mark.parametrize('visibility', [Idea.Visibility.PRIVATE, Idea.Visibility.DEPARTMENT])
    def test_ideas_the_reviewer_cannot_read_are_excluded(self, world, visibility):
        make_idea(world['acme'], world['author'], visibility=visibility)

        assert queue_ids(world['reviewer'], world['acme']) == []

    def test_a_public_idea_is_in_its_own_organizations_queue(self, world):
        idea = make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

        assert queue_ids(world['reviewer'], world['acme']) == [idea.pk]

    def test_oldest_submission_first_with_a_stable_tie_break(self, world):
        now = timezone.now()
        newer = make_idea(world['acme'], world['author'], submitted_at=now)
        older = make_idea(
            world['acme'], world['author'], submitted_at=now - timezone.timedelta(days=1)
        )
        tied = make_idea(world['acme'], world['author'], submitted_at=now)

        assert queue_ids(world['reviewer'], world['acme']) == [older.pk, newer.pk, tied.pk]

    def test_an_empty_queue(self, world):
        page = selectors.review_queue(world['reviewer'], world['acme'].pk)

        assert page.items == []
        assert page.total_count == 0


@pytest.mark.django_db
class TestQueuePagination:
    def test_pages_are_disjoint_and_complete(self, world):
        now = timezone.now()
        ideas = [
            make_idea(
                world['acme'], world['author'], submitted_at=now + timezone.timedelta(minutes=i)
            )
            for i in range(5)
        ]

        first = selectors.review_queue(world['reviewer'], world['acme'].pk, offset=0, limit=2)
        second = selectors.review_queue(world['reviewer'], world['acme'].pk, offset=2, limit=2)
        third = selectors.review_queue(world['reviewer'], world['acme'].pk, offset=4, limit=2)

        assert first.total_count == 5
        assert first.has_next_page
        assert not first.has_previous_page
        assert not third.has_next_page
        assert [i.pk for page in (first, second, third) for i in page.items] == [
            idea.pk for idea in ideas
        ]

    def test_the_page_size_is_clamped_to_the_discovery_maximum(self, world):
        from ideas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

        assert selectors.review_queue(world['reviewer'], world['acme'].pk, limit=10_000).limit == (
            MAX_PAGE_SIZE
        )
        assert selectors.review_queue(world['reviewer'], world['acme'].pk).limit == (
            DEFAULT_PAGE_SIZE
        )
        assert selectors.review_queue(world['reviewer'], world['acme'].pk, offset=-5).offset == 0


@pytest.mark.django_db
class TestQueueAuthorization:
    @pytest.fixture(autouse=True)
    def submitted(self, world):
        return make_idea(world['acme'], world['author'], visibility=Idea.Visibility.PUBLIC)

    @pytest.mark.parametrize(
        'who', ['member', 'author', 'outsider', 'globex_reviewer', 'globex_owner']
    )
    def test_non_reviewers_get_an_empty_page(self, world, who):
        page = selectors.review_queue(world[who], world['acme'].pk)

        assert page.items == []
        assert page.total_count == 0

    def test_anonymous(self, world):
        assert selectors.review_queue(None, world['acme'].pk).items == []

    def test_an_inactive_reviewer(self, world):
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)

        assert queue_ids(world['reviewer'], world['acme']) == []

    def test_a_deactivated_account(self, world):
        User.objects.filter(pk=world['reviewer'].pk).update(is_active=False)
        world['reviewer'].refresh_from_db()

        assert queue_ids(world['reviewer'], world['acme']) == []

    @pytest.mark.parametrize('organization_id', ['not-a-number', None, 999_999])
    def test_malformed_or_unknown_organizations(self, world, organization_id):
        assert selectors.review_queue(world['reviewer'], organization_id).items == []

    def test_a_reviewer_cannot_read_another_organizations_queue(self, world):
        make_idea(world['globex'], world['globex_owner'], visibility=Idea.Visibility.PUBLIC)

        assert queue_ids(world['reviewer'], world['globex']) == []
        assert queue_ids(world['globex_reviewer'], world['acme']) == []

    def test_a_member_of_both_organizations_reviews_only_where_they_hold_the_role(self, world):
        add_member(world['globex'], world['reviewer'])
        make_idea(world['globex'], world['globex_owner'])

        assert queue_ids(world['reviewer'], world['globex']) == []
        assert len(queue_ids(world['reviewer'], world['acme'])) == 1


@pytest.mark.django_db
class TestQueueIsReadOnly:
    def test_listing_creates_no_review_and_moves_no_status(self, world):
        idea = make_idea(world['acme'], world['author'])

        selectors.review_queue(world['reviewer'], world['acme'].pk)

        assert not Review.objects.exists()
        idea.refresh_from_db()
        assert idea.status == Idea.Status.SUBMITTED_TO_ORGANIZATION

    def test_every_queued_idea_is_one_the_viewer_can_start(self, world):
        make_idea(world['acme'], world['author'])
        make_idea(world['acme'], world['member'])

        for idea in selectors.review_queue(world['reviewer'], world['acme'].pk).items:
            assert idea.viewer_can_start_organization_review is True
            # The page's shortcut agrees with the per-idea rule it replaces.
            assert eligibility.can_start_organization_review(world['reviewer'], idea)

            # ...and nothing on this queue is on the platform's.
            assert eligibility.can_start_review(world['reviewer'], idea) is False

    def test_the_queue_costs_a_fixed_number_of_queries(self, world, django_assert_num_queries):
        for _ in range(8):
            make_idea(world['acme'], world['author'])

        # Eight for eight ideas is the point: a page of fifty must cost what a
        # page of one costs.
        #
        # Nine for eight ideas, and the same nine for fifty: membership and
        # permission (eligibility), membership again (the organization feed), the
        # three tenant facts the visibility filter needs - active organizations,
        # active teams, and whether this user is a platform reviewer - the two
        # vote-count subqueries, the page count, and the rows. **Not one per
        # idea**, which is the property this number exists to protect: the tenant
        # facts are memoised per request (`ideas.selectors._organization_ids`,
        # `_team_ids`, `_is_platform_reviewer`) precisely so that building a second
        # queryset in the same request does not repeat them.
        with django_assert_num_queries(9):
            page = selectors.review_queue(world['reviewer'], world['acme'].pk)
            assert len(page.items) == 8
            for idea in page.items:
                assert idea.vote_count == 0
                assert idea.category is not None


@pytest.mark.django_db
class TestHistory:
    @pytest.fixture
    def idea(self, world):
        return make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)

    @pytest.fixture
    def rounds(self, world, idea):
        first = make_review(idea, world['acme_owner'], decision=Review.Decision.CHANGES_REQUESTED)
        second = make_review(idea, world['reviewer'], round=2)
        return first, second

    def test_a_reviewer_sees_every_round_in_order(self, world, idea, rounds):
        history = selectors.list_idea_reviews(world['reviewer'], idea.pk)

        assert history.reviews == list(rounds)
        assert history.viewer_is_reviewer is True

    def test_the_author_sees_completed_rounds_only(self, world, idea, rounds):
        history = selectors.list_idea_reviews(world['author'], idea.pk)

        assert history.reviews == [rounds[0]]
        assert history.viewer_is_reviewer is False

    def test_an_ordinary_reader_of_the_idea_sees_nothing(self, world, idea, rounds):
        assert selectors.list_idea_reviews(world['member'], idea.pk).reviews == []

    def test_a_public_ideas_readers_elsewhere_see_nothing(self, world, rounds):
        """
        A `PUBLIC` idea is readable by everybody; its platform review history is
        not.

        Because platform review is authorized by a platform permission, somebody
        holding only an *organization* role in another tenant sees nothing - which
        is the isolation this file exists to pin. A platform reviewer would see
        it; `test_a_platform_reviewer_sees_the_history` covers that separately.
        """
        idea = rounds[0].idea
        Idea.objects.filter(pk=idea.pk).update(visibility=Idea.Visibility.PUBLIC)

        for who in ('outsider', 'globex_reviewer', 'globex_owner', 'member'):
            revoke_platform_reviewer(fresh(world[who]))
            assert selectors.list_idea_reviews(fresh(world[who]), idea.pk).reviews == [], who

    def test_a_platform_reviewer_sees_the_history(self, world, rounds):
        """
        The other side of the same coin: the platform permission is what makes a
        submission's history readable across tenants, and nothing else does.
        """
        idea = rounds[0].idea
        Idea.objects.filter(pk=idea.pk).update(visibility=Idea.Visibility.PUBLIC)
        outsider = fresh(world['outsider'])
        assert selectors.list_idea_reviews(outsider, idea.pk).reviews == []

        grant_platform_reviewer(fresh(world['outsider']))

        history = selectors.list_idea_reviews(fresh(world['outsider']), idea.pk)
        assert history.reviews == list(rounds)
        assert history.viewer_is_reviewer is True

    def test_another_organizations_reviewer_guessing_the_id(self, world, idea, rounds):
        assert selectors.list_idea_reviews(world['globex_reviewer'], idea.pk).reviews == []

    def test_an_inactive_reviewer_sees_nothing(self, world, idea, rounds):
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)

        assert selectors.list_idea_reviews(world['reviewer'], idea.pk).reviews == []

    def test_anonymous_and_unknown_ids(self, world, idea, rounds):
        assert selectors.list_idea_reviews(None, idea.pk).reviews == []
        assert selectors.list_idea_reviews(world['reviewer'], 999_999).reviews == []
        assert selectors.list_idea_reviews(world['reviewer'], 'nope').reviews == []

    def test_a_reviewer_loses_history_when_the_idea_becomes_unreadable(self, world, idea, rounds):
        Idea.objects.filter(pk=idea.pk).update(visibility=Idea.Visibility.PRIVATE)

        assert selectors.list_idea_reviews(world['reviewer'], idea.pk).reviews == []
        # The author can always read their own idea, so they keep their feedback.
        assert selectors.list_idea_reviews(world['author'], idea.pk).reviews == [rounds[0]]


@pytest.mark.django_db
class TestActiveReview:
    def test_the_viewers_own_open_review(self, world):
        idea = make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)
        review = make_review(idea, world['reviewer'])

        assert selectors.active_review_id_for(world['reviewer'], idea) == review.pk

    def test_never_another_reviewers_open_review(self, world):
        idea = make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)
        make_review(idea, world['reviewer'])

        for who in ('acme_owner', 'author', 'member', 'globex_reviewer'):
            assert selectors.active_review_id_for(world[who], idea) is None, who

    def test_a_completed_review_is_not_active(self, world):
        idea = make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)
        make_review(idea, world['reviewer'], decision=Review.Decision.APPROVED)

        assert selectors.active_review_id_for(world['reviewer'], idea) is None

    def test_a_former_reviewer_has_no_active_review(self, world):
        """
        Losing the **platform** permission takes the active review with it.

        Organization membership is not involved: platform review never consults it,
        so an inactive organization membership would not stop somebody deciding a
        submission they already hold.
        """
        idea = make_idea(world['acme'], world['author'], status=Idea.Status.UNDER_REVIEW)
        make_review(idea, world['reviewer'])
        revoke_platform_reviewer(fresh(world['reviewer']))

        assert selectors.active_review_id_for(fresh(world['reviewer']), idea) is None

    def test_no_query_outside_under_review(self, world, django_assert_num_queries):
        """
        No query at all for an idea that is not under review.

        The status is checked first and answers without touching the database for
        every state but `UNDER_REVIEW` - which matters because the capability flag
        is resolved for every idea a client renders, and most of them are not under
        review.
        """
        idea = make_idea(world['acme'], world['author'], status=Idea.Status.SUBMITTED)

        with django_assert_num_queries(0):
            assert selectors.active_review_id_for(world['reviewer'], idea) is None
            assert selectors.active_review_id_for(world['reviewer'], None) is None


@pytest.mark.django_db
class TestIsReviewerIn:
    def test_reviewers_and_owners(self, world):
        assert eligibility.is_reviewer_in(world['reviewer'], world['acme'].pk)
        assert eligibility.is_reviewer_in(world['acme_owner'], world['acme'].pk)

    @pytest.mark.parametrize('who', ['member', 'author', 'outsider', 'globex_reviewer'])
    def test_everybody_else(self, world, who):
        assert not eligibility.is_reviewer_in(world[who], world['acme'].pk)

    def test_anonymous_inactive_and_malformed(self, world):
        assert not eligibility.is_reviewer_in(None, world['acme'].pk)
        assert not eligibility.is_reviewer_in(world['reviewer'], 'x')
        Membership.objects.filter(user=world['reviewer']).update(status=Membership.Status.INACTIVE)
        assert not eligibility.is_reviewer_in(world['reviewer'], world['acme'].pk)
