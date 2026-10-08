"""
Organization-scoped authorization.

Every check in this module is scoped to an organization: the question is
never "may this user do X" but "may this user do X *in this organization*",
and the answer is derived from the user's ACTIVE membership of that
organization and the permissions on the roles attached to that membership.

Two things this module deliberately does NOT do:

1. **No organization-level permission for creating an organization.** See
   `create_organization_for_user`'s docstring for the full bootstrap
   argument. `ORGANIZATION_CREATE` exists as a permission *record* and is
   granted to the Owner role for display and completeness, but it is never
   used as a gate - requiring it would be circular, since the membership
   that carries it cannot exist until the organization does.

2. **No client-supplied identity.** Nothing here reads a role, an
   organization or a user from a request. Callers pass the authenticated
   `User` (resolved once, from the access token, by
   `identity.authentication.get_authenticated_user`) and the organization or
   ids that the operation is about; this module decides whether that pair is
   allowed. A resolver that wanted to authorize differently would have to
   bypass all of it.
"""

from typing import Literal

from identity.models import User
from organizations.models import Membership, MembershipRole, Organization

ORGANIZATION_VIEW = 'organization.view'
# Defined, granted to the Owner role, and never used as a gate - see this
# module's docstring. Kept as a code so the Owner role's permission set
# describes the organization surface completely.
ORGANIZATION_CREATE = 'organization.create'
ORGANIZATION_UPDATE = 'organization.update'
ORGANIZATION_MEMBERS_VIEW = 'organization.members.view'
ORGANIZATION_MEMBERS_MANAGE = 'organization.members.manage'
# Act as a reviewer on the organization's ideas (S3-002). Checked, like every
# code here, against the membership of the idea's *own* organization, so a
# grant in one tenant authorizes nothing in another. Held by the Owner role
# and by the non-system Reviewer role; see `docs/reviews-domain.md` §6.
IDEA_REVIEW = 'idea.review'

AuthorizationReason = Literal['unauthenticated', 'membership_required', 'forbidden']


class AuthorizationError(Exception):
    def __init__(
        self,
        message: str,
        reason: AuthorizationReason = 'forbidden',
        field: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.field = field


def _organization_id(organization: Organization | object) -> int | None:
    try:
        return int(str(getattr(organization, 'pk', organization)))
    except (TypeError, ValueError):
        return None


def _active_user(user: User | None) -> User | None:
    if user is None or not user.is_active:
        return None
    return user


def get_membership(user: User | None, organization: Organization | object) -> Membership | None:
    active_user = _active_user(user)
    normalized_id = _organization_id(organization)
    if active_user is None or normalized_id is None:
        return None

    return (
        Membership.objects.filter(
            user=active_user,
            organization_id=normalized_id,
            status=Membership.Status.ACTIVE,
        )
        .select_related('organization')
        .first()
    )


def is_member_of(user: User | None, organization: Organization | object) -> bool:
    return get_membership(user, organization) is not None


def active_organization_ids(user: User | None) -> list[int]:
    """
    The ids of every organization `user` is an *active* member of.

    The set-valued sibling of `get_membership`, for the questions that are
    about many organizations at once rather than one - a tenant-scoped feed,
    or a visibility filter that has to answer "is this reader in the same
    organization as this idea?" in a single query.

    It exists here, and not in the domain that first needed it, so that the
    definition of "an active membership" stays in exactly one place. A
    visibility filter written against its own copy of the Membership query
    would be free to drift from the one `require_permission` uses - and the
    drift would be invisible, because both would look correct in isolation.
    """
    active_user = _active_user(user)
    if active_user is None:
        return []

    return list(
        Membership.objects.filter(
            user=active_user,
            status=Membership.Status.ACTIVE,
        ).values_list('organization_id', flat=True)
    )


def membership_has_permission(membership: Membership | None, permission_code: str) -> bool:
    if membership is None or not permission_code:
        return False

    return MembershipRole.objects.filter(
        membership=membership,
        role__organization_id=membership.organization_id,
        role__role_permissions__permission__code=permission_code,
    ).exists()


def has_permission(
    user: User | None,
    organization: Organization | object,
    permission_code: str,
) -> bool:
    return membership_has_permission(get_membership(user, organization), permission_code)


def require_permission(
    user: User | None,
    organization: Organization | object,
    permission_code: str,
) -> Membership:
    if _active_user(user) is None:
        raise AuthorizationError('Authentication is required.', reason='unauthenticated')

    normalized_id = _organization_id(organization)
    if normalized_id is None or not Organization.objects.filter(id=normalized_id).exists():
        raise AuthorizationError('Organization is unavailable.')

    membership = get_membership(user, organization)
    if membership is None:
        raise AuthorizationError(
            'You must be an active member of this organization.',
            reason='membership_required',
        )

    if not has_permission(user, organization, permission_code):
        raise AuthorizationError('You do not have permission to perform this action.')

    return membership
