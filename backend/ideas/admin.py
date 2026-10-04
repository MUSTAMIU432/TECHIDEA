from typing import ClassVar

from django.contrib import admin

from ideas.models import Attachment, Category, Comment, Idea, IdeaTransition, Vote


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['name', 'slug', 'is_active', 'created_at']
    list_filter: ClassVar[list[str]] = ['is_active']
    search_fields: ClassVar[list[str]] = ['name', 'slug', 'description']
    readonly_fields: ClassVar[list[str]] = ['created_at', 'updated_at']


@admin.register(Idea)
class IdeaAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = [
        'title',
        'organization',
        'author',
        'status',
        'visibility',
        'category',
        'created_at',
    ]
    list_filter: ClassVar[list[str]] = ['status', 'visibility', 'category', 'frequency']
    # The organization-first ordering mirrors the indexes in the model: every
    # admin list is a tenant-scoped read, and the composite index exists for
    # exactly this shape of query.
    search_fields: ClassVar[list[str]] = [
        'title',
        'description',
        'problem_statement',
        'author__email',
        'organization__name',
    ]
    readonly_fields: ClassVar[list[str]] = ['created_at', 'updated_at', 'submitted_at']


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['idea', 'author', 'created_at']
    search_fields: ClassVar[list[str]] = ['content', 'author__email', 'idea__title']
    readonly_fields: ClassVar[list[str]] = ['created_at', 'updated_at']


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['idea', 'user', 'created_at']
    search_fields: ClassVar[list[str]] = ['user__email', 'idea__title']
    readonly_fields: ClassVar[list[str]] = ['created_at']


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['filename', 'idea', 'uploaded_by', 'size', 'created_at']
    list_filter: ClassVar[list[str]] = ['content_type']
    search_fields: ClassVar[list[str]] = ['filename', 'storage_key', 'uploaded_by__email']
    readonly_fields: ClassVar[list[str]] = ['created_at']


@admin.register(IdeaTransition)
class IdeaTransitionAdmin(admin.ModelAdmin):
    """
    The lifecycle audit trail, read-only (S3-007). Rows are written only by
    `ideas.lifecycle` in the transaction that changes the status; an admin
    form that could add, edit or delete one would be a way to rewrite history.
    """

    list_display: ClassVar[list[str]] = ['idea', 'from_status', 'to_status', 'actor', 'created_at']
    list_filter: ClassVar[list[str]] = ['to_status']
    search_fields: ClassVar[list[str]] = ['idea__title', 'actor__email']
    list_select_related: ClassVar[list[str]] = ['idea', 'actor']
    actions = None

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
