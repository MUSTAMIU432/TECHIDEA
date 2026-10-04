from typing import ClassVar

from django.contrib import admin

from administration.models import AdminAuditEntry


@admin.register(AdminAuditEntry)
class AdminAuditEntryAdmin(admin.ModelAdmin):
    """
    The administrative audit trail, read-only - the same stance as the idea
    lifecycle trail (`ideas.admin.IdeaTransitionAdmin`): an admin form that
    could add, edit or delete an entry would be a way to rewrite the record of
    what administrators did.
    """

    list_display: ClassVar[list[str]] = [
        'created_at',
        'action',
        'result',
        'actor',
        'target_type',
        'target_label',
    ]
    list_filter: ClassVar[list[str]] = ['action', 'result', 'target_type']
    search_fields: ClassVar[list[str]] = ['actor__email', 'target_label', 'target_id']
    list_select_related: ClassVar[list[str]] = ['actor']
    actions = None

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
