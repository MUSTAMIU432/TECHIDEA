"""
Reading the platform for the administration console.

This module is the console's *explicit* cross-tenant read path. Every other
read in the project is scoped to the caller - `ideas.selectors` by visibility
and membership, `reviews.selectors` by reviewer eligibility - and none of that
is loosened here. Instead each function below:

1. asks `administration.authorization.require_admin` first, so a caller
   without `ACCESS_CONSOLE` gets `None` or an empty page - the same "nothing
   here" answer the rest of the project gives to unauthorized reads, so these
   are not an oracle either;
2. reads the models directly, across organizations, which is the purpose of
   platform administration and is only reachable through step 1.

Content is a second gate on top of that. Idea content, discussion, evidence
and review feedback are only returned under `INSPECT_IDEA_CONTENT` (see
`authorization.may_see_idea_content`); callers are handed the capabilities so
the GraphQL adapter can redact without re-deciding, and the searches below
never match on content the caller could not read.

**`INSPECT_IDEA_CONTENT` is also what gates a private message body**, because a
message is the one thing in the platform a person wrote *for named people*
rather than for the platform. Everything else the console shows about teams,
invitations, messages and notifications is metadata about the platform and is
under `ACCESS_CONSOLE` alone; see the notes on those four sections at the end of
this module.

Performance
-----------
Every list is paged with `ideas.pagination.paginate` (bounded by
`MAX_PAGE_SIZE`), filtered in the database, and built so that a page costs a
fixed number of queries whatever its size: per-row counts are correlated
subqueries (a `Count` over several joins would both multiply rows and add a
`GROUP BY` to a query that is also sliced and counted), and related rows are
`select_related`/`prefetch_related`. The tests pin the query counts.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import Permission as AuthPermission
from django.db.models import (
    BooleanField,
    Case,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from administration import authorization
from administration.authorization import AdminCapabilities, AdministrationError
from administration.models import AdminAuditEntry
from ideas.models import Attachment, Category, Comment, Idea, IdeaTransition, Vote
from ideas.pagination import Page, empty_page, paginate
from identity.models import RefreshSession, User
from invitations.models import Invitation
from messaging.models import Message, MessageParticipant, MessageThread
from notifications.models import Notification
from organizations.models import Membership, MembershipRole, Organization, Role
from organizations.services import DEFAULT_OWNER_ROLE_SLUG, REVIEWER_ROLE_SLUG
from reviews.models import Review
from teams.models import Team, TeamMembership, TeamMembershipRole, TeamRole

RECENT_ACTIVITY_LIMIT = 10
USER_AUDIT_LIMIT = 20


def _capabilities(user: User | None) -> AdminCapabilities | None:
    """The caller's capabilities, or `None` if they may not use the console at all."""
    try:
        authorization.require_admin(user)
    except AdministrationError:
        return None
    return authorization.capabilities_for(user)


def _normalize_id(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _count_of(model, field: str) -> Coalesce:
    """A correlated `COUNT(*)` of `model` rows whose `field` is the outer row."""
    return Coalesce(
        Subquery(
            model.objects.filter(**{field: OuterRef('pk')})
            .order_by()
            .values(field)
            .annotate(total=Count('*'))
            .values('total')[:1],
            output_field=IntegerField(),
        ),
        0,
    )


def _content_search(search: str, capabilities: AdminCapabilities, prefix: str = '') -> Q:
    """
    A title match an administrator is allowed to make: every idea's title under
    `INSPECT_IDEA_CONTENT`, PUBLIC ideas' titles otherwise - so searching cannot
    be used to confirm a private title the caller could not read.
    """
    title_match = Q(**{f'{prefix}title__icontains': search})
    if capabilities.can_inspect_idea_content:
        return title_match
    return title_match & Q(**{f'{prefix}visibility': Idea.Visibility.PUBLIC})


# --- overview ------------------------------------------------------------------


@dataclass(frozen=True)
class StatusCount:
    status: str
    count: int


@dataclass(frozen=True)
class Overview:
    capabilities: AdminCapabilities
    user_count: int
    active_user_count: int
    organization_count: int
    idea_count: int
    ideas_by_status: list[StatusCount]
    open_review_count: int
    completed_review_count: int
    recent_transitions: list[IdeaTransition]
    recent_admin_actions: list[AdminAuditEntry]


def overview(user: User | None) -> Overview | None:
    """The platform at a glance, from live data. `None` for a non-administrator."""
    capabilities = _capabilities(user)
    if capabilities is None:
        return None

    user_counts = User.objects.aggregate(
        total=Count('pk'), active=Count('pk', filter=Q(is_active=True))
    )
    by_status = dict(
        Idea.objects.order_by()
        .values_list('status')
        .annotate(total=Count('pk'))
        .values_list('status', 'total')
    )
    review_counts = Review.objects.aggregate(
        open=Count('pk', filter=Q(completed_at__isnull=True)),
        completed=Count('pk', filter=Q(completed_at__isnull=False)),
    )

    return Overview(
        capabilities=capabilities,
        user_count=user_counts['total'],
        active_user_count=user_counts['active'],
        organization_count=Organization.objects.count(),
        idea_count=sum(by_status.values()),
        # Every status, in lifecycle order, zeros included - a status with no
        # ideas is an answer, not a missing row.
        ideas_by_status=[
            StatusCount(status=value, count=by_status.get(value, 0))
            for value, _label in Idea.Status.choices
        ],
        open_review_count=review_counts['open'],
        completed_review_count=review_counts['completed'],
        recent_transitions=list(
            IdeaTransition.objects.select_related('idea__organization', 'actor').order_by(
                '-created_at', '-pk'
            )[:RECENT_ACTIVITY_LIMIT]
        ),
        recent_admin_actions=list(
            AdminAuditEntry.objects.select_related('actor')[:RECENT_ACTIVITY_LIMIT]
        ),
    )


# --- users ---------------------------------------------------------------------


@dataclass(frozen=True)
class UserFilters:
    search: str | None = None
    is_active: bool | None = None
    organization_id: object = None
    platform_admins_only: bool = False


def _platform_admin_condition() -> Q:
    """
    The SQL form of "holds `ACCESS_CONSOLE`", for the listing badge and filter:
    a superuser, a direct grant, or a grant through a group. The same three
    sources Django's `ModelBackend` answers `has_perm` from; `require_admin`
    remains the only check that authorizes anything.
    """
    access = AuthPermission.objects.filter(
        content_type__app_label='administration', codename='access_console'
    )
    direct = User.user_permissions.through.objects.filter(
        user_id=OuterRef('pk'), permission__in=access
    )
    via_group = User.groups.through.objects.filter(
        user_id=OuterRef('pk'), group__permissions__in=access
    )
    return Q(is_superuser=True) | Q(Exists(direct)) | Q(Exists(via_group))


def _membership_prefetch() -> Prefetch:
    return Prefetch(
        'memberships',
        queryset=Membership.objects.select_related('organization')
        .prefetch_related(
            Prefetch(
                'membership_roles',
                queryset=MembershipRole.objects.select_related('role').order_by('role__name'),
            )
        )
        .order_by('organization__name', 'organization_id'),
    )


def _user_queryset() -> QuerySet[User]:
    return (
        User.objects.annotate(
            is_platform_admin=Case(
                When(_platform_admin_condition(), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
            # Refresh credentials rotate on every renewal, so the newest one
            # is when the account was last signed in and active.
            last_active_at=Subquery(
                RefreshSession.objects.filter(user=OuterRef('pk'))
                .order_by('-created_at')
                .values('created_at')[:1]
            ),
        )
        .prefetch_related(_membership_prefetch())
        .order_by('-created_at', '-pk')
    )


def list_users(
    user: User | None, filters: UserFilters | None = None, *, offset=0, limit=None
) -> Page[User]:
    if _capabilities(user) is None:
        return empty_page(offset, limit)

    filters = filters or UserFilters()
    queryset = _user_queryset()

    search = (filters.search or '').strip()
    if search:
        queryset = queryset.filter(
            Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )
    if filters.is_active is not None:
        queryset = queryset.filter(is_active=filters.is_active)
    if filters.organization_id is not None:
        queryset = queryset.filter(
            Exists(
                Membership.objects.filter(
                    user=OuterRef('pk'), organization_id=_normalize_id(filters.organization_id)
                )
            )
        )
    if filters.platform_admins_only:
        queryset = queryset.filter(is_platform_admin=True)

    return paginate(queryset, offset=offset, limit=limit)


@dataclass(frozen=True)
class UserDetail:
    user: User
    sign_in_methods: list[str]
    idea_count: int
    review_count: int
    comment_count: int
    audit_entries: list[AdminAuditEntry]


def get_user(user: User | None, user_id: object) -> UserDetail | None:
    if _capabilities(user) is None:
        return None
    normalized_id = _normalize_id(user_id)
    if normalized_id is None:
        return None

    target = (
        _user_queryset()
        .annotate(
            idea_count=_count_of(Idea, 'author'),
            review_count=_count_of(Review, 'reviewer'),
            comment_count=_count_of(Comment, 'author'),
        )
        .prefetch_related('external_identities')
        .filter(pk=normalized_id)
        .first()
    )
    if target is None:
        return None

    # How the account signs in, never with what: the provider's name, not its
    # subject identifier, and "password" only as the fact that one is set.
    methods = ['password'] if target.has_usable_password() else []
    methods += sorted({identity.provider for identity in target.external_identities.all()})

    return UserDetail(
        user=target,
        sign_in_methods=methods,
        idea_count=target.idea_count,
        review_count=target.review_count,
        comment_count=target.comment_count,
        audit_entries=list(
            AdminAuditEntry.objects.select_related('actor').filter(
                target_type='user', target_id=str(target.pk)
            )[:USER_AUDIT_LIMIT]
        ),
    )


# --- organizations -------------------------------------------------------------


def _role_holder_count(slug: str) -> Coalesce:
    return Coalesce(
        Subquery(
            MembershipRole.objects.filter(
                membership__organization=OuterRef('pk'),
                membership__status=Membership.Status.ACTIVE,
                role__slug=slug,
            )
            .order_by()
            .values('membership__organization')
            .annotate(total=Count('*'))
            .values('total')[:1],
            output_field=IntegerField(),
        ),
        0,
    )


def _organization_queryset() -> QuerySet[Organization]:
    return Organization.objects.annotate(
        member_count=Coalesce(
            Subquery(
                Membership.objects.filter(
                    organization=OuterRef('pk'), status=Membership.Status.ACTIVE
                )
                .order_by()
                .values('organization')
                .annotate(total=Count('*'))
                .values('total')[:1],
                output_field=IntegerField(),
            ),
            0,
        ),
        idea_count=_count_of(Idea, 'organization'),
        owner_count=_role_holder_count(DEFAULT_OWNER_ROLE_SLUG),
        reviewer_count=_role_holder_count(REVIEWER_ROLE_SLUG),
    ).order_by('name', 'pk')


def list_organizations(
    user: User | None, search: str | None = None, *, offset=0, limit=None
) -> Page[Organization]:
    if _capabilities(user) is None:
        return empty_page(offset, limit)

    queryset = _organization_queryset()
    search = (search or '').strip()
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(slug__icontains=search))
    return paginate(queryset, offset=offset, limit=limit)


@dataclass(frozen=True)
class OrganizationDetail:
    organization: Organization
    roles: list[Role]
    ideas_by_status: list[StatusCount]


def get_organization(user: User | None, organization_id: object) -> OrganizationDetail | None:
    if _capabilities(user) is None:
        return None
    normalized_id = _normalize_id(organization_id)
    if normalized_id is None:
        return None

    organization = _organization_queryset().filter(pk=normalized_id).first()
    if organization is None:
        return None

    roles = list(
        Role.objects.filter(organization=organization)
        .annotate(
            holder_count=Count(
                'membership_roles',
                filter=Q(membership_roles__membership__status=Membership.Status.ACTIVE),
            )
        )
        .prefetch_related('role_permissions__permission')
        .order_by('-is_system', 'name')
    )
    by_status = dict(
        Idea.objects.filter(organization=organization)
        .order_by()
        .values_list('status')
        .annotate(total=Count('pk'))
        .values_list('status', 'total')
    )
    return OrganizationDetail(
        organization=organization,
        roles=roles,
        ideas_by_status=[
            StatusCount(status=value, count=by_status.get(value, 0))
            for value, _label in Idea.Status.choices
        ],
    )


def list_organization_members(
    user: User | None,
    organization_id: object,
    search: str | None = None,
    *,
    offset=0,
    limit=None,
) -> Page[Membership]:
    """Every membership of one organization - active and inactive - with its roles."""
    if _capabilities(user) is None:
        return empty_page(offset, limit)
    normalized_id = _normalize_id(organization_id)
    if normalized_id is None:
        return empty_page(offset, limit)

    queryset = (
        Membership.objects.filter(organization_id=normalized_id)
        .select_related('user', 'organization')
        .prefetch_related(
            Prefetch(
                'membership_roles',
                queryset=MembershipRole.objects.select_related('role').order_by('role__name'),
            )
        )
        .order_by('user__email', 'pk')
    )
    search = (search or '').strip()
    if search:
        queryset = queryset.filter(
            Q(user__email__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
        )
    return paginate(queryset, offset=offset, limit=limit)


# --- ideas ---------------------------------------------------------------------


@dataclass(frozen=True)
class IdeaFilters:
    search: str | None = None
    status: str | None = None
    visibility: str | None = None
    organization_id: object = None
    category_id: object = None
    author_id: object = None
    created_from: date | None = None
    created_to: date | None = None


def _idea_queryset() -> QuerySet[Idea]:
    return (
        Idea.objects.select_related('organization', 'author', 'category')
        .annotate(
            vote_count=_count_of(Vote, 'idea'),
            comment_count=_count_of(Comment, 'idea'),
            attachment_count=_count_of(Attachment, 'idea'),
        )
        .order_by('-created_at', '-pk')
    )


def _start_of(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min))


def _apply_idea_filters(
    queryset: QuerySet[Idea], filters: IdeaFilters, capabilities: AdminCapabilities
) -> QuerySet[Idea]:
    search = (filters.search or '').strip()
    if search:
        queryset = queryset.filter(
            _content_search(search, capabilities)
            | Q(author__email__icontains=search)
            | Q(organization__name__icontains=search)
        )
    if filters.status:
        queryset = queryset.filter(status=filters.status)
    if filters.visibility:
        queryset = queryset.filter(visibility=filters.visibility)
    for field, value in (
        ('organization_id', filters.organization_id),
        ('category_id', filters.category_id),
        ('author_id', filters.author_id),
    ):
        if value is not None:
            queryset = queryset.filter(**{field: _normalize_id(value)})
    if filters.created_from is not None:
        queryset = queryset.filter(created_at__gte=_start_of(filters.created_from))
    if filters.created_to is not None:
        # Inclusive of the whole "to" day.
        queryset = queryset.filter(created_at__lt=_start_of(filters.created_to) + timedelta(days=1))
    return queryset


@dataclass(frozen=True)
class IdeaListing:
    page: Page[Idea]
    capabilities: AdminCapabilities


def list_ideas(
    user: User | None, filters: IdeaFilters | None = None, *, offset=0, limit=None
) -> IdeaListing:
    capabilities = _capabilities(user)
    if capabilities is None:
        return IdeaListing(page=empty_page(offset, limit), capabilities=AdminCapabilities())

    queryset = _apply_idea_filters(_idea_queryset(), filters or IdeaFilters(), capabilities)
    return IdeaListing(
        page=paginate(queryset, offset=offset, limit=limit), capabilities=capabilities
    )


@dataclass(frozen=True)
class IdeaDetail:
    idea: Idea
    capabilities: AdminCapabilities
    content_visible: bool
    reviews: list[Review]
    transitions: list[IdeaTransition]
    attachments: list[Attachment]


def get_idea(user: User | None, idea_id: object) -> IdeaDetail | None:
    """
    One idea for the console. Metadata, lifecycle history and review metadata
    under `ACCESS_CONSOLE`; content, evidence and review feedback only when
    `may_see_idea_content` allows it - `attachments` is empty otherwise, and the
    adapter redacts the rest from `content_visible`.
    """
    capabilities = _capabilities(user)
    if capabilities is None:
        return None
    normalized_id = _normalize_id(idea_id)
    if normalized_id is None:
        return None

    idea = _idea_queryset().filter(pk=normalized_id).first()
    if idea is None:
        return None

    content_visible = authorization.may_see_idea_content(capabilities, idea.visibility)
    return IdeaDetail(
        idea=idea,
        capabilities=capabilities,
        content_visible=content_visible,
        reviews=list(
            Review.objects.filter(idea=idea)
            .select_related('reviewer', 'idea__organization')
            .prefetch_related('assessments')
            .order_by('round')
        ),
        transitions=list(
            IdeaTransition.objects.filter(idea=idea)
            .select_related('actor')
            .order_by('created_at', 'pk')
        ),
        attachments=list(
            Attachment.objects.filter(idea=idea)
            .select_related('uploaded_by')
            .order_by('created_at', 'pk')
        )
        if content_visible
        else [],
    )


def list_idea_comments(
    user: User | None, idea_id: object, *, offset=0, limit=None
) -> Page[Comment]:
    """One idea's discussion, oldest first - content, so behind the same gate as the idea's."""
    capabilities = _capabilities(user)
    normalized_id = _normalize_id(idea_id)
    if capabilities is None or normalized_id is None:
        return empty_page(offset, limit)

    idea = Idea.objects.filter(pk=normalized_id).only('pk', 'visibility').first()
    if idea is None or not authorization.may_see_idea_content(capabilities, idea.visibility):
        return empty_page(offset, limit)

    return paginate(
        Comment.objects.filter(idea=idea).select_related('author').order_by('created_at', 'pk'),
        offset=offset,
        limit=limit,
    )


def get_attachment_for_download(user: User | None, attachment_id: object) -> Attachment | None:
    """
    An attachment the administrator may download, or `None`.

    Evidence is content: it takes `INSPECT_IDEA_CONTENT` *whatever* the idea's
    visibility - a file is where the most sensitive material in an idea tends
    to be, and the ordinary download path (`ideas.views`) is the one a PUBLIC
    idea's readers use.
    """
    try:
        authorization.require_admin(user, authorization.INSPECT_IDEA_CONTENT)
    except AdministrationError:
        return None
    normalized_id = _normalize_id(attachment_id)
    if normalized_id is None:
        return None
    return Attachment.objects.select_related('idea').filter(pk=normalized_id).first()


# --- reviews -------------------------------------------------------------------

REVIEW_STATE_OPEN = 'open'
REVIEW_STATE_COMPLETED = 'completed'


@dataclass(frozen=True)
class ReviewFilters:
    search: str | None = None
    state: str | None = None
    decisions: tuple[str, ...] = ()
    organization_id: object = None
    reviewer_id: object = None


@dataclass(frozen=True)
class ReviewListing:
    page: Page[Review]
    capabilities: AdminCapabilities


def _review_queryset() -> QuerySet[Review]:
    return (
        Review.objects.select_related('idea__organization', 'reviewer')
        .prefetch_related('assessments')
        .order_by('-created_at', '-pk')
    )


def list_reviews(
    user: User | None, filters: ReviewFilters | None = None, *, offset=0, limit=None
) -> ReviewListing:
    """
    Review rounds across the platform, newest first. Also the approvals view:
    completed rounds filtered to the lifecycle decisions.
    """
    capabilities = _capabilities(user)
    if capabilities is None:
        return ReviewListing(page=empty_page(offset, limit), capabilities=AdminCapabilities())

    filters = filters or ReviewFilters()
    queryset = _review_queryset()

    search = (filters.search or '').strip()
    if search:
        queryset = queryset.filter(
            _content_search(search, capabilities, prefix='idea__')
            | Q(reviewer__email__icontains=search)
            | Q(idea__organization__name__icontains=search)
        )
    if filters.state == REVIEW_STATE_OPEN:
        queryset = queryset.filter(completed_at__isnull=True)
    elif filters.state == REVIEW_STATE_COMPLETED:
        queryset = queryset.filter(completed_at__isnull=False)
    if filters.decisions:
        queryset = queryset.filter(decision__in=filters.decisions)
    if filters.organization_id is not None:
        queryset = queryset.filter(idea__organization_id=_normalize_id(filters.organization_id))
    if filters.reviewer_id is not None:
        queryset = queryset.filter(reviewer_id=_normalize_id(filters.reviewer_id))

    return ReviewListing(
        page=paginate(queryset, offset=offset, limit=limit), capabilities=capabilities
    )


@dataclass(frozen=True)
class ReviewDetail:
    review: Review
    capabilities: AdminCapabilities
    history: list[Review]


def get_review(user: User | None, review_id: object) -> ReviewDetail | None:
    capabilities = _capabilities(user)
    if capabilities is None:
        return None
    normalized_id = _normalize_id(review_id)
    if normalized_id is None:
        return None

    review = _review_queryset().filter(pk=normalized_id).first()
    if review is None:
        return None
    return ReviewDetail(
        review=review,
        capabilities=capabilities,
        history=list(_review_queryset().filter(idea_id=review.idea_id).order_by('round')),
    )


# --- categories and audit --------------------------------------------------------


def list_categories(user: User | None) -> list[Category]:
    """
    Every category, active and retired, with how many ideas use it.

    A list rather than a page: categories are a short, curated, platform-wide
    vocabulary maintained by administrators, not user-generated rows.
    """
    if _capabilities(user) is None:
        return []
    return list(Category.objects.annotate(idea_count=_count_of(Idea, 'category')).order_by('name'))


def list_audit_entries(
    user: User | None,
    *,
    target_type: str | None = None,
    target_id: object = None,
    offset=0,
    limit=None,
) -> Page[AdminAuditEntry]:
    if _capabilities(user) is None:
        return empty_page(offset, limit)

    queryset = AdminAuditEntry.objects.select_related('actor')
    if target_type:
        queryset = queryset.filter(target_type=target_type)
    if target_id is not None:
        queryset = queryset.filter(target_id=str(target_id))
    return paginate(queryset, offset=offset, limit=limit)


# --- teams, invitations, messages, notifications ---------------------------------
#
# The collaboration set (`teams`, `invitations`, `messaging`, `notifications`)
# read here for the same reason the tenant domains above do: an administrator
# has to be able to see that a team exists, who was invited to it, that two
# people are talking and that somebody was told something - and the member-facing
# reads cannot answer any of those, because every one of them is scoped to the
# caller. Nothing here loosens those scopes: this module asks `require_admin`
# first, exactly as above, and reads across tenants only after that.
#
# **Two rules these four surfaces share.**
#
# 1. *Metadata is not content.* A team's name, an invitation's address, who is in
#    a conversation and when it was last active are facts about the platform, and
#    an administrator who cannot see them cannot investigate anything. They are
#    under `ACCESS_CONSOLE`.
# 2. *Anything a member wrote is content.* A message body is the one place in
#    this group where a person wrote something meant for named people, so it is
#    behind `INSPECT_IDEA_CONTENT` and the adapter reports `contentRestricted`
#    rather than returning an empty string that reads like an empty message.
#    Idea titles follow the rule the rest of the console uses: every title under
#    that permission, PUBLIC ideas' titles otherwise.


def _active_member_count(field: str) -> Coalesce:
    """A correlated count of one team's *active* memberships."""
    return Coalesce(
        Subquery(
            TeamMembership.objects.filter(
                **{field: OuterRef('pk')}, status=TeamMembership.Status.ACTIVE
            )
            .order_by()
            .values(field)
            .annotate(total=Count('*'))
            .values('total')[:1],
            output_field=IntegerField(),
        ),
        0,
    )


def _team_queryset() -> QuerySet[Team]:
    return (
        Team.objects.select_related('owner')
        .annotate(
            member_count=_active_member_count('team'),
            # Inactive rows are kept so a team does not re-invite somebody who
            # declined; an administrator asking "how many people has this team ever
            # had" needs both numbers, and the console is where that question is
            # legitimate.
            inactive_member_count=Coalesce(
                Subquery(
                    TeamMembership.objects.filter(
                        team=OuterRef('pk'), status=TeamMembership.Status.INACTIVE
                    )
                    .order_by()
                    .values('team')
                    .annotate(total=Count('*'))
                    .values('total')[:1],
                    output_field=IntegerField(),
                ),
                0,
            ),
            idea_count=_count_of(Idea, 'team'),
            invitation_count=_count_of(Invitation, 'team'),
        )
        .order_by('name', 'pk')
    )


def list_teams(user: User | None, search: str | None = None, *, offset=0, limit=None) -> Page[Team]:
    """Every team on the platform, by name. Empty for a non-administrator."""
    if _capabilities(user) is None:
        return empty_page(offset, limit)

    queryset = _team_queryset()
    search = (search or '').strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(slug__icontains=search)
            | Q(owner__email__icontains=search)
        )
    return paginate(queryset, offset=offset, limit=limit)


@dataclass(frozen=True)
class TeamDetail:
    team: Team
    roles: list[TeamRole]
    members: list[TeamMembership]
    ideas_by_status: list[StatusCount]


def get_team(user: User | None, team_id: object) -> TeamDetail | None:
    """
    One team with its roles and its full membership - active and inactive, the
    way `get_organization` shows memberships, because a team that keeps its rows
    is a fact an administrator can otherwise not see.
    """
    if _capabilities(user) is None:
        return None
    normalized_id = _normalize_id(team_id)
    if normalized_id is None:
        return None

    team = _team_queryset().filter(pk=normalized_id).first()
    if team is None:
        return None

    roles = list(
        TeamRole.objects.filter(team=team)
        .annotate(
            holder_count=Count(
                'membership_roles',
                filter=Q(membership_roles__membership__status=TeamMembership.Status.ACTIVE),
            )
        )
        .prefetch_related('role_permissions__permission')
        .order_by('-is_system', 'name')
    )
    members = list(
        TeamMembership.objects.filter(team=team)
        .select_related('user')
        .prefetch_related(
            Prefetch(
                'membership_roles',
                queryset=TeamMembershipRole.objects.select_related('role').order_by('role__name'),
            )
        )
        .order_by('user__email', 'pk')
    )
    by_status = dict(
        Idea.objects.filter(team=team)
        .order_by()
        .values_list('status')
        .annotate(total=Count('pk'))
        .values_list('status', 'total')
    )
    return TeamDetail(
        team=team,
        roles=roles,
        members=members,
        ideas_by_status=[
            StatusCount(status=value, count=by_status.get(value, 0))
            for value, _label in Idea.Status.choices
        ],
    )


def list_team_invitations(
    user: User | None, team_id: object, *, offset=0, limit=None
) -> Page[Invitation]:
    """One team's invitations, newest first. Empty for a non-administrator."""
    if _capabilities(user) is None:
        return empty_page(offset, limit)
    normalized_id = _normalize_id(team_id)
    if normalized_id is None:
        return empty_page(offset, limit)
    queryset = _invitation_queryset().filter(team_id=normalized_id)
    return paginate(queryset, offset=offset, limit=limit)


@dataclass(frozen=True)
class InvitationFilters:
    """Narrowing only: `scope`, `status` and the tenant ids can each remove rows, never add them."""

    scope: str | None = None
    status: str | None = None
    organization_id: object = None
    team_id: object = None


def _invitation_queryset() -> QuerySet[Invitation]:
    return Invitation.objects.select_related(
        'organization', 'team', 'invited_by', 'accepted_by'
    ).order_by('-created_at', '-pk')


def list_invitations(
    user: User | None, filters: InvitationFilters | None = None, *, offset=0, limit=None
) -> Page[Invitation]:
    """
    Every invitation the platform has sent, newest first.

    **No token, and no way to reconstruct one.** `Invitation` stores only the
    SHA-256 digest, so this cannot leak a usable credential even by accident -
    which is why there is no `adminInvitationToken` field to redact instead.
    """
    if _capabilities(user) is None:
        return empty_page(offset, limit)

    queryset = _invitation_queryset()
    filters = filters or InvitationFilters()
    if filters.scope:
        queryset = queryset.filter(scope=filters.scope)
    if filters.status:
        queryset = queryset.filter(status=filters.status)
    if filters.organization_id is not None:
        queryset = queryset.filter(organization_id=_normalize_id(filters.organization_id))
    if filters.team_id is not None:
        queryset = queryset.filter(team_id=_normalize_id(filters.team_id))
    return paginate(queryset, offset=offset, limit=limit)


def _thread_queryset() -> QuerySet[MessageThread]:
    return (
        MessageThread.objects.select_related('idea', 'started_by')
        .annotate(
            message_count=_count_of(Message, 'thread'),
            participant_count=_count_of(MessageParticipant, 'thread'),
            # An unread count is per reader, so there is no platform-wide number to
            # report. What is countable is how many participants have not opened it,
            # which is the closest honest aggregate: it says a conversation is going
            # unread by somebody, not by whom.
            stale_participant_count=Coalesce(
                Subquery(
                    MessageParticipant.objects.filter(
                        thread=OuterRef('pk'),
                        last_read_at__lt=OuterRef('latest_message_at'),
                    )
                    .order_by()
                    .values('thread')
                    .annotate(total=Count('*'))
                    .values('total')[:1],
                    output_field=IntegerField(),
                ),
                0,
            ),
        )
        .order_by('-latest_message_at', '-pk')
    )


@dataclass(frozen=True)
class MessageThreadListing:
    page: Page[MessageThread]
    capabilities: AdminCapabilities


def list_message_threads(
    user: User | None,
    search: str | None = None,
    *,
    anchored: bool | None = None,
    offset=0,
    limit=None,
) -> MessageThreadListing:
    """
    Every conversation on the platform, most recently active first.

    `search` matches the subject, the starter's address and the **idea title
    only when the caller may read it** - the same rule as everywhere else in the
    console, so searching cannot confirm a private idea's title.
    """
    capabilities = _capabilities(user)
    if capabilities is None:
        return MessageThreadListing(
            page=empty_page(offset, limit), capabilities=AdminCapabilities()
        )

    queryset = _thread_queryset()
    search = (search or '').strip()
    if search:
        # The starter's address is metadata, so it always matches. The subject
        # does not: `messaging.services.start_thread` defaults an anchored
        # thread's subject to its idea's title, so matching a subject would let
        # a search confirm a title the caller may not read. Under the content
        # permission every subject matches; without it, only an *unanchored*
        # thread's subject is genuinely the writer's own words.
        match = Q(started_by__email__icontains=search)
        if capabilities.can_inspect_idea_content:
            match |= Q(subject__icontains=search)
        else:
            match |= Q(idea__isnull=True, subject__icontains=search)
        queryset = queryset.filter(match | _content_search(search, capabilities, prefix='idea__'))
    if anchored is True:
        queryset = queryset.filter(idea__isnull=False)
    elif anchored is False:
        queryset = queryset.filter(idea__isnull=True)
    return MessageThreadListing(
        page=paginate(queryset, offset=offset, limit=limit), capabilities=capabilities
    )


@dataclass(frozen=True)
class MessageThreadDetail:
    thread: MessageThread
    capabilities: AdminCapabilities
    participants: list[MessageParticipant]


def get_message_thread(user: User | None, thread_id: object) -> MessageThreadDetail | None:
    """One conversation with its participants. Null for a non-administrator."""
    capabilities = _capabilities(user)
    if capabilities is None:
        return None
    normalized_id = _normalize_id(thread_id)
    if normalized_id is None:
        return None

    thread = _thread_queryset().filter(pk=normalized_id).first()
    if thread is None:
        return None
    participants = list(
        MessageParticipant.objects.filter(thread=thread)
        .select_related('user')
        .order_by('user__email', 'pk')
    )
    return MessageThreadDetail(thread=thread, capabilities=capabilities, participants=participants)


def list_thread_messages(
    user: User | None, thread_id: object, *, offset=0, limit=None
) -> tuple[Page[Message], AdminCapabilities]:
    """
    One conversation's messages, oldest first, with the caller's capabilities.

    The bodies are returned whole: redaction is the adapter's job, because only
    it knows the type shape the UI renders, and returning a blank body from here
    would make an empty message indistinguishable from a withheld one in every
    other caller too.
    """
    capabilities = _capabilities(user)
    if capabilities is None:
        return empty_page(offset, limit), AdminCapabilities()

    normalized_id = _normalize_id(thread_id)
    if normalized_id is None:
        return empty_page(offset, limit), capabilities

    queryset = (
        Message.objects.filter(thread_id=normalized_id)
        .select_related('sender', 'thread')
        .order_by('created_at', 'pk')
    )
    return paginate(queryset, offset=offset, limit=limit), capabilities


@dataclass(frozen=True)
class NotificationFilters:
    kind: str | None = None
    unread_only: bool = False


def list_notifications(
    user: User | None,
    filters: NotificationFilters | None = None,
    *,
    offset=0,
    limit=None,
) -> tuple[Page[Notification], AdminCapabilities]:
    """
    Every notification the platform has sent, newest first.

    A notification is something **the platform** wrote - never anything a user
    wrote - so unlike a message body it is metadata under `ACCESS_CONSOLE`. The
    idea it points at still is not: the adapter applies the same title rule as
    everywhere else.
    """
    capabilities = _capabilities(user)
    if capabilities is None:
        return empty_page(offset, limit), AdminCapabilities()

    queryset = Notification.objects.select_related('user', 'idea')
    filters = filters or NotificationFilters()
    if filters.kind:
        queryset = queryset.filter(kind=filters.kind)
    if filters.unread_only:
        queryset = queryset.filter(is_read_at__isnull=True)
    return (
        paginate(queryset.order_by('-created_at', '-pk'), offset=offset, limit=limit),
        capabilities,
    )
