"""
HTTP endpoints for profile-photo bytes, for the same reason attachments have
their own (see `ideas/views.py`): a multipart upload and an image response are
not shaped like GraphQL. Both authenticate from the `Authorization: Bearer`
header exactly as `/graphql/` does.
"""

from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from identity import avatars
from identity.authentication import get_authenticated_user
from identity.models import User
from identity.schema import UserType


def _json(user: User, message: str, status: int = 200) -> JsonResponse:
    return JsonResponse(
        {
            'success': True,
            'message': message,
            'field': None,
            'user': {'avatarUrl': UserType.from_model(user).avatar_url},
        },
        status=status,
    )


@csrf_exempt  # Authorized by the bearer header, which a cross-site request cannot attach.
@require_http_methods(['POST', 'DELETE'])
def avatar_view(request: HttpRequest) -> HttpResponse:
    """`POST /account/avatar/` (multipart, field `file`) sets the photo; `DELETE` removes it."""
    user = get_authenticated_user(request)
    if user is None:
        return JsonResponse(
            {'success': False, 'message': 'Sign in to change your photo.', 'field': None},
            status=401,
        )

    if request.method == 'DELETE':
        return _json(avatars.remove_avatar(user), 'Your photo has been removed.')

    uploaded = request.FILES.get('file')
    if uploaded is None:
        return JsonResponse(
            {'success': False, 'message': 'Choose a photo to upload.', 'field': 'file'},
            status=400,
        )
    try:
        user = avatars.set_avatar(user, uploaded)
    except avatars.AvatarError as exc:
        return JsonResponse({'success': False, 'message': str(exc), 'field': 'file'}, status=400)
    return _json(user, 'Your photo has been updated.')


@require_http_methods(['GET'])
def user_avatar_view(request: HttpRequest, user_id: int) -> HttpResponse:
    """
    `GET /users/<id>/avatar/`: any signed-in user may see another's photo, as in
    any collaboration product. A 404 for every other case (signed out, no photo,
    missing object), so it is not an oracle.
    """
    if get_authenticated_user(request) is None:
        raise Http404
    key = (
        User.objects.filter(pk=user_id, is_active=True).values_list('avatar_key', flat=True).first()
    )
    if not key:
        raise Http404

    from django.core.files.storage import storages

    try:
        handle = storages['attachments'].open(key, 'rb')
    except (FileNotFoundError, OSError):
        raise Http404 from None

    extension = '.' + key.rsplit('.', 1)[-1]
    response = FileResponse(
        handle, content_type=avatars.CONTENT_TYPE_BY_EXTENSION.get(extension, 'image/png')
    )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, max-age=86400'
    return response
