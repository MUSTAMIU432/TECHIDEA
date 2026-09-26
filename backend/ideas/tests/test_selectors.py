"""
Reading ideas: tenancy and visibility (S2-002).

`ideas/selectors.py` is the only sanctioned way to read an idea, so this file
is where the two read rules are pinned:

1. **Tenancy.** An idea belongs to an organization, and a reader sees it only
   if they are an active member - except a `PUBLIC` idea, which is
   platform-wide by definition.
2. **Visibility.** `PRIVATE` is author-only, `ORGANIZATION` is tenant-wide,
   `DEPARTMENT` is author-only until the tier exists, and `PUBLIC` is
   everyone signed in.

The property that is easiest to break and hardest to notice is the negative
one: an idea that comes back as `None`/absent must be indistinguishable from
one that does not exist. A selector that returned "no such idea" for id 1 and
"exists, not yours" for id 2 would be an enumeration oracle for the whole
table, so the tests below assert the *same* answer across all of those cases
rather than each one individually.

The filter and the predicate are also asserted to agree with each other, since
they are two implementations of one rule in the same module - a divergence
would mean the list and the "may I open this?" answer disagree.
"""

import pytest

from ideas import selectors
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership, Organization

VALID_PASSWORD = 'a-strong-unique-pass-1'


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


def add_active_member(organization, user):
    return Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )


def make_idea(organization, author, **overrides):
    fields = {
        'organization': organization,
        'author': author,
        'title': 'An idea',
        'description': 'A description long enough to be useful.',
        'visibility': Idea.Visibility.ORGANIZATION,
    }
    fields.update(overrides)
    return Idea.objects.create(**fields)


@pytest.mark.django_db
class TestCanViewIdea:
    def test_an_author_always_sees_their_own_idea(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        for visibility in Idea.Visibility.values:
            idea = make_idea(organization, user, visibility=visibility)

            assert selectors.can_view_idea(user, idea) is True, visibility

    def test_a_private_idea_is_invisible_to_a_colleague(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        colleague = make_user('colleague@example.com')
        add_active_member(organization, colleague)
        idea = make_idea(organization, author, visibility=Idea.Visibility.PRIVATE)

        assert selectors.can_view_idea(colleague, idea) is False

    def test_an_organization_idea_is_visible_to_a_colleague(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        colleague = make_user('colleague@example.com')
        add_active_member(organization, colleague)
        idea = make_idea(organization, author, visibility=Idea.Visibility.ORGANIZATION)

        assert selectors.can_view_idea(colleague, idea) is True

    def test_a_public_idea_is_visible_to_anyone_signed_in(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        stranger = make_user('stranger@example.com')
        make_organization(name='Other Co', owner=stranger)
        idea = make_idea(organization, author, visibility=Idea.Visibility.PUBLIC)

        assert selectors.can_view_idea(stranger, idea) is True

    def test_a_public_idea_is_not_visible_to_an_anonymous_caller(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        idea = make_idea(organization, author, visibility=Idea.Visibility.PUBLIC)

        assert selectors.can_view_idea(None, idea) is False

    def test_a_public_idea_is_not_visible_to_a_deactivated_caller(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        idea = make_idea(organization, author, visibility=Idea.Visibility.PUBLIC)
        author.is_active = False

        assert selectors.can_view_idea(author, idea) is False

    def test_a_department_idea_is_author_only_for_now(self):
        """
        Reserved vocabulary with no tier behind it, so it is treated exactly
        like `PRIVATE`. Treating it as `ORGANIZATION` would mean an idea an
        author deliberately scoped to their department was readable by the
        whole organization - the exact failure the reserved value prevents.
        """
        author = make_user()
        organization, _ = make_organization(owner=author)
        colleague = make_user('colleague@example.com')
        add_active_member(organization, colleague)
        idea = make_idea(organization, author, visibility=Idea.Visibility.DEPARTMENT)

        assert selectors.can_view_idea(colleague, idea) is False
        assert selectors.can_view_idea(author, idea) is True

    def test_a_departed_member_loses_access(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        colleague = make_user('colleague@example.com')
        theirs = add_active_member(organization, colleague)
        idea = make_idea(organization, author, visibility=Idea.Visibility.ORGANIZATION)
        assert selectors.can_view_idea(colleague, idea) is True

        theirs.status = Membership.Status.INACTIVE
        theirs.save(update_fields=['status'])

        assert selectors.can_view_idea(colleague, idea) is False

    def test_another_tenants_idea_is_invisible(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        idea = make_idea(organization, author)

        assert selectors.can_view_idea(outsider, idea) is False

    def test_a_missing_idea_is_not_viewable(self):
        user = make_user()

        assert selectors.can_view_idea(user, None) is False


@pytest.mark.django_db
class TestGetIdea:
    def test_it_returns_an_idea_the_reader_may_see(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        assert selectors.get_idea(user, idea.pk) == idea

    @pytest.mark.parametrize('visibility', ['nonexistent', 'another-tenant', 'not-mine'])
    def test_every_unreadable_case_answers_the_same_way(self, visibility):
        """
        The enumeration property. A caller must not be able to tell "no such
        id" from "exists, in another tenant" from "exists, not yours" - the
        answer is `None` in all three, and this asserts that in one place
        rather than three times with three different messages.
        """
        user = make_user()
        organization, _ = make_organization(owner=user)

        if visibility == 'nonexistent':
            target = 999999
        elif visibility == 'another-tenant':
            other_organization, _ = make_organization(
                name='Other Co', owner=make_user('owner@other.test')
            )
            target = make_idea(other_organization, make_user('a@other.test')).pk
        else:
            # Private, so it is genuinely the reader's-unreadable case rather
            # than an ORGANIZATION idea this reader may in fact see.
            colleague = make_user('colleague@example.com')
            add_active_member(organization, colleague)
            target = make_idea(organization, colleague, visibility=Idea.Visibility.PRIVATE).pk

        assert selectors.get_idea(user, target) is None

    def test_an_unauthenticated_caller_gets_nothing(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        assert selectors.get_idea(None, idea.pk) is None

    @pytest.mark.parametrize('bad_id', ['not-an-id', '', None])
    def test_a_malformed_id_answers_none_rather_than_raising(self, bad_id):
        user = make_user()

        assert selectors.get_idea(user, bad_id) is None

    def test_the_predicate_and_the_filter_agree(self):
        """
        Two implementations of one rule in the same module (`can_view_idea` and
        `_visibility_filter`). If they ever diverge, the list and the
        "may I open this?" answer disagree - the worst kind of bug here,
        because both are individually correct-looking.
        """
        author = make_user()
        organization, _ = make_organization(owner=author)
        colleague = make_user('colleague@example.com')
        add_active_member(organization, colleague)
        stranger = make_user('stranger@example.com')
        make_organization(name='Other Co', owner=stranger)

        ideas = [
            make_idea(organization, author, visibility=Idea.Visibility.PRIVATE),
            make_idea(organization, author, visibility=Idea.Visibility.ORGANIZATION),
            make_idea(organization, author, visibility=Idea.Visibility.PUBLIC),
            make_idea(organization, author, visibility=Idea.Visibility.DEPARTMENT),
            make_idea(organization, colleague, visibility=Idea.Visibility.PRIVATE),
        ]

        for reader in (author, colleague, stranger):
            listed = set(selectors.list_ideas(reader).values_list('pk', flat=True))
            for idea in ideas:
                assert (idea.pk in listed) == selectors.can_view_idea(reader, idea), (
                    reader.email,
                    idea.visibility,
                )


@pytest.mark.django_db
class TestListIdeas:
    def test_it_returns_the_readers_visible_ideas_newest_first(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        make_idea(organization, author, title='Older')
        make_idea(organization, author, title='Newer')

        listed = list(selectors.list_ideas(author))

        assert [idea.title for idea in listed] == ['Newer', 'Older']

    def test_it_never_returns_another_tenants_private_ideas(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        hidden = make_idea(organization, outsider, visibility=Idea.Visibility.PRIVATE)

        assert hidden not in selectors.list_ideas(author)

    def test_an_unauthenticated_caller_gets_an_empty_queryset(self):
        assert list(selectors.list_ideas(None)) == []

    def test_a_deactivated_caller_gets_an_empty_queryset(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        make_idea(organization, user)
        user.is_active = False

        assert list(selectors.list_ideas(user)) == []


@pytest.mark.django_db
class TestListOrganizationIdeas:
    def test_it_returns_the_tenants_visible_ideas(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        colleague = make_user('colleague@example.com')
        add_active_member(organization, colleague)
        shared = make_idea(organization, colleague, visibility=Idea.Visibility.ORGANIZATION)
        private = make_idea(organization, colleague, visibility=Idea.Visibility.PRIVATE)

        listed = list(selectors.list_organization_ideas(author, organization.pk))

        assert shared in listed
        assert private not in listed

    def test_it_is_empty_for_a_tenant_the_reader_is_not_in(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        make_idea(organization, author)

        assert list(selectors.list_organization_ideas(outsider, organization.pk)) == []

    def test_a_public_idea_in_another_tenant_is_not_in_that_tenants_feed(self):
        """
        A tenant's feed is not a listing of another tenant's contents, even for
        an idea that is public. `list_ideas` does include it - that is what
        public means - but the organization-scoped view is the organization's.
        """
        author = make_user()
        organization, _ = make_organization(owner=author)
        outsider = make_user('outsider@example.com')
        other, _ = make_organization(name='Other Co', owner=outsider)
        public = make_idea(organization, author, visibility=Idea.Visibility.PUBLIC)

        assert public not in selectors.list_organization_ideas(outsider, other.pk)
        assert public in selectors.list_ideas(outsider)

    def test_it_never_confirms_that_an_unknown_organization_exists(self):
        user = make_user()

        assert list(selectors.list_organization_ideas(user, 999999)) == []
        assert list(selectors.list_organization_ideas(user, 'nope')) == []

    def test_an_unauthenticated_caller_gets_an_empty_queryset(self):
        author = make_user()
        organization, _ = make_organization(owner=author)

        assert list(selectors.list_organization_ideas(None, organization.pk)) == []


@pytest.mark.django_db
class TestListOwnIdeas:
    def test_it_returns_everything_the_author_wrote_in_any_state(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        draft = make_idea(organization, user, visibility=Idea.Visibility.PRIVATE)
        colleague = make_user('colleague@example.com')
        add_active_member(organization, colleague)
        theirs = make_idea(organization, colleague)

        listed = list(selectors.list_own_ideas(user))

        assert draft in listed
        assert theirs not in listed

    def test_it_is_not_narrowed_by_visibility(self):
        """
        An author can always see their own idea, so filtering their own work by
        who else might read it would be a strange thing to do to a person.
        """
        user = make_user()
        organization, _ = make_organization(owner=user)
        private = make_idea(organization, user, visibility=Idea.Visibility.PRIVATE)

        assert private in selectors.list_own_ideas(user)

    def test_an_unauthenticated_caller_gets_an_empty_queryset(self):
        assert list(selectors.list_own_ideas(None)) == []


@pytest.mark.django_db
class TestListActiveCategories:
    def test_it_returns_active_categories_by_name(self):
        Category.objects.create(name='Zebra crossing')
        Category.objects.create(name='Customer support')

        assert [c.name for c in selectors.list_active_categories()] == [
            'Customer support',
            'Zebra crossing',
        ]

    def test_a_retired_category_is_hidden(self):
        """Retirement, not deletion: `Idea.category` is PROTECT, so the row and
        its history stay - it just stops being offered."""
        Category.objects.create(name='Retired', is_active=False)
        Category.objects.create(name='Current')

        assert [c.name for c in selectors.list_active_categories()] == ['Current']

    def test_categories_are_not_tenant_scoped(self):
        """
        Platform-wide on purpose - it is what makes ideas comparable across
        organizations, so a category picker cannot be filtered per tenant.
        """
        Category.objects.create(name='Shared')

        assert len(selectors.list_active_categories()) == 1


@pytest.mark.django_db
def test_an_organization_with_no_ideas_lists_none():
    organization = Organization.objects.create(name='Empty Co', slug='empty-co')

    assert list(selectors.list_organization_ideas(make_user(), organization.pk)) == []
