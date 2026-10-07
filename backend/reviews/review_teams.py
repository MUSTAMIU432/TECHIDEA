"""
Platform reviewers and the teams they work in.

Three things a platform admin needs and did not have: a way to **make** somebody a
reviewer (until now only a test helper could), a way to **form** reviewers into a
team, and a way to **route an idea to a team**. All three are gated by the new
`administration.manage_reviewers` permission (the first two) and the existing
`assign_platform_reviewers` (routing), which are different powers on purpose: whoever
builds the teams need not be whoever hands them work, and neither of them decides an
idea.

How a team works on an idea
---------------------------
Every member can read it and **send it back to its owner** asking for changes or more
documents. Only the team's **lead** can approve or reject, so a decision always has
one accountable name. `Review.reviewer` stays the person who opened the round and
`Review.decided_by` records who actually pressed the button when that is someone
else. The rules are enforced in `reviews.services` (`team_may_decide`); this module
only owns who is in which team.

Everything here is audited in the same append-only table the console uses.
"""

import logging

from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission as AuthPermission
from django.db import IntegrityError, transaction
from django.utils import timezone

from administration import authorization as admin_authorization
from administration import services as admin_services
from administration.authorization import AdministrationError
from administration.models import AdminAuditEntry
from ideas.models import Idea
from identity.models import User
from reviews.models import Review, ReviewAssignment, ReviewTeam, ReviewTeamMember

logger = logging.getLogger(__name__)

PLATFORM_REVIEWER_GROUP = 'Platform reviewers'
#: What a reviewer holds: the console, the content they review, and the right to review.
REVIEWER_PERMISSIONS = (
    admin_authorization.ACCESS_CONSOLE,
    admin_authorization.INSPECT_IDEA_CONTENT,
    admin_authorization.REVIEW_PLATFORM_SUBMISSIONS,
)
#: The stages in which a submission can be routed (it has reached the platform).
_PLATFORM_STAGES = (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED)


class ReviewTeamError(AdministrationError):
    """A refused reviewer or team operation, in the project's `(message, field)` shape."""


def _reviewer_group() -> Group:
    group, _ = Group.objects.get_or_create(name=PLATFORM_REVIEWER_GROUP)
    codenames = [code.split('.', 1)[1] for code in REVIEWER_PERMISSIONS]
    permissions = AuthPermission.objects.filter(
        content_type__app_label='administration', codename__in=codenames
    )
    if permissions.count() != len(codenames):
        raise ReviewTeamError('The administration permissions are missing. Run migrate first.')
    group.permissions.add(*permissions)
    return group


def _active_user_by_email(email: str) -> User:
    target = User.objects.filter(email=User.objects.normalize_email(email or '')).first()
    if target is None:
        raise ReviewTeamError('No account uses this email address.', field='email')
    if not target.is_active:
        raise ReviewTeamError('This account is deactivated.', field='email')
    return target


def is_reviewer(user: User | None) -> bool:
    """Whether `user` holds the platform review permission (and the console it needs)."""
    return admin_authorization.capabilities_for(user).can_review_platform_submissions


# --- reviewers ---------------------------------------------------------------------------


def list_reviewers(actor: User | None) -> list[User]:
    """Every active account that can review for the platform. Empty without the permission."""
    if not admin_authorization.capabilities_for(actor).can_manage_reviewers:
        return []
    from ideas.services import platform_reviewers

    return sorted(platform_reviewers(), key=lambda u: (u.first_name, u.last_name, u.email))


@transaction.atomic
def grant_reviewer(actor: User | None, email: str) -> User:
    """Make an existing account a platform reviewer. Authors still cannot review their own."""
    admin_authorization.require_manage_reviewers(actor)
    target = _active_user_by_email(email)
    target.groups.add(_reviewer_group())
    admin_services.record_event(
        actor, AdminAuditEntry.Action.REVIEWER_GRANTED, 'user', target.pk, target.email
    )
    return target


@transaction.atomic
def revoke_reviewer(actor: User | None, user_id: object) -> User:
    """
    Remove an account's reviewer group, and take it out of every team.

    Refused while they lead a team (name another lead first) or hold an open review,
    so no idea is left waiting on somebody who can no longer act.
    """
    admin_authorization.require_manage_reviewers(actor)
    target = User.objects.filter(pk=_as_int(user_id)).first()
    if target is None:
        raise ReviewTeamError('That person is not available.', field='user')
    if ReviewTeam.objects.filter(lead=target, is_active=True).exists():
        raise ReviewTeamError(
            'They lead a review team. Choose another lead before removing them.', field='user'
        )
    if Review.objects.filter(
        reviewer=target, scope=Review.Scope.PLATFORM, completed_at__isnull=True
    ).exists():
        raise ReviewTeamError('They have a review in progress. Finish or reassign it first.')

    target.groups.remove(_reviewer_group())
    ReviewTeamMember.objects.filter(user=target).delete()
    admin_services.record_event(
        actor, AdminAuditEntry.Action.REVIEWER_REVOKED, 'user', target.pk, target.email
    )
    return target


# --- teams ---------------------------------------------------------------------------------


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _eligible_members(user_ids: list[int]) -> list[User]:
    users = list(User.objects.filter(pk__in=user_ids, is_active=True))
    if len(users) != len(set(user_ids)):
        raise ReviewTeamError('One of those people is not available.', field='members')
    for user in users:
        if not is_reviewer(user):
            raise ReviewTeamError(
                f'{user.email} is not a platform reviewer. Make them one first.', field='members'
            )
    return users


def _clean_name(name: str | None) -> str:
    text = (name or '').strip()
    if not text:
        raise ReviewTeamError('Give the team a name.', field='name')
    if len(text) > 120:
        raise ReviewTeamError('The name must be 120 characters or fewer.', field='name')
    return text


def list_teams(actor: User | None) -> list[ReviewTeam]:
    """Every review team. Visible to whoever can manage reviewers or route work."""
    caps = admin_authorization.capabilities_for(actor)
    if not (caps.can_manage_reviewers or caps.can_assign_platform_reviewers):
        return []
    return list(ReviewTeam.objects.select_related('lead').prefetch_related('members__user'))


def teams_of(user: User | None) -> list[ReviewTeam]:
    """The active teams `user` is a member of."""
    if user is None or not user.is_active:
        return []
    return list(
        ReviewTeam.objects.filter(is_active=True, members__user=user).select_related('lead')
    )


@transaction.atomic
def create_team(
    actor: User | None, name: str, lead_id: object, member_ids: list[object] | None = None
) -> ReviewTeam:
    admin_authorization.require_manage_reviewers(actor)
    clean = _clean_name(name)
    if ReviewTeam.objects.filter(name__iexact=clean).exists():
        raise ReviewTeamError('A team with that name already exists.', field='name')

    ids = {_as_int(lead_id), *(_as_int(m) for m in (member_ids or []))}
    people = {u.pk: u for u in _eligible_members(sorted(ids))}
    lead = people.get(_as_int(lead_id))
    if lead is None:
        raise ReviewTeamError('Choose a lead for the team.', field='lead')

    team = ReviewTeam.objects.create(name=clean, lead=lead, created_by=actor)
    for user in people.values():
        ReviewTeamMember.objects.create(team=team, user=user, added_by=actor)
    admin_services.record_event(
        actor,
        AdminAuditEntry.Action.REVIEW_TEAM_CREATED,
        'review_team',
        team.pk,
        team.name,
        metadata={'lead': lead.pk, 'members': sorted(people)},
    )
    return team


@transaction.atomic
def update_team(
    actor: User | None,
    team_id: object,
    *,
    name: str | None = None,
    lead_id: object | None = None,
    member_ids: list[object] | None = None,
    is_active: bool | None = None,
) -> ReviewTeam:
    """Rename a team, change its lead or its members, or retire it. Only what is passed changes."""
    admin_authorization.require_manage_reviewers(actor)
    team = ReviewTeam.objects.select_for_update().filter(pk=_as_int(team_id)).first()
    if team is None:
        raise ReviewTeamError('That team is not available.', field='team')

    if name is not None:
        clean = _clean_name(name)
        if ReviewTeam.objects.filter(name__iexact=clean).exclude(pk=team.pk).exists():
            raise ReviewTeamError('A team with that name already exists.', field='name')
        team.name = clean

    current = set(team.members.values_list('user_id', flat=True))
    wanted = {_as_int(m) for m in member_ids} if member_ids is not None else set(current)
    new_lead_id = _as_int(lead_id) if lead_id is not None else team.lead_id
    wanted.add(new_lead_id)  # the lead is always a member
    people = {u.pk: u for u in _eligible_members(sorted(wanted))}
    team.lead = people[new_lead_id]
    if is_active is not None:
        team.is_active = bool(is_active)
    team.save()

    for user_id in current - wanted:
        ReviewTeamMember.objects.filter(team=team, user_id=user_id).delete()
    for user_id in wanted - current:
        ReviewTeamMember.objects.create(team=team, user=people[user_id], added_by=actor)

    admin_services.record_event(
        actor,
        AdminAuditEntry.Action.REVIEW_TEAM_UPDATED,
        'review_team',
        team.pk,
        team.name,
        metadata={'lead': team.lead_id, 'members': sorted(wanted), 'active': team.is_active},
    )
    return team


# --- routing an idea to a team ----------------------------------------------------------------


@transaction.atomic
def assign_team(actor: User | None, idea_id: object, team_id: object) -> ReviewAssignment:
    """
    Route a submitted idea to a review team.

    The same preconditions as routing it to one reviewer (it must have reached the
    platform and not already be under review), and the same permission. The
    assignment names the team's lead as its `reviewer`, the one accountable name.
    An admin cannot route an idea to a team they themselves lead: intake and decision
    would then be one person.
    """
    admin_authorization.require_assign_reviewer(actor)
    team = ReviewTeam.objects.select_related('lead').filter(pk=_as_int(team_id)).first()
    if team is None or not team.is_active:
        raise ReviewTeamError('That team is not available.', field='team')
    if team.lead_id == actor.pk:
        raise ReviewTeamError(
            'You lead this team, so somebody else has to hand it work.', field='team'
        )

    # Read by the routing permission itself: whoever hands out the platform's work has to be
    # able to see that work, and a submission is the platform's from the moment it arrives.
    idea = (
        Idea.objects.select_for_update()
        .filter(pk=_as_int(idea_id), platform_version__gte=1)
        .first()
    )
    if idea is None:
        raise ReviewTeamError('Idea is unavailable.')
    if idea.status not in _PLATFORM_STAGES:
        raise ReviewTeamError('Only an idea submitted to the platform can be assigned.')
    if Review.objects.filter(idea=idea, completed_at__isnull=True).exists():
        raise ReviewTeamError('This idea is already being reviewed.')
    if idea.author_id in set(team.members.values_list('user_id', flat=True)):
        raise ReviewTeamError('A team member wrote this idea, so this team cannot review it.')

    ReviewAssignment.objects.filter(idea=idea, released_at__isnull=True).update(
        released_at=timezone.now()
    )
    try:
        with transaction.atomic():
            assignment = ReviewAssignment.objects.create(
                idea=idea, reviewer=team.lead, team=team, assigned_by=actor
            )
    except IntegrityError:
        raise ReviewTeamError('This idea is already assigned.') from None

    admin_services.record_event(
        actor,
        AdminAuditEntry.Action.REVIEW_TEAM_ASSIGNED,
        'idea',
        idea.pk,
        idea.title,
        metadata={'team': team.pk, 'lead': team.lead_id},
    )
    _tell_the_team(team, idea)
    return assignment


def _tell_the_team(team: ReviewTeam, idea: Idea) -> None:
    from notifications import services as notifications

    members = [m.user for m in team.members.select_related('user') if m.user.is_active]
    notifications.deliver(
        recipients=members,
        kind='review.assigned',
        title=f'"{idea.title}" was assigned to {team.name}',
        body=f'{team.lead.get_full_name() or team.lead.email} leads this review.',
        idea=idea,
        send_email=False,
    )


# --- the rules a team review is held to ------------------------------------------------------


def current_team(idea: Idea) -> ReviewTeam | None:
    """The team the idea is currently routed to, if any."""
    assignment = (
        ReviewAssignment.objects.select_related('team__lead')
        .filter(idea=idea, released_at__isnull=True, team__isnull=False)
        .first()
    )
    return assignment.team if assignment else None


def is_member(user: User | None, team: ReviewTeam) -> bool:
    return (
        user is not None
        and user.is_active
        and ReviewTeamMember.objects.filter(team=team, user=user).exists()
    )


def team_may_review(user: User | None, idea: Idea) -> bool:
    """
    For an idea routed to a team: only the team's members may review it. For one that
    is not, this says nothing (`True`), so unrouted ideas keep working as before.
    """
    team = current_team(idea)
    return team is None or is_member(user, team)


def team_may_decide(user: User | None, idea: Idea, decision: str) -> bool:
    """
    Whether `user` may record `decision` on a team-routed idea: any member may send it
    back for changes, only the lead may approve or reject. `True` for an unrouted idea.
    """
    team = current_team(idea)
    if team is None:
        return True
    if not is_member(user, team):
        return False
    if decision == Review.Decision.CHANGES_REQUESTED:
        return True
    return user.pk == team.lead_id
