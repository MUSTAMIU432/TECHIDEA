"""
The temporary development categories (`ideas/dev_categories.py`).

They are a bootstrap seed until Django Admin manages categories, so what is
pinned here is the seed's contract - additive, idempotent, never touching an
administrator's rows - and that seeded rows are ordinary `Category` records
that travel the same GraphQL path, and obey the same tenant rules, as any
other category.
"""

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from ideas.dev_categories import DEV_CATEGORIES, seed_dev_categories
from ideas.models import Category, Idea
from identity.models import User
from identity.tokens import issue_access_token
from organizations.services import CreateOrganizationInput, create_organization_for_user

CATEGORIES_QUERY = 'query { categories { id name slug description } }'

CREATE_IDEA = """
mutation CreateIdea($input: CreateIdeaInput!) {
  createIdea(input: $input) { success message idea { id } }
}
"""

SUBMIT_IDEA = """
mutation SubmitIdea($id: ID!) {
  submitIdea(id: $id) { success message idea { id status } }
}
"""

IDEA_QUERY = """
query Idea($id: ID!) {
  idea(id: $id) { id category { id name } }
}
"""


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password='a-strong-unique-pass-1',
    )


def make_member(email, organization_name):
    """A user who owns - and so is a member of - an organization of their own."""
    user = make_user(email)
    organization = create_organization_for_user(
        user, CreateOrganizationInput(name=organization_name)
    ).organization
    return user, organization


def run(client, query, root_field, variables=None, user=None):
    headers = {}
    if user is not None:
        headers['HTTP_AUTHORIZATION'] = f'Bearer {issue_access_token(user.pk)[0]}'
    body = client.post(
        '/graphql/',
        data=json.dumps({'query': query, 'variables': variables or {}}),
        content_type='application/json',
        **headers,
    ).json()
    assert 'errors' not in body, body
    return body['data'][root_field]


def file_idea(client, user, organization, category, visibility='ORGANIZATION'):
    return run(
        client,
        CREATE_IDEA,
        'createIdea',
        {
            'input': {
                'submissionContext': 'ORGANIZATION',
                'organizationId': str(organization.pk),
                'idea': {
                    'title': 'Automate the invoice run',
                    'description': 'We key every invoice in by hand each month.',
                    'categoryId': str(category.pk),
                    'visibility': visibility,
                },
            }
        },
        user=user,
    )


EXPECTED_SLUGS = [
    'finance',
    'education',
    'human-resources',
    'operations',
    'information-technology',
    'customer-service',
    'healthcare',
    'procurement',
    'logistics',
    'government-public-services',
    'sales-marketing',
    'other',
]


def seeded(slug: str) -> Category:
    return Category.objects.get(slug=slug)


# --- the seed --------------------------------------------------------------------------


@pytest.mark.django_db
class TestSeed:
    def test_it_creates_the_development_dataset(self):
        result = seed_dev_categories()

        assert result.created == EXPECTED_SLUGS
        assert result.skipped == []
        assert Category.objects.count() == len(EXPECTED_SLUGS)
        for name, slug, description in DEV_CATEGORIES:
            category = Category.objects.get(slug=slug)
            assert category.name == name
            assert category.description == description
            assert category.is_active is True

    def test_running_it_twice_creates_no_duplicates(self):
        seed_dev_categories()
        first = dict(Category.objects.values_list('slug', 'pk'))

        again = seed_dev_categories()

        assert again.created == []
        assert again.skipped == EXPECTED_SLUGS
        assert dict(Category.objects.values_list('slug', 'pk')) == first

    def test_an_administrators_edits_to_a_seeded_row_survive_a_rerun(self):
        seed_dev_categories()
        Category.objects.filter(slug='finance').update(
            name='Finance & Accounting', description='Edited by an admin.', is_active=False
        )

        seed_dev_categories()

        finance = seeded('finance')
        assert finance.name == 'Finance & Accounting'
        assert finance.description == 'Edited by an admin.'
        # Retired stays retired: the seed never reactivates.
        assert finance.is_active is False
        assert Category.objects.filter(name='Finance').exists() is False

    def test_an_administrator_created_category_is_neither_duplicated_nor_changed(self):
        # Same name, the administrator's own slug: `name` is unique, so this is
        # their category and the seed must step around it.
        admin_made = Category.objects.create(name='Logistics', slug='supply-chain')
        unrelated = Category.objects.create(name='Legal', slug='legal')

        result = seed_dev_categories()

        assert 'logistics' in result.skipped
        assert Category.objects.filter(name__iexact='logistics').count() == 1
        admin_made.refresh_from_db()
        assert admin_made.slug == 'supply-chain'
        assert Category.objects.filter(pk=unrelated.pk).exists()
        assert Category.objects.count() == len(EXPECTED_SLUGS) - 1 + 2

    def test_it_deletes_nothing(self):
        kept = Category.objects.create(name='Legal', slug='legal')

        seed_dev_categories()
        seed_dev_categories()

        assert Category.objects.filter(pk=kept.pk).exists()


@pytest.mark.django_db
class TestCommand:
    def test_the_command_seeds_and_reports(self, settings):
        settings.DEBUG = True
        out = StringIO()

        call_command('seed_dev_categories', stdout=out)
        call_command('seed_dev_categories', stdout=out)

        assert Category.objects.count() == len(EXPECTED_SLUGS)
        assert '12 created, 0 already present' in out.getvalue()
        assert '0 created, 12 already present' in out.getvalue()

    def test_the_command_refuses_with_debug_off(self, settings):
        settings.DEBUG = False

        with pytest.raises(CommandError, match='temporary development categories'):
            call_command('seed_dev_categories', stdout=StringIO())

        assert Category.objects.count() == 0

    def test_the_command_can_be_forced_for_a_test_database(self, settings):
        settings.DEBUG = False

        call_command('seed_dev_categories', '--allow-non-debug', stdout=StringIO())

        assert Category.objects.count() == len(EXPECTED_SLUGS)


# --- through the API -------------------------------------------------------------------


@pytest.mark.django_db
class TestSeededCategoriesOverGraphQL:
    def test_they_are_served_by_the_categories_query(self, client):
        seed_dev_categories()

        categories = run(client, CATEGORIES_QUERY, 'categories')

        # Ordered by name, as `Category.Meta.ordering` has it - no special order.
        assert [c['name'] for c in categories] == sorted(name for name, _, _ in DEV_CATEGORIES)
        assert {c['slug'] for c in categories} == set(EXPECTED_SLUGS)
        assert all(c['id'] for c in categories)

    def test_the_payload_is_what_the_form_consumes(self, client):
        # The fields `frontend/src/features/ideas/api/ideasApi.ts` asks for,
        # and every one of them populated from the record.
        seed_dev_categories()
        finance = seeded('finance')

        categories = run(client, CATEGORIES_QUERY, 'categories')

        assert {
            'id': str(finance.pk),
            'name': 'Finance',
            'slug': 'finance',
            'description': finance.description,
        } in categories

    def test_a_retired_seeded_category_leaves_the_picker(self, client):
        seed_dev_categories()
        Category.objects.filter(slug='other').update(is_active=False)

        slugs = {c['slug'] for c in run(client, CATEGORIES_QUERY, 'categories')}

        assert 'other' not in slugs
        assert len(slugs) == len(EXPECTED_SLUGS) - 1

    def test_an_idea_can_be_filed_and_submitted_under_a_seeded_category(self, client):
        seed_dev_categories()
        finance = seeded('finance')
        author, organization = make_member('author@acme.example', 'Acme')

        created = file_idea(client, author, organization, finance)
        assert created['success'] is True, created
        idea_id = created['idea']['id']

        idea = run(client, IDEA_QUERY, 'idea', {'id': idea_id}, user=author)
        assert idea['category'] == {'id': str(finance.pk), 'name': 'Finance'}

        submitted = run(client, SUBMIT_IDEA, 'submitIdea', {'id': idea_id}, user=author)
        assert submitted['success'] is True, submitted
        # Filed for an organization, so submission is submission *to that
        # organization*; the category is what this test is about, and it is the
        # same category either way.
        assert submitted['idea']['status'] == 'SUBMITTED_TO_ORGANIZATION'


# --- tenant rules are unchanged --------------------------------------------------------


@pytest.mark.django_db
class TestTenantRulesStillHold:
    def test_a_seeded_category_does_not_let_a_non_member_file_into_an_organization(self, client):
        seed_dev_categories()
        _, acme = make_member('owner@acme.example', 'Acme')
        outsider, _ = make_member('outsider@other.example', 'Other Co')

        result = file_idea(client, outsider, acme, seeded('finance'))

        assert result['success'] is False
        assert Idea.objects.count() == 0

    def test_sharing_a_category_does_not_share_ideas_across_organizations(self, client):
        seed_dev_categories()
        author, acme = make_member('author@acme.example', 'Acme')
        outsider, _ = make_member('outsider@other.example', 'Other Co')
        created = file_idea(client, author, acme, seeded('finance'))

        assert run(client, IDEA_QUERY, 'idea', {'id': created['idea']['id']}, user=outsider) is None

    def test_the_category_type_carries_no_tenant_data(self, client):
        fields = run(client, '{ __type(name: "CategoryType") { fields { name } } }', '__type')

        assert {f['name'] for f in fields['fields']} == {'id', 'name', 'slug', 'description'}
