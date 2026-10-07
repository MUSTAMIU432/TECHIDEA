"""
Who may do what in the automation delivery lifecycle.

Three audiences, kept apart on purpose:

- **Readers** may see an opportunity exactly when they may read its source idea
  (`ideas.selectors.can_view_idea`), so delivery adds no second visibility rule
  to drift from the first, and an opportunity can never be reached by changing an
  id when the idea behind it could not be.
- **Owner side** - the idea's author, and for a team idea its active members -
  may *start* the opportunity and *define requirements*. That is what the
  stakeholder knows. It does **not** extend to the solution, the proposal or
  anything past them: owning the problem is not holding developer permissions.
- **Delivery managers** hold the platform permission
  `automation.manage_delivery` (a platform-scoped `auth.Permission`, never an
  organization role). They run the lifecycle and may read every opportunity, the
  way platform reviewers may read every submission.

Nothing here reads an identity from a request: callers pass the authenticated
user and the objects the operation is about.
"""

from automation.models import AutomationOpportunity
from ideas import selectors as idea_selectors
from ideas.models import Idea
from identity.models import User
from organizations.authorization import AuthorizationError
from teams import authorization as team_authorization

MANAGE_DELIVERY = 'automation.manage_delivery'
#: Held by a developer who may be handed delivery work (a team qualifies through its members).
BE_ASSIGNABLE = 'automation.be_assignable'


class AutomationError(AuthorizationError):
    """A refused delivery operation, in the project's `(message, field, reason)` shape."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message, reason=reason, field=field)


def _active(user: User | None) -> User | None:
    return user if user is not None and user.is_active else None


def is_delivery_manager(user: User | None) -> bool:
    active = _active(user)
    return active is not None and active.has_perm(MANAGE_DELIVERY)


def is_owner_side(user: User | None, idea: Idea) -> bool:
    """The idea's author, or - for a team idea - an active member of that team."""
    active = _active(user)
    if active is None:
        return False
    if idea.author_id == active.pk:
        return True
    if idea.submission_context == Idea.SubmissionContext.TEAM and idea.team_id is not None:
        return team_authorization.is_member_of(active, idea.team_id)
    return False


def is_assigned_to(user: User | None, opportunity: AutomationOpportunity) -> bool:
    """The developer, or a member of the team, the opportunity is actively assigned to."""
    active = _active(user)
    if active is None:
        return False
    for assignment in opportunity.assignments.filter(status='active'):
        if assignment.assignee_user_id == active.pk:
            return True
        if assignment.assignee_team_id is not None and team_authorization.is_member_of(
            active, assignment.assignee_team_id
        ):
            return True
    return False


def can_view(user: User | None, opportunity: AutomationOpportunity) -> bool:
    """
    The idea's readers, delivery managers, and whoever is actively assigned to deliver it:
    the developer must be able to open the work they were handed even when the idea
    itself is private to its owner.
    """
    return (
        is_delivery_manager(user)
        or idea_selectors.can_view_idea(user, opportunity.idea)
        or is_assigned_to(user, opportunity)
    )


def can_create_for(user: User | None, idea: Idea) -> bool:
    """
    Whether `user` may open an opportunity by hand. Only a delivery manager: the normal path is
    the owner's go-ahead, which opens it automatically.
    """
    return is_delivery_manager(user)


def can_manage_requirements(user: User | None, opportunity: AutomationOpportunity) -> bool:
    return is_delivery_manager(user) or is_owner_side(user, opportunity.idea)


def can_edit_solution(user: User | None, opportunity: AutomationOpportunity) -> bool:
    return is_delivery_manager(user)


def can_transition(user: User | None, opportunity: AutomationOpportunity) -> bool:
    return is_delivery_manager(user)


# --- proposal, assignment and the project -----------------------------------------


def can_edit_proposal(user: User | None, opportunity: AutomationOpportunity) -> bool:
    """Writing and submitting the proposal is the delivery team's."""
    return is_delivery_manager(user)


def can_review_proposal(user: User | None, opportunity: AutomationOpportunity) -> bool:
    """Accepting, rejecting or sending back a proposal is the owner side's - it is their problem."""
    return is_owner_side(user, opportunity.idea)


def can_assign(user: User | None, opportunity: AutomationOpportunity) -> bool:
    return is_delivery_manager(user)


def is_eligible_assignee(user: User | None) -> bool:
    active = _active(user)
    return active is not None and active.has_perm(BE_ASSIGNABLE)


def is_eligible_team(team) -> bool:
    """A team qualifies when at least one active member may be assigned delivery work."""
    from teams.models import TeamMembership

    members = TeamMembership.objects.filter(
        team=team, status=TeamMembership.Status.ACTIVE
    ).select_related('user')
    return any(is_eligible_assignee(m.user) for m in members)


def is_assignee(user: User | None, project) -> bool:
    """The developer, or an active member of the team, the project was assigned to."""
    active = _active(user)
    if active is None:
        return False
    if project.assigned_user_id == active.pk:
        return True
    return project.assigned_team_id is not None and team_authorization.is_member_of(
        active, project.assigned_team_id
    )


def can_view_project(user: User | None, project) -> bool:
    return can_view(user, project.opportunity)


def can_manage_project(user: User | None, project) -> bool:
    """Run the project: its tasks, milestones, tests, deployments. Delivery side only."""
    return is_delivery_manager(user) or is_assignee(user, project)


def can_perform_uat(user: User | None, project) -> bool:
    """
    Accept or reject the delivered work. The stakeholder's, never the builder's: UAT
    that the developer could pass for themselves would not be acceptance.
    """
    return is_owner_side(user, project.opportunity.idea)


def can_record_impact(user: User | None, project) -> bool:
    return can_manage_project(user, project) or is_owner_side(user, project.opportunity.idea)
