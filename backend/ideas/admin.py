from typing import ClassVar

from django.contrib import admin

from ideas.models import Attachment, Category, Comment, Idea, Vote


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
    list_filter: ClassVar[list[str]] = ['status', 'visibility', 'category']
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
