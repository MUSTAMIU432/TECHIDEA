"""
GraphQL for the platform's roles (`administration.platform_roles`).

Business refusals are `success: false` payloads with the field they name, like every
other domain. Who may do what is `platform_roles`' to decide; nothing here does.
"""

import strawberry

from administration import platform_roles
from administration.authorization import AdministrationError


@strawberry.type
class RoleHolderType:
    id: strawberry.ID
    email: str
    name: str


@strawberry.type
class PlatformRoleType:
    key: str
    label: str
    description: str
    holders: list[RoleHolderType]


@strawberry.type
class PlatformRolePayload:
    success: bool
    message: str
    field: str | None = None
    holder: RoleHolderType | None = None


def _holder(user) -> RoleHolderType:
    return RoleHolderType(
        id=strawberry.ID(str(user.pk)), email=user.email, name=user.get_full_name() or user.email
    )


def _camel(name: str | None) -> str | None:
    if not name:
        return None
    head, *rest = name.split('_')
    return head + ''.join(part.capitalize() for part in rest)


@strawberry.type
class Query:
    @strawberry.field(
        description='Every platform role and who holds it. Empty without `managePlatformRoles`.'
    )
    def platform_roles(self, info: strawberry.Info) -> list[PlatformRoleType]:
        return [
            PlatformRoleType(
                key=entry.role.key,
                label=entry.role.label,
                description=entry.role.description,
                holders=[_holder(u) for u in entry.holders],
            )
            for entry in platform_roles.roles_overview(info.context.user)
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation(description='Give an existing account a platform role.')
    def grant_platform_role(
        self, info: strawberry.Info, role: str, email: str
    ) -> PlatformRolePayload:
        try:
            user = platform_roles.grant_role(info.context.user, role, email)
        except AdministrationError as exc:
            return PlatformRolePayload(success=False, message=exc.message, field=_camel(exc.field))
        return PlatformRolePayload(success=True, message='Role given.', holder=_holder(user))

    @strawberry.mutation(description='Take a platform role away from an account.')
    def revoke_platform_role(
        self, info: strawberry.Info, role: str, user_id: strawberry.ID
    ) -> PlatformRolePayload:
        try:
            user = platform_roles.revoke_role(info.context.user, role, user_id)
        except AdministrationError as exc:
            return PlatformRolePayload(success=False, message=exc.message, field=_camel(exc.field))
        return PlatformRolePayload(success=True, message='Role removed.', holder=_holder(user))
