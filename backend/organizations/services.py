from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from identity.models import User
from organizations import authorization
from organizations.authorization import (
    IDEA_REVIEW,
    ORGANIZATION_CREATE,
    ORGANIZATION_MEMBERS_MANAGE,
    ORGANIZATION_MEMBERS_VIEW,
    ORGANIZATION_UPDATE,
    ORGANIZATION_VIEW,
    AuthorizationError,
)
from organizations.models import (
    Membership,
    MembershipRole,
    Organization,
    Permission,
    Role,
    RolePermission,
)


class OrganizationError(AuthorizationError):
    def __init__(
        self,
        message: str,
        field: str | None = None,
        reason: str = 'forbidden',
    ):
        super().__init__(message, reason=reason, field=field)


@dataclass(frozen=True)
class CreateOrganizationInput:
    name: str
    slug: str | None = None


@dataclass(frozen=True)
class OrganizationMembership:
    organization: Organization
    membership: Membership


PERMISSION_DEFINITIONS = (
    (
        ORGANIZATION_VIEW,
        'View organization',
        'View the organization workspace and its details.',
    ),
    (
        ORGANIZATION_CREATE,
        'Create organization',
        'Create a new organization workspace.',
    ),
    (
        ORGANIZATION_UPDATE,
        'Update organization',
        'Update organization details.',
    ),
    (
        ORGANIZATION_MEMBERS_VIEW,
        'View organization members',
        'View active memberships in the organization.',
    ),
    (
        ORGANIZATION_MEMBERS_MANAGE,
        'Manage organization members',
        'Manage organization memberships and their roles.',
    ),
    (
        IDEA_REVIEW,
        'Review ideas',
        "Review and decide on the organization's submitted ideas.",
    ),
)
DEFAULT_OWNER_ROLE_SLUG = 'owner'

# The non-system Reviewer role (S3-002): reviewing without ownership. Granted
# and removed through the ordinary `assign_role_to_membership` /
# `remove_role_from_membership`, like any other role. `ORGANIZATION_VIEW` is
# included because a role without it is invisible to `list_roles_for_user`,
# and a reviewer has to be able to see the organization they review for.
REVIEWER_ROLE_SLUG = 'reviewer'
REVIEWER_ROLE_PERMISSIONS = frozenset({ORGANIZATION_VIEW, IDEA_REVIEW})


def _require_active_user(user: User | None) -> User:
    if user is None or not user.is_active:
        raise OrganizationError(
            'You must be signed in to access organizations.',
            reason='unauthenticated',
        )
    return user


def _validate_name(name: str | None) -> str:
    normalized_name = (name or '').strip()
    if not normalized_name:
        raise OrganizationError('Organization name is required.', field='name')
    if len(normalized_name) > Organization._meta.get_field('name').max_length:
        raise OrganizationError('Organization name is too long.', field='name')
    return normalized_name


def _normalize_slug(name: str, value: str | None) -> str:
    slug_source = value if value and value.strip() else name
    normalized_slug = slugify(slug_source)
    if not normalized_slug:
        raise OrganizationError('Enter a name that can be represented as a slug.', field='slug')
    if len(normalized_slug) > Organization._meta.get_field('slug').max_length:
        raise OrganizationError('Organization slug is too long.', field='slug')
    return normalized_slug


def _normalize_id(value: object, field: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise OrganizationError(f'Invalid {field}.', field=field) from None


def _permission_set() -> list[Permission]:
    permissions = []
    for code, name, description in PERMISSION_DEFINITIONS:
        permission, _ = Permission.objects.get_or_create(
            code=code,
            defaults={'name': name, 'description': description},
        )
        permissions.append(permission)
    return permissions


def _role_queryset():
    return Role.objects.select_related('organization').prefetch_related(
        'role_permissions__permission'
    )


def _require_permission(
    user: User | None,
    organization: Organization | object,
    permission_code: str,
) -> Membership:
    try:
        return authorization.require_permission(user, organization, permission_code)
    except AuthorizationError as exc:
        raise OrganizationError(exc.message, field=exc.field, reason=exc.reason) from None


def create_organization_for_user(
    user: User | None, data: CreateOrganizationInput
) -> OrganizationMembership:
    """
    Create an organization, and bootstrap the caller into it as its Owner.

    The authorization model here is deliberately NOT "the caller must hold
    ORGANIZATION_CREATE somewhere", and the reason is structural rather than
    a preference. Creating an organization is the bootstrap step that comes
    *before* any membership exists:

        authenticated user
          -> create organization
          -> creator membership (ACTIVE)
          -> Owner role for that organization
          -> Owner permissions (including organization.create)

    At the moment of the call there is no organization to be a member of, so
    there is no membership row, no role assignment, and therefore no
    permission set that could authorize it. Requiring `organization.create`
    would be circular: the grant that would satisfy the check is created by
    the very operation being checked. The only thing that can be required is
    that the caller is an authenticated, active user - which is exactly what
    `_require_active_user` does - and everything after that is bootstrap
    rather than authorization.

    The flip side, which is why this is safe to ship: an organization is a
    resource the platform provisions for the user who asks for one. There is
    no pre-existing organization whose members this could be used to reach,
    and the new one contains only the creator. Every subsequent operation on
    it *is* permission-checked (`organization.view` for reads,
    `organization.members.manage` for membership writes), and tenant
    isolation for those is enforced by
    `organizations.authorization.require_permission`.

    `ORGANIZATION_CREATE` is still provisioned and still granted to the Owner
    role, so the Owner role's permission set accurately describes the
    organization surface. It is simply never used as a gate - see
    `organizations/authorization.py`'s module docstring.
    """
    user = _require_active_user(user)
    name = _validate_name(data.name)
    normalized_slug = _normalize_slug(name, data.slug)

    if Organization.objects.filter(slug=normalized_slug).exists():
        raise OrganizationError('An organization with this slug already exists.', field='slug')

    with transaction.atomic():
        try:
            organization = Organization.objects.create(name=name, slug=normalized_slug)
        except IntegrityError:
            raise OrganizationError(
                'An organization with this slug already exists.', field='slug'
            ) from None

        permissions = _permission_set()
        try:
            owner_role = Role.objects.create(
                organization=organization,
                name='Owner',
                slug=DEFAULT_OWNER_ROLE_SLUG,
                description='Full access to the organization foundation.',
                is_system=True,
            )
            RolePermission.objects.bulk_create(
                [
                    RolePermission(role=owner_role, permission=permission)
                    for permission in permissions
                ]
            )
            reviewer_role = Role.objects.create(
                organization=organization,
                name='Reviewer',
                slug=REVIEWER_ROLE_SLUG,
                description="Review and decide on the organization's submitted ideas.",
                is_system=False,
            )
            RolePermission.objects.bulk_create(
                [
                    RolePermission(role=reviewer_role, permission=permission)
                    for permission in permissions
                    if permission.code in REVIEWER_ROLE_PERMISSIONS
                ]
            )
        except IntegrityError:
            raise OrganizationError(
                'We could not provision the organization roles. Please try again.'
            ) from None

        try:
            membership = Membership.objects.create(
                user=user,
                organization=organization,
                status=Membership.Status.ACTIVE,
            )
        except IntegrityError:
            raise OrganizationError(
                'We could not create the organization membership. Please try again.'
            ) from None

        try:
            MembershipRole.objects.create(membership=membership, role=owner_role)
        except IntegrityError:
            raise OrganizationError(
                'We could not assign the organization owner role. Please try again.'
            ) from None

    return OrganizationMembership(organization=organization, membership=membership)


def list_organizations_for_user(user: User | None) -> list[OrganizationMembership]:
    if user is None or not user.is_active:
        return []

    memberships = (
        Membership.objects.filter(
            user=user,
            status=Membership.Status.ACTIVE,
        )
        .select_related('organization', 'user')
        .prefetch_related('membership_roles__role__role_permissions__permission')
        .order_by('organization__name', 'organization_id')
    )
    return [
        OrganizationMembership(organization=membership.organization, membership=membership)
        for membership in memberships
    ]


def get_organization_for_user(user: User | None, organization_id: object) -> Organization | None:
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(organization_id))
    except (TypeError, ValueError):
        return None

    membership = authorization.get_membership(user, normalized_id)
    if membership is None or not authorization.membership_has_permission(
        membership, ORGANIZATION_VIEW
    ):
        return None
    return membership.organization


def list_memberships_for_organization(
    user: User | None, organization_id: object
) -> list[Membership]:
    if user is None or not user.is_active:
        return []

    try:
        normalized_id = int(str(organization_id))
    except (TypeError, ValueError):
        return []

    if not authorization.has_permission(user, normalized_id, ORGANIZATION_MEMBERS_VIEW):
        return []

    memberships = (
        Membership.objects.filter(
            organization_id=normalized_id,
            status=Membership.Status.ACTIVE,
            organization__memberships__user=user,
            organization__memberships__status=Membership.Status.ACTIVE,
        )
        .select_related('user', 'organization')
        .prefetch_related('membership_roles__role__role_permissions__permission')
        .order_by('user__email')
        .distinct()
    )
    return list(memberships)


def list_roles_for_user(user: User | None, organization_id: object | None = None) -> list[Role]:
    if user is None or not user.is_active:
        return []

    if organization_id is not None:
        try:
            normalized_id = int(str(organization_id))
        except (TypeError, ValueError):
            return []
        if not authorization.has_permission(user, normalized_id, ORGANIZATION_VIEW):
            return []
        organization_filter = {'organization_id': normalized_id}
    else:
        organization_filter = {}

    roles = _role_queryset().filter(
        organization__memberships__user=user,
        organization__memberships__status=Membership.Status.ACTIVE,
        role_permissions__permission__code=ORGANIZATION_VIEW,
        **organization_filter,
    )
    return list(roles.distinct().order_by('organization__name', 'name', 'slug'))


def get_role_for_user(user: User | None, role_id: object) -> Role | None:
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(role_id))
    except (TypeError, ValueError):
        return None

    role = (
        _role_queryset()
        .filter(
            id=normalized_id,
            organization__memberships__user=user,
            organization__memberships__status=Membership.Status.ACTIVE,
        )
        .first()
    )
    if role is None or not authorization.has_permission(user, role.organization, ORGANIZATION_VIEW):
        return None
    return role


def list_permissions_for_role(user: User | None, role_id: object) -> list[Permission]:
    role = get_role_for_user(user, role_id)
    if role is None:
        return []
    return [role_permission.permission for role_permission in role.role_permissions.all()]


def _membership_in_actor_organization(user: User, membership_id: int) -> Membership | None:
    return (
        Membership.objects.filter(
            id=membership_id,
            organization__memberships__user=user,
            organization__memberships__status=Membership.Status.ACTIVE,
        )
        .select_for_update()
        .select_related('organization')
        .first()
    )


def assign_role_to_membership(
    user: User | None, membership_id: object, role_id: object
) -> MembershipRole:
    user = _require_active_user(user)
    normalized_membership_id = _normalize_id(membership_id, 'membershipId')
    normalized_role_id = _normalize_id(role_id, 'roleId')

    with transaction.atomic():
        membership = _membership_in_actor_organization(user, normalized_membership_id)
        if membership is None:
            raise OrganizationError('Membership or role not found.')

        role = (
            _role_queryset()
            .filter(id=normalized_role_id, organization_id=membership.organization_id)
            .select_for_update()
            .first()
        )
        if role is None:
            raise OrganizationError('Membership or role not found.')

        _require_permission(user, membership.organization, ORGANIZATION_MEMBERS_MANAGE)
        if membership.status != Membership.Status.ACTIVE:
            raise OrganizationError('Roles can only be assigned to active memberships.')

        if MembershipRole.objects.filter(membership=membership, role=role).exists():
            raise OrganizationError('This membership already has this role.')

        try:
            return MembershipRole.objects.create(membership=membership, role=role)
        except IntegrityError:
            raise OrganizationError('This membership already has this role.') from None


def remove_role_from_membership(user: User | None, membership_id: object, role_id: object) -> None:
    user = _require_active_user(user)
    normalized_membership_id = _normalize_id(membership_id, 'membershipId')
    normalized_role_id = _normalize_id(role_id, 'roleId')

    with transaction.atomic():
        membership = _membership_in_actor_organization(user, normalized_membership_id)
        if membership is None:
            raise OrganizationError('Membership or role not found.')

        role = (
            _role_queryset()
            .filter(id=normalized_role_id, organization_id=membership.organization_id)
            .select_for_update()
            .first()
        )
        if role is None:
            raise OrganizationError('Membership or role not found.')

        _require_permission(user, membership.organization, ORGANIZATION_MEMBERS_MANAGE)
        if membership.status != Membership.Status.ACTIVE:
            raise OrganizationError('Roles can only be removed from active memberships.')

        if role.is_system:
            other_active_holders = MembershipRole.objects.filter(
                role=role,
                membership__status=Membership.Status.ACTIVE,
            ).exclude(membership=membership)
            if not other_active_holders.exists():
                raise OrganizationError(
                    'The last active holder of a system role cannot be removed.'
                )

        deleted, _ = MembershipRole.objects.filter(membership=membership, role=role).delete()
        if not deleted:
            raise OrganizationError('This membership does not have this role.')
