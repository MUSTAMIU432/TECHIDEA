"""
Team-scoped authorization.

The exact counterpart of `organizations.authorization`, and deliberately
nothing more. The question is never "may this user do X" but "may this user do
X *in this team*", and the answer is derived from the user's ACTIVE
`TeamMembership` of that team and the `organizations.Permission` records on the
roles attached to it - the same permission table the organization side uses, so
"what a role may do" has one definition per code.

**What a team can and cannot decide.** A team's reviewers (`team.ideas.review`,
held by the Owner role and the Reviewer role) may *verify* a team idea or send it
back for changes - the same confirm-or-return decision an organization's reviewers
make, and for the same reason: it is the team saying "this is what we want to put
forward", not an approval. There is still no `team.ideas.approve`, and none can be
added: approval belongs to the platform (`administration.REVIEW_PLATFORM_SUBMISSIONS`),
and a permission code that does not exist cannot be granted to a role
(`teams.services` refuses to attach a code the platform has not declared). So a
team can check an idea before the platform sees it, and still cannot approve one.

No client-supplied identity: callers pass the authenticated `User` and the
team, and this module decides. Nothing here reads a request.
"""

from typing import Literal

from identity.models import User
from teams.models import Team, TeamMembership, TeamMembershipRole

TEAM_VIEW = 'team.view'
TEAM_UPDATE = 'team.update'
TEAM_MEMBERS_VIEW = 'team.members.view'
TEAM_MEMBERS_MANAGE = 'team.members.manage'
# Any active member may put the team's idea forward to the platform. It is a
# submission permission, not an approval one: it cannot confirm, and it cannot
# approve, because neither code exists on a team role.
TEAM_IDEAS_SUBMIT = 'team.ideas.submit'
# Verify a team idea, or send it back for changes or more documents. Not an
# approval: the platform still decides. See the module docstring.
TEAM_IDEAS_REVIEW = 'team.ideas.review'

ALL_TEAM_PERMISSIONS = (
    TEAM_VIEW,
    TEAM_UPDATE,
    TEAM_MEMBERS_VIEW,
    TEAM_MEMBERS_MANAGE,
    TEAM_IDEAS_SUBMIT,
    TEAM_IDEAS_REVIEW,
)

# Which system role holds which code, seeded by `teams.seed_team_roles` and
# re-read by `teams.services.assert_known_permissions`. The Member role gets
# only what it needs to take part: see the team, see who is in it, and file.
ROLE_OWNER_PERMISSIONS = ALL_TEAM_PERMISSIONS
ROLE_MEMBER_PERMISSIONS = (TEAM_VIEW, TEAM_MEMBERS_VIEW, TEAM_IDEAS_SUBMIT)
# A Member who is also trusted to check the team's ideas before the platform does.
ROLE_REVIEWER_PERMISSIONS = (*ROLE_MEMBER_PERMISSIONS, TEAM_IDEAS_REVIEW)

AuthorizationReason = Literal['unauthenticated', 'membership_required', 'forbidden']


class TeamAuthorizationError(Exception):
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


def _team_id(team: Team | object) -> int | None:
    try:
        return int(str(getattr(team, 'pk', team)))
    except (TypeError, ValueError):
        return None


def _active_user(user: User | None) -> User | None:
    if user is None or not user.is_active:
        return None
    return user


def get_membership(user: User | None, team: Team | object) -> TeamMembership | None:
    """`user`'s ACTIVE membership of `team`, or `None`."""
    active_user = _active_user(user)
    normalized_id = _team_id(team)
    if active_user is None or normalized_id is None:
        return None

    return (
        TeamMembership.objects.filter(
            user=active_user,
            team_id=normalized_id,
            status=TeamMembership.Status.ACTIVE,
        )
        .select_related('team')
        .first()
    )


def is_member_of(user: User | None, team: Team | object) -> bool:
    return get_membership(user, team) is not None


def active_team_ids(user: User | None) -> list[int]:
    """
    The ids of every team `user` is an *active* member of.

    The set-valued sibling of `get_membership`, present for the same reason as
    `organizations.authorization.active_organization_ids`: "which teams am I in"
    has to be answerable in one query for a visibility filter, and its
    definition must not be copied into whichever domain first needs it.
    """
    active_user = _active_user(user)
    if active_user is None:
        return []

    return list(
        TeamMembership.objects.filter(
            user=active_user,
            status=TeamMembership.Status.ACTIVE,
        ).values_list('team_id', flat=True)
    )


def membership_has_permission(membership: TeamMembership | None, permission_code: str) -> bool:
    if membership is None or not permission_code:
        return False

    return TeamMembershipRole.objects.filter(
        membership=membership,
        role__team_id=membership.team_id,
        role__role_permissions__permission__code=permission_code,
    ).exists()


def has_permission(user: User | None, team: Team | object, permission_code: str) -> bool:
    return membership_has_permission(get_membership(user, team), permission_code)


def require_permission(
    user: User | None,
    team: Team | object,
    permission_code: str,
) -> TeamMembership:
    """
    `membership` if `user` is an active member of `team` holding
    `permission_code`, otherwise `TeamAuthorizationError`.

    The order of the refusals matches `organizations.authorization.require_permission`
    exactly: unauthenticated first, then membership, then the permission. A team
    the caller is not in and a team that does not exist are the same answer, so
    the argument cannot be used to discover teams.
    """
    if _active_user(user) is None:
        raise TeamAuthorizationError('Authentication is required.', reason='unauthenticated')

    normalized_id = _team_id(team)
    if normalized_id is None or not Team.objects.filter(pk=normalized_id).exists():
        raise TeamAuthorizationError('Team is unavailable.')

    membership = get_membership(user, normalized_id)
    if membership is None:
        raise TeamAuthorizationError(
            'You must be an active member of this team.',
            reason='membership_required',
        )

    if not has_permission(user, normalized_id, permission_code):
        raise TeamAuthorizationError('You do not have permission to perform this action.')

    return membership


def can_submit_for(user: User | None, team: Team | object) -> bool:
    """
    Whether `user` holds the standing to submit **for** this team.

    The permission half of the answer, not the whole of it. A team idea is put
    forward by **its author**, on the same rule as every other context
    (`ideas.lifecycle.TRANSITIONS` makes `DRAFT -> SUBMITTED` the author's move,
    and that is the product's shape rather than an accident). This function says
    the additional thing the author must also hold: they are still an active
    member of the team the idea belongs to.

    So the two answers are combined in `ideas.services.submission_target`:
    author *and* `can_submit_for`. The earlier version of this docstring claimed
    "any active member may submit that team's idea, which is the point of
    authorized team submission", and the platform did not do that - the lifecycle
    refused it. The claim was wrong rather than the code, and it is corrected
    here rather than in the matrix, because the matrix is what the product asked
    for: an idea is put forward by whoever wrote it, and a team decides nothing.

    Confirmation and approval remain refused structurally, by the absence of a
    code to hold.
    """
    return has_permission(user, team, TEAM_IDEAS_SUBMIT)


def can_review_for(user: User | None, team: Team | object) -> bool:
    """
    Whether `user` may verify the team's ideas (an active member holding
    `team.ideas.review`). Per-idea rules - not being the author, the idea being a
    team idea in the team's own review stage - are `ideas.lifecycle`'s.
    """
    return has_permission(user, team, TEAM_IDEAS_REVIEW)
