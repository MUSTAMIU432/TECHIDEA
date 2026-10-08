import strawberry

from identity.schema import UserType
from organizations import services
from organizations.models import Membership, MembershipRole, Organization, Permission, Role


@strawberry.type(description='An organization a platform user belongs to.')
class OrganizationType:
    id: strawberry.ID
    name: str
    slug: str
    created_at: str
    updated_at: str

    @staticmethod
    def from_model(organization: Organization) -> 'OrganizationType':
        return OrganizationType(
            id=strawberry.ID(str(organization.pk)),
            name=organization.name,
            slug=organization.slug,
            created_at=organization.created_at.isoformat(),
            updated_at=organization.updated_at.isoformat(),
        )


@strawberry.type(description='An atomic organization capability.')
class PermissionType:
    id: strawberry.ID
    code: str
    name: str
    description: str
    created_at: str
    updated_at: str

    @staticmethod
    def from_model(permission: Permission) -> 'PermissionType':
        return PermissionType(
            id=strawberry.ID(str(permission.pk)),
            code=permission.code,
            name=permission.name,
            description=permission.description,
            created_at=permission.created_at.isoformat(),
            updated_at=permission.updated_at.isoformat(),
        )


@strawberry.type(description='An organization-scoped collection of permissions.')
class RoleType:
    id: strawberry.ID
    name: str
    slug: str
    description: str
    is_system: bool
    created_at: str
    updated_at: str
    organization: OrganizationType
    permissions: list[PermissionType]

    @staticmethod
    def from_model(role: Role) -> 'RoleType':
        return RoleType(
            id=strawberry.ID(str(role.pk)),
            name=role.name,
            slug=role.slug,
            description=role.description,
            is_system=role.is_system,
            created_at=role.created_at.isoformat(),
            updated_at=role.updated_at.isoformat(),
            organization=OrganizationType.from_model(role.organization),
            permissions=[
                PermissionType.from_model(role_permission.permission)
                for role_permission in role.role_permissions.all()
            ],
        )


@strawberry.type(description='A user membership in an organization.')
class MembershipType:
    id: strawberry.ID
    status: str
    created_at: str
    updated_at: str
    user: UserType
    organization: OrganizationType
    roles: list[RoleType]

    @staticmethod
    def from_model(membership: Membership) -> 'MembershipType':
        return MembershipType(
            id=strawberry.ID(str(membership.pk)),
            status=membership.status,
            created_at=membership.created_at.isoformat(),
            updated_at=membership.updated_at.isoformat(),
            user=UserType.from_model(membership.user),
            organization=OrganizationType.from_model(membership.organization),
            roles=[
                RoleType.from_model(membership_role.role)
                for membership_role in membership.membership_roles.all()
            ],
        )


@strawberry.type(description='A role assigned to a membership.')
class MembershipRoleType:
    membership: MembershipType
    role: RoleType

    @staticmethod
    def from_model(membership_role: MembershipRole) -> 'MembershipRoleType':
        return MembershipRoleType(
            membership=MembershipType.from_model(membership_role.membership),
            role=RoleType.from_model(membership_role.role),
        )


@strawberry.type(description='An organization paired with the current user membership.')
class OrganizationMembershipType:
    organization: OrganizationType
    membership: MembershipType


@strawberry.input(description='Fields for creating an organization.')
class CreateOrganizationInput:
    name: str
    slug: str | None = None


@strawberry.input(description='Fields for assigning or removing a membership role.')
class MembershipRoleInput:
    membership_id: strawberry.ID
    role_id: strawberry.ID


@strawberry.type(description='Result of creating an organization.')
class CreateOrganizationPayload:
    success: bool
    message: str
    field: str | None = None
    organization: OrganizationType | None = None
    membership: MembershipType | None = None


@strawberry.type(description='Result of assigning a role to a membership.')
class AssignMembershipRolePayload:
    success: bool
    message: str
    field: str | None = None
    membership_role: MembershipRoleType | None = None


@strawberry.type(description='Result of removing a role from a membership.')
class RemoveMembershipRolePayload:
    success: bool
    message: str
    field: str | None = None


@strawberry.type
class Query:
    @strawberry.field(description='Organizations the current user can access.')
    def me_organizations(self, info: strawberry.Info) -> list[OrganizationMembershipType]:
        user = info.context.user
        return [
            OrganizationMembershipType(
                organization=OrganizationType.from_model(item.organization),
                membership=MembershipType.from_model(item.membership),
            )
            for item in services.list_organizations_for_user(user)
        ]

    @strawberry.field(description='Active memberships for the current user.')
    def me_memberships(self, info: strawberry.Info) -> list[MembershipType]:
        user = info.context.user
        memberships = [item.membership for item in services.list_organizations_for_user(user)]
        return [MembershipType.from_model(membership) for membership in memberships]

    @strawberry.field(description='Roles visible to the current user across their organizations.')
    def my_organization_roles(self, info: strawberry.Info) -> list[RoleType]:
        return [
            RoleType.from_model(role) for role in services.list_roles_for_user(info.context.user)
        ]

    @strawberry.field(description='Roles in an organization visible to the current user.')
    def organization_roles(
        self, info: strawberry.Info, organization_id: strawberry.ID
    ) -> list[RoleType]:
        return [
            RoleType.from_model(role)
            for role in services.list_roles_for_user(info.context.user, organization_id)
        ]

    @strawberry.field(description='An organization visible to the current user.')
    def organization(self, info: strawberry.Info, id: strawberry.ID) -> OrganizationType | None:
        organization = services.get_organization_for_user(info.context.user, id)
        return OrganizationType.from_model(organization) if organization else None

    @strawberry.field(description='Active members of an organization the current user can access.')
    def organization_members(
        self, info: strawberry.Info, organization_id: strawberry.ID
    ) -> list[MembershipType]:
        memberships = services.list_memberships_for_organization(info.context.user, organization_id)
        return [MembershipType.from_model(membership) for membership in memberships]


@strawberry.type
class Mutation:
    @strawberry.mutation(description='Create an organization and its creator membership.')
    def create_organization(
        self, info: strawberry.Info, input: CreateOrganizationInput
    ) -> CreateOrganizationPayload:
        # The one mutation that is gated on authentication alone rather than
        # on a permission: creating an organization is the bootstrap step
        # that precedes any membership, so there is no permission set that
        # could authorize it. See
        # organizations/services.create_organization_for_user's docstring for
        # the full argument - the short version is that requiring
        # `organization.create` here would be circular, since the grant that
        # would satisfy it is created by this very call.
        user = info.context.user
        if user is None:
            return CreateOrganizationPayload(
                success=False,
                message='You must be signed in to create an organization.',
            )

        try:
            result = services.create_organization_for_user(
                user,
                services.CreateOrganizationInput(name=input.name, slug=input.slug),
            )
        except services.OrganizationError as exc:
            return CreateOrganizationPayload(success=False, message=exc.message, field=exc.field)

        return CreateOrganizationPayload(
            success=True,
            message='Organization created successfully.',
            organization=OrganizationType.from_model(result.organization),
            membership=MembershipType.from_model(result.membership),
        )

    @strawberry.mutation(description='Assign an organization role to an active membership.')
    def assign_role_to_membership(
        self, info: strawberry.Info, input: MembershipRoleInput
    ) -> AssignMembershipRolePayload:
        try:
            membership_role = services.assign_role_to_membership(
                info.context.user, input.membership_id, input.role_id
            )
        except services.OrganizationError as exc:
            return AssignMembershipRolePayload(success=False, message=exc.message, field=exc.field)

        return AssignMembershipRolePayload(
            success=True,
            message='Role assigned successfully.',
            membership_role=MembershipRoleType.from_model(membership_role),
        )

    @strawberry.mutation(description='Remove an organization role from a membership.')
    def remove_role_from_membership(
        self, info: strawberry.Info, input: MembershipRoleInput
    ) -> RemoveMembershipRolePayload:
        try:
            services.remove_role_from_membership(
                info.context.user, input.membership_id, input.role_id
            )
        except services.OrganizationError as exc:
            return RemoveMembershipRolePayload(success=False, message=exc.message, field=exc.field)

        return RemoveMembershipRolePayload(success=True, message='Role removed successfully.')
