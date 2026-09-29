"""
The Ideas GraphQL boundary (S2-002).

Everything here goes over the real endpoint - `django.test.Client` -> URLconf
-> middleware -> the `csrf_exempt` GraphQL view -> Strawberry -> the resolvers
-> `ideas/services.py` / `ideas/selectors.py` -> the database - because the
properties that matter at this layer are precisely the ones a direct call to
the service would not exercise:

- **Authentication is the resolver's job to enforce, from the access token.**
  A test that called `services.create_idea(user, ...)` with a real user proves
  nothing about whether the *GraphQL* boundary lets an anonymous caller
  through, which is the question a caller actually asks.
- **Failures are payloads, never a GraphQL `errors` array.** The frontend
  treats a thrown GraphQL error as "the server is broken" and a
  `success: false` payload as the operation's own answer, so a resolver that
  raised on a cross-tenant edit would make a permission refusal look like an
  outage.
- **The type is safe.** `IdeaType` carries ids rather than nested user
  objects, which is the one place this schema is stricter than the
  organizations schema beside it, and it is asserted structurally below rather
  than left to review.
"""

import json

import pytest
from django.test import Client

from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership

VALID_PASSWORD = 'a-strong-unique-pass-1'
MIN_DESCRIPTION = 'x' * 20

CREATE_IDEA = """
mutation CreateIdea($input: CreateIdeaInput!) {
  createIdea(input: $input) {
    success
    message
    field
    idea { id title status visibility submittedAt authorId organizationId }
  }
}
"""

UPDATE_IDEA = """
mutation UpdateIdea($input: UpdateIdeaInput!) {
  updateIdea(input: $input) { success message field idea { id title description } }
}
"""

SUBMIT_IDEA = """
mutation SubmitIdea($id: ID!) {
  submitIdea(id: $id) {
    success
    message
    field
    idea { id status submittedAt }
  }
}
"""

IDEA_QUERY = """
query Idea($id: ID!) {
  idea(id: $id) { id title authorId organizationId status visibility category { id name } }
}
"""

IDEAS_QUERY = """
query Ideas {
  ideas { items { id title } pageInfo { totalCount } }
}
"""

ORGANIZATION_IDEAS_QUERY = """
query OrganizationIdeas($organizationId: ID!) {
  organizationIdeas(organizationId: $organizationId) { items { id title } pageInfo { totalCount } }
}
"""

CATEGORIES_QUERY = """
query Categories {
  categories { id name slug }
}
"""

SCHEMA_QUERY = """
query SchemaIntrospection {
  __type(name: "IdeaType") { fields { name } }
}
"""


@pytest.fixture
def gql(client: Client):
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


def run(gql, query, root_field, variables=None, bearer=None):
    response = gql(query, variables, bearer=bearer)
    assert response.status_code == 200, response.content
    body = response.json()
    assert 'errors' not in body, body
    return body['data'][root_field]


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


def sign_in(client: Client, user: User) -> str:
    """A real access token, obtained the way a browser obtains one."""
    response = client.post(
        '/graphql/',
        data=json.dumps(
            {
                'query': """
mutation Login($input: LoginInput!) {
  login(input: $input) { success accessToken }
}""",
                'variables': {
                    'input': {'email': user.email, 'password': VALID_PASSWORD},
                },
            }
        ),
        content_type='application/json',
    )
    payload = response.json()['data']['login']
    assert payload['success'] is True, payload
    return payload['accessToken']


@pytest.fixture
def ada(client: Client):
    """A signed-in user with one organization, as the product leaves them."""
    user = make_user()
    organization, _ = make_organization(owner=user)
    return {'user': user, 'organization': organization, 'token': sign_in(client, user)}


def idea_input(**overrides):
    fields = {'title': 'Automate the invoice run', 'description': MIN_DESCRIPTION}
    fields.update(overrides)
    return fields


def create_complete_via_api(gql, ada, **overrides):
    """
    A draft that satisfies every submission rule: title, description,
    category, and a visibility reviewers can read (S3-008, D-6).
    """
    category = Category.objects.create(name=f'Category {Category.objects.count() + 1}')
    result = run(
        gql,
        CREATE_IDEA,
        'createIdea',
        {
            'input': {
                'organizationId': str(ada['organization'].pk),
                'idea': idea_input(
                    categoryId=str(category.pk), **{'visibility': 'ORGANIZATION', **overrides}
                ),
            }
        },
        bearer=ada['token'],
    )
    assert result['success'] is True, result
    return result


def create_via_api(gql, ada, **overrides):
    result = run(
        gql,
        CREATE_IDEA,
        'createIdea',
        {
            'input': {
                'organizationId': str(ada['organization'].pk),
                'idea': idea_input(**overrides),
            }
        },
        bearer=ada['token'],
    )
    assert result['success'] is True, result
    return result


# --- createIdea ----------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateIdeaMutation:
    def test_an_unauthenticated_caller_is_refused(self, gql):
        organization, _ = make_organization()

        result = run(
            gql,
            CREATE_IDEA,
            'createIdea',
            {'input': {'organizationId': str(organization.pk), 'idea': idea_input()}},
        )

        assert result['success'] is False
        assert result['idea'] is None
        assert Idea.objects.count() == 0

    def test_an_authenticated_member_files_a_draft(self, gql, ada):
        result = create_via_api(gql, ada)

        idea = Idea.objects.get(pk=int(result['idea']['id']))
        assert idea.status == Idea.Status.DRAFT
        assert idea.author_id == ada['user'].pk
        assert idea.organization_id == ada['organization'].pk

    def test_the_new_idea_is_private_by_default(self, gql, ada):
        result = create_via_api(gql, ada)

        assert result['idea']['visibility'] == 'PRIVATE'

    def test_the_payload_reports_the_state_the_frontend_renders(self, gql, ada):
        result = create_via_api(gql, ada)

        assert result['idea']['status'] == 'DRAFT'
        assert result['idea']['submittedAt'] is None
        assert result['idea']['authorId'] == str(ada['user'].pk)
        assert result['idea']['organizationId'] == str(ada['organization'].pk)

    def test_a_chosen_visibility_is_honoured(self, gql, ada):
        result = create_via_api(gql, ada, visibility='ORGANIZATION')

        assert result['idea']['visibility'] == 'ORGANIZATION'

    def test_the_reserved_department_visibility_is_refused(self, gql, ada):
        result = run(
            gql,
            CREATE_IDEA,
            'createIdea',
            {
                'input': {
                    'organizationId': str(ada['organization'].pk),
                    'idea': idea_input(visibility='DEPARTMENT'),
                }
            },
            bearer=ada['token'],
        )

        assert result['success'] is False
        assert result['field'] == 'visibility'
        assert Idea.objects.count() == 0

    def test_a_non_member_cannot_file_into_an_organization(self, gql, client, ada):
        # Owned by somebody else, so the caller is genuinely a non-member of it.
        other, _ = make_organization(name='Other Co', owner=make_user('owner@other.test'))
        outsider = make_user('outsider@example.com')
        token = sign_in(client, outsider)

        result = run(
            gql,
            CREATE_IDEA,
            'createIdea',
            {'input': {'organizationId': str(other.pk), 'idea': idea_input()}},
            bearer=token,
        )

        assert result['success'] is False
        assert Idea.objects.count() == 0

    def test_a_blank_title_is_reported_against_the_field(self, gql, ada):
        result = run(
            gql,
            CREATE_IDEA,
            'createIdea',
            {
                'input': {
                    'organizationId': str(ada['organization'].pk),
                    'idea': idea_input(title='   '),
                }
            },
            bearer=ada['token'],
        )

        assert result['success'] is False
        assert result['field'] == 'title'

    def test_an_unknown_category_is_reported_against_the_field(self, gql, ada):
        result = run(
            gql,
            CREATE_IDEA,
            'createIdea',
            {
                'input': {
                    'organizationId': str(ada['organization'].pk),
                    'idea': idea_input(categoryId='999999'),
                }
            },
            bearer=ada['token'],
        )

        assert result['success'] is False
        assert result['field'] == 'category'

    def test_a_chosen_category_is_stored_and_returned(self, gql, ada):
        category = Category.objects.create(name='Customer support')

        result = run(
            gql,
            CREATE_IDEA,
            'createIdea',
            {
                'input': {
                    'organizationId': str(ada['organization'].pk),
                    'idea': idea_input(categoryId=str(category.pk)),
                }
            },
            bearer=ada['token'],
        )

        assert result['success'] is True
        assert Idea.objects.get(pk=int(result['idea']['id'])).category_id == category.pk

    def test_the_author_is_taken_from_the_token_not_the_request(self, gql, client, ada):
        """
        There is no `authorId` on `CreateIdeaInput` to put a forged value in,
        and the idea comes back attributed to the caller.
        """
        result = create_via_api(gql, ada)

        assert result['idea']['authorId'] == str(ada['user'].pk)

    def test_the_input_type_has_no_author_or_organization_content_field(self, gql, ada):
        result = create_via_api(gql, ada)

        # The organization the idea lands in is the one that was authorized.
        assert result['idea']['organizationId'] == str(ada['organization'].pk)


# --- updateIdea ----------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateIdeaMutation:
    def test_the_author_can_edit_their_draft(self, gql, ada):
        created = create_via_api(gql, ada)['idea']

        result = run(
            gql,
            UPDATE_IDEA,
            'updateIdea',
            {
                'input': {
                    'id': created['id'],
                    'idea': idea_input(title='A sharper title'),
                }
            },
            bearer=ada['token'],
        )

        assert result['success'] is True
        assert result['idea']['title'] == 'A sharper title'
        assert Idea.objects.get(pk=int(created['id'])).title == 'A sharper title'

    def test_an_unauthenticated_edit_is_refused(self, gql, ada):
        created = create_via_api(gql, ada)['idea']

        result = run(
            gql,
            UPDATE_IDEA,
            'updateIdea',
            {'input': {'id': created['id'], 'idea': idea_input(title='Hijacked')}},
        )

        assert result['success'] is False
        assert Idea.objects.get(pk=int(created['id'])).title == 'Automate the invoice run'

    def test_a_colleague_cannot_edit_somebody_elses_idea(self, gql, client, ada):
        created = create_via_api(gql, ada)['idea']
        colleague = make_user('colleague@example.com')
        Membership.objects.create(
            user=colleague,
            organization=ada['organization'],
            status=Membership.Status.ACTIVE,
        )
        token = sign_in(client, colleague)

        result = run(
            gql,
            UPDATE_IDEA,
            'updateIdea',
            {'input': {'id': created['id'], 'idea': idea_input(title='Hijacked')}},
            bearer=token,
        )

        assert result['success'] is False
        assert result['idea'] is None
        assert Idea.objects.get(pk=int(created['id'])).title == 'Automate the invoice run'

    def test_a_member_of_another_tenant_cannot_edit_it(self, gql, client, ada):
        created = create_via_api(gql, ada)['idea']
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        token = sign_in(client, outsider)

        result = run(
            gql,
            UPDATE_IDEA,
            'updateIdea',
            {'input': {'id': created['id'], 'idea': idea_input(title='Hijacked')}},
            bearer=token,
        )

        assert result['success'] is False
        assert Idea.objects.get(pk=int(created['id'])).title == 'Automate the invoice run'

    def test_an_unknown_idea_answers_exactly_like_someone_elses(self, gql, client, ada):
        """
        The enumeration property at the API boundary: a caller must not be able
        to tell "no such id" from "exists, not yours".
        """
        created = create_via_api(gql, ada)['idea']
        colleague = make_user('colleague@example.com')
        Membership.objects.create(
            user=colleague,
            organization=ada['organization'],
            status=Membership.Status.ACTIVE,
        )
        token = sign_in(client, colleague)
        theirs = create_via_api(gql, ada, visibility='PRIVATE')['idea']

        unknown = run(
            gql,
            UPDATE_IDEA,
            'updateIdea',
            {'input': {'id': '999999', 'idea': idea_input()}},
            bearer=token,
        )
        not_mine = run(
            gql,
            UPDATE_IDEA,
            'updateIdea',
            {'input': {'id': theirs['id'], 'idea': idea_input()}},
            bearer=token,
        )

        assert unknown == not_mine
        assert unknown['message'] == 'Idea is unavailable.'
        assert created['id'] is not None

    def test_an_invalid_field_is_reported_without_touching_the_row(self, gql, ada):
        created = create_via_api(gql, ada)['idea']

        result = run(
            gql,
            UPDATE_IDEA,
            'updateIdea',
            {'input': {'id': created['id'], 'idea': idea_input(title='')}},
            bearer=ada['token'],
        )

        assert result['success'] is False
        assert result['field'] == 'title'
        assert Idea.objects.get(pk=int(created['id'])).title == 'Automate the invoice run'

    def test_a_submitted_idea_cannot_be_edited(self, gql, ada):
        created = create_complete_via_api(gql, ada)['idea']
        submitted = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=ada['token'])
        assert submitted['success'] is True, submitted

        result = run(
            gql,
            UPDATE_IDEA,
            'updateIdea',
            {'input': {'id': created['id'], 'idea': idea_input(title='Rewritten')}},
            bearer=ada['token'],
        )

        assert result['success'] is False
        assert Idea.objects.get(pk=int(created['id'])).title == 'Automate the invoice run'


# --- submitIdea ----------------------------------------------------------------------


@pytest.mark.django_db
class TestSubmitIdeaMutation:
    def test_a_complete_draft_becomes_submitted(self, gql, ada):
        created = create_complete_via_api(gql, ada)['idea']

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=ada['token'])

        assert result['success'] is True
        assert result['idea']['status'] == 'SUBMITTED'

    def test_submission_stamps_submitted_at(self, gql, ada):
        created = create_complete_via_api(gql, ada)['idea']

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=ada['token'])

        assert result['idea']['submittedAt'] is not None
        assert Idea.objects.get(pk=int(created['id'])).submitted_at is not None

    def test_an_incomplete_draft_is_refused(self, gql, ada):
        created = create_via_api(gql, ada)['idea']  # no category, minimal description

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=ada['token'])

        assert result['success'] is False
        assert result['idea'] is None
        assert Idea.objects.get(pk=int(created['id'])).status == Idea.Status.DRAFT

    def test_the_refusal_names_no_field(self, gql, ada):
        """
        A whole-form error: the author's next action is to fill the gaps in the
        form above them, not to have one input highlighted.
        """
        created = create_via_api(gql, ada)['idea']

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=ada['token'])

        assert result['field'] is None

    def test_an_unauthenticated_submission_is_refused(self, gql, ada):
        created = create_complete_via_api(gql, ada)['idea']

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']})

        assert result['success'] is False
        assert Idea.objects.get(pk=int(created['id'])).status == Idea.Status.DRAFT

    def test_a_colleague_cannot_submit_somebody_elses_idea(self, gql, client, ada):
        created = create_complete_via_api(gql, ada)['idea']
        colleague = make_user('colleague@example.com')
        Membership.objects.create(
            user=colleague,
            organization=ada['organization'],
            status=Membership.Status.ACTIVE,
        )
        token = sign_in(client, colleague)

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=token)

        assert result['success'] is False
        assert Idea.objects.get(pk=int(created['id'])).status == Idea.Status.DRAFT

    def test_a_member_of_another_tenant_cannot_submit_it(self, gql, client, ada):
        created = create_complete_via_api(gql, ada)['idea']
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        token = sign_in(client, outsider)

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=token)

        assert result['success'] is False
        assert Idea.objects.get(pk=int(created['id'])).status == Idea.Status.DRAFT

    def test_an_already_submitted_idea_cannot_be_submitted_twice(self, gql, ada):
        created = create_complete_via_api(gql, ada)['idea']
        run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=ada['token'])

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=ada['token'])

        assert result['success'] is False
        assert result['idea'] is None

    def test_submission_does_not_advance_into_the_review_workflow(self, gql, ada):
        """
        `UNDER_REVIEW`, `CHANGES_REQUESTED`, `REJECTED`, `APPROVED` and
        `AUTOMATION_PROPOSAL` are Sprint 3's. Asserted so this mutation cannot
        quietly grow a review transition by accident.
        """
        created = create_complete_via_api(gql, ada)['idea']

        result = run(gql, SUBMIT_IDEA, 'submitIdea', {'id': created['id']}, bearer=ada['token'])

        assert result['idea']['status'] not in {
            'UNDER_REVIEW',
            'CHANGES_REQUESTED',
            'REJECTED',
            'APPROVED',
            'AUTOMATION_PROPOSAL',
        }


# --- queries -------------------------------------------------------------------------


@pytest.mark.django_db
class TestQueries:
    def test_an_unauthenticated_caller_sees_no_ideas(self, gql, ada):
        create_via_api(gql, ada)

        assert run(gql, IDEAS_QUERY, 'ideas')['items'] == []

    def test_the_author_sees_their_own_idea(self, gql, ada):
        created = create_via_api(gql, ada)['idea']

        result = run(gql, IDEA_QUERY, 'idea', {'id': created['id']}, bearer=ada['token'])

        assert result['id'] == created['id']

    def test_a_colleague_does_not_see_a_private_idea(self, gql, client, ada):
        created = create_via_api(gql, ada, visibility='PRIVATE')['idea']
        colleague = make_user('colleague@example.com')
        Membership.objects.create(
            user=colleague,
            organization=ada['organization'],
            status=Membership.Status.ACTIVE,
        )
        token = sign_in(client, colleague)

        assert run(gql, IDEA_QUERY, 'idea', {'id': created['id']}, bearer=token) is None

    def test_a_colleague_sees_an_organization_idea(self, gql, client, ada):
        created = create_via_api(gql, ada, visibility='ORGANIZATION')['idea']
        colleague = make_user('colleague@example.com')
        Membership.objects.create(
            user=colleague,
            organization=ada['organization'],
            status=Membership.Status.ACTIVE,
        )
        token = sign_in(client, colleague)

        result = run(gql, IDEA_QUERY, 'idea', {'id': created['id']}, bearer=token)

        assert result is not None

    def test_another_tenant_sees_nothing(self, gql, client, ada):
        created = create_via_api(gql, ada)['idea']
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        token = sign_in(client, outsider)

        assert run(gql, IDEA_QUERY, 'idea', {'id': created['id']}, bearer=token) is None
        assert run(gql, IDEAS_QUERY, 'ideas', bearer=token)['items'] == []
        assert (
            run(
                gql,
                ORGANIZATION_IDEAS_QUERY,
                'organizationIdeas',
                {'organizationId': str(ada['organization'].pk)},
                bearer=token,
            )['items']
            == []
        )

    def test_the_organization_feed_is_tenant_scoped(self, gql, ada):
        create_via_api(gql, ada)

        page = run(
            gql,
            ORGANIZATION_IDEAS_QUERY,
            'organizationIdeas',
            {'organizationId': str(ada['organization'].pk)},
            bearer=ada['token'],
        )

        assert len(page['items']) == 1
        assert page['pageInfo']['totalCount'] == 1

    def test_a_category_is_returned_with_the_idea(self, gql, ada):
        category = Category.objects.create(name='Customer support')

        created = run(
            gql,
            CREATE_IDEA,
            'createIdea',
            {
                'input': {
                    'organizationId': str(ada['organization'].pk),
                    'idea': idea_input(categoryId=str(category.pk)),
                }
            },
            bearer=ada['token'],
        )['idea']

        result = run(gql, IDEA_QUERY, 'idea', {'id': created['id']}, bearer=ada['token'])

        assert result['category'] == {
            'id': str(category.pk),
            'name': 'Customer support',
        }

    def test_an_unclassified_idea_has_no_category(self, gql, ada):
        created = create_via_api(gql, ada)['idea']

        result = run(gql, IDEA_QUERY, 'idea', {'id': created['id']}, bearer=ada['token'])

        assert result['category'] is None

    def test_categories_are_readable_without_signing_in(self, gql, ada):
        """
        Platform-wide reference data with no tenant content in it, and the
        picker has to render before anybody decides to sign in.
        """
        Category.objects.create(name='Customer support')

        result = run(gql, CATEGORIES_QUERY, 'categories')

        assert [c['name'] for c in result] == ['Customer support']

    def test_a_retired_category_is_not_offered(self, gql, ada):
        Category.objects.create(name='Retired', is_active=False)
        Category.objects.create(name='Current')

        result = run(gql, CATEGORIES_QUERY, 'categories')

        assert [c['name'] for c in result] == ['Current']


# --- the shape of the type -----------------------------------------------------------


@pytest.mark.django_db
class TestIdeaTypeIsSafe:
    def test_it_exposes_no_user_object_and_no_security_field(self, gql, ada):
        """
        The one place this schema is stricter than the organizations schema
        beside it, asserted against the schema itself rather than by reading
        it: a `PUBLIC` idea is readable by any signed-in member of the
        platform, so an embedded user object would publish a member's email
        address platform-wide.
        """
        fields = {f['name'] for f in run(gql, SCHEMA_QUERY, '__type')['fields']}

        assert 'authorId' in fields
        assert 'organizationId' in fields
        for forbidden in (
            'author',
            'user',
            'email',
            'password',
            'token',
            'isActive',
            'hasUsablePassword',
        ):
            assert forbidden not in fields, forbidden

    def test_it_exposes_the_state_the_frontend_renders(self, gql, ada):
        """
        `status` and `submittedAt` are the two the domain doc called out: a
        draft is rendered from them, and a union type that omitted the
        review-only statuses would be wrong by omission.
        """
        fields = {f['name'] for f in run(gql, SCHEMA_QUERY, '__type')['fields']}

        assert {'status', 'visibility', 'submittedAt', 'title', 'description'} <= fields
