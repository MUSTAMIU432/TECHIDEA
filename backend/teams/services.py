"""
Team operations.

The write side of the teams app, in the same shape as
`organizations.services`: one `(message, field, reason)` error class, and one
transaction per operation.

Three operations, and what is deliberately *not* among them:

- `create_team` - the creator becomes the Owner and the team's first member, and
  the team gets its system roles seeded in the same transaction, so a team
  exists in a usable state or not at all.
- `seed_team_roles` - creates the Owner and Member roles for a team and grants
  them the permission records `teams.authorization` declares. Refuses any code
  the platform has not defined, which is how "a team role cannot hold a review
  or approval permission" is enforced at the only place permissions are granted.
- `assert_known_permissions` - the guard the other two share.

There is no `add_member(user_id)`. Membership is created by accepting an
invitation (`invitations.services.accept_invitation`) and by `create_team`, and
nothing else. Typing somebody's email address is an *invitation*, not a
membership: it creates a revocable, expiring, single-use record that the
recipient has to act on, and until they do, the only truthful answer to "is this
person in my team" is no.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils.text import slugify

from identity.models import User
from organizations.models import Permission
from teams import authorization
from teams.models import (
    Team,
    TeamMembership,
    TeamMembershipRole,
    TeamRole,
    TeamRolePermission,
)

logger = logging.getLogger(__name__)

MAX_TEAM_NAME_LENGTH = 200
MAX_TEAM_DESCRIPTION_LENGTH = 2000

# The two system roles every team has. Named here rather than discovered, so
# `services` and the frontend's role picker cannot disagree about the vocabulary.
OWNER_ROLE_SLUG = 'owner'
MEMBER_ROLE_SLUG = 'member'

SYSTEM_ROLES: tuple[tuple[str, str, str], ...] = (
    (OWNER_ROLE_SLUG, 'Owner', 'Can manage the team, its members and its ideas.'),
    (MEMBER_ROLE_SLUG, 'Member', 'Can see the team and file ideas for it.'),
)


class TeamError(authorization.TeamAuthorizationError):
    """A refused team operation, in the project's `(message, field, reason)` shape."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message, reason=reason, field=field)


@dataclass(frozen=True)
class TeamInput:
    name: str
    description: str = ''


def assert_known_permissions(codes) -> None:
    """
    Refuse any permission code the platform has not declared.

    The single guard on what a team role may be granted, and it exists because
    the permission table is shared with organizations: without it, a caller
    could attach `organization.members.manage` - or a code invented on the spot
    - to a team role and quietly build a second authorization path. "A team
    cannot self-approve" starts here.
    """
    unknown = [code for code in codes if code not in authorization.ALL_TEAM_PERMISSIONS]
    if unknown:
        raise TeamError(f'Not a team permission: {", ".join(sorted(unknown))}', field='permissions')


def _validate_name(name: str | None) -> str:
    normalized = (name or '').strip()
    if not normalized:
        raise TeamError('Give the team a name.', field='name')
    if len(normalized) > MAX_TEAM_NAME_LENGTH:
        raise TeamError(
            f'The team name must be at most {MAX_TEAM_NAME_LENGTH} characters.', field='name'
        )
    return normalized


def _validate_description(description: str | None) -> str:
    normalized = (description or '').strip()
    if len(normalized) > MAX_TEAM_DESCRIPTION_LENGTH:
        raise TeamError(
            f'The description must be at most {MAX_TEAM_DESCRIPTION_LENGTH} characters.',
            field='description',
        )
    return normalized


def _unique_slug(name: str) -> str:
    """
    A slug no other team has.

    `Organization` and `Team` slugs are global (they are URL identifiers), so a
    simple `slugify(name)` collides as soon as two people each create a
    "University Automation Team" - which is the *ordinary* case, not an abuse.
    A numeric suffix is therefore the rule rather than a uniqueness check the
    caller has to handle.
    """
    base = slugify(name)[:90] or 'team'
    candidate = base
    suffix = 2
    while Team.objects.filter(slug=candidate).exists():
        candidate = f'{base}-{suffix}'
        suffix += 1
    return candidate


def _ensure_permissions() -> dict[str, Permission]:
    """
    The `organizations.Permission` records for every team code, created if
    absent.

    Reusing the organization app's table is what makes this "reuse the existing
    authorization architecture" rather than a new one. `Permission.save()` runs
    `full_clean`, which normalises the code, so the codes recorded here are
    exactly the lowercase strings the authorization module compares against.
    """
    permissions: dict[str, Permission] = {}
    for code in authorization.ALL_TEAM_PERMISSIONS:
        permission, _created = Permission.objects.get_or_create(
            code=code,
            defaults={'name': code.replace('.', ' ').replace('_', ' ').capitalize()},
        )
        permissions[code] = permission
    return permissions


def seed_team_roles(team: Team) -> dict[str, TeamRole]:
    """
    Create this team's system roles and their permissions. Idempotent.

    Returns a mapping of slug to role, so a caller that has just created a team
    can attach the Owner role without a second round trip to look the role up.
    """
    permissions = _ensure_permissions()

    granted = {
        OWNER_ROLE_SLUG: authorization.ROLE_OWNER_PERMISSIONS,
        MEMBER_ROLE_SLUG: authorization.ROLE_MEMBER_PERMISSIONS,
    }

    roles: dict[str, TeamRole] = {}
    for slug, name, description in SYSTEM_ROLES:
        role, _created = TeamRole.objects.get_or_create(
            team=team,
            slug=slug,
            defaults={'name': name, 'description': description, 'is_system': True},
        )
        codes = granted[slug]
        assert_known_permissions(codes)
        for code in codes:
            TeamRolePermission.objects.get_or_create(role=role, permission=permissions[code])
        roles[slug] = role
    return roles


def _require_active_user(user: User | None) -> User:
    if user is None or not user.is_active:
        raise TeamError('You must be signed in to manage teams.', reason='unauthenticated')
    return user


@transaction.atomic
def create_team(user: User | None, data: TeamInput) -> Team:
    """
    Create a team with `user` as its Owner and first member.

    One transaction for all three writes - the team, its roles and the owner's
    membership - because a team without roles cannot authorize anything and a
    team whose creator is not a member is a team nobody can open. All three or
    none.

    Creating a team does **not** create an organization and does not require
    one: the whole point is that a user can file a team idea without belonging
    to any tenant.
    """
    active_user = _require_active_user(user)

    name = _validate_name(data.name)
    description = _validate_description(data.description)

    team = Team.objects.create(
        name=name,
        slug=_unique_slug(name),
        description=description,
        owner=active_user,
    )
    roles = seed_team_roles(team)

    membership = TeamMembership.objects.create(team=team, user=active_user)
    TeamMembershipRole.objects.create(membership=membership, role=roles[OWNER_ROLE_SLUG])

    logger.info('Created team (team=%s, owner=%s).', team.pk, active_user.pk)
    return team


def add_existing_member(actor: User | None, team: Team | object, member: User) -> TeamMembership:
    """
    Put an **existing** account into `team` as a plain Member.

    Not the invitation path - it exists for the one case the invitation flow
    cannot serve: somebody who is already a member of the platform joining a
    team without an email round trip. It grants nothing but the Member role, so
    it cannot be used to mint a team Owner, and `team.members.manage` is
    required to call it, so it is as gated as inviting is.

    Re-adding somebody who was previously a member **reactivates** their row
    rather than creating a second one; the uniqueness constraint is on
    `(team, user)` and not on active-ness, precisely so this is the only
    possible outcome.
    """
    active_actor = _require_active_user(actor)
    try:
        authorization.require_permission(active_actor, team, authorization.TEAM_MEMBERS_MANAGE)
    except authorization.TeamAuthorizationError as exc:
        raise TeamError(exc.message, field=exc.field, reason=exc.reason) from None

    if not member.is_active:
        raise TeamError('That account is not active.')

    resolved = Team.objects.filter(pk=getattr(team, 'pk', team)).first()
    if resolved is None:
        raise TeamError('Team is unavailable.')

    with transaction.atomic():
        membership, _created = TeamMembership.objects.get_or_create(
            team=resolved,
            user=member,
            defaults={'status': TeamMembership.Status.ACTIVE},
        )
        if membership.status != TeamMembership.Status.ACTIVE:
            membership.status = TeamMembership.Status.ACTIVE
            membership.save()
        roles = seed_team_roles(resolved)
        TeamMembershipRole.objects.get_or_create(
            membership=membership, role=roles[MEMBER_ROLE_SLUG]
        )

    logger.info('Added team member (team=%s, user=%s).', resolved.pk, member.pk)
    return membership


def leave_team(user: User | None, team: Team | object) -> None:
    """
    Mark the caller's own team membership inactive.

    Refused for the team's Owner while they are its only active member: the
    alternative is a team nobody can administer, which cannot be repaired from
    the UI because managing it requires the permission they just gave up. They
    must hand the role on or add somebody else first.

    The team and the ideas filed for it stay exactly as they are. Deactivating a
    membership removes a person from a roster, not their work from the record.
    """
    active_user = _require_active_user(user)
    membership = authorization.get_membership(active_user, team)
    if membership is None:
        raise TeamError('You are not a member of this team.')

    resolved = Team.objects.filter(pk=membership.team_id).first()
    if resolved is None:
        raise TeamError('Team is unavailable.')

    with transaction.atomic():
        active_members = TeamMembership.objects.filter(
            team=resolved, status=TeamMembership.Status.ACTIVE
        ).count()
        is_owner = authorization.has_permission(
            active_user, resolved, authorization.TEAM_MEMBERS_MANAGE
        )
        if is_owner and active_members <= 1:
            raise TeamError(
                'You are the only member of this team. Add somebody else before you leave.'
            )

        membership.status = TeamMembership.Status.INACTIVE
        membership.save()

    logger.info('Team membership ended (team=%s, user=%s).', resolved.pk, active_user.pk)
