"""
The platform's jobs, and who holds them.

Before this module the only way to make somebody a developer, a delivery manager,
the person who routes submissions or the person who releases proposals was a
Django shell or the Django admin. Each of those is a platform-scoped
`auth.Permission`, and this module hands them out the way `reviews.review_teams`
hands out reviewing: an administrator holding `manage_platform_roles` adds an
**existing** account to the role's group, or takes it out, from the console's
*Platform roles* page, and every change is audited.

- **Developer** ("Developers"): may be assigned delivery work (`automation.be_assignable`).
- **Delivery manager** ("Delivery managers"): runs delivery - the Developer Queue and
  assigning developers (`automation.manage_delivery`).
- **Intake** ("Platform intake"): routes submitted ideas to a review team
  (`assign_platform_reviewers`).
- **Decisions & proposals** ("Proposal approvers"): sends each platform approval or
  rejection to its owner as a letter, and releases, sends back or declines a review
  team's proposal (`release_proposals`).

Reviewers are made on the *Reviewers* page (`reviews.review_teams`), because a
reviewer also belongs to review teams.

**Intake and proposal approver are for platform administrators only.** Their work
happens inside the console, and the console is the administrators' alone, so those
two roles are refused for anybody else; their groups hold no console access of
their own. Developer and delivery manager work in the ordinary app.

**What a role is not.** Holding one is eligibility, not work: a developer is given
a specific opportunity by a delivery manager, and a review team is given a
specific idea by intake. The conflicts are refused where the work happens, not
here - a lead cannot route to their own team, a proposal's writer cannot release
it, a builder cannot accept their own work - so an administrator may hold a role
themselves (a small platform needs one person to wear several hats) and the
audit trail says so.
"""

from dataclasses import dataclass

from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission as AuthPermission
from django.db import transaction
from django.db.models import Q

from administration import authorization
from administration import services as admin_services
from administration.authorization import AdministrationError
from administration.models import AdminAuditEntry
from identity.models import User

#: Opportunity statuses after which a developer is no longer delivering it.
_CLOSED_OPPORTUNITY_STATUSES = ('completed', 'cancelled')


@dataclass(frozen=True)
class PlatformRole:
    key: str
    label: str
    description: str
    group: str
    permissions: tuple[str, ...]
    #: The role's work is done in the console, so only a platform administrator may hold it.
    administrators_only: bool = False


DEVELOPER = PlatformRole(
    key='developer',
    label='Developer',
    description='Can be assigned an approved idea to build, from the Developer Queue.',
    group='Developers',
    permissions=('automation.be_assignable',),
)
DELIVERY_MANAGER = PlatformRole(
    key='delivery_manager',
    label='Delivery manager',
    description='Runs delivery: sees the Developer Queue and assigns each opportunity to a '
    'developer or team.',
    group='Delivery managers',
    permissions=('automation.manage_delivery',),
)
INTAKE = PlatformRole(
    key='intake',
    label='Intake',
    description='Routes ideas submitted to the platform to a review team. Platform '
    'administrators only.',
    group='Platform intake',
    permissions=(authorization.ASSIGN_PLATFORM_REVIEWERS,),
    administrators_only=True,
)
PROPOSAL_APPROVER = PlatformRole(
    key='proposal_approver',
    label='Decisions & proposals',
    description='Speaks to idea owners for the platform: sends each approval or rejection to '
    "its owner as a letter, and releases, sends back or declines a review team's proposal. "
    'Never for a review they decided or a proposal they helped write. Platform '
    'administrators only.',
    group='Proposal approvers',
    permissions=(authorization.RELEASE_PROPOSALS,),
    administrators_only=True,
)

ROLES: tuple[PlatformRole, ...] = (DEVELOPER, DELIVERY_MANAGER, INTAKE, PROPOSAL_APPROVER)
_BY_KEY = {role.key: role for role in ROLES}


@dataclass(frozen=True)
class RoleHolders:
    role: PlatformRole
    holders: list[User]


class PlatformRoleError(AdministrationError):
    """A refused role change, in the project's `(message, field)` shape."""


def _role(key: str) -> PlatformRole:
    role = _BY_KEY.get(key or '')
    if role is None:
        raise PlatformRoleError('Choose a valid role.', field='role')
    return role


def _group(role: PlatformRole) -> Group:
    """The role's group, holding exactly the role's permissions (created on first use)."""
    group, _ = Group.objects.get_or_create(name=role.group)
    query = Q()
    for code in role.permissions:
        app_label, codename = code.split('.', 1)
        query |= Q(content_type__app_label=app_label, codename=codename)
    permissions = list(AuthPermission.objects.filter(query))
    if len(permissions) != len(role.permissions):
        raise PlatformRoleError('The platform permissions are missing. Run migrate first.')
    # `set`, not `add`: the group holds exactly the role's permissions.
    group.permissions.set(permissions)
    return group


def _holders(role: PlatformRole) -> list[User]:
    return list(
        User.objects.filter(groups__name=role.group, is_active=True)
        .distinct()
        .order_by('first_name', 'last_name', 'email')
    )


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise PlatformRoleError('That person is not available.', field='user') from None


def roles_overview(actor: User | None) -> list[RoleHolders]:
    """Every role and its active holders. Empty without `manage_platform_roles`."""
    if not authorization.capabilities_for(actor).can_manage_platform_roles:
        return []
    return [RoleHolders(role, _holders(role)) for role in ROLES]


@transaction.atomic
def grant_role(actor: User | None, role_key: str, email: str) -> User:
    """Give an existing, active account one of the platform's roles."""
    authorization.require_manage_platform_roles(actor)
    role = _role(role_key)
    target = User.objects.filter(email=User.objects.normalize_email(email or '')).first()
    if target is None:
        raise PlatformRoleError('No account uses this email address.', field='email')
    if not target.is_active:
        raise PlatformRoleError('This account is deactivated.', field='email')
    if role.administrators_only and not authorization.is_platform_admin(target):
        raise PlatformRoleError(
            f'Only a platform administrator can hold the {role.label} role: the work is done '
            'in the administration console.',
            field='email',
        )
    group = _group(role)
    if target.groups.filter(pk=group.pk).exists():
        raise PlatformRoleError(f'They already hold the {role.label} role.', field='email')

    target.groups.add(group)
    admin_services.record_event(
        actor,
        AdminAuditEntry.Action.PLATFORM_ROLE_GRANTED,
        'user',
        target.pk,
        target.email,
        metadata={'role': role.key},
    )
    return target


@transaction.atomic
def revoke_role(actor: User | None, role_key: str, user_id: object) -> User:
    """
    Take a role away from an account.

    Refused for a developer still delivering an opportunity (a delivery manager
    releases or completes it first), and for the last delivery manager while
    opportunities are still open, so no work is left with nobody able to act on it.
    """
    authorization.require_manage_platform_roles(actor)
    role = _role(role_key)
    group = _group(role)
    target = User.objects.filter(pk=_as_int(user_id), groups=group).first()
    if target is None:
        raise PlatformRoleError('That person does not hold this role.', field='user')

    from automation.models import Assignment, AutomationOpportunity

    if role is DEVELOPER and (
        Assignment.objects.filter(assignee_user=target, status='active')
        .exclude(opportunity__status__in=_CLOSED_OPPORTUNITY_STATUSES)
        .exists()
    ):
        raise PlatformRoleError(
            'They are still delivering an opportunity. Finish or reassign it first.',
            field='user',
        )
    if (
        role is DELIVERY_MANAGER
        and not User.objects.filter(groups=group, is_active=True).exclude(pk=target.pk).exists()
        and AutomationOpportunity.objects.exclude(status__in=_CLOSED_OPPORTUNITY_STATUSES).exists()
    ):
        raise PlatformRoleError(
            'They are the last delivery manager and delivery work is still open. '
            'Make somebody else a delivery manager first.',
            field='user',
        )

    target.groups.remove(group)
    admin_services.record_event(
        actor,
        AdminAuditEntry.Action.PLATFORM_ROLE_REVOKED,
        'user',
        target.pk,
        target.email,
        metadata={'role': role.key},
    )
    return target
