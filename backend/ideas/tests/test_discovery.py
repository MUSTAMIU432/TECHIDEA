"""
Discovery: categories, filters, search and pagination (S2-004).

Discovery is where a read layer is most likely to leak, because every one of
its arguments narrows a result the server produced — and an argument that
*widens* it is indistinguishable from a feature until somebody uses it against
a private idea. So the tests are organised around that:

1. **The visibility policy is applied first and nothing can widen it.** Every
   filter is exercised against a corpus that includes ideas the caller must
   not see, and the assertion is that those rows are still absent. The
   `visibility` filter that a well-meaning client would ask for does not exist,
   and its absence is asserted.
2. **Categories behave as S2-001 decided** — active ones are offered, retired
   ones keep their history, and neither exposes an admin-only field.
3. **Search is a parameterized substring match** over the two fields a reader
   actually sees, and cannot be used to confirm the presence of text in an
   idea the caller cannot read.
4. **Paging is bounded and deterministic**, which is what makes `offset` a
   correct way to page at all.

The behaviours are asserted through the selector and the GraphQL layer; the
tests avoid asserting on queryset internals, except where the ordering
determinism *is* the property under test.
"""

import json

import pytest
from django.test import Client
from django.utils import timezone

from ideas import selectors
from ideas.models import Category, Idea
from ideas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from identity.models import User
from organizations.models import Membership, MembershipRole, Role

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'


def make_user(email='ada@example.com', **overrides):
    fields = {
        'email': email,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password=VALID_PASSWORD, **fields)


def make_organization(name='Acme Labs', owner=None):
    from organizations.services import CreateOrganizationInput, create_organization_for_user

    if owner is None:
        owner = make_user(f'{name.split()[0].lower()}@example.com')
    result = create_organization_for_user(owner, CreateOrganizationInput(name=name))
    return result.organization, result.membership


def add_active_member(organization, user, *, system_role=False):
    membership = Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )
    role = (
        Role.objects.get(organization=organization, is_system=True)
        if system_role
        else Role.objects.create(organization=organization, name='Contributor', slug='contributor')
    )
    MembershipRole.objects.create(membership=membership, role=role)
    return membership


def make_idea(organization, author, **overrides):
    fields = {
        'organization': organization,
        'author': author,
        'title': 'An idea',
        'description': DESCRIPTION,
        'visibility': Idea.Visibility.ORGANIZATION,
    }
    fields.update(overrides)
    return Idea.objects.create(**fields)


def make_category(name, **overrides):
    return Category.objects.create(name=name, **overrides)


@pytest.fixture
def world():
    """
    A corpus with something of everything, because every filter test needs
    rows that *must not* appear as much as rows that must.

    - one organization the caller belongs to, one they do not;
    - ideas that are public, organization-wide, private, and one retired-
      category idea;
    - a second category, and a retired one.
    """
    reader = make_user('reader@example.com')
    organization, _ = make_organization(owner=reader)
    colleague = make_user('colleague@example.com')
    add_active_member(organization, colleague)

    outsider = make_user('outsider@example.com')
    other, _ = make_organization(name='Other Co', owner=outsider)

    support = make_category('Customer support')
    finance = make_category('Finance')
    retired = make_category('Legacy', is_active=False)

    return {
        'reader': reader,
        'colleague': colleague,
        'outsider': outsider,
        'organization': organization,
        'other': other,
        'support': support,
        'finance': finance,
        'retired': retired,
    }


# --- categories ---------------------------------------------------------------------


@pytest.mark.django_db
class TestCategoryDiscovery:
    def test_active_categories_are_returned(self, world):
        found = list(selectors.list_active_categories())

        assert {c.name for c in found} == {'Customer support', 'Finance'}

    def test_a_retired_category_is_not_offered(self, world):
        names = [c.name for c in selectors.list_active_categories()]

        assert 'Legacy' not in names

    def test_the_order_is_deterministic(self, world):
        make_category('Aardvark')
        make_category('Zebra')

        first = [c.name for c in selectors.list_active_categories()]
        second = [c.name for c in selectors.list_active_categories()]

        assert first == second
        # Name-ordered, not insertion- or id-ordered, so a picker is stable
        # across environments and across a category being retired.
        assert first == sorted(first)

    def test_categories_carry_only_their_public_fields(self, world):
        from ideas.schema import CategoryType

        exposed = {field.name for field in CategoryType.__strawberry_definition__.fields}

        # `is_active`, `created_at` and `updated_at` exist on the model and are
        # deliberately not offered: a retired category is simply absent from
        # discovery, and a client has no business timestamping a taxonomy it
        # does not administer.
        assert exposed == {'id', 'name', 'slug', 'description'}

    def test_a_category_is_readable_without_signing_in(self, world):
        # Platform-wide reference data with no tenant content in it, so the
        # category picker can render before anybody decides to sign in. The
        # assertion is that the *selector* does not require a user, not that
        # ideas are public.
        assert list(selectors.list_active_categories())


# --- discovery basics ----------------------------------------------------------------


@pytest.mark.django_db
class TestDiscovery:
    def test_an_authenticated_member_sees_visible_ideas(self, world):
        mine = make_idea(world['organization'], world['reader'])
        page = selectors.list_discoverable_ideas(world['reader'])

        assert mine in page.items
        assert page.total_count >= 1

    def test_private_ideas_of_others_are_excluded(self, world):
        hidden = make_idea(
            world['organization'], world['colleague'], visibility=Idea.Visibility.PRIVATE
        )

        page = selectors.list_discoverable_ideas(world['reader'])

        assert hidden not in page.items

    def test_organization_visibility_is_enforced(self, world):
        shared = make_idea(
            world['organization'], world['colleague'], visibility=Idea.Visibility.ORGANIZATION
        )

        assert shared in selectors.list_discoverable_ideas(world['reader']).items

    def test_department_visibility_remains_fail_closed(self, world):
        """
        Unchanged from S2-003: `DEPARTMENT` is author-only, because nothing
        can honour a department scope. Discovery does not become the place that
        quietly approximates it as organization-wide.
        """
        idea = make_idea(
            world['organization'], world['colleague'], visibility=Idea.Visibility.DEPARTMENT
        )

        page = selectors.list_discoverable_ideas(world['reader'])

        assert idea not in page.items

    def test_cross_organization_ideas_are_excluded(self, world):
        theirs = make_idea(world['other'], world['outsider'])

        page = selectors.list_discoverable_ideas(world['reader'])

        assert theirs not in page.items

    def test_a_public_idea_from_another_organization_is_visible(self, world):
        """
        The one cross-tenant case that is intended: `PUBLIC` means platform-
        wide, which is what makes an idea comparable across organizations.
        """
        public = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)

        assert public in selectors.list_discoverable_ideas(world['reader']).items

    def test_an_unauthenticated_caller_gets_an_empty_page(self, world):
        make_idea(world['organization'], world['reader'], visibility=Idea.Visibility.PUBLIC)

        page = selectors.list_discoverable_ideas(None)

        assert page.items == []
        assert page.total_count == 0

    def test_a_deactivated_caller_gets_an_empty_page(self, world):
        reader = world['reader']
        reader.is_active = False
        reader.save(update_fields=['is_active'])
        make_idea(world['organization'], reader, visibility=Idea.Visibility.PUBLIC)

        assert selectors.list_discoverable_ideas(reader).items == []

    def test_newest_first(self, world):
        older = make_idea(world['organization'], world['reader'], title='Older')
        newer = make_idea(world['organization'], world['reader'], title='Newer')
        # Force the timestamps apart; `auto_now_add` would otherwise be
        # microseconds apart anyway, and this makes the intent explicit
        # instead of relying on the clock.
        Idea.objects.filter(pk=older.pk).update(created_at=timezone.now())
        Idea.objects.filter(pk=newer.pk).update(
            created_at=older.created_at + timezone.timedelta(minutes=1)
        )

        page = selectors.list_discoverable_ideas(world['reader'])

        titles = [idea.title for idea in page.items]
        assert titles.index('Newer') < titles.index('Older')

    def test_the_ordering_is_total_so_pages_do_not_overlap(self, world):
        """
        `-created_at` alone is not a total order: `created_at` is
        microsecond-resolution, so ideas filed in the same instant come back in
        an arbitrary order and an offset page boundary can repeat one row and
        skip another. The primary key tie-breaker is what makes paging correct,
        so it is asserted on ideas that share a timestamp exactly.
        """
        stamp = timezone.now()
        for index in range(5):
            idea = make_idea(world['organization'], world['reader'], title=f'Same {index}')
            Idea.objects.filter(pk=idea.pk).update(created_at=stamp)

        first = selectors.list_discoverable_ideas(world['reader'], limit=2)
        second = selectors.list_discoverable_ideas(world['reader'], offset=2, limit=2)

        assert [i.pk for i in first.items] == sorted((i.pk for i in first.items), reverse=True)
        assert not {i.pk for i in first.items} & {i.pk for i in second.items}


# --- the category filter --------------------------------------------------------------


@pytest.mark.django_db
class TestCategoryFilter:
    def test_it_narrows_to_one_category(self, world):
        support = make_idea(world['organization'], world['reader'], category=world['support'])
        make_idea(world['organization'], world['reader'], category=world['finance'])

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(category_id=world['support'].pk)
        )

        assert [idea.pk for idea in page.items] == [support.pk]

    def test_it_cannot_reach_an_invisible_idea_in_that_category(self, world):
        """
        The filter narrows; it does not grant. A private idea filed under a
        category the reader can see is still not returned.
        """
        hidden = make_idea(
            world['organization'],
            world['colleague'],
            category=world['support'],
            visibility=Idea.Visibility.PRIVATE,
        )

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(category_id=world['support'].pk)
        )

        assert hidden not in page.items

    def test_a_retired_category_still_matches_its_own_visible_ideas(self, world):
        """
        Retirement stops a category being *offered*; it does not rewrite
        history. An idea filed under it before it was retired is still a real
        idea a reader may see, and S2-001's `PROTECT` decision is what makes
        that consistent.
        """
        legacy = make_idea(world['organization'], world['reader'], category=world['retired'])

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(category_id=world['retired'].pk)
        )

        assert [idea.pk for idea in page.items] == [legacy.pk]
        # ...and it is not in the picker.
        assert 'Legacy' not in [c.name for c in selectors.list_active_categories()]

    def test_a_retired_category_cannot_reach_a_private_idea(self, world):
        hidden = make_idea(
            world['organization'],
            world['colleague'],
            category=world['retired'],
            visibility=Idea.Visibility.PRIVATE,
        )

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(category_id=world['retired'].pk)
        )

        assert hidden not in page.items

    def test_an_unknown_category_gives_an_empty_page(self, world):
        make_idea(world['organization'], world['reader'], category=world['support'])

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(category_id=999999)
        )

        # Empty rather than the unfiltered list: a client that asked for a
        # category that does not exist must not be handed everything.
        assert page.items == []

    def test_a_malformed_category_id_gives_an_empty_page(self, world):
        make_idea(world['organization'], world['reader'], category=world['support'])

        for bad in ('abc', '', 3.5, [1]):
            page = selectors.list_discoverable_ideas(
                world['reader'], selectors.IdeaFilters(category_id=bad)
            )
            assert page.items == [], bad

    def test_ideas_with_no_category_are_reachable_by_not_filtering(self, world):
        make_idea(world['organization'], world['reader'], category=None)

        assert len(selectors.list_discoverable_ideas(world['reader']).items) == 1


# --- search ----------------------------------------------------------------------------


@pytest.mark.django_db
class TestSearch:
    def test_it_matches_the_title(self, world):
        match = make_idea(world['organization'], world['reader'], title='Automate invoicing')
        make_idea(world['organization'], world['reader'], title='Something else')

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search='invoicing')
        )

        assert [i.pk for i in page.items] == [match.pk]

    def test_it_matches_the_description(self, world):
        match = make_idea(
            world['organization'],
            world['reader'],
            title='Untitled',
            description='We still reconcile the ledger by hand.',
        )

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search='reconcile')
        )

        assert [i.pk for i in page.items] == [match.pk]

    def test_it_is_case_insensitive(self, world):
        make_idea(world['organization'], world['reader'], title='Automate Invoicing')

        for term in ('invoicing', 'INVOICING', 'InVoIcInG'):
            page = selectors.list_discoverable_ideas(
                world['reader'], selectors.IdeaFilters(search=term)
            )
            assert len(page.items) == 1, term

    def test_no_match_gives_an_empty_page(self, world):
        make_idea(world['organization'], world['reader'], title='Automate invoicing')

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search='zzzz-no-such-thing')
        )

        assert page.items == []
        assert page.total_count == 0

    def test_it_matches_a_substring_rather_than_whole_words(self, world):
        make_idea(world['organization'], world['reader'], title='Nightly reconciliation')

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search='concil')
        )

        assert len(page.items) == 1

    def test_search_cannot_reach_a_private_idea(self, world):
        """
        The property that matters. A search is an oracle waiting to happen: if
        it could match inside text the caller may not read, then "does this
        phrase exist anywhere on the platform?" becomes answerable by anybody
        signed in.
        """
        make_idea(
            world['organization'],
            world['colleague'],
            title='Secret restructuring plan',
            visibility=Idea.Visibility.PRIVATE,
        )

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search='restructuring')
        )

        assert page.items == []

    def test_search_cannot_reach_another_tenants_idea(self, world):
        make_idea(
            world['other'],
            world['outsider'],
            title='Confidential competitor note',
            visibility=Idea.Visibility.ORGANIZATION,
        )

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search='competitor')
        )

        assert page.items == []

    def test_it_does_not_search_fields_the_api_does_not_return(self, world):
        """
        `problem_statement` and the other long-form fields are not exposed
        anywhere yet. Searching them would let a reader confirm the presence of
        a phrase in text they cannot otherwise see — the same oracle as above,
        through a field the UI does not even show.
        """
        hidden_phrase = 'zebra-unique-phrase'
        idea = make_idea(
            world['organization'],
            world['reader'],
            title='An ordinary idea',
            problem_statement=f'Contains {hidden_phrase} in an unexposed field.',
            visibility=Idea.Visibility.ORGANIZATION,
        )

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search=hidden_phrase)
        )

        assert idea not in page.items
        # The row is still visible as a whole; it is the phrase that is not
        # searchable.
        assert idea in selectors.list_discoverable_ideas(world['reader']).items

    @pytest.mark.parametrize('term', ['', '   ', None])
    def test_an_empty_search_is_not_a_filter(self, world, term):
        make_idea(world['organization'], world['reader'], title='Automate invoicing')

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search=term)
        )

        assert len(page.items) == 1

    def test_a_sql_metacharacter_is_searched_for_literally(self, world):
        """
        The ORM binds the term as a parameter, so a quote or a semicolon is
        text to match rather than syntax. Asserted by searching for something
        that would break a naive interpolation, and by the fact that the
        surrounding query still works.
        """
        make_idea(
            world['organization'], world['reader'], title="Robert'); DROP TABLE ideas_idea;--"
        )

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(search="Robert'); DROP")
        )

        assert len(page.items) == 1
        assert Idea.objects.count() == 1

    def test_a_wildcard_character_is_searched_for_literally(self, world):
        """
        A reader who types `%` is looking for a percent sign, not asking for
        everything. Django escapes the pattern metacharacters in `icontains`,
        so the term stays literal.

        Pinned because it is a property of the ORM's escaping rather than of
        anything written here: the day somebody swaps `icontains` for `raw` to
        get trigram search, this test fails instead of a reader's search
        quietly returning the whole corpus.
        """
        literal = make_idea(world['organization'], world['reader'], title='Report 40% of overruns')
        make_idea(world['organization'], world['reader'], title='Anything at all')

        page = selectors.list_discoverable_ideas(world['reader'], selectors.IdeaFilters(search='%'))

        assert [i.pk for i in page.items] == [literal.pk]

    def test_an_underscore_is_searched_for_literally(self, world):
        literal = make_idea(world['organization'], world['reader'], title='field_name_v2 proposal')
        make_idea(world['organization'], world['reader'], title='fieldXnameYv2 proposal')

        page = selectors.list_discoverable_ideas(world['reader'], selectors.IdeaFilters(search='_'))

        assert [i.pk for i in page.items] == [literal.pk]


# --- organization scope ------------------------------------------------------------------


@pytest.mark.django_db
class TestOrganizationScope:
    def test_it_narrows_to_a_tenant_the_caller_belongs_to(self, world):
        mine = make_idea(world['organization'], world['reader'])
        theirs = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(organization_id=world['organization'].pk)
        )

        assert [i.pk for i in page.items] == [mine.pk]
        assert theirs not in page.items

    def test_an_unrelated_organization_gives_an_empty_page(self, world):
        make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(organization_id=world['other'].pk)
        )

        # Empty even though the public idea there would otherwise be visible:
        # naming a tenant you are not in is not a way to list it.
        assert page.items == []
        assert page.total_count == 0

    def test_an_unknown_organization_answers_exactly_like_an_unrelated_one(self, world):
        """
        An organization that does not exist and one the caller has no business
        in must be indistinguishable, or the argument becomes a way to test
        which organization ids are real.
        """
        make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)
        reader = world['reader']

        unrelated = selectors.list_discoverable_ideas(
            reader, selectors.IdeaFilters(organization_id=world['other'].pk)
        )
        unknown = selectors.list_discoverable_ideas(
            reader, selectors.IdeaFilters(organization_id=999999)
        )

        assert unrelated.items == unknown.items == []
        assert unrelated.total_count == unknown.total_count == 0

    def test_a_departed_member_loses_the_scope(self, world):
        colleague = world['colleague']
        membership = Membership.objects.get(user=colleague, organization=world['organization'])
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])
        make_idea(world['organization'], colleague, visibility=Idea.Visibility.PUBLIC)

        page = selectors.list_discoverable_ideas(
            colleague, selectors.IdeaFilters(organization_id=world['organization'].pk)
        )

        assert page.items == []


# --- status filter ----------------------------------------------------------------------


@pytest.mark.django_db
class TestStatusFilter:
    def _submitted(self, organization, author, **overrides):
        fields = {'status': Idea.Status.SUBMITTED, 'submitted_at': timezone.now()}
        fields.update(overrides)
        return make_idea(organization, author, **fields)

    def test_it_narrows_to_one_state(self, world):
        draft = make_idea(world['organization'], world['reader'])
        submitted = self._submitted(world['organization'], world['reader'])

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(status=Idea.Status.SUBMITTED)
        )

        assert [i.pk for i in page.items] == [submitted.pk]
        assert draft not in page.items

    def test_a_draft_of_somebody_elses_is_still_not_discoverable(self, world):
        """
        Status is a filter, not a grant. A colleague's private draft is not
        reachable by asking for drafts, which is the same shape as the
        category and search cases.
        """
        hidden = make_idea(
            world['organization'], world['colleague'], visibility=Idea.Visibility.PRIVATE
        )

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(status=Idea.Status.DRAFT)
        )

        assert hidden not in page.items

    def test_an_unknown_status_gives_an_empty_page(self, world):
        make_idea(world['organization'], world['reader'])

        page = selectors.list_discoverable_ideas(
            world['reader'], selectors.IdeaFilters(status='approved_by_myself')
        )

        assert page.items == []

    def test_every_known_status_is_accepted(self, world):
        """
        All seven, including the review-only states: a client must be able to
        ask for them even though only some are reachable in this sprint, and
        an unknown *name* is what should be refused.
        """
        assert set(Idea.Status.values) == {
            'draft',
            'submitted',
            'under_review',
            'changes_requested',
            'rejected',
            'approved',
            'automation_proposal',
        }
        for status in Idea.Status.values:
            page = selectors.list_discoverable_ideas(
                world['reader'], selectors.IdeaFilters(status=status)
            )
            assert page.items == [], status


# --- pagination -------------------------------------------------------------------------


@pytest.mark.django_db
class TestPagination:
    def _make(self, world, count, prefix='Idea'):
        return [
            make_idea(world['organization'], world['reader'], title=f'{prefix} {index}')
            for index in range(count)
        ]

    def test_the_first_page_is_bounded_by_the_limit(self, world):
        self._make(world, 5)

        page = selectors.list_discoverable_ideas(world['reader'], limit=2)

        assert len(page.items) == 2
        assert page.total_count == 5
        assert page.has_next_page is True
        assert page.has_previous_page is False

    def test_the_next_page_continues_where_the_first_stopped(self, world):
        made = self._make(world, 5)
        expected = [idea.pk for idea in reversed(made)]

        first = selectors.list_discoverable_ideas(world['reader'], limit=2)
        second = selectors.list_discoverable_ideas(world['reader'], offset=2, limit=2)
        third = selectors.list_discoverable_ideas(world['reader'], offset=4, limit=2)

        collected = (
            [i.pk for i in first.items] + [i.pk for i in second.items] + [i.pk for i in third.items]
        )
        assert collected == expected

    def test_the_last_page_reports_no_next_page(self, world):
        self._make(world, 3)

        page = selectors.list_discoverable_ideas(world['reader'], offset=2, limit=2)

        assert len(page.items) == 1
        assert page.has_next_page is False
        assert page.has_previous_page is True

    def test_an_offset_past_the_end_is_an_empty_page_not_an_error(self, world):
        self._make(world, 3)

        page = selectors.list_discoverable_ideas(world['reader'], offset=99, limit=2)

        assert page.items == []
        # The total is still reported, so a UI can tell "past the end" from
        # "nothing matches".
        assert page.total_count == 3
        assert page.has_previous_page is True

    def test_the_total_counts_matches_not_the_page(self, world):
        self._make(world, 7)

        page = selectors.list_discoverable_ideas(world['reader'], limit=3)

        assert page.total_count == 7
        assert len(page.items) == 3

    def test_a_limit_above_the_maximum_is_clamped(self, world):
        self._make(world, 5)

        page = selectors.list_discoverable_ideas(world['reader'], limit=10_000)

        assert page.limit == MAX_PAGE_SIZE
        assert len(page.items) <= MAX_PAGE_SIZE

    @pytest.mark.parametrize('requested', [None, 0, -5, 'lots', 3.5])
    def test_an_unusable_limit_becomes_the_default(self, world, requested):
        self._make(world, 3)

        page = selectors.list_discoverable_ideas(world['reader'], limit=requested)

        assert page.limit == DEFAULT_PAGE_SIZE

    def test_a_negative_offset_becomes_zero(self, world):
        self._make(world, 2)

        page = selectors.list_discoverable_ideas(world['reader'], offset=-10)

        assert page.offset == 0
        assert page.has_previous_page is False

    def test_the_default_page_size_is_a_real_bound(self, world):
        self._make(world, 3)

        page = selectors.list_discoverable_ideas(world['reader'])

        assert page.limit == DEFAULT_PAGE_SIZE

    def test_paging_never_returns_something_a_filter_excluded(self, world):
        for index in range(6):
            make_idea(
                world['organization'],
                world['colleague'],
                title=f'Private {index}',
                visibility=Idea.Visibility.PRIVATE,
            )
        self._make(world, 2, prefix='Shared')

        first = selectors.list_discoverable_ideas(world['reader'], limit=1)
        second = selectors.list_discoverable_ideas(world['reader'], offset=1, limit=1)

        assert len(first.items) == 1
        assert len(second.items) == 1
        assert first.total_count == second.total_count == 2

    def test_paging_costs_a_fixed_number_of_queries(self, world, django_assert_num_queries):
        """
        Three queries per page and no more, whatever the page size: the
        membership lookup the visibility filter needs, the `COUNT` behind
        `total_count`, and the page fetch itself.

        Pinned for two reasons. The count is a genuine trade — the cheaper
        alternative is to fetch `limit + 1` rows and infer "has more" from a
        short page, but that cannot answer "showing 1-20 of 137", which is the
        number people use to decide between refining the search and paging.
        And the invariance matters: a per-row query sneaking back in here would
        turn a page of 20 into 40 queries without any test noticing.
        """
        self._make(world, 4)
        reader = world['reader']

        with django_assert_num_queries(3):
            selectors.list_discoverable_ideas(reader, limit=1)

        with django_assert_num_queries(3):
            selectors.list_discoverable_ideas(reader, limit=4)

    def test_the_count_is_cheaper_than_materializing_everything(
        self, world, django_assert_num_queries
    ):
        """
        The `COUNT` is answered by the database, not by building the page in
        Python first — which is the whole reason `total_count` can be reported
        for a table this query never loads. Asserted structurally: the count
        query must not select the idea columns.
        """
        self._make(world, 4)
        reader = world['reader']

        with django_assert_num_queries(3) as captured:
            selectors.list_discoverable_ideas(reader, limit=2)

        count_query = next(q['sql'] for q in captured.captured_queries if 'COUNT' in q['sql'])
        assert 'title' not in count_query

    def test_the_page_carries_no_extra_queries_for_its_rows(self, world, django_assert_num_queries):
        """
        `select_related` on the organization, author and category means
        serializing a page does not fan out into per-row queries. Asserted at
        the boundary the serializer actually uses.
        """
        self._make(world, 3)
        page = selectors.list_discoverable_ideas(world['reader'], limit=3)

        with django_assert_num_queries(0):
            for idea in page.items:
                assert idea.author.email
                assert idea.organization.name
                assert idea.category is None or idea.category.name


# --- combined filters -------------------------------------------------------------------


@pytest.mark.django_db
class TestCombinedFilters:
    def test_category_and_search_together(self, world):
        match = make_idea(
            world['organization'],
            world['reader'],
            title='Automate invoicing',
            category=world['support'],
        )
        make_idea(
            world['organization'],
            world['reader'],
            title='Automate invoicing',
            category=world['finance'],
        )
        make_idea(
            world['organization'],
            world['reader'],
            title='Unrelated title',
            category=world['support'],
        )

        page = selectors.list_discoverable_ideas(
            world['reader'],
            selectors.IdeaFilters(category_id=world['support'].pk, search='invoicing'),
        )

        assert [i.pk for i in page.items] == [match.pk]

    def test_category_and_status_together(self, world):
        draft = make_idea(world['organization'], world['reader'], category=world['support'])
        submitted = make_idea(
            world['organization'],
            world['reader'],
            category=world['support'],
            status=Idea.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )

        page = selectors.list_discoverable_ideas(
            world['reader'],
            selectors.IdeaFilters(category_id=world['support'].pk, status=Idea.Status.SUBMITTED),
        )

        assert [i.pk for i in page.items] == [submitted.pk]
        assert draft not in page.items

    def test_search_and_status_together(self, world):
        match = make_idea(
            world['organization'],
            world['reader'],
            title='Invoicing reconciliation',
            status=Idea.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        make_idea(
            world['organization'],
            world['reader'],
            title='Invoicing something else',
        )

        page = selectors.list_discoverable_ideas(
            world['reader'],
            selectors.IdeaFilters(search='reconciliation', status=Idea.Status.SUBMITTED),
        )

        assert [i.pk for i in page.items] == [match.pk]

    def test_organization_category_and_search_together(self, world):
        match = make_idea(
            world['organization'],
            world['reader'],
            title='Automate the ledger',
            category=world['finance'],
        )
        make_idea(
            world['other'],
            world['outsider'],
            title='Automate the ledger',
            category=world['finance'],
            visibility=Idea.Visibility.PUBLIC,
        )

        page = selectors.list_discoverable_ideas(
            world['reader'],
            selectors.IdeaFilters(
                organization_id=world['organization'].pk,
                category_id=world['finance'].pk,
                search='ledger',
            ),
        )

        # The public idea in the other tenant is excluded by the organization
        # scope, not by anything about the search.
        assert [i.pk for i in page.items] == [match.pk]

    def test_every_filter_together_still_excludes_what_it_should(self, world):
        make_idea(
            world['organization'],
            world['colleague'],
            title='Automate the ledger',
            category=world['finance'],
            status=Idea.Status.SUBMITTED,
            submitted_at=timezone.now(),
            visibility=Idea.Visibility.PRIVATE,
        )

        page = selectors.list_discoverable_ideas(
            world['reader'],
            selectors.IdeaFilters(
                organization_id=world['organization'].pk,
                category_id=world['finance'].pk,
                search='ledger',
                status=Idea.Status.SUBMITTED,
            ),
        )

        # Every argument matches the hidden idea perfectly, and it is still
        # not returned. This is the test that says the filters cannot compose
        # into a grant.
        assert page.items == []
        assert page.total_count == 0


# --- through the GraphQL layer --------------------------------------------------------


DISCOVERY_QUERY = """
query Discover($filters: IdeaFiltersInput) {
  ideas(filters: $filters) {
    items { id title status category { name } }
    pageInfo { offset limit totalCount hasNextPage hasPreviousPage }
  }
}
"""

SCOPED_DISCOVERY_QUERY = """
query ScopedDiscover($organizationId: ID!, $filters: IdeaFiltersInput) {
  organizationIdeas(organizationId: $organizationId, filters: $filters) {
    items { id title }
    pageInfo { offset limit totalCount hasNextPage hasPreviousPage }
  }
}
"""

CATEGORIES_QUERY = """
query Categories { categories { id name slug description } }
"""


@pytest.fixture
def gql(client: Client):
    """Post to the real endpoint, the way the browser does."""

    def post(query, variables=None, bearer=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        headers = {}
        if bearer is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {bearer}'
        return client.post(
            '/graphql/', data=json.dumps(payload), content_type='application/json', **headers
        )

    return post


def sign_in(client: Client, user: User) -> str:
    """A real access token, obtained the way the browser obtains one."""
    response = client.post(
        '/graphql/',
        data=json.dumps(
            {
                'query': """
mutation Login($input: LoginInput!) { login(input: $input) { success accessToken } }""",
                'variables': {'input': {'email': user.email, 'password': VALID_PASSWORD}},
            }
        ),
        content_type='application/json',
    )
    payload = response.json()['data']['login']
    assert payload['success'] is True, payload
    return payload['accessToken']


def run(gql, query, root_field, variables=None, bearer=None):
    response = gql(query, variables, bearer=bearer)
    assert response.status_code == 200, response.content
    body = response.json()
    assert 'errors' not in body, body
    return body['data'][root_field]


@pytest.mark.django_db
class TestDiscoveryThroughGraphQL:
    """The schema's job is to translate arguments, not to decide anything.

    So these tests assert two things and no more: that the arguments arrive
    as the selector was asked for them, and that the visibility rule still
    holds when they are given. The policy itself is proved in the selector
    tests; re-proving it per argument here would only duplicate them.
    """

    def test_it_returns_a_page_rather_than_a_list(self, client, gql, world):
        reader = world['reader']
        make_idea(world['organization'], reader)
        token = sign_in(client, reader)

        page = run(gql, DISCOVERY_QUERY, 'ideas', bearer=token)

        assert len(page['items']) == 1
        assert page['pageInfo'] == {
            'offset': 0,
            'limit': DEFAULT_PAGE_SIZE,
            'totalCount': 1,
            'hasNextPage': False,
            'hasPreviousPage': False,
        }

    def test_an_unauthenticated_caller_gets_an_empty_page_not_an_error(self, client, gql, world):
        make_idea(world['organization'], world['reader'], visibility=Idea.Visibility.PUBLIC)

        page = run(gql, DISCOVERY_QUERY, 'ideas')

        # An anonymous reader browsing categories is a real state, so the
        # answer is a page with nothing in it rather than an error the UI has
        # to special-case.
        assert page['items'] == []
        assert page['pageInfo']['totalCount'] == 0

    def test_the_filters_argument_is_optional(self, client, gql, world):
        reader = world['reader']
        make_idea(world['organization'], reader)
        token = sign_in(client, reader)

        page = run(gql, DISCOVERY_QUERY, 'ideas', {'filters': None}, bearer=token)

        assert len(page['items']) == 1

    def test_the_category_filter_arrives_as_the_written_enum(self, client, gql, world):
        reader = world['reader']
        support = world['support']
        make_idea(world['organization'], reader, category=support)
        make_idea(world['organization'], reader, category=world['finance'])
        token = sign_in(client, reader)

        page = run(
            gql,
            DISCOVERY_QUERY,
            'ideas',
            {'filters': {'categoryId': str(support.pk)}},
            bearer=token,
        )

        assert [item['category']['name'] for item in page['items']] == ['Customer support']

    def test_the_status_filter_arrives_as_the_written_enum(self, client, gql, world):
        reader = world['reader']
        submitted = make_idea(
            world['organization'],
            reader,
            status=Idea.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        make_idea(world['organization'], reader)
        token = sign_in(client, reader)

        page = run(
            gql,
            DISCOVERY_QUERY,
            'ideas',
            {'filters': {'status': 'SUBMITTED'}},
            bearer=token,
        )

        assert [item['id'] for item in page['items']] == [str(submitted.pk)]

    def test_an_unknown_status_is_a_validation_error_not_a_filter(self, client, gql, world):
        """
        The enum is the schema's job, so an unknown value is refused before it
        reaches the selector. That is a different failure from an unknown
        status reaching the selector, which yields an empty page - the schema
        guarantees the vocabulary, the selector guarantees the rows.
        """
        reader = world['reader']
        make_idea(world['organization'], reader)
        token = sign_in(client, reader)

        response = gql(DISCOVERY_QUERY, {'filters': {'status': 'APPROVED_BY_ME'}}, bearer=token)

        assert response.status_code == 200
        assert 'errors' in response.json()

    def test_paging_arguments_are_honoured(self, client, gql, world):
        reader = world['reader']
        for index in range(5):
            make_idea(world['organization'], reader, title=f'Page {index}')
        token = sign_in(client, reader)

        page = run(
            gql,
            DISCOVERY_QUERY,
            'ideas',
            {'filters': {'offset': 2, 'limit': 2}},
            bearer=token,
        )

        assert len(page['items']) == 2
        assert page['pageInfo'] == {
            'offset': 2,
            'limit': 2,
            'totalCount': 5,
            'hasNextPage': True,
            'hasPreviousPage': True,
        }

    def test_an_oversized_limit_is_clamped_and_reported(self, client, gql, world):
        reader = world['reader']
        for index in range(3):
            make_idea(world['organization'], reader, title=f'Page {index}')
        token = sign_in(client, reader)

        page = run(gql, DISCOVERY_QUERY, 'ideas', {'filters': {'limit': 10_000}}, bearer=token)

        # Echoed back as applied, so a client can see its ceiling.
        assert page['pageInfo']['limit'] == MAX_PAGE_SIZE

    def test_search_and_status_compose(self, client, gql, world):
        reader = world['reader']
        match = make_idea(
            world['organization'],
            reader,
            title='Automate the ledger',
            status=Idea.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        make_idea(world['organization'], reader, title='Automate the ledger')
        token = sign_in(client, reader)

        page = run(
            gql,
            DISCOVERY_QUERY,
            'ideas',
            {'filters': {'search': 'ledger', 'status': 'SUBMITTED'}},
            bearer=token,
        )

        assert [item['id'] for item in page['items']] == [str(match.pk)]

    def test_no_filter_argument_widens_the_visibility_filter(self, client, gql, world):
        """
        Every one of these arguments would be refused by the schema as an
        unknown field, and the assertion is that the idea the caller must not
        see is absent regardless. If a future change adds a `visibility` or
        `authorId` filter, this is the test that should stop making sense -
        which is the point of naming the fields here.
        """
        reader = world['reader']
        hidden = make_idea(
            world['organization'],
            world['colleague'],
            title='Automate the ledger',
            visibility=Idea.Visibility.PRIVATE,
        )
        token = sign_in(client, reader)

        page = run(
            gql,
            DISCOVERY_QUERY,
            'ideas',
            {'filters': {'search': 'ledger', 'status': 'DRAFT'}},
            bearer=token,
        )
        assert [item['id'] for item in page['items']] != [str(hidden.pk)]

        for field in ('visibility', 'authorId', 'statuses'):
            response = gql(DISCOVERY_QUERY, {'filters': {field: 'PRIVATE'}}, bearer=token)
            assert 'errors' in response.json(), field

    def test_organization_ideas_stays_scoped(self, client, gql, world):
        reader = world['reader']
        mine = make_idea(world['organization'], reader)
        theirs = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)
        token = sign_in(client, reader)

        page = run(
            gql,
            SCOPED_DISCOVERY_QUERY,
            'organizationIdeas',
            {'organizationId': str(world['organization'].pk)},
            bearer=token,
        )

        assert [item['id'] for item in page['items']] == [str(mine.pk)]
        assert theirs not in [Idea.objects.get(pk=int(i['id'])) for i in page['items']]

    def test_organization_ideas_applies_the_same_filters(self, client, gql, world):
        reader = world['reader']
        match = make_idea(
            world['organization'],
            reader,
            title='Automate invoicing',
            category=world['support'],
        )
        make_idea(world['organization'], reader, title='Automate invoicing')
        token = sign_in(client, reader)

        page = run(
            gql,
            SCOPED_DISCOVERY_QUERY,
            'organizationIdeas',
            {
                'organizationId': str(world['organization'].pk),
                'filters': {'search': 'invoicing', 'categoryId': str(world['support'].pk)},
            },
            bearer=token,
        )

        assert [item['id'] for item in page['items']] == [str(match.pk)]

    def test_organization_ideas_for_a_tenant_the_caller_is_not_in_is_empty(
        self, client, gql, world
    ):
        reader = world['reader']
        make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)
        token = sign_in(client, reader)

        page = run(
            gql,
            SCOPED_DISCOVERY_QUERY,
            'organizationIdeas',
            {'organizationId': str(world['other'].pk)},
            bearer=token,
        )

        assert page['items'] == []
        assert page['pageInfo']['totalCount'] == 0

    def test_a_filter_cannot_re_scope_a_scoped_query(self, client, gql, world):
        """
        `organizationIdeas(organizationId: A)` with `filters.organizationId: B`
        asks for two tenants at once. The shared filter input has no
        organization field at all, so the schema refuses it rather than the
        server picking a winner — a silent precedence rule here would be a way
        to read a tenant's feed by accident, and a validation error is the
        honest answer to a contradictory request.
        """
        reader = world['reader']
        make_idea(world['organization'], reader)
        token = sign_in(client, reader)

        response = gql(
            SCOPED_DISCOVERY_QUERY,
            {
                'organizationId': str(world['organization'].pk),
                'filters': {'organizationId': str(world['other'].pk)},
            },
            bearer=token,
        )

        assert 'errors' in response.json()

    def test_ideas_is_platform_wide_and_the_scoped_query_is_not(self, client, gql, world):
        """
        The split the two queries exist to express: `ideas` is what the caller
        may see anywhere, so a public idea from a tenant they do not belong to
        is in it; `organizationIdeas` is that tenant's feed and never lists
        another tenant's contents.
        """
        reader = world['reader']
        theirs = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)
        token = sign_in(client, reader)

        platform = run(gql, DISCOVERY_QUERY, 'ideas', bearer=token)
        scoped = run(
            gql,
            SCOPED_DISCOVERY_QUERY,
            'organizationIdeas',
            {'organizationId': str(world['organization'].pk)},
            bearer=token,
        )

        assert [item['id'] for item in platform['items']] == [str(theirs.pk)]
        assert [item['id'] for item in scoped['items']] == []

    def test_categories_are_readable_without_signing_in(self, client, gql, world):
        categories = run(gql, CATEGORIES_QUERY, 'categories')

        assert [c['name'] for c in categories] == ['Customer support', 'Finance']
        assert 'Legacy' not in [c['name'] for c in categories]
