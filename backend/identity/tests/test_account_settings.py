"""Profile update and password change for the signed-in user."""

import json

import pytest
from django.test import Client

from identity.models import RefreshSession, User
from identity.schema import REFRESH_TOKEN_COOKIE_NAME

PASSWORD = 'a-strong-unique-pass-1'
NEW_PASSWORD = 'an-even-stronger-pass-2'

LOGIN = 'mutation($i: LoginInput!){ login(input: $i){ success accessToken } }'
UPDATE_PROFILE = """
mutation($i: UpdateProfileInput!){
  updateProfile(input: $i){ success message field user { firstName lastName phoneNumber } }
}
"""
CHANGE_PASSWORD = """
mutation($i: ChangePasswordInput!){ changePassword(input: $i){ success message field } }
"""


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='ada@example.com',
        password=PASSWORD,
        first_name='Ada',
        last_name='Lovelace',
        phone_number='+255712345678',
    )


def post(client, query, variables, token=None):
    headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
    response = client.post(
        '/graphql/',
        data=json.dumps({'query': query, 'variables': variables}),
        content_type='application/json',
        **headers,
    )
    body = response.json()
    assert 'errors' not in body, body
    return body['data']


def sign_in(client):
    data = post(client, LOGIN, {'i': {'email': 'ada@example.com', 'password': PASSWORD}})
    return data['login']['accessToken']


def test_update_profile_saves_the_new_details(user):
    client = Client()
    token = sign_in(client)
    data = post(
        client,
        UPDATE_PROFILE,
        {'i': {'firstName': ' Grace ', 'lastName': 'Hopper', 'phoneNumber': '+255700000000'}},
        token,
    )['updateProfile']
    assert data['success'] is True
    user.refresh_from_db()
    assert (user.first_name, user.last_name, user.phone_number) == (
        'Grace',
        'Hopper',
        '+255700000000',
    )


def test_update_profile_names_the_bad_field(user):
    client = Client()
    token = sign_in(client)
    data = post(
        client,
        UPDATE_PROFILE,
        {'i': {'firstName': 'Grace', 'lastName': 'Hopper', 'phoneNumber': 'nope'}},
        token,
    )['updateProfile']
    assert data['success'] is False
    assert data['field'] == 'phoneNumber'


def test_account_mutations_need_a_session(user):
    client = Client()
    assert (
        post(
            client,
            UPDATE_PROFILE,
            {'i': {'firstName': 'A', 'lastName': 'B', 'phoneNumber': '+255700000000'}},
        )['updateProfile']['success']
        is False
    )
    assert (
        post(
            client,
            CHANGE_PASSWORD,
            {'i': {'currentPassword': PASSWORD, 'newPassword': NEW_PASSWORD}},
        )['changePassword']['success']
        is False
    )


def test_change_password_needs_the_current_password(user):
    client = Client()
    token = sign_in(client)
    data = post(
        client,
        CHANGE_PASSWORD,
        {'i': {'currentPassword': 'wrong', 'newPassword': NEW_PASSWORD}},
        token,
    )['changePassword']
    assert data['success'] is False
    assert data['field'] == 'currentPassword'
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_change_password_applies_the_password_rules(user):
    client = Client()
    token = sign_in(client)
    data = post(
        client,
        CHANGE_PASSWORD,
        {'i': {'currentPassword': PASSWORD, 'newPassword': '123'}},
        token,
    )['changePassword']
    assert data['success'] is False
    assert data['field'] == 'newPassword'


def test_change_password_signs_out_other_devices_but_not_this_one(user):
    this_device = Client()
    token = sign_in(this_device)
    other_device = Client()
    sign_in(other_device)
    assert RefreshSession.objects.filter(user=user, revoked_at__isnull=True).count() == 2

    data = post(
        this_device,
        CHANGE_PASSWORD,
        {'i': {'currentPassword': PASSWORD, 'newPassword': NEW_PASSWORD}},
        token,
    )['changePassword']

    assert data['success'] is True
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    live = RefreshSession.objects.filter(user=user, revoked_at__isnull=True)
    assert live.count() == 1
    assert REFRESH_TOKEN_COOKIE_NAME in this_device.cookies


# --- profile photo ----------------------------------------------------------------

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


@pytest.fixture
def media(settings, tmp_path):
    settings.STORAGES = {
        **settings.STORAGES,
        'attachments': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
            'OPTIONS': {'location': str(tmp_path)},
        },
    }


def upload(client, token, data, name='me.png'):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        '/account/avatar/',
        {'file': SimpleUploadedFile(name, data)},
        HTTP_AUTHORIZATION=f'Bearer {token}',
    )


def test_a_photo_can_be_uploaded_served_and_removed(user, media):
    client = Client()
    token = sign_in(client)

    response = upload(client, token, PNG)
    assert response.status_code == 200, response.content
    url = response.json()['user']['avatarUrl']
    assert url.startswith(f'/users/{user.pk}/avatar/')

    served = client.get(url, HTTP_AUTHORIZATION=f'Bearer {token}')
    assert served.status_code == 200
    assert served['Content-Type'] == 'image/png'
    assert b''.join(served.streaming_content) == PNG

    removed = client.delete('/account/avatar/', HTTP_AUTHORIZATION=f'Bearer {token}')
    assert removed.json()['user']['avatarUrl'] is None
    assert client.get(url, HTTP_AUTHORIZATION=f'Bearer {token}').status_code == 404


def test_a_disguised_file_is_refused(user, media):
    client = Client()
    token = sign_in(client)
    response = upload(client, token, b'<svg onload="alert(1)"/>', name='me.png')
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.avatar_key == ''


def test_an_oversized_photo_is_refused(user, media, settings):
    settings.AVATAR_MAX_UPLOAD_BYTES = 1024
    client = Client()
    token = sign_in(client)
    assert upload(client, token, PNG + b'\x00' * 1024).status_code == 400


def test_the_default_limits_allow_100_mb_photos_and_200_mb_attachments(settings):
    assert settings.AVATAR_MAX_UPLOAD_BYTES == 100 * 1024 * 1024
    assert settings.ATTACHMENT_MAX_UPLOAD_BYTES == 200 * 1024 * 1024


def test_photos_need_a_session(user, media):
    client = Client()
    assert upload(client, 'not-a-token', PNG).status_code == 401
    assert client.get(f'/users/{user.pk}/avatar/').status_code == 404
