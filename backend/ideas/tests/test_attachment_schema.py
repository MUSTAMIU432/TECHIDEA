"""
Attachments at the GraphQL boundary (S2-007).

`ideas/services.py` and `ideas/selectors.py` decide; these tests check that
the boundary neither widens nor narrows them, and that the one thing this
domain must never do - accept binary content through GraphQL - is actually
true of the shipped schema, not just of the intent.
"""

import json

import pytest
from django.test import Client

from ideas.models import Attachment, Idea
from identity.models import User
from organizations.models import Membership, MembershipRole, Role

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'

ATTACHMENT_FIELDS = 'id ideaId uploaderId filename contentType size createdAt downloadUrl'

ATTACHMENTS_QUERY = f"""
query Attachments($ideaId: ID!) {{
  attachments(ideaId: $ideaId) {{
    items {{ {ATTACHMENT_FIELDS} }}
    pageInfo {{ totalCount offset limit hasNextPage hasPreviousPage }}
  }}
}}
"""

ATTACHMENT_QUERY = f"""
query Attachment($id: ID!) {{
  attachment(id: $id) {{ {ATTACHMENT_FIELDS} }}
}}
"""

DELETE_ATTACHMENT = """
mutation DeleteAttachment($id: ID!) {
  deleteAttachment(id: $id) { success message field }
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


def fails(gql, query, root_field, variables=None, bearer=None):
    result = run(gql, query, root_field, variables, bearer)
    assert result['success'] is False, result
    return result


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
    membership = Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )
    role = Role.objects.create(organization=organization, name='Contributor', slug='contributor')
    MembershipRole.objects.create(membership=membership, role=role)
    return membership


def sign_in(client: Client, user: User) -> str:
    response = client.post(
        '/graphql/',
        data=json.dumps(
            {
                'query': """
mutation Login($input: LoginInput!) {
  login(input: $input) { success accessToken }
}""",
                'variables': {'input': {'email': user.email, 'password': VALID_PASSWORD}},
            }
        ),
        content_type='application/json',
    )
    payload = response.json()['data']['login']
    assert payload['success'] is True, payload
    return payload['accessToken']


def make_idea(organization, author, **overrides):
    fields = {
        'organization': organization,
        'author': author,
        'title': 'Automate the invoice run',
        'description': DESCRIPTION,
        'visibility': Idea.Visibility.ORGANIZATION,
    }
    fields.update(overrides)
    return Idea.objects.create(**fields)


def make_attachment(idea, uploaded_by, **overrides):
    fields = {
        'idea': idea,
        'uploaded_by': uploaded_by,
        'filename': 'evidence.pdf',
        'content_type': 'application/pdf',
        'size': 2048,
        'storage_key': f'ideas/{idea.pk}/{overrides.get("storage_key_suffix", "a")}.pdf',
    }
    fields.update({k: v for k, v in overrides.items() if k != 'storage_key_suffix'})
    return Attachment.objects.create(**fields)


@pytest.fixture
def world(client: Client):
    author = make_user('author@example.com')
    organization, _ = make_organization(owner=author)
    colleague = make_user('colleague@example.com')
    add_active_member(organization, colleague)

    outsider = make_user('outsider@example.com')
    other, _ = make_organization(name='Other Co', owner=outsider)

    return {
        'author': author,
        'colleague': colleague,
        'outsider': outsider,
        'organization': organization,
        'other': other,
        'author_token': sign_in(client, author),
        'colleague_token': sign_in(client, colleague),
        'outsider_token': sign_in(client, outsider),
    }


# --- listing --------------------------------------------------------------------------


@pytest.mark.django_db
class TestAttachmentsQuery:
    def test_a_reader_sees_the_ideas_attachments(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = make_attachment(idea, world['author'])

        result = run(
            gql,
            ATTACHMENTS_QUERY,
            'attachments',
            {'ideaId': str(idea.pk)},
            bearer=world['colleague_token'],
        )

        assert result['pageInfo']['totalCount'] == 1
        assert result['items'][0]['id'] == str(attachment.pk)
        assert result['items'][0]['filename'] == 'evidence.pdf'
        assert result['items'][0]['uploaderId'] == str(world['author'].pk)
        assert result['items'][0]['downloadUrl'] == (
            f'/ideas/{idea.pk}/attachments/{attachment.pk}/download/'
        )

    def test_an_unreadable_ideas_attachments_are_an_empty_page(self, gql, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        make_attachment(idea, world['author'])

        result = run(
            gql,
            ATTACHMENTS_QUERY,
            'attachments',
            {'ideaId': str(idea.pk)},
            bearer=world['colleague_token'],
        )

        assert result['items'] == []
        assert result['pageInfo']['totalCount'] == 0

    def test_another_tenants_attachments_are_not_listed(self, gql, world):
        idea = make_idea(world['other'], world['outsider'])
        make_attachment(idea, world['outsider'])

        result = run(
            gql,
            ATTACHMENTS_QUERY,
            'attachments',
            {'ideaId': str(idea.pk)},
            bearer=world['author_token'],
        )

        assert result['items'] == []

    def test_an_unauthenticated_caller_sees_nothing(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        make_attachment(idea, world['author'])

        result = run(gql, ATTACHMENTS_QUERY, 'attachments', {'ideaId': str(idea.pk)})

        assert result['items'] == []

    def test_no_storage_key_is_ever_exposed(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = make_attachment(idea, world['author'])

        result = run(
            gql,
            ATTACHMENTS_QUERY,
            'attachments',
            {'ideaId': str(idea.pk)},
            bearer=world['colleague_token'],
        )

        payload = json.dumps(result)
        assert attachment.storage_key not in payload
        assert 'storageKey' not in payload


# --- single attachment ------------------------------------------------------------------


@pytest.mark.django_db
class TestAttachmentQuery:
    def test_a_reader_can_fetch_one_attachment(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = make_attachment(idea, world['author'])

        result = run(
            gql,
            ATTACHMENT_QUERY,
            'attachment',
            {'id': str(attachment.pk)},
            bearer=world['colleague_token'],
        )

        assert result['id'] == str(attachment.pk)

    def test_an_unreadable_attachment_is_null_not_an_error(self, gql, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        attachment = make_attachment(idea, world['author'])

        result = run(
            gql,
            ATTACHMENT_QUERY,
            'attachment',
            {'id': str(attachment.pk)},
            bearer=world['colleague_token'],
        )

        assert result is None

    def test_an_unknown_id_answers_exactly_like_an_unreadable_one(self, gql, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        attachment = make_attachment(idea, world['author'])
        token = world['colleague_token']

        unknown = run(gql, ATTACHMENT_QUERY, 'attachment', {'id': '999999'}, bearer=token)
        invisible = run(
            gql, ATTACHMENT_QUERY, 'attachment', {'id': str(attachment.pk)}, bearer=token
        )

        assert unknown == invisible is None


# --- deleting ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteAttachmentMutation:
    def test_the_author_can_delete_their_own_attachment(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = make_attachment(idea, world['author'])

        result = run(
            gql,
            DELETE_ATTACHMENT,
            'deleteAttachment',
            {'id': str(attachment.pk)},
            bearer=world['author_token'],
        )

        assert result['success'] is True
        assert not Attachment.objects.filter(pk=attachment.pk).exists()

    def test_a_colleague_cannot_delete_it(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = make_attachment(idea, world['author'])

        result = fails(
            gql,
            DELETE_ATTACHMENT,
            'deleteAttachment',
            {'id': str(attachment.pk)},
            bearer=world['colleague_token'],
        )

        assert result['message'] == 'Attachment is unavailable.'
        assert Attachment.objects.filter(pk=attachment.pk).exists()

    def test_another_tenant_cannot_delete_it(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = make_attachment(idea, world['author'])

        fails(
            gql,
            DELETE_ATTACHMENT,
            'deleteAttachment',
            {'id': str(attachment.pk)},
            bearer=world['outsider_token'],
        )

        assert Attachment.objects.filter(pk=attachment.pk).exists()

    def test_an_unauthenticated_caller_cannot_delete(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = make_attachment(idea, world['author'])

        fails(gql, DELETE_ATTACHMENT, 'deleteAttachment', {'id': str(attachment.pk)})

        assert Attachment.objects.filter(pk=attachment.pk).exists()

    def test_deleting_twice_is_refused_the_second_time(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        attachment = make_attachment(idea, world['author'])
        token = world['author_token']

        run(gql, DELETE_ATTACHMENT, 'deleteAttachment', {'id': str(attachment.pk)}, bearer=token)
        result = fails(
            gql, DELETE_ATTACHMENT, 'deleteAttachment', {'id': str(attachment.pk)}, bearer=token
        )

        assert result['message'] == 'Attachment is unavailable.'


# --- what the schema does and does not expose --------------------------------------------


@pytest.mark.django_db
class TestSchemaSurface:
    def test_there_is_no_upload_mutation(self, world):
        """
        Binary content never travels through GraphQL - uploads go through
        `ideas/views.py`'s HTTP endpoint. There is deliberately no
        `addAttachment`/`createAttachment`/`uploadAttachment` mutation.
        """
        from graphql_api.schema import schema

        sdl = str(schema)
        for name in ('addAttachment', 'createAttachment', 'uploadAttachment'):
            assert name not in sdl, name

    def test_the_storage_key_is_never_a_field(self):
        from graphql_api.schema import schema

        sdl = str(schema)
        assert 'storageKey' not in sdl

    def test_attachments_and_attachment_and_delete_attachment_are_present(self):
        from graphql_api.schema import schema

        sdl = str(schema)
        assert 'attachments(ideaId: ID!' in sdl
        assert 'attachment(id: ID!): AttachmentType' in sdl
        assert 'deleteAttachment(id: ID!): DeleteAttachmentPayload!' in sdl
