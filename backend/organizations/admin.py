from typing import ClassVar

from django.contrib import admin

from organizations.models import (
    Membership,
    MembershipRole,
    Organization,
    Permission,
    Role,
    RolePermission,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['name', 'slug', 'created_at']
    search_fields: ClassVar[list[str]] = ['name', 'slug']
    readonly_fields: ClassVar[list[str]] = ['created_at', 'updated_at']


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = [
        'user',
        'organization',
        'status',
        'created_at',
    ]
    list_filter: ClassVar[list[str]] = ['status']
    search_fields: ClassVar[list[str]] = [
        'user__email',
        'organization__name',
        'organization__slug',
    ]
    readonly_fields: ClassVar[list[str]] = ['created_at', 'updated_at']


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['code', 'name', 'created_at']
    search_fields: ClassVar[list[str]] = ['code', 'name', 'description']
    readonly_fields: ClassVar[list[str]] = ['created_at', 'updated_at']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['name', 'slug', 'organization', 'is_system', 'created_at']
    list_filter: ClassVar[list[str]] = ['is_system']
    search_fields: ClassVar[list[str]] = ['name', 'slug', 'organization__name']
    readonly_fields: ClassVar[list[str]] = ['created_at', 'updated_at']


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['role', 'permission', 'created_at']
    search_fields: ClassVar[list[str]] = ['role__name', 'permission__code']
    readonly_fields: ClassVar[list[str]] = ['created_at']


@admin.register(MembershipRole)
class MembershipRoleAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = ['membership', 'role', 'created_at']
    search_fields: ClassVar[list[str]] = ['membership__user__email', 'role__name']
    readonly_fields: ClassVar[list[str]] = ['created_at']
