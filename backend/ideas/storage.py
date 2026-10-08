"""
Attachment binary storage (S2-007).

The only module that touches `django.core.files.storage` for attachments.
Everything else - `ideas/services.py`, `ideas/views.py` - calls the four
functions below and never constructs a storage backend, opens a path, or
knows whether the bytes are sitting on the local disk or in an object-storage
bucket. That indirection is the whole point: `config/settings/base.py`'s
`STORAGES['attachments']` entry names the backend class, and swapping from
the filesystem to an object-storage driver (once one is actually installed
and configured) is a settings change, not a change to this module or to
anything that calls it.

**The storage key is never a value a caller supplies.** Every key this module
hands out comes from `generate_storage_key`, which builds it from the idea's
own id and a random token - never from a client-provided filename. That is
what makes path traversal structurally impossible here rather than merely
validated against: there is no code path in this module that turns
attacker-controlled text into a path segment.

**Database is the source of truth for existence.** `Attachment.storage_key`
in PostgreSQL is what says an attachment exists; the object behind it is a
consequence of that row, not a second copy of the same fact. `delete_object`
is therefore best-effort from this module's point of view - see its
docstring and `ideas.services.delete_attachment` for the ordering that makes
that safe.
"""

import logging
import uuid

from django.core.files.base import File
from django.core.files.storage import Storage, storages

logger = logging.getLogger(__name__)


class AttachmentStorageError(Exception):
    """
    Raised when the storage backend itself fails - a disk full, a bucket
    unreachable, a permissions error. Distinct from `FileNotFoundError`
    (a normal, expected outcome for a key that was never written or was
    already removed) so callers can tell "this object never existed" apart
    from "the storage layer is broken right now".
    """


def attachment_storage() -> Storage:
    """The configured backend for attachment bytes. See the module docstring."""
    return storages['attachments']


def generate_storage_key(idea_id: int, extension: str) -> str:
    """
    A fresh, server-chosen storage key for one file belonging to `idea_id`.

    `uuid4` gives 122 bits of randomness - long enough that two calls never
    collide in practice - and the key is namespaced by idea id so that
    listing or clearing one idea's objects (a bulk export, a future admin
    tool) never has to scan the whole bucket. `extension` is the caller's own
    validated extension (see `ideas.attachments.resolve_extension`), included
    so the object's name on disk/in the bucket carries a sensible suffix for
    tooling that inspects it directly - it plays no role in this project's own
    content-type handling, which always reads `Attachment.content_type` from
    the database instead.

    Never built from a client-supplied filename. That is the actual
    path-traversal defense: there is nothing here for `../../etc/passwd` to
    reach, because the filename never enters this function at all.
    """
    token = uuid.uuid4().hex
    return f'ideas/{idea_id}/{token}{extension}'


def save_object(key: str, file: File) -> str:
    """
    Write `file`'s contents to storage under `key`.

    Returns the name the backend actually stored it under. Ordinarily this
    equals `key`; Django's `Storage.save` only changes it if something is
    already there under that exact name (astronomically unlikely given
    `generate_storage_key`'s randomness, and guarded again by
    `Attachment.storage_key`'s own database uniqueness), in which case the
    backend appends a suffix rather than overwriting the existing object -
    so an accidental overwrite is prevented at this layer too, not only by
    the database constraint.
    """
    try:
        return attachment_storage().save(key, file)
    except OSError as exc:
        raise AttachmentStorageError(f'Could not store {key!r}: {exc}') from exc


def open_object(key: str):
    """
    Open the object at `key` for reading.

    Raises `FileNotFoundError` for a key nothing was ever written to, or that
    has since been removed - the ordinary shape of "this attachment's bytes
    are gone", which a caller (`ideas/views.py`) turns into a 404 rather than
    a 500. Any other storage failure is `AttachmentStorageError`.
    """
    try:
        return attachment_storage().open(key, 'rb')
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise AttachmentStorageError(f'Could not open {key!r}: {exc}') from exc


def delete_object(key: str) -> None:
    """
    Remove the object at `key`, if it is there.

    Best-effort and silent about an object that is already gone - deleting
    twice must not raise, since the database row (not this call) is what
    decides whether the attachment still exists. A genuine backend failure is
    logged rather than raised: `ideas.services.delete_attachment` calls this
    *after* the database row is already gone, deliberately, so that the
    database and the API caller agree the attachment no longer exists even if
    the underlying object outlives it as an orphan. An orphaned object is a
    storage-hygiene concern to clean up later; a deleted row that a raised
    exception left the caller unsure about would be a correctness one.
    """
    try:
        storage = attachment_storage()
        if storage.exists(key):
            storage.delete(key)
    except OSError:
        logger.warning('Could not delete attachment object %r from storage.', key, exc_info=True)
