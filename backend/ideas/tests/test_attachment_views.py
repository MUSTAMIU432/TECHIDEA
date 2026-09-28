"""
The attachment HTTP endpoints (S2-007): upload and download.

These exist for exactly one reason - binary content is not something GraphQL
carries - and this suite's job is to check that the HTTP boundary applies
the same authorization `ideas/services.py` and `ideas/selectors.py` already
enforce, not a second, looser copy of it.
"""

import json

import pytest
from django.core.files.storage import storages
from django.test import Client

from ideas.models import Attachment, Idea
from identity.models import User
from organizations.models import Membership

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'

PDF_BYTES = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer\n<< >>\n%%EOF'


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


def auth_headers(token):
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}


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


@pytest.fixture(autouse=True)
def _isolated_attachment_storage(tmp_path, settings):
    settings.STORAGES = {
        **settings.STORAGES,
        'attachments': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
            'OPTIONS': {'location': str(tmp_path)},
        },
    }
    storages._storages.clear()
    yield
    storages._storages.clear()


def upload_pdf(client, idea_id, token, *, filename='evidence.pdf', content=PDF_BYTES):
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload = SimpleUploadedFile(filename, content, content_type='application/pdf')
    return client.post(
        f'/ideas/{idea_id}/attachments/', data={'file': upload}, **auth_headers(token)
    )


def download(client, idea_id, attachment_id, token):
    return client.get(
        f'/ideas/{idea_id}/attachments/{attachment_id}/download/', **auth_headers(token)
    )


# --- upload ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUploadView:
    def test_the_author_can_upload_a_file(self, client, world):
        idea = make_idea(world['organization'], world['author'])

        response = upload_pdf(client, idea.pk, world['author_token'])

        assert response.status_code == 201
        body = response.json()
        assert body['success'] is True
        assert body['attachment']['filename'] == 'evidence.pdf'
        assert Attachment.objects.filter(idea=idea).count() == 1

    def test_a_get_request_is_not_allowed(self, client, world):
        idea = make_idea(world['organization'], world['author'])

        response = client.get(
            f'/ideas/{idea.pk}/attachments/', **auth_headers(world['author_token'])
        )

        assert response.status_code == 405

    def test_a_colleague_cannot_upload_to_someone_elses_idea(self, client, world):
        idea = make_idea(world['organization'], world['author'])

        response = upload_pdf(client, idea.pk, world['colleague_token'])

        assert response.status_code in (403, 404)
        assert response.json()['success'] is False
        assert Attachment.objects.count() == 0

    def test_another_tenant_cannot_upload(self, client, world):
        idea = make_idea(world['other'], world['outsider'])

        response = upload_pdf(client, idea.pk, world['author_token'])

        assert response.json()['success'] is False
        assert Attachment.objects.count() == 0

    def test_an_unauthenticated_request_is_refused(self, client, world):
        idea = make_idea(world['organization'], world['author'])

        response = upload_pdf(client, idea.pk, token=None)

        assert response.status_code == 401
        assert Attachment.objects.count() == 0

    def test_a_missing_file_field_is_refused(self, client, world):
        idea = make_idea(world['organization'], world['author'])

        response = client.post(
            f'/ideas/{idea.pk}/attachments/', data={}, **auth_headers(world['author_token'])
        )

        assert response.status_code == 400
        assert response.json()['field'] == 'file'

    def test_a_disallowed_file_type_is_refused(self, client, world):
        idea = make_idea(world['organization'], world['author'])

        response = upload_pdf(
            client,
            idea.pk,
            world['author_token'],
            filename='script.exe',
            content=b'MZ' + b'\x00' * 32,
        )

        assert response.json()['success'] is False
        assert Attachment.objects.count() == 0

    def test_an_oversized_file_is_refused(self, client, world, settings):
        settings.ATTACHMENT_MAX_UPLOAD_BYTES = 16
        idea = make_idea(world['organization'], world['author'])

        response = upload_pdf(client, idea.pk, world['author_token'])

        assert response.json()['success'] is False
        assert Attachment.objects.count() == 0

    def test_a_path_traversal_filename_is_sanitized_not_written_outside_storage(
        self, client, world
    ):
        idea = make_idea(world['organization'], world['author'])

        response = upload_pdf(
            client, idea.pk, world['author_token'], filename='../../../etc/passwd.pdf'
        )

        assert response.status_code == 201
        attachment = Attachment.objects.get(idea=idea)
        assert '..' not in attachment.storage_key
        assert '/' not in attachment.filename.replace('passwd.pdf', '')  # no directory left

    def test_an_unusable_idea_id_in_the_url_is_not_found(self, client, world):
        response = client.post(
            '/ideas/not-a-number/attachments/', **auth_headers(world['author_token'])
        )

        assert response.status_code == 404

    def test_a_second_upload_does_not_overwrite_the_first(self, client, world):
        idea = make_idea(world['organization'], world['author'])

        first = upload_pdf(client, idea.pk, world['author_token'])
        second = upload_pdf(client, idea.pk, world['author_token'])

        assert first.status_code == second.status_code == 201
        assert Attachment.objects.filter(idea=idea).count() == 2
        keys = list(Attachment.objects.filter(idea=idea).values_list('storage_key', flat=True))
        assert keys[0] != keys[1]


# --- download ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDownloadView:
    def test_the_uploader_can_download_their_own_attachment(self, client, world):
        idea = make_idea(world['organization'], world['author'])
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = download(client, idea.pk, attachment_id, world['author_token'])

        assert response.status_code == 200
        assert b''.join(response.streaming_content) == PDF_BYTES
        assert response['Content-Type'] == 'application/pdf'
        assert 'attachment' in response['Content-Disposition']
        assert 'evidence.pdf' in response['Content-Disposition']

    def test_a_colleague_who_can_read_the_idea_can_download_its_evidence(self, client, world):
        """
        Download follows *readability*, unlike upload/delete which require
        authorship - see `ideas/services.py::_load_attachable_idea`'s
        docstring for why the two rules differ.
        """
        idea = make_idea(world['organization'], world['author'])
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = download(client, idea.pk, attachment_id, world['colleague_token'])

        assert response.status_code == 200

    def test_an_unreadable_ideas_attachment_cannot_be_downloaded(self, client, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = download(client, idea.pk, attachment_id, world['colleague_token'])

        assert response.status_code == 404

    def test_another_tenant_cannot_download(self, client, world):
        idea = make_idea(world['organization'], world['author'])
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = download(client, idea.pk, attachment_id, world['outsider_token'])

        assert response.status_code == 404

    def test_an_unauthenticated_request_is_refused(self, client, world):
        idea = make_idea(world['organization'], world['author'])
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = download(client, idea.pk, attachment_id, token=None)

        assert response.status_code == 404

    def test_an_unknown_attachment_id_is_not_found(self, client, world):
        idea = make_idea(world['organization'], world['author'])

        response = download(client, idea.pk, 999999, world['author_token'])

        assert response.status_code == 404

    def test_an_attachment_belonging_to_a_different_idea_is_not_found_at_this_url(
        self, client, world
    ):
        """
        The two ids in the URL must agree - see
        `selectors.get_idea_attachment`'s docstring.
        """
        idea_one = make_idea(world['organization'], world['author'], title='One')
        idea_two = make_idea(world['organization'], world['author'], title='Two')
        upload_response = upload_pdf(client, idea_one.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = download(client, idea_two.pk, attachment_id, world['author_token'])

        assert response.status_code == 404

    def test_a_missing_storage_object_is_a_404_not_a_crash(self, client, world):
        idea = make_idea(world['organization'], world['author'])
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']
        attachment = Attachment.objects.get(pk=attachment_id)
        storages['attachments'].delete(attachment.storage_key)

        response = download(client, idea.pk, attachment_id, world['author_token'])

        assert response.status_code == 404

    def test_the_content_disposition_is_never_inline(self, client, world):
        """
        Never `inline`, whatever the content type - an uploaded file, image
        included, is never trusted content this app renders in its own
        origin.
        """
        idea = make_idea(world['organization'], world['author'])
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = download(client, idea.pk, attachment_id, world['author_token'])

        assert 'inline' not in response['Content-Disposition']

    def test_a_post_request_is_not_allowed(self, client, world):
        idea = make_idea(world['organization'], world['author'])
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = client.post(
            f'/ideas/{idea.pk}/attachments/{attachment_id}/download/',
            **auth_headers(world['author_token']),
        )

        assert response.status_code == 405

    def test_x_content_type_options_is_set(self, client, world):
        idea = make_idea(world['organization'], world['author'])
        upload_response = upload_pdf(client, idea.pk, world['author_token'])
        attachment_id = upload_response.json()['attachment']['id']

        response = download(client, idea.pk, attachment_id, world['author_token'])

        assert response['X-Content-Type-Options'] == 'nosniff'
