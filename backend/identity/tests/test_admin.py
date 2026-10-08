from django.test import RequestFactory

from identity.admin import RefreshSessionAdmin
from identity.models import RefreshSession


def test_refresh_session_admin_cannot_add_rows_manually():
    # Only a hash is ever stored, and rows are only ever meant to be
    # created by identity.authentication - there's nothing a manually
    # added row could usefully contain.
    admin_instance = RefreshSessionAdmin(RefreshSession, admin_site=None)
    request = RequestFactory().get('/admin/identity/refreshsession/add/')

    assert admin_instance.has_add_permission(request) is False
