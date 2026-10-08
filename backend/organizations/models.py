from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


class Organization(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['name', 'created_at']

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['user', 'organization'],
                name='unique_membership_per_user_organization',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self) -> str:
        return f'{self.user.email} - {self.organization.name}'


class Permission(models.Model):
    code_validator = RegexValidator(
        regex=r'^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$',
        message='Permission codes must use lowercase dot-separated segments.',
    )
    code = models.CharField(max_length=100, unique=True, validators=[code_validator])
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['code']

    def __str__(self) -> str:
        return self.code

    def save(self, *args, **kwargs):
        self.code = self.code.strip().lower()
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        self.code = self.code.strip().lower()
        self.code_validator(self.code)


class Role(models.Model):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='roles',
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['name', 'created_at']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['organization', 'slug'],
                name='unique_role_slug_per_organization',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['organization', 'slug']),
        ]

    def __str__(self) -> str:
        return f'{self.organization.name}: {self.name}'


class RolePermission(models.Model):
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name='role_permissions',
    )
    permission = models.ForeignKey(
        Permission,
        on_delete=models.CASCADE,
        related_name='role_permissions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['role', 'permission'],
                name='unique_permission_per_role',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.role} - {self.permission}'


class MembershipRole(models.Model):
    membership = models.ForeignKey(
        Membership,
        on_delete=models.CASCADE,
        related_name='membership_roles',
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name='membership_roles',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['membership', 'role'],
                name='unique_role_per_membership',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['membership', 'role']),
            models.Index(fields=['role', 'membership']),
        ]

    def __str__(self) -> str:
        return f'{self.membership} - {self.role}'

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.membership_id and self.role_id:
            membership_organization_id = self.membership.organization_id
            role_organization_id = self.role.organization_id
            if membership_organization_id != role_organization_id:
                raise ValidationError('Membership and role must belong to the same organization.')
