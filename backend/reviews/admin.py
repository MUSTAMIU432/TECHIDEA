"""
Read-only admin for review history (S3-002).

Reviews are created and completed only by the domain services (S3-004), which
authorize the reviewer against the idea's organization and move the idea's
status in the same transaction. An admin form that could add, edit or delete
a review would bypass all of that and could rewrite an audit record, so both
models are view-only here: history is readable, and nothing else.
"""

from typing import ClassVar

from django.contrib import admin

from reviews.models import Review, ReviewCriterionAssessment


class _ReadOnlyAdminMixin:
    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class ReviewCriterionAssessmentInline(_ReadOnlyAdminMixin, admin.TabularInline):
    model = ReviewCriterionAssessment
    fields: ClassVar[list[str]] = ['criterion', 'rating', 'note', 'created_at']
    readonly_fields: ClassVar[list[str]] = fields
    extra = 0


@admin.register(Review)
class ReviewAdmin(_ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display: ClassVar[list[str]] = [
        'idea',
        'round',
        'reviewer',
        'decision',
        'created_at',
        'completed_at',
    ]
    list_filter: ClassVar[list[str]] = ['decision']
    search_fields: ClassVar[list[str]] = [
        'idea__title',
        'reviewer__email',
        'idea__organization__name',
    ]
    list_select_related: ClassVar[list[str]] = ['idea', 'reviewer']
    inlines: ClassVar[list[type]] = [ReviewCriterionAssessmentInline]
    # Nothing is actually actionable on these (see the module docstring); the
    # bulk "delete selected" action is removed so it is not even offered.
    actions = None
