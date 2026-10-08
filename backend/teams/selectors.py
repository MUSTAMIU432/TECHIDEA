"""
Reading teams.

Every read of a team goes through this module, for the same structural reason
`ideas/selectors.py` exists: `teams/services.py` decides who may *write*, and
nothing decides who may *read* unless it is written down once here. A caller
that reached for `Team.objects` directly would get every team on the platform.

The rules, in order:

1. **Unauthenticated or inactive** - nothing, ever. Same answer for a team that
   does not exist and one the caller is not in, so a team id cannot be used to
   discover teams.
2. **Membership** - a team is readable by its active members. `TEAM_VIEW` is
   held by both system roles, so membership is the rule; the permission is
   still what is checked, so a future role without `TEAM_VIEW` would not leak
   through.

**Nothing here filters ideas.** A team's ideas are read through
`ideas/selectors.py`, which joins the team's ids from
`teams.authorization.active_team_ids` - so "who may see this team's ideas" has
one definition, in the domain that owns ideas, rather than a copy per reader.
"""

from django.db.models import Prefetch, QuerySet

from identity.models import User
from teams import authorization
from teams.models import Team, TeamMembership, TeamMembershipRole


def _base_queryset() -> QuerySet[Team]:
    # `owner` is select-related because every team card renders whose team it
    # is; `prefetch_related` on the roster is what keeps a list of teams to one
    # query for the teams plus two for all their rosters, rather than two per
    # row.
    return Team.objects.select_related('owner').prefetch_related(
        Prefetch(
            'memberships',
            queryset=TeamMembership.objects.select_related('user')
            .filter(status=TeamMembership.Status.ACTIVE)
            .order_by('created_at', 'pk'),
            to_attr='active_memberships',
        ),
        'memberships__membership_roles__role__role_permissions__permission',
    )


def get_team(user: User | None, team_id: object) -> Team | None:
    """One team `user` is an active member of, or `None`."""
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(team_id))
    except (TypeError, ValueError):
        return None

    if authorization.get_membership(user, normalized_id) is None:
        return None

    return _base_queryset().filter(pk=normalized_id).first()


def list_teams(user: User | None) -> QuerySet[Team]:
    """Every team `user` is an active member of, by name."""
    if user is None or not user.is_active:
        return Team.objects.none()

    return (
        _base_queryset()
        .filter(pk__in=authorization.active_team_ids(user))
        .order_by('name', 'created_at')
    )


def owned_teams(user: User | None) -> QuerySet[Team]:
    """The teams `user` owns. Narrower than `list_teams` and used for the switcher."""
    if user is None or not user.is_active:
        return Team.objects.none()

    return _base_queryset().filter(owner=user).order_by('name', 'created_at')


def list_members(team: Team) -> QuerySet[TeamMembership]:
    """One team's active roster, oldest join first. `team` must already be authorized."""
    return (
        TeamMembership.objects.select_related('user')
        .prefetch_related(
            Prefetch(
                'membership_roles',
                queryset=TeamMembershipRole.objects.select_related('role'),
            )
        )
        .filter(team=team, status=TeamMembership.Status.ACTIVE)
        .order_by('created_at', 'pk')
    )


def roles_for(membership: TeamMembership) -> list[str]:
    """The role slugs a membership holds, for a card or a header. Sorted, deduplicated."""
    return sorted({link.role.slug for link in membership.membership_roles.select_related('role')})
