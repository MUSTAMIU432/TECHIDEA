"""
Platform administration authorization.

The console's single answer to "may this user do this administrative thing?",
asked by every selector, service and view in this app before it touches any
data. Nothing here reads a role, a flag or a route from the request: callers
pass the authenticated `User` (resolved once from the access token by
`identity.authentication.get_authenticated_user`) and this module asks
Django's permission backend about it.

The model
---------
Platform administration is a separate concern from organization
authorization, and it is expressed with the permission mechanism that is
already platform-scoped - Django's `auth.Permission`, declared on
`administration.AdminAuditEntry` (see `administration/models.py` for why):

- `ACCESS_CONSOLE` is the gate to the console at all: operational metadata -
  counts, user and organization listings, idea and review *metadata*.
- Every other permission is an **additional** capability and is only ever
  honoured together with `ACCESS_CONSOLE`:
  `INSPECT_IDEA_CONTENT` (the content of ideas that are not PUBLIC, their
  discussion and evidence, and review feedback), `MANAGE_USER_ACCOUNTS`,
  `MANAGE_ORGANIZATION_ROLES`, `MANAGE_CATEGORIES`.
- **Except `REVIEW_PLATFORM_SUBMISSIONS`**, which stands on its own. A platform
  reviewer works in the review workspace and on the idea's page, not in the
  console, and the console - every account, every organization, the audit trail -
  is the administrators' alone. So a reviewer holds the review permission and
  nothing else here, and never sees the console.

Who holds them: a user granted them directly or through a group (the
`grant_platform_admin` command creates the "Platform administrators" group),
and an active superuser, for whom Django's backend answers yes to every
permission. Nothing else: an organization Owner or Reviewer holds
organization-scoped permissions in `organizations.Permission`, which this
module never consults, and `is_staff` alone grants nothing here.

What this is not
----------------
It is not an authorization bypass. Holding a platform permission does not make
the other domains' checks disappear; the console has its own read paths
(`administration.selectors`) and its own write paths
(`administration.services`), each of which asks this module first and then
applies the owning domain's rules. It never calls into a domain's
member-facing function pretending to be a member.
"""

from dataclasses import dataclass

from ideas.models import Idea
from identity.models import User
from organizations.authorization import AuthorizationError

ACCESS_CONSOLE = 'administration.access_console'
INSPECT_IDEA_CONTENT = 'administration.inspect_idea_content'
MANAGE_USER_ACCOUNTS = 'administration.manage_user_accounts'
MANAGE_ORGANIZATION_ROLES = 'administration.manage_organization_roles'
MANAGE_CATEGORIES = 'administration.manage_categories'
# Decide an idea that has been submitted to the platform. Granted only by
# `grant_platform_reviewer` (which puts the holder in the "Platform reviewers"
# group), and deliberately unreachable from any organization or team role: a
# platform role is a platform-scoped `auth.Permission`, and an organization
# Reviewer holds organization-scoped codes that this module never consults.
REVIEW_PLATFORM_SUBMISSIONS = 'administration.review_platform_submissions'
# Hand a submission to a platform reviewer. Separate from the above because
# intake and deciding are different acts: the platform admin who routes work
# need not be the person who approves it.
ASSIGN_PLATFORM_REVIEWERS = 'administration.assign_platform_reviewers'
# Make people reviewers and form them into review teams. Separate from routing a
# submission to one: whoever builds the teams need not be whoever hands them work.
MANAGE_REVIEWERS = 'administration.manage_reviewers'
# Decide whether a review team's proposal is released to the idea's owner. Not held by the
# reviewers who write it: the author of a proposal never approves it.
RELEASE_PROPOSALS = 'administration.release_proposals'
# Give an existing account one of the platform's jobs - developer, delivery manager,
# intake, proposal approver (`administration.platform_roles`) - and take it away.
MANAGE_PLATFORM_ROLES = 'administration.manage_platform_roles'

# The console's own permissions: the gate, the additional capabilities, and
# *staffing* - making reviewers and handing out the platform's other jobs - but
# nothing about *deciding* anything. Named separately from `ALL_PERMISSIONS`
# because the "Platform administrators" group holds exactly this set, and the
# reason it does not hold review, routing or release is a separation of duties
# rather than an oversight: inspecting content and staffing the platform is
# running it, while routing submissions and deciding them is *doing* the work.
# Those jobs are given to named accounts on the console's Platform roles and
# Reviewers pages, each grant audited, and the operations themselves still refuse
# the conflicts (a lead routing to their own team, a writer releasing their own
# proposal, an author reviewing their own idea, a builder accepting their own work).
CONSOLE_PERMISSIONS = (
    ACCESS_CONSOLE,
    INSPECT_IDEA_CONTENT,
    MANAGE_USER_ACCOUNTS,
    MANAGE_ORGANIZATION_ROLES,
    MANAGE_CATEGORIES,
    MANAGE_REVIEWERS,
    MANAGE_PLATFORM_ROLES,
)

ALL_PERMISSIONS = (
    ACCESS_CONSOLE,
    INSPECT_IDEA_CONTENT,
    MANAGE_USER_ACCOUNTS,
    MANAGE_ORGANIZATION_ROLES,
    MANAGE_CATEGORIES,
    REVIEW_PLATFORM_SUBMISSIONS,
    ASSIGN_PLATFORM_REVIEWERS,
    MANAGE_REVIEWERS,
    RELEASE_PROPOSALS,
    MANAGE_PLATFORM_ROLES,
)


class AdministrationError(AuthorizationError):
    """A refused administrative operation, in the project's `(message, field, reason)` shape."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message, reason=reason, field=field)


@dataclass(frozen=True)
class AdminCapabilities:
    """
    What one user may do in the console. All false for anybody without
    `ACCESS_CONSOLE`, whatever else they hold - the additional permissions are
    never honoured on their own - except `can_review_platform_submissions`, which
    is the reviewer's and needs no console (see the module docstring).
    """

    can_access_console: bool = False
    can_inspect_idea_content: bool = False
    can_manage_user_accounts: bool = False
    can_manage_organization_roles: bool = False
    can_manage_categories: bool = False
    # The platform review track, held independently of the console: a platform
    # reviewer decides submissions from the review workspace and never needs the
    # console, which is for administrators only.
    can_review_platform_submissions: bool = False
    can_assign_platform_reviewers: bool = False
    can_manage_reviewers: bool = False
    can_release_proposals: bool = False
    can_manage_platform_roles: bool = False


NO_CAPABILITIES = AdminCapabilities()


def _active(user: User | None) -> User | None:
    if user is None or not user.is_active:
        return None
    return user


def capabilities_for(user: User | None) -> AdminCapabilities:
    """The console capabilities of `user`, from Django's per-request permission cache."""
    active_user = _active(user)
    if active_user is None:
        return NO_CAPABILITIES
    if not active_user.has_perm(ACCESS_CONSOLE):
        if active_user.has_perm(REVIEW_PLATFORM_SUBMISSIONS):
            return AdminCapabilities(can_review_platform_submissions=True)
        return NO_CAPABILITIES
    return AdminCapabilities(
        can_access_console=True,
        can_inspect_idea_content=active_user.has_perm(INSPECT_IDEA_CONTENT),
        can_manage_user_accounts=active_user.has_perm(MANAGE_USER_ACCOUNTS),
        can_manage_organization_roles=active_user.has_perm(MANAGE_ORGANIZATION_ROLES),
        can_manage_categories=active_user.has_perm(MANAGE_CATEGORIES),
        can_review_platform_submissions=active_user.has_perm(REVIEW_PLATFORM_SUBMISSIONS),
        can_assign_platform_reviewers=active_user.has_perm(ASSIGN_PLATFORM_REVIEWERS),
        can_manage_reviewers=active_user.has_perm(MANAGE_REVIEWERS),
        can_release_proposals=active_user.has_perm(RELEASE_PROPOSALS),
        can_manage_platform_roles=active_user.has_perm(MANAGE_PLATFORM_ROLES),
    )


def require_platform_reviewer(user: User | None) -> User:
    """
    `user`, if they hold `REVIEW_PLATFORM_SUBMISSIONS`; otherwise
    `AdministrationError`. No console access is needed or implied.

    The one gate for every platform review decision. It asks Django's backend
    about platform-scoped permissions and nothing else - no organization role,
    no team role, no `is_staff`, no membership of any kind - which is what
    makes an organization Reviewer structurally unable to approve an idea, and
    a team Owner structurally unable to approve their own.
    """
    active_user = _active(user)
    if active_user is None:
        raise AdministrationError('Authentication is required.', reason='unauthenticated')
    if not active_user.has_perm(REVIEW_PLATFORM_SUBMISSIONS):
        raise AdministrationError('You do not have permission to perform this action.')
    return active_user


def require_assign_reviewer(user: User | None) -> User:
    """
    `user`, if they hold both `ACCESS_CONSOLE` and `ASSIGN_PLATFORM_REVIEWERS`.

    Separate from `require_platform_reviewer` on purpose: routing a submission to
    a reviewer decides nothing about the idea, and the person who does the
    platform's intake need not be the person who approves. Asking for both would
    force every small platform team to be the same person.
    """
    return require_admin(user, ASSIGN_PLATFORM_REVIEWERS)


def require_manage_reviewers(user: User | None) -> User:
    """`user`, if they hold both `ACCESS_CONSOLE` and `MANAGE_REVIEWERS`."""
    return require_admin(user, MANAGE_REVIEWERS)


def require_release_proposals(user: User | None) -> User:
    """`user`, if they hold both `ACCESS_CONSOLE` and `RELEASE_PROPOSALS`."""
    return require_admin(user, RELEASE_PROPOSALS)


def require_manage_platform_roles(user: User | None) -> User:
    """`user`, if they hold both `ACCESS_CONSOLE` and `MANAGE_PLATFORM_ROLES`."""
    return require_admin(user, MANAGE_PLATFORM_ROLES)


def is_platform_admin(user: User | None) -> bool:
    return capabilities_for(user).can_access_console


def require_admin(user: User | None, *permissions: str) -> User:
    """
    `user`, if they hold `ACCESS_CONSOLE` and every one of `permissions`;
    otherwise `AdministrationError`.

    `unauthenticated` for no (or an inactive) user, `forbidden` for everyone
    else - one message for both "not an administrator" and "an administrator
    without this capability", so a refusal does not describe the permission
    model to somebody probing it.
    """
    active_user = _active(user)
    if active_user is None:
        raise AdministrationError('Authentication is required.', reason='unauthenticated')
    if not active_user.has_perms((ACCESS_CONSOLE, *permissions)):
        raise AdministrationError('You do not have permission to perform this action.')
    return active_user


def may_see_idea_content(capabilities: AdminCapabilities, visibility: str) -> bool:
    """
    Whether an administrator may see an idea's content (its title included).

    A PUBLIC idea is readable by every signed-in member of the platform, so an
    administrator reading it learns nothing a member could not. Anything
    narrower was shared with a smaller audience on purpose, and seeing it
    across organizations takes the explicit `INSPECT_IDEA_CONTENT` grant.
    """
    if not capabilities.can_access_console:
        return False
    return capabilities.can_inspect_idea_content or visibility == Idea.Visibility.PUBLIC
