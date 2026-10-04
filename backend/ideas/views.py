"""
HTTP endpoints for attachment binary transfer (S2-007).

GraphQL is the API for everything about an attachment *except* its bytes:
metadata (`AttachmentType`), listing (`attachments`), a single lookup
(`attachment`) and deletion (`deleteAttachment`) all go through
`ideas/schema.py`, exactly like every other Ideas operation. Binary content
never travels through GraphQL - a multipart file upload and a streamed
download are not something a JSON-in/JSON-out GraphQL mutation is shaped for,
and forcing one through it would mean base64-encoding a file into a string
inside a JSON payload, inflating it by a third for no benefit. So the two
views below are this domain's only HTTP surface beyond `/graphql/` and
`/health/`, and they exist for exactly this reason and no other: uploading
and downloading the bytes themselves.

Both authenticate exactly the way a GraphQL request does -
`identity.authentication.get_authenticated_user`, reading the same
`Authorization: Bearer <token>` header - so there is one authentication
mechanism for the whole API, not one per transport. Both then hand off
immediately to `ideas/services.py` and `ideas/selectors.py` for
authorization, validation and storage; nothing here re-implements any of
that.

CSRF is exempted on the upload view for the same reason `graphql_view` is
exempt (see `graphql_api/views.py`'s comment): the actual authorization
decision is made from the `Authorization` header, which a cross-site request
cannot forge or attach automatically the way a browser attaches a cookie. A
plain HTML form cannot set a custom header, so the classic CSRF vector does
not apply here either. The download view is a `GET` and needs no exemption -
Django's CSRF protection never applies to safe methods - but it is not
CSRF-vulnerable for the same reason: a cross-site `<img>` or `<a>` cannot
attach the bearer token, so a third-party page linking to a download URL gets
exactly the "not authenticated" 404 an anonymous request gets.
"""

from urllib.parse import quote

from django.core.files.uploadedfile import UploadedFile
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ideas import selectors, services
from ideas.models import Attachment
from ideas.storage import AttachmentStorageError, open_object
from identity.authentication import get_authenticated_user

# One failure shape for both views, matching the `(success, message, field)`
# triple every GraphQL payload in this domain uses - so a client handles a
# refused upload the same way whether it came from HTTP or from GraphQL.
_STATUS_BY_REASON = {
    'unauthenticated': 401,
    'forbidden': 404,
    'membership_required': 403,
    # The storage tier failed, which is the server's problem and not the
    # caller's - 502 for the same reason `download_attachment_view` answers a
    # storage failure with one. Never 404: the idea exists and the caller may
    # attach to it (S2-008).
    'storage_unavailable': 502,
}


def _error_response(exc: services.IdeaError) -> JsonResponse:
    """
    The HTTP status for one refused upload.

    A field-scoped refusal is checked *first*, ahead of `exc.reason` - and
    that order is the fix, not an optimization. `services.upload_attachment`
    re-raises every validation failure (a bad extension, a mismatched magic
    number, an oversized or empty file) as an `IdeaError` carrying `field`
    and no explicit `reason`, which leaves `reason` at `IdeaError`'s own
    default, `'forbidden'` - the exact value an authorization refusal uses.
    Read through `_STATUS_BY_REASON` alone, a rejected file type was
    reported as 404, indistinguishable from an idea that does not exist,
    which is wrong twice over: it is not a missing resource, and this
    endpoint's 404 is supposed to mean specifically that (see
    `download_attachment_view`). Every field-level refusal in this domain
    (`services.py`'s validators throughout) is a statement about the input,
    never about whether something exists, so it is always a 400 regardless
    of `reason` - S2-008's
    `test_a_validation_failure_is_never_reported_as_not_found` pins this.
    """
    status = 400 if exc.field is not None else _STATUS_BY_REASON.get(exc.reason, 400)
    return JsonResponse(
        {'success': False, 'message': exc.message, 'field': exc.field}, status=status
    )


def _attachment_json(attachment) -> dict:
    """
    The same fields `AttachmentType.from_model` returns, so the frontend's
    `IdeaAttachment` type describes both this response and the GraphQL one
    without a second shape to learn.
    """
    return {
        'id': str(attachment.pk),
        'ideaId': str(attachment.idea_id),
        'uploaderId': str(attachment.uploaded_by_id),
        'filename': attachment.filename,
        'contentType': attachment.content_type,
        'size': attachment.size,
        'createdAt': attachment.created_at.isoformat(),
        'downloadUrl': f'/ideas/{attachment.idea_id}/attachments/{attachment.pk}/download/',
    }


@csrf_exempt
@require_http_methods(['POST'])
def upload_attachment_view(request: HttpRequest, idea_id: int) -> HttpResponse:
    """
    `POST /ideas/<idea_id>/attachments/`, `multipart/form-data`, one file in
    the `file` field.

    Everything that decides whether this succeeds - idea authorship, active
    membership, file type, size, and the magic-byte check against a renamed
    or disguised upload - happens in `ideas.services.upload_attachment`; this
    view's whole job is translating an `UploadedFile` in and a JSON payload
    out.

    **Authenticate and authorize before touching the body** (S2-008). The
    multipart body is parsed lazily, the first time `request.FILES` is read,
    so everything that does not need the file runs first: the token, then
    whether this caller may attach to this idea at all. An anonymous or
    unauthorized caller is therefore refused (401/403/404) without Django
    parsing its body or spooling a large file to temporary disk, and never
    sees a "choose a file" 400 that would answer a question it had no
    standing to ask. Nothing here can stop the web server reading the bytes
    off the socket - a body-size limit belongs at the proxy - but the
    application does no work with them. `services.upload_attachment`
    re-applies the same gate, so this is an early exit, not the control.
    """
    user = get_authenticated_user(request)

    try:
        services.authorize_attachment_upload(user, idea_id)
    except services.IdeaError as exc:
        return _error_response(exc)

    uploaded_file: UploadedFile | None = request.FILES.get('file')
    if uploaded_file is None:
        return JsonResponse(
            {'success': False, 'message': 'Choose a file to attach.', 'field': 'file'}, status=400
        )

    try:
        attachment = services.upload_attachment(user, idea_id, uploaded_file)
    except services.IdeaError as exc:
        return _error_response(exc)

    return JsonResponse(
        {
            'success': True,
            'message': 'Attachment uploaded.',
            'field': None,
            'attachment': _attachment_json(attachment),
        },
        status=201,
    )


@require_http_methods(['GET'])
def download_attachment_view(
    request: HttpRequest, idea_id: int, attachment_id: int
) -> HttpResponse:
    """
    `GET /ideas/<idea_id>/attachments/<attachment_id>/download/`.

    A 404 for every reason the file cannot be handed back - unauthenticated,
    the idea is not readable, the attachment does not belong to this idea, or
    the object is genuinely missing from storage - so this endpoint is never
    an oracle for which of those is true. `selectors.get_idea_attachment`
    does the authorization; this view does not repeat it.

    `Content-Disposition: attachment` unconditionally, whatever the stored
    content type - never `inline`. An uploaded file is never trusted content:
    an HTML or SVG file that a browser might otherwise render inline, in this
    app's own origin, would be exactly the injection this line exists to
    refuse. Forcing a download (rather than in-tab rendering) is this
    project's whole "safe open" story for every supported type, images
    included, so there is one rule instead of an allow-list of "safe to
    render" content types to maintain.
    """
    user = get_authenticated_user(request)

    attachment = selectors.get_idea_attachment(user, idea_id, attachment_id)
    if attachment is None:
        return HttpResponse(status=404)

    return attachment_file_response(attachment)


def attachment_file_response(attachment: Attachment) -> HttpResponse:
    """
    Stream one *already authorized* attachment back as a forced download.

    Every caller has decided, before reaching this, that the requester may
    have the file: `download_attachment_view` through the idea's visibility
    rule, and the administration console's download view
    (`administration.views`) through its own platform permission. This
    function authorizes nothing; it is the one place the safe-download rules
    below live, so a second download path cannot quietly relax them.
    """
    try:
        file_handle = open_object(attachment.storage_key)
    except FileNotFoundError:
        return HttpResponse(status=404)
    except AttachmentStorageError:
        return HttpResponse(status=502)

    response = FileResponse(file_handle, content_type=attachment.content_type)
    response['Content-Length'] = str(attachment.size)
    # Both forms of the filename: the plain one for clients that only read
    # `filename`, and the percent-encoded `filename*` (RFC 5987/6266) for a
    # name with non-ASCII characters, which the plain form cannot carry
    # correctly. A stray `"` in the plain form is replaced rather than
    # escaped - it would otherwise terminate the quoted value early and let
    # the rest of the filename be read as header syntax.
    safe_quoted_name = attachment.filename.replace('"', "'")
    response['Content-Disposition'] = (
        f'attachment; filename="{safe_quoted_name}"; '
        f"filename*=UTF-8''{quote(attachment.filename)}"
    )
    # An uploaded file's declared type is never trusted for *this* header
    # either - `attachment.content_type` is the canonical value
    # `ideas.attachments.canonical_content_type` derived from the validated
    # extension, not whatever the uploading browser claimed. Combined with
    # `nosniff`, the browser is told exactly one type and refused permission
    # to guess a more "helpful" (and more dangerous) one from the bytes.
    response['X-Content-Type-Options'] = 'nosniff'
    return response
