"""
Attachment upload validation (S2-007): what file this domain will accept.

Kept apart from `ideas/services.py` for the same reason `ideas/lifecycle.py`
is: this is one cohesive rule set (which files are supported evidence, and
how that is checked) rather than a step inside a bigger operation, and a
future change to *what* is accepted should not have to be found inside a
function whose name is about authorization.

Two independent checks, both required, because they defend against different
lies:

- **The extension**, taken from the display filename *after* it has been
  reduced to a bare name (see `safe_display_filename`) - checked against an
  explicit allow-list. Everything not listed is refused; there is no
  denylist of "known-bad" extensions to keep up to date, because an allow-
  list cannot be bypassed by a extension nobody thought to ban yet.
- **The file's own leading bytes**, checked against the signature the
  extension claims. A client can rename anything to `evidence.pdf`; it
  cannot rewrite the first four bytes of a real PDF into something else
  without the file stopping being a PDF. This is "do not trust only the
  browser-provided MIME type" - the browser's `Content-Type` header is not
  read as data at all here (see `ideas/views.py`); the type this domain
  records is always derived from the extension and confirmed against the
  bytes, never from what the client claims.

`Attachment.content_type` therefore always ends up as one of the values on
the right of `ALLOWED_ATTACHMENT_TYPES`, whatever the uploading browser sent.
"""

from pathlib import PurePosixPath

from django.conf import settings

# Extension -> the content type this domain records for it. Deliberately an
# allow-list and nothing else: a file whose extension is not a key here is
# refused before its bytes are even read, so "supported evidence" has exactly
# one definition, in one place.
#
# `.csv` and `.txt` have no reliable magic-byte signature (arbitrary text is
# a valid file of either), so they are the two extensions with no entry in
# `_MAGIC_SIGNATURES` below and are accepted on extension + the
# executable-signature denylist alone.
ALLOWED_ATTACHMENT_TYPES: dict[str, str] = {
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.csv': 'text/csv',
    '.txt': 'text/plain',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}

# The legacy OLE compound-file signature both `.doc` and `.xls` share -
# Microsoft's pre-2007 binary formats are not distinguishable from their
# first bytes alone, so both extensions are checked against it.
_OLE_SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
# `.docx`/`.xlsx` are ZIP containers under a different name; the ZIP local
# file header is what a genuine Office Open XML document actually starts with.
_ZIP_SIGNATURE = b'PK\x03\x04'

_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    '.pdf': (b'%PDF',),
    '.png': (b'\x89PNG\r\n\x1a\n',),
    '.jpg': (b'\xff\xd8\xff',),
    '.jpeg': (b'\xff\xd8\xff',),
    '.gif': (b'GIF87a', b'GIF89a'),
    '.doc': (_OLE_SIGNATURE,),
    '.xls': (_OLE_SIGNATURE,),
    '.docx': (_ZIP_SIGNATURE,),
    '.xlsx': (_ZIP_SIGNATURE,),
    # '.webp' is checked separately below: RIFF containers share one 4-byte
    # signature across several formats, and WEBP is only confirmed by the
    # 4 bytes at offset 8, not the leading 4 alone.
}

# Checked against *every* upload's leading bytes, regardless of the extension
# claimed - defense in depth for a renamed executable, script or bundle that
# would otherwise only be caught if its real extension happened to be
# missing from the allow-list above. `#!` is a Unix shebang (any
# interpreted script); the rest are compiled-binary container formats.
_DANGEROUS_SIGNATURES: tuple[bytes, ...] = (
    b'MZ',  # Windows PE: .exe, .dll, .msi's embedded payload
    b'\x7fELF',  # Linux/Unix ELF binary
    b'#!',  # a script with a shebang line
    b'\xca\xfe\xba\xbe',  # Mach-O fat binary / Java .class
    b'\xfe\xed\xfa',  # Mach-O (32/64-bit, either byte order start)
)

# How many leading bytes are enough to check every signature above. The
# longest is 8 (`_OLE_SIGNATURE`); rounded up for headroom.
SNIFF_BYTES = 16

MAX_DISPLAY_FILENAME_LENGTH = 255


class AttachmentValidationError(Exception):
    """
    Raised for any file this module refuses.

    Carries the same `(message, field)` shape `ideas.services.IdeaError`
    does - `ideas.services.upload_attachment` catches this and re-raises it
    as an `IdeaError`, so the GraphQL/HTTP boundary has exactly one failure
    shape to render regardless of which layer refused the file.
    """

    def __init__(self, message: str, field: str = 'file'):
        self.message = message
        self.field = field
        super().__init__(message)


def safe_display_filename(raw_name: str) -> str:
    """
    The name to store and show for this file, or a refusal.

    **This is the path-traversal defense for the display name.** Whatever a
    client sends - `../../etc/passwd`, `C:\\Windows\\System32\\x.dll`, a bare
    `..` - is reduced to its last path segment on both separator styles, so
    there is no directory component left in the value this domain ever
    stores or renders. It is stored purely for display (`Attachment.filename`)
    and is never used to build a filesystem path or a storage key - see
    `ideas.storage.generate_storage_key`, which is server-generated and does
    not read this value at all. Belt and suspenders: even if this function's
    stripping were somehow bypassed, nothing downstream would use the result
    as a path.
    """
    name = (raw_name or '').strip()
    if not name:
        raise AttachmentValidationError('Choose a file to attach.')

    # Normalize both separator styles before taking the last segment, so a
    # Windows-style path from a client on that platform is handled exactly
    # like a POSIX one.
    name = name.replace('\\', '/').rsplit('/', 1)[-1]

    if name in ('', '.', '..'):
        raise AttachmentValidationError('That is not a usable filename.')
    if len(name) > MAX_DISPLAY_FILENAME_LENGTH:
        raise AttachmentValidationError('The filename is too long.')
    if any(ord(character) < 32 for character in name):
        raise AttachmentValidationError('The filename contains characters that are not allowed.')

    return name


def resolve_extension(display_filename: str) -> str:
    """
    The lower-cased extension of an already-sanitized display filename, or a
    refusal if it names a file type this domain does not accept.

    The allow-list check happens here, once, so every caller - upload
    validation and storage-key generation alike - agrees on the same set of
    acceptable extensions.
    """
    extension = PurePosixPath(display_filename).suffix.lower()
    if extension not in ALLOWED_ATTACHMENT_TYPES:
        raise AttachmentValidationError(
            'That file type is not supported. Supported types: PDF, images (PNG, JPEG, GIF, '
            'WebP), spreadsheets (CSV, XLS, XLSX) and documents (TXT, DOC, DOCX).'
        )
    return extension


def canonical_content_type(extension: str) -> str:
    """
    The content type this domain records for `extension`, never the
    browser's own claim - see the module docstring.
    """
    return ALLOWED_ATTACHMENT_TYPES[extension]


def validate_size(size: int) -> None:
    """
    Refuse an empty or oversized file.

    `size` is the uploaded file's own reported size (`UploadedFile.size`),
    which Django has already determined by measuring the request body - not
    a value read from a client-supplied header - so this is a check on a
    fact, not on an assertion. Enforced again by
    `ideas.services.upload_attachment` reading no more than the limit either
    way; this function is what decides the number.
    """
    if size <= 0:
        raise AttachmentValidationError('The file is empty.')

    max_bytes = settings.ATTACHMENT_MAX_UPLOAD_BYTES
    if size > max_bytes:
        max_megabytes = max_bytes // (1024 * 1024)
        raise AttachmentValidationError(f'The file must be {max_megabytes} MB or smaller.')


def validate_content(extension: str, head: bytes) -> None:
    """
    Refuse a file whose own leading bytes do not back up `extension`.

    `head` is the first `SNIFF_BYTES` of the *actual* upload, read by the
    caller before it is handed to storage - never the filename, never a
    client-supplied `Content-Type`. See the module docstring for why this
    check exists alongside the extension allow-list rather than instead of
    it.
    """
    for signature in _DANGEROUS_SIGNATURES:
        if head.startswith(signature):
            raise AttachmentValidationError('That file could not be verified as a safe upload.')

    if extension == '.webp':
        if not (head[:4] == b'RIFF' and head[8:12] == b'WEBP'):
            raise AttachmentValidationError('The file contents do not match its extension.')
        return

    expected = _MAGIC_SIGNATURES.get(extension)
    if expected is not None and not any(head.startswith(signature) for signature in expected):
        raise AttachmentValidationError('The file contents do not match its extension.')
