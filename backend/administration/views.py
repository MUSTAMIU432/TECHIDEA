"""
The administration console's one HTTP surface beyond GraphQL: downloading an
idea's evidence.

It exists for the same reason `ideas/views.py` does - file bytes do not travel
through GraphQL - and it is a separate endpoint rather than a flag on that one
because the authorization is different, not wider by accident. The ordinary
download authorizes through the idea's visibility rule; this one requires the
platform permissions `ACCESS_CONSOLE` and `INSPECT_IDEA_CONTENT`, and records
every download in the administrative audit trail before the file is sent.

Everything else is shared, not copied: authentication is the same
`get_authenticated_user` (the bearer token, never a cookie, so a cross-site
link cannot trigger it), and the response is built by
`ideas.views.attachment_file_response`, so the forced-download,
canonical-content-type and `nosniff` rules are the same single implementation.
A 404 answers every refusal, as the ordinary endpoint does.
"""

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_http_methods

from administration import selectors, services
from ideas.views import attachment_file_response
from identity.authentication import get_authenticated_user


@require_http_methods(['GET'])
def admin_download_attachment_view(request: HttpRequest, attachment_id: int) -> HttpResponse:
    """`GET /administration/attachments/<attachment_id>/download/`."""
    user = get_authenticated_user(request)

    attachment = selectors.get_attachment_for_download(user, attachment_id)
    if attachment is None:
        return HttpResponse(status=404)

    services.record_attachment_download(user, attachment)
    return attachment_file_response(attachment)
