"""
Profile photos.

The bytes go through the same pluggable storage the idea attachments use
(`STORAGES['attachments']`), under an `avatars/<user id>/` prefix, so a
deployment that moves attachments to object storage moves these too. The
storage key is always server-generated - never derived from a filename - and
the type is decided from the file's own leading bytes, not its name or the
client's `Content-Type`. Only JPEG, PNG and WebP are accepted: no SVG, which
can carry script.
"""

import hashlib
import logging
import uuid

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import storages

from identity.models import User

logger = logging.getLogger(__name__)


_TYPES = (
    ('.jpg', 'image/jpeg'),
    ('.png', 'image/png'),
    ('.webp', 'image/webp'),
)
CONTENT_TYPE_BY_EXTENSION = dict(_TYPES)


class AvatarError(Exception):
    """A refused photo; the message is safe to show verbatim."""


def detect_extension(head: bytes) -> str | None:
    if head.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    return None


def avatar_url(user: User) -> str | None:
    """The URL the photo is served from, or None. `v` changes with each new photo."""
    if not user.avatar_key:
        return None
    version = hashlib.sha256(user.avatar_key.encode()).hexdigest()[:10]
    return f'/users/{user.pk}/avatar/?v={version}'


def set_avatar(user: User, uploaded) -> User:
    """Validate and store `uploaded` as the user's photo, replacing any previous one."""
    if uploaded.size == 0:
        raise AvatarError('That file is empty.')
    max_bytes = settings.AVATAR_MAX_UPLOAD_BYTES
    if uploaded.size > max_bytes:
        raise AvatarError(f'The photo must be {max_bytes // (1024 * 1024)} MB or smaller.')

    head = uploaded.read(16)
    uploaded.seek(0)
    extension = detect_extension(head)
    if extension is None:
        raise AvatarError('Choose a JPEG, PNG or WebP image.')

    storage = storages['attachments']
    key = f'avatars/{user.pk}/{uuid.uuid4().hex}{extension}'
    try:
        stored_key = storage.save(key, File(uploaded))
    except OSError:
        logger.exception('Could not store avatar for user %s', user.pk)
        raise AvatarError('We could not save your photo. Please try again.') from None

    previous = user.avatar_key
    user.avatar_key = stored_key
    user.save(update_fields=['avatar_key', 'updated_at'])
    _delete(previous)
    return user


def remove_avatar(user: User) -> User:
    previous = user.avatar_key
    user.avatar_key = ''
    user.save(update_fields=['avatar_key', 'updated_at'])
    _delete(previous)
    return user


def _delete(key: str) -> None:
    if not key:
        return
    try:
        storage = storages['attachments']
        if storage.exists(key):
            storage.delete(key)
    except OSError:
        logger.warning('Could not delete avatar object %r', key, exc_info=True)
