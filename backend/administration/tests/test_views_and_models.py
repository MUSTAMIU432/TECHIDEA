"""
The console's evidence download, the audit model's guarantees, the
Django admin registration and the `grant_platform_admin` command.
"""

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import RequestFactory

from administration.admin import AdminAuditEntryAdmin
from administration.models import AdminAuditEntry
from administration.tests.conftest import PDF_BYTES, make_user
from identity.tokens import issue_access_token

pytestmark = pytest.mark.django_db


def _download(client, attachment_id, user=None):
    headers = {}
    if user is not None:
        headers['HTTP_AUTHORIZATION'] = f'Bearer {issue_access_token(user.pk)[0]}'
    return client.get(f'/administration/attachments/{attachment_id}/download/', **headers)


class TestEvidenceDownload:
    def test_an_administrator_downloads_evidence_and_it_is_audited(self, client, world):
        attachment = world['org_idea'].attachments.get()

        response = _download(client, attachment.pk, world['admin'])

        assert response.status_code == 200
        assert b''.join(response.streaming_content) == PDF_BYTES
        assert response['Content-Disposition'].startswith('attachment;')
        assert response['X-Content-Type-Options'] == 'nosniff'
        entry = AdminAuditEntry.objects.get(action='attachment.downloaded')
        assert entry.actor == world['admin']
        assert entry.target_id == str(attachment.pk)
        assert entry.target_label == 'evidence.pdf'
        assert entry.organization_id == world['acme'].pk
        assert entry.metadata == {'idea_id': world['org_idea'].pk}

    @pytest.mark.parametrize(
        'who', [None, 'author', 'member', 'acme_owner', 'reviewer', 'limited_admin']
    )
    def test_everybody_else_gets_a_404_and_no_audit(self, client, world, who):
        attachment = world['org_idea'].attachments.get()
        user = world[who] if who else None

        assert _download(client, attachment.pk, user).status_code == 404
        assert not AdminAuditEntry.objects.filter(action='attachment.downloaded').exists()

    def test_an_unknown_attachment_is_a_404(self, client, world):
        assert _download(client, 999999, world['admin']).status_code == 404

    def test_only_get_is_allowed(self, client, world):
        attachment = world['org_idea'].attachments.get()
        response = client.post(f'/administration/attachments/{attachment.pk}/download/')
        assert response.status_code == 405


class TestAuditEntryIsAppendOnly:
    def test_an_entry_cannot_be_changed_or_deleted(self, world):
        entry = AdminAuditEntry.objects.first()
        entry.message = 'rewritten'
        with pytest.raises(ValidationError):
            entry.save()
        with pytest.raises(ValidationError):
            entry.delete()

    def test_an_unknown_action_is_refused(self, world):
        with pytest.raises(ValidationError):
            AdminAuditEntry.objects.create(
                action='user.deleted', result='succeeded', target_type='user', target_id='1'
            )

    def test_str(self, world):
        entry = AdminAuditEntry.objects.first()
        assert str(entry) == f'platform_admin.granted user:{world["admin"].pk} (succeeded)'

    def test_the_django_admin_is_read_only(self, world):
        model_admin = AdminAuditEntryAdmin(AdminAuditEntry, site)
        request = RequestFactory().get('/')
        request.user = world['superuser']
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)


class TestGrantPlatformAdminCommand:
    def test_grant_and_revoke(self, world):
        user = make_user('new-admin@platform.example')

        call_command('grant_platform_admin', user.email)
        user = type(user).objects.get(pk=user.pk)
        assert user.has_perm('administration.access_console')
        assert user.has_perm('administration.manage_categories')

        call_command('grant_platform_admin', user.email, '--revoke')
        user = type(user).objects.get(pk=user.pk)
        assert not user.has_perm('administration.access_console')

        actions = list(
            AdminAuditEntry.objects.filter(target_id=str(user.pk))
            .order_by('pk')
            .values_list('action', 'actor')
        )
        assert actions == [('platform_admin.granted', None), ('platform_admin.revoked', None)]

    def test_an_unknown_email_is_an_error(self, world):
        with pytest.raises(CommandError, match='No account uses this email address'):
            call_command('grant_platform_admin', 'nobody@example.com')
        with pytest.raises(CommandError, match='No account uses this email address'):
            call_command('grant_platform_admin', 'nobody@example.com', '--revoke')

    def test_a_deactivated_account_cannot_be_made_an_administrator(self, world):
        world['member'].is_active = False
        world['member'].save()
        with pytest.raises(CommandError, match='This account is deactivated'):
            call_command('grant_platform_admin', world['member'].email)
