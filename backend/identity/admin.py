from typing import ClassVar

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from identity.forms import UserChangeForm, UserCreationForm
from identity.models import EmailToken, ExternalIdentity, RefreshSession, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """
    Admin for the platform User.

    Subclasses Django's UserAdmin with our own forms/fieldsets (no
    `username` field exists on this model) rather than building one from
    scratch. The password is still rendered through Django's masked,
    non-editable hash widget - `UserChangeForm` never puts the raw hash in
    an editable input, so it can't be read back out or edited directly.
    """

    add_form = UserCreationForm
    form = UserChangeForm
    model = User

    ordering: ClassVar[list[str]] = ['-created_at']
    list_display: ClassVar[list[str]] = [
        'email',
        'first_name',
        'last_name',
        'phone_number',
        'is_active',
        'is_verified',
        'is_staff',
    ]
    list_filter: ClassVar[list[str]] = ['is_active', 'is_verified', 'is_staff', 'is_superuser']
    search_fields: ClassVar[list[str]] = ['email', 'first_name', 'last_name', 'phone_number']
    readonly_fields: ClassVar[list[str]] = ['last_login', 'created_at', 'updated_at']

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone_number')}),
        (
            'Status',
            {
                'fields': (
                    'is_active',
                    'is_verified',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                )
            },
        ),
        ('Important dates', {'fields': ('last_login', 'created_at', 'updated_at')}),
    )
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': (
                    'email',
                    'first_name',
                    'last_name',
                    'phone_number',
                    'password1',
                    'password2',
                ),
            },
        ),
    )


@admin.register(ExternalIdentity)
class ExternalIdentityAdmin(admin.ModelAdmin):
    list_display: ClassVar[list[str]] = [
        'user',
        'provider',
        'provider_subject',
        'email',
        'created_at',
    ]
    list_filter: ClassVar[list[str]] = ['provider']
    search_fields: ClassVar[list[str]] = ['user__email', 'provider_subject', 'email']
    readonly_fields: ClassVar[list[str]] = ['created_at', 'updated_at']


@admin.register(RefreshSession)
class RefreshSessionAdmin(admin.ModelAdmin):
    """
    Read-only visibility into issued refresh sessions - never a way to read
    a raw credential back out (only its hash is ever stored, so there's
    nothing to display even if this were editable).
    """

    list_display: ClassVar[list[str]] = [
        'user',
        'created_at',
        'expires_at',
        'revoked_at',
        'last_used_at',
    ]
    list_filter: ClassVar[list[str]] = ['revoked_at']
    search_fields: ClassVar[list[str]] = ['user__email']
    readonly_fields: ClassVar[list[str]] = [
        'user',
        'token_hash',
        'created_at',
        'expires_at',
        'revoked_at',
        'last_used_at',
        'replaced_by',
    ]

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(EmailToken)
class EmailTokenAdmin(admin.ModelAdmin):
    """
    Read-only visibility into issued emailed tokens (password reset,
    activation) - never a way to read a raw token back out (only its hash is
    ever stored, so there's nothing to display even if this were editable).

    No add permission either: a token row is only ever created as a side
    effect of minting one, which is what puts the matching email in some
    inbox. Adding one here would create a live credential whose owner was
    never told about it.
    """

    list_display: ClassVar[list[str]] = [
        'user',
        'purpose',
        'created_at',
        'expires_at',
        'used_at',
    ]
    list_filter: ClassVar[list[str]] = ['purpose', 'used_at']
    search_fields: ClassVar[list[str]] = ['user__email']
    readonly_fields: ClassVar[list[str]] = [
        'user',
        'purpose',
        'token_hash',
        'created_at',
        'expires_at',
        'used_at',
    ]

    def has_add_permission(self, request) -> bool:
        return False
