"""
The administration console's GraphQL adapter.

The same shape as every other domain's adapter: types with `from_model`,
inputs, `(success, message, field)` payloads, and resolvers that only move
data between the schema and `administration.selectors` /
`administration.services`. Nothing is authorized here - every resolver hands
`info.context.user` to a selector or service that asks
`administration.authorization` first - and nothing is decided here beyond
*presenting* what the selectors returned under the capabilities they
returned with it.

Naming: every root field is prefixed `admin`, so the console's surface is one
recognisable group in the schema and can never be mistaken for, or merged
into, a member-facing field of the same name.

Unauthorized reads answer like the rest of the API - `null` for one object, an
empty page for a list - and never an error that would describe what exists.
`adminCapabilities` is the one field that answers every caller: all `false`
for anybody who is not an administrator, which is what the frontend uses to
decide whether to *offer* the console. It is an offer only; each field below
authorizes on its own.

Redaction
---------
A type that can carry idea content has a `contentRestricted` flag and nullable
content fields. When the administrator lacks `INSPECT_IDEA_CONTENT` (and, for
ideas, the idea is not PUBLIC) the content fields are `null` and the flag is
`true`, so the UI can say "restricted" rather than showing an empty string
that looks like an empty answer. Review feedback, criteria and snapshots are
behind `INSPECT_IDEA_CONTENT` whatever the visibility - review history is not
public because an idea is (`reviews.selectors.list_idea_reviews`).

No type here carries a password, a hash, a token, a session or an external
identity's subject. `signInMethods` names providers, and "password" only as
the fact that one is set.
"""

import enum
from datetime import date

import strawberry
from strawberry.scalars import JSON

from administration import authorization, selectors, services
from administration.authorization import AdminCapabilities, AdministrationError
from administration.models import AdminAuditEntry
from ideas.models import Attachment, Category, Comment, Idea, IdeaTransition
from ideas.pagination import Page
from ideas.schema import (
    IdeaCurrentTool,
    IdeaFrequency,
    IdeaImpact,
    IdeaStatus,
    IdeaVisibility,
    PageInfo,
)
from identity.models import User
from organizations.models import Membership, Organization, Role
from reviews import eligibility
from reviews.models import Review
from reviews.schema import CriterionAssessmentType, ReviewDecision, ReviewScope

AdminAuditAction = strawberry.enum(AdminAuditEntry.Action, name='AdminAuditAction')
AdminAuditResult = strawberry.enum(AdminAuditEntry.Result, name='AdminAuditResult')


@strawberry.enum(description='Whether a review round is still open or has been completed.')
class AdminReviewState(enum.Enum):
    OPEN = selectors.REVIEW_STATE_OPEN
    COMPLETED = selectors.REVIEW_STATE_COMPLETED


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _page_info(page: Page) -> PageInfo:
    return PageInfo(
        offset=page.offset,
        limit=page.limit,
        total_count=page.total_count,
        has_next_page=page.has_next_page,
        has_previous_page=page.has_previous_page,
    )


# --- shared references -------------------------------------------------------------


@strawberry.type(description='What the current user may do in the administration console.')
class AdminCapabilitiesType:
    can_access_console: bool
    can_inspect_idea_content: bool
    can_manage_user_accounts: bool
    can_manage_organization_roles: bool
    can_manage_categories: bool

    @staticmethod
    def from_capabilities(capabilities: AdminCapabilities) -> 'AdminCapabilitiesType':
        return AdminCapabilitiesType(
            can_access_console=capabilities.can_access_console,
            can_inspect_idea_content=capabilities.can_inspect_idea_content,
            can_manage_user_accounts=capabilities.can_manage_user_accounts,
            can_manage_organization_roles=capabilities.can_manage_organization_roles,
            can_manage_categories=capabilities.can_manage_categories,
        )


@strawberry.type(description='A platform account, as a reference from another record.')
class AdminPersonType:
    id: strawberry.ID
    email: str
    name: str

    @staticmethod
    def from_model(user: User) -> 'AdminPersonType':
        return AdminPersonType(
            id=strawberry.ID(str(user.pk)),
            email=user.email,
            name=user.get_full_name() or user.email,
        )


@strawberry.type(description='An organization, as a reference from another record.')
class AdminOrganizationRefType:
    id: strawberry.ID
    name: str

    @staticmethod
    def from_model(organization: Organization) -> 'AdminOrganizationRefType':
        return AdminOrganizationRefType(
            id=strawberry.ID(str(organization.pk)), name=organization.name
        )


@strawberry.type(description='A role, as a reference from a membership.')
class AdminRoleRefType:
    id: strawberry.ID
    name: str
    slug: str
    is_system: bool

    @staticmethod
    def from_model(role: Role) -> 'AdminRoleRefType':
        return AdminRoleRefType(
            id=strawberry.ID(str(role.pk)), name=role.name, slug=role.slug, is_system=role.is_system
        )


def _roles_of(membership: Membership) -> list[AdminRoleRefType]:
    # `.all()` reads the prefetch every selector makes.
    return [AdminRoleRefType.from_model(item.role) for item in membership.membership_roles.all()]


@strawberry.type(description='How many ideas are in one lifecycle status.')
class AdminStatusCountType:
    status: IdeaStatus
    count: int


def _status_counts(counts: list[selectors.StatusCount]) -> list[AdminStatusCountType]:
    return [
        AdminStatusCountType(status=IdeaStatus(item.status), count=item.count) for item in counts
    ]


@strawberry.type(description='One administrative action, as recorded in the audit trail.')
class AdminAuditEntryType:
    id: strawberry.ID
    action: AdminAuditAction
    result: AdminAuditResult
    actor: AdminPersonType | None = strawberry.field(
        description='Null for an operation run from the command line.'
    )
    target_type: str
    target_id: str
    target_label: str
    organization_id: strawberry.ID | None
    message: str
    metadata: JSON
    created_at: str

    @staticmethod
    def from_model(entry: AdminAuditEntry) -> 'AdminAuditEntryType':
        return AdminAuditEntryType(
            id=strawberry.ID(str(entry.pk)),
            action=AdminAuditAction(entry.action),
            result=AdminAuditResult(entry.result),
            actor=AdminPersonType.from_model(entry.actor) if entry.actor_id else None,
            target_type=entry.target_type,
            target_id=entry.target_id,
            target_label=entry.target_label,
            organization_id=strawberry.ID(str(entry.organization_id))
            if entry.organization_id
            else None,
            message=entry.message,
            metadata=entry.metadata,
            created_at=entry.created_at.isoformat(),
        )


@strawberry.type(description='One page of the administrative audit trail, newest first.')
class AdminAuditPage:
    items: list[AdminAuditEntryType]
    page_info: PageInfo


# --- overview ----------------------------------------------------------------------


@strawberry.type(description='One lifecycle move of an idea, for the recent-activity feed.')
class AdminActivityType:
    id: strawberry.ID
    idea_id: strawberry.ID
    idea_title: str | None = strawberry.field(
        description="Null when the administrator may not see this idea's content."
    )
    organization: AdminOrganizationRefType
    from_status: IdeaStatus
    to_status: IdeaStatus
    actor: AdminPersonType
    created_at: str

    @staticmethod
    def from_model(
        transition: IdeaTransition, capabilities: AdminCapabilities
    ) -> 'AdminActivityType':
        idea = transition.idea
        return AdminActivityType(
            id=strawberry.ID(str(transition.pk)),
            idea_id=strawberry.ID(str(idea.pk)),
            idea_title=idea.title
            if authorization.may_see_idea_content(capabilities, idea.visibility)
            else None,
            organization=AdminOrganizationRefType.from_model(idea.organization),
            from_status=IdeaStatus(transition.from_status),
            to_status=IdeaStatus(transition.to_status),
            actor=AdminPersonType.from_model(transition.actor),
            created_at=transition.created_at.isoformat(),
        )


@strawberry.type(description='The platform at a glance, computed from live data.')
class AdminOverviewType:
    user_count: int
    active_user_count: int
    organization_count: int
    idea_count: int
    ideas_by_status: list[AdminStatusCountType]
    open_review_count: int
    completed_review_count: int
    recent_activity: list[AdminActivityType]
    recent_admin_actions: list[AdminAuditEntryType]


# --- users -------------------------------------------------------------------------


@strawberry.type(description="One of a user's organization memberships, with its roles.")
class AdminUserMembershipType:
    id: strawberry.ID
    organization: AdminOrganizationRefType
    status: str
    roles: list[AdminRoleRefType]


@strawberry.type(description='A platform account, as the console lists it. No credentials.')
class AdminUserType:
    id: strawberry.ID
    email: str
    first_name: str
    last_name: str
    is_active: bool
    is_verified: bool
    is_platform_admin: bool
    is_superuser: bool
    created_at: str
    last_active_at: str | None = strawberry.field(
        description='When the account last signed in or renewed its session.'
    )
    memberships: list[AdminUserMembershipType]

    @staticmethod
    def fields_from(user: User) -> dict:
        return {
            'id': strawberry.ID(str(user.pk)),
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_active': user.is_active,
            'is_verified': user.is_verified,
            'is_platform_admin': bool(getattr(user, 'is_platform_admin', False)),
            'is_superuser': user.is_superuser,
            'created_at': user.created_at.isoformat(),
            'last_active_at': _iso(getattr(user, 'last_active_at', None)),
            'memberships': [
                AdminUserMembershipType(
                    id=strawberry.ID(str(membership.pk)),
                    organization=AdminOrganizationRefType.from_model(membership.organization),
                    status=membership.status,
                    roles=_roles_of(membership),
                )
                for membership in user.memberships.all()
            ],
        }

    @staticmethod
    def from_model(user: User) -> 'AdminUserType':
        return AdminUserType(**AdminUserType.fields_from(user))


@strawberry.type(description='One page of platform accounts.')
class AdminUserPage:
    items: list[AdminUserType]
    page_info: PageInfo


@strawberry.type(description='One platform account in detail. Still no credentials.')
class AdminUserDetailType(AdminUserType):
    phone_number: str
    sign_in_methods: list[str] = strawberry.field(
        description="How the account signs in: 'password' (one is set) and/or provider names."
    )
    idea_count: int
    review_count: int
    comment_count: int
    audit_entries: list[AdminAuditEntryType] = strawberry.field(
        description='The most recent administrative actions on this account.'
    )

    @staticmethod
    def from_detail(detail: selectors.UserDetail) -> 'AdminUserDetailType':
        return AdminUserDetailType(
            **AdminUserType.fields_from(detail.user),
            phone_number=detail.user.phone_number,
            sign_in_methods=detail.sign_in_methods,
            idea_count=detail.idea_count,
            review_count=detail.review_count,
            comment_count=detail.comment_count,
            audit_entries=[AdminAuditEntryType.from_model(e) for e in detail.audit_entries],
        )


@strawberry.input(description='Narrow the account list. Every filter is applied by the server.')
class AdminUserFiltersInput:
    search: str | None = None
    is_active: bool | None = None
    organization_id: strawberry.ID | None = None
    platform_admins_only: bool = False


# --- organizations -----------------------------------------------------------------


@strawberry.type(description='An organization, as the console lists it.')
class AdminOrganizationType:
    id: strawberry.ID
    name: str
    slug: str
    created_at: str
    member_count: int
    idea_count: int
    owner_count: int
    reviewer_count: int

    @staticmethod
    def fields_from(organization: Organization) -> dict:
        return {
            'id': strawberry.ID(str(organization.pk)),
            'name': organization.name,
            'slug': organization.slug,
            'created_at': organization.created_at.isoformat(),
            'member_count': organization.member_count,
            'idea_count': organization.idea_count,
            'owner_count': organization.owner_count,
            'reviewer_count': organization.reviewer_count,
        }

    @staticmethod
    def from_model(organization: Organization) -> 'AdminOrganizationType':
        return AdminOrganizationType(**AdminOrganizationType.fields_from(organization))


@strawberry.type(description='One page of organizations.')
class AdminOrganizationPage:
    items: list[AdminOrganizationType]
    page_info: PageInfo


@strawberry.type(description="One of an organization's roles and how many active members hold it.")
class AdminRoleType:
    id: strawberry.ID
    name: str
    slug: str
    description: str
    is_system: bool
    permissions: list[str]
    holder_count: int

    @staticmethod
    def from_model(role: Role) -> 'AdminRoleType':
        return AdminRoleType(
            id=strawberry.ID(str(role.pk)),
            name=role.name,
            slug=role.slug,
            description=role.description,
            is_system=role.is_system,
            permissions=[item.permission.code for item in role.role_permissions.all()],
            holder_count=role.holder_count,
        )


@strawberry.type(description='One organization in detail.')
class AdminOrganizationDetailType(AdminOrganizationType):
    roles: list[AdminRoleType]
    ideas_by_status: list[AdminStatusCountType]


@strawberry.type(description='One membership of an organization, active or not, with its roles.')
class AdminMemberType:
    id: strawberry.ID
    status: str
    joined_at: str
    user: AdminPersonType
    user_is_active: bool
    roles: list[AdminRoleRefType]

    @staticmethod
    def from_model(membership: Membership) -> 'AdminMemberType':
        return AdminMemberType(
            id=strawberry.ID(str(membership.pk)),
            status=membership.status,
            joined_at=membership.created_at.isoformat(),
            user=AdminPersonType.from_model(membership.user),
            user_is_active=membership.user.is_active,
            roles=_roles_of(membership),
        )


@strawberry.type(description="One page of an organization's memberships.")
class AdminMemberPage:
    items: list[AdminMemberType]
    page_info: PageInfo


# --- ideas -------------------------------------------------------------------------


@strawberry.type(description='An idea, as the console lists it.')
class AdminIdeaType:
    id: strawberry.ID
    title: str | None = strawberry.field(
        description='Null when `contentRestricted`: the idea is not PUBLIC and the '
        'administrator does not hold the content-inspection permission.'
    )
    content_restricted: bool
    status: IdeaStatus
    visibility: IdeaVisibility
    organization: AdminOrganizationRefType
    author: AdminPersonType
    category_name: str | None
    created_at: str
    updated_at: str
    submitted_at: str | None
    vote_count: int
    comment_count: int
    attachment_count: int

    @staticmethod
    def fields_from(idea: Idea, capabilities: AdminCapabilities) -> dict:
        visible = authorization.may_see_idea_content(capabilities, idea.visibility)
        return {
            'id': strawberry.ID(str(idea.pk)),
            'title': idea.title if visible else None,
            'content_restricted': not visible,
            'status': IdeaStatus(idea.status),
            'visibility': IdeaVisibility(idea.visibility),
            'organization': AdminOrganizationRefType.from_model(idea.organization),
            'author': AdminPersonType.from_model(idea.author),
            'category_name': idea.category.name if idea.category_id else None,
            'created_at': idea.created_at.isoformat(),
            'updated_at': idea.updated_at.isoformat(),
            'submitted_at': _iso(idea.submitted_at),
            'vote_count': idea.vote_count,
            'comment_count': idea.comment_count,
            'attachment_count': idea.attachment_count,
        }

    @staticmethod
    def from_model(idea: Idea, capabilities: AdminCapabilities) -> 'AdminIdeaType':
        return AdminIdeaType(**AdminIdeaType.fields_from(idea, capabilities))


@strawberry.type(description='One page of ideas across the platform.')
class AdminIdeaPage:
    items: list[AdminIdeaType]
    page_info: PageInfo


@strawberry.type(description="An idea's content: the problem story in the author's words.")
class AdminIdeaContentType:
    description: str
    current_process: str
    current_tools: list[IdeaCurrentTool]
    current_tools_other: str
    performed_by: str
    affected_people: str
    frequency: IdeaFrequency | None
    time_required: str
    people_involved: int | None
    impacts: list[IdeaImpact]
    impact_details: str
    improvement_goal: str
    desired_outcome: str
    easier_for_people: str
    expected_benefit: str
    important_considerations: str

    @staticmethod
    def from_model(idea: Idea) -> 'AdminIdeaContentType':
        return AdminIdeaContentType(
            description=idea.description,
            current_process=idea.current_process,
            current_tools=[IdeaCurrentTool(tool) for tool in idea.current_tools],
            current_tools_other=idea.current_tools_other,
            performed_by=idea.performed_by,
            affected_people=idea.affected_people,
            frequency=IdeaFrequency(idea.frequency) if idea.frequency else None,
            time_required=idea.time_required,
            people_involved=idea.people_involved,
            impacts=[IdeaImpact(impact) for impact in idea.impacts],
            impact_details=idea.impact_details,
            improvement_goal=idea.improvement_goal,
            desired_outcome=idea.desired_outcome,
            easier_for_people=idea.easier_for_people,
            expected_benefit=idea.expected_benefit,
            important_considerations=idea.important_considerations,
        )


@strawberry.type(
    description='Metadata of one piece of evidence. The bytes are downloaded separately.'
)
class AdminAttachmentType:
    id: strawberry.ID
    filename: str
    content_type: str
    size: int
    uploaded_by: AdminPersonType
    created_at: str
    download_path: str = strawberry.field(
        description='The console download endpoint, relative to the API origin. '
        'Authorized and audited on every request.'
    )

    @staticmethod
    def from_model(attachment: Attachment) -> 'AdminAttachmentType':
        return AdminAttachmentType(
            id=strawberry.ID(str(attachment.pk)),
            filename=attachment.filename,
            content_type=attachment.content_type,
            size=attachment.size,
            uploaded_by=AdminPersonType.from_model(attachment.uploaded_by),
            created_at=attachment.created_at.isoformat(),
            download_path=f'/administration/attachments/{attachment.pk}/download/',
        )


@strawberry.type(description='One recorded lifecycle move of an idea.')
class AdminTransitionType:
    id: strawberry.ID
    from_status: IdeaStatus
    to_status: IdeaStatus
    actor: AdminPersonType
    created_at: str

    @staticmethod
    def from_model(transition: IdeaTransition) -> 'AdminTransitionType':
        return AdminTransitionType(
            id=strawberry.ID(str(transition.pk)),
            from_status=IdeaStatus(transition.from_status),
            to_status=IdeaStatus(transition.to_status),
            actor=AdminPersonType.from_model(transition.actor),
            created_at=transition.created_at.isoformat(),
        )


@strawberry.type(description='One comment on an idea.')
class AdminCommentType:
    id: strawberry.ID
    author: AdminPersonType
    content: str
    created_at: str
    updated_at: str

    @staticmethod
    def from_model(comment: Comment) -> 'AdminCommentType':
        return AdminCommentType(
            id=strawberry.ID(str(comment.pk)),
            author=AdminPersonType.from_model(comment.author),
            content=comment.content,
            created_at=comment.created_at.isoformat(),
            updated_at=comment.updated_at.isoformat(),
        )


@strawberry.type(description="One page of an idea's discussion, oldest first.")
class AdminCommentPage:
    items: list[AdminCommentType]
    page_info: PageInfo


# --- reviews -----------------------------------------------------------------------


@strawberry.type(description='The idea a review belongs to, as a reference.')
class AdminReviewedIdeaType:
    id: strawberry.ID
    title: str | None
    status: IdeaStatus
    visibility: IdeaVisibility
    organization: AdminOrganizationRefType


@strawberry.type(
    description=(
        'One review round. Metadata for every administrator; `feedback` and '
        '`assessments` are null and `contentRestricted` true without the '
        'content-inspection permission. Read-only: the console has no operation '
        'that changes a review.'
    )
)
class AdminReviewType:
    id: strawberry.ID
    # Which track this round belongs to. Load-bearing rather than decorative: an
    # idea that reached the platform has an organization confirmation *and* a
    # platform round, both numbered 1 because rounds are counted per scope, and
    # "Acme approved this" and "the platform approved this" are very different
    # facts for an auditor. Without the scope the console cannot tell them apart.
    scope: ReviewScope
    round: int
    idea: AdminReviewedIdeaType
    reviewer: AdminPersonType
    decision: ReviewDecision | None
    is_completed: bool
    created_at: str
    completed_at: str | None
    content_restricted: bool
    feedback: str | None
    assessments: list[CriterionAssessmentType] | None
    _review: strawberry.Private[Review]

    @strawberry.field(
        description=(
            'Whether this open round is stalled: its reviewer can no longer review the '
            'idea, so another reviewer may take it over. Always false once completed. '
            'Computed on request - select it for one review, not a whole page.'
        )
    )
    def is_stalled(self) -> bool:
        review = self._review
        if review.completed_at is not None:
            return False
        return eligibility.review_is_stalled(review.idea)

    @staticmethod
    def fields_from(review: Review, capabilities: AdminCapabilities) -> dict:
        idea = review.idea
        content = capabilities.can_inspect_idea_content
        return {
            'id': strawberry.ID(str(review.pk)),
            'scope': ReviewScope(review.scope),
            'round': review.round,
            'idea': AdminReviewedIdeaType(
                id=strawberry.ID(str(idea.pk)),
                title=idea.title
                if authorization.may_see_idea_content(capabilities, idea.visibility)
                else None,
                status=IdeaStatus(idea.status),
                visibility=IdeaVisibility(idea.visibility),
                organization=AdminOrganizationRefType.from_model(idea.organization),
            ),
            'reviewer': AdminPersonType.from_model(review.reviewer),
            'decision': ReviewDecision(review.decision) if review.decision else None,
            'is_completed': review.completed_at is not None,
            'created_at': review.created_at.isoformat(),
            'completed_at': _iso(review.completed_at),
            'content_restricted': not content,
            'feedback': review.feedback if content else None,
            'assessments': [
                CriterionAssessmentType.from_model(assessment)
                for assessment in review.assessments.all()
            ]
            if content
            else None,
            '_review': review,
        }

    @staticmethod
    def from_model(review: Review, capabilities: AdminCapabilities) -> 'AdminReviewType':
        return AdminReviewType(**AdminReviewType.fields_from(review, capabilities))


@strawberry.type(description='One page of review rounds across the platform, newest first.')
class AdminReviewPage:
    items: list[AdminReviewType]
    page_info: PageInfo


@strawberry.type(description='One review round in detail, with every round of the same idea.')
class AdminReviewDetailType(AdminReviewType):
    submission_snapshot: JSON | None = strawberry.field(
        description='The idea as it was when this round started. Content-inspection only.'
    )
    history: list[AdminReviewType]


@strawberry.input(description='Narrow the review list. Every filter is applied by the server.')
class AdminReviewFiltersInput:
    search: str | None = None
    state: AdminReviewState | None = None
    decisions: list[ReviewDecision] | None = None
    organization_id: strawberry.ID | None = None
    reviewer_id: strawberry.ID | None = None


@strawberry.type(description='One idea in detail, for the console.')
class AdminIdeaDetailType(AdminIdeaType):
    can_inspect_content: bool = strawberry.field(
        description='Whether evidence and review feedback are available to this administrator.'
    )
    content: AdminIdeaContentType | None
    attachments: list[AdminAttachmentType]
    reviews: list[AdminReviewType]
    transitions: list[AdminTransitionType]
    _viewer: strawberry.Private[object]

    @strawberry.field(description="The idea's discussion, oldest first. Empty when restricted.")
    def comments(self, offset: int | None = None, limit: int | None = None) -> AdminCommentPage:
        page = selectors.list_idea_comments(self._viewer, self.id, offset=offset, limit=limit)
        return AdminCommentPage(
            items=[AdminCommentType.from_model(comment) for comment in page.items],
            page_info=_page_info(page),
        )


@strawberry.input(description='Narrow the idea list. Every filter is applied by the server.')
class AdminIdeaFiltersInput:
    search: str | None = None
    status: IdeaStatus | None = None
    visibility: IdeaVisibility | None = None
    organization_id: strawberry.ID | None = None
    category_id: strawberry.ID | None = None
    author_id: strawberry.ID | None = None
    created_from: date | None = None
    created_to: date | None = None


# --- categories --------------------------------------------------------------------


@strawberry.type(description='A category, active or retired, with how many ideas use it.')
class AdminCategoryType:
    id: strawberry.ID
    name: str
    slug: str
    description: str
    is_active: bool
    idea_count: int
    created_at: str
    updated_at: str

    @staticmethod
    def from_model(category: Category) -> 'AdminCategoryType':
        return AdminCategoryType(
            id=strawberry.ID(str(category.pk)),
            name=category.name,
            slug=category.slug,
            description=category.description,
            is_active=category.is_active,
            # Annotated by `selectors.list_categories`; counted for the one row a
            # mutation just wrote.
            idea_count=category.idea_count
            if hasattr(category, 'idea_count')
            else category.ideas.count(),
            created_at=category.created_at.isoformat(),
            updated_at=category.updated_at.isoformat(),
        )


# --- queries -----------------------------------------------------------------------


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            'What the current user may do in the administration console. All false '
            'for anybody who is not a platform administrator. For deciding what to '
            'offer only: every admin field authorizes on its own.'
        )
    )
    def admin_capabilities(self, info: strawberry.Info) -> AdminCapabilitiesType:
        return AdminCapabilitiesType.from_capabilities(
            authorization.capabilities_for(info.context.user)
        )

    @strawberry.field(description='The platform at a glance. Null for a non-administrator.')
    def admin_overview(self, info: strawberry.Info) -> AdminOverviewType | None:
        data = selectors.overview(info.context.user)
        if data is None:
            return None
        return AdminOverviewType(
            user_count=data.user_count,
            active_user_count=data.active_user_count,
            organization_count=data.organization_count,
            idea_count=data.idea_count,
            ideas_by_status=_status_counts(data.ideas_by_status),
            open_review_count=data.open_review_count,
            completed_review_count=data.completed_review_count,
            recent_activity=[
                AdminActivityType.from_model(transition, data.capabilities)
                for transition in data.recent_transitions
            ],
            recent_admin_actions=[
                AdminAuditEntryType.from_model(entry) for entry in data.recent_admin_actions
            ],
        )

    @strawberry.field(description='Platform accounts, newest first. Empty for a non-administrator.')
    def admin_users(
        self,
        info: strawberry.Info,
        filters: AdminUserFiltersInput | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> AdminUserPage:
        filters = filters or AdminUserFiltersInput()
        page = selectors.list_users(
            info.context.user,
            selectors.UserFilters(
                search=filters.search,
                is_active=filters.is_active,
                organization_id=filters.organization_id,
                platform_admins_only=filters.platform_admins_only,
            ),
            offset=offset,
            limit=limit,
        )
        return AdminUserPage(
            items=[AdminUserType.from_model(user) for user in page.items],
            page_info=_page_info(page),
        )

    @strawberry.field(description='One platform account. Null for a non-administrator.')
    def admin_user(self, info: strawberry.Info, id: strawberry.ID) -> AdminUserDetailType | None:
        detail = selectors.get_user(info.context.user, id)
        return AdminUserDetailType.from_detail(detail) if detail else None

    @strawberry.field(description='Organizations, by name. Empty for a non-administrator.')
    def admin_organizations(
        self,
        info: strawberry.Info,
        search: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> AdminOrganizationPage:
        page = selectors.list_organizations(info.context.user, search, offset=offset, limit=limit)
        return AdminOrganizationPage(
            items=[AdminOrganizationType.from_model(item) for item in page.items],
            page_info=_page_info(page),
        )

    @strawberry.field(description='One organization. Null for a non-administrator.')
    def admin_organization(
        self, info: strawberry.Info, id: strawberry.ID
    ) -> AdminOrganizationDetailType | None:
        detail = selectors.get_organization(info.context.user, id)
        if detail is None:
            return None
        return AdminOrganizationDetailType(
            **AdminOrganizationType.fields_from(detail.organization),
            roles=[AdminRoleType.from_model(role) for role in detail.roles],
            ideas_by_status=_status_counts(detail.ideas_by_status),
        )

    @strawberry.field(description="An organization's memberships. Empty for a non-administrator.")
    def admin_organization_members(
        self,
        info: strawberry.Info,
        organization_id: strawberry.ID,
        search: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> AdminMemberPage:
        page = selectors.list_organization_members(
            info.context.user, organization_id, search, offset=offset, limit=limit
        )
        return AdminMemberPage(
            items=[AdminMemberType.from_model(item) for item in page.items],
            page_info=_page_info(page),
        )

    @strawberry.field(description='Ideas across the platform, newest first.')
    def admin_ideas(
        self,
        info: strawberry.Info,
        filters: AdminIdeaFiltersInput | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> AdminIdeaPage:
        filters = filters or AdminIdeaFiltersInput()
        listing = selectors.list_ideas(
            info.context.user,
            selectors.IdeaFilters(
                search=filters.search,
                status=filters.status.value if filters.status else None,
                visibility=filters.visibility.value if filters.visibility else None,
                organization_id=filters.organization_id,
                category_id=filters.category_id,
                author_id=filters.author_id,
                created_from=filters.created_from,
                created_to=filters.created_to,
            ),
            offset=offset,
            limit=limit,
        )
        return AdminIdeaPage(
            items=[
                AdminIdeaType.from_model(idea, listing.capabilities) for idea in listing.page.items
            ],
            page_info=_page_info(listing.page),
        )

    @strawberry.field(description='One idea in detail. Null for a non-administrator.')
    def admin_idea(self, info: strawberry.Info, id: strawberry.ID) -> AdminIdeaDetailType | None:
        detail = selectors.get_idea(info.context.user, id)
        if detail is None:
            return None
        capabilities = detail.capabilities
        return AdminIdeaDetailType(
            **AdminIdeaType.fields_from(detail.idea, capabilities),
            can_inspect_content=capabilities.can_inspect_idea_content,
            content=AdminIdeaContentType.from_model(detail.idea)
            if detail.content_visible
            else None,
            attachments=[AdminAttachmentType.from_model(item) for item in detail.attachments],
            reviews=[AdminReviewType.from_model(review, capabilities) for review in detail.reviews],
            transitions=[AdminTransitionType.from_model(item) for item in detail.transitions],
            _viewer=info.context.user,
        )

    @strawberry.field(
        description=(
            'Review rounds across the platform, newest first. With state COMPLETED and '
            'the lifecycle decisions, this is the approvals view.'
        )
    )
    def admin_reviews(
        self,
        info: strawberry.Info,
        filters: AdminReviewFiltersInput | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> AdminReviewPage:
        filters = filters or AdminReviewFiltersInput()
        listing = selectors.list_reviews(
            info.context.user,
            selectors.ReviewFilters(
                search=filters.search,
                state=filters.state.value if filters.state else None,
                decisions=tuple(decision.value for decision in filters.decisions or ()),
                organization_id=filters.organization_id,
                reviewer_id=filters.reviewer_id,
            ),
            offset=offset,
            limit=limit,
        )
        return AdminReviewPage(
            items=[
                AdminReviewType.from_model(review, listing.capabilities)
                for review in listing.page.items
            ],
            page_info=_page_info(listing.page),
        )

    @strawberry.field(description='One review round in detail. Null for a non-administrator.')
    def admin_review(
        self, info: strawberry.Info, id: strawberry.ID
    ) -> AdminReviewDetailType | None:
        detail = selectors.get_review(info.context.user, id)
        if detail is None:
            return None
        capabilities = detail.capabilities
        return AdminReviewDetailType(
            **AdminReviewType.fields_from(detail.review, capabilities),
            submission_snapshot=detail.review.submission_snapshot
            if capabilities.can_inspect_idea_content
            else None,
            history=[AdminReviewType.from_model(item, capabilities) for item in detail.history],
        )

    @strawberry.field(
        description='Every category, active and retired. Empty for a non-administrator.'
    )
    def admin_categories(self, info: strawberry.Info) -> list[AdminCategoryType]:
        return [
            AdminCategoryType.from_model(category)
            for category in selectors.list_categories(info.context.user)
        ]

    @strawberry.field(description='The administrative audit trail, newest first.')
    def admin_audit_entries(
        self,
        info: strawberry.Info,
        target_type: str | None = None,
        target_id: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> AdminAuditPage:
        page = selectors.list_audit_entries(
            info.context.user,
            target_type=target_type,
            target_id=target_id,
            offset=offset,
            limit=limit,
        )
        return AdminAuditPage(
            items=[AdminAuditEntryType.from_model(entry) for entry in page.items],
            page_info=_page_info(page),
        )


# --- mutations ---------------------------------------------------------------------


@strawberry.input(description='Activate or deactivate an account. The reason is audited.')
class AdminSetUserActiveInput:
    user_id: strawberry.ID
    is_active: bool
    reason: str | None = None


@strawberry.type(description='Result of an account operation.')
class AdminUserPayload:
    success: bool
    message: str
    field: str | None = None
    user: AdminUserType | None = None


@strawberry.input(description="Assign or remove one of an organization's roles on a membership.")
class AdminMembershipRoleInput:
    membership_id: strawberry.ID
    role_id: strawberry.ID
    reason: str | None = None


@strawberry.type(description='Result of an organization role operation.')
class AdminMemberPayload:
    success: bool
    message: str
    field: str | None = None
    member: AdminMemberType | None = None


@strawberry.input(description='A new category.')
class AdminCreateCategoryInput:
    name: str
    description: str = ''


@strawberry.input(description="A category's new name and description. The slug is kept.")
class AdminUpdateCategoryInput:
    id: strawberry.ID
    name: str
    description: str = ''


@strawberry.input(description='Retire a category from new ideas, or bring it back.')
class AdminSetCategoryActiveInput:
    id: strawberry.ID
    is_active: bool


@strawberry.type(description='Result of a category operation.')
class AdminCategoryPayload:
    success: bool
    message: str
    field: str | None = None
    category: AdminCategoryType | None = None


def _refused(payload_type, exc: AdministrationError):
    return payload_type(success=False, message=exc.message, field=exc.field)


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            'Activate or deactivate an account. Deactivation takes effect on the next '
            'request and revokes every session; nothing is deleted. Requires the '
            'account-management permission; audited.'
        )
    )
    def admin_set_user_active(
        self, info: strawberry.Info, input: AdminSetUserActiveInput
    ) -> AdminUserPayload:
        try:
            user = services.set_user_active(
                info.context.user, input.user_id, input.is_active, input.reason
            )
        except AdministrationError as exc:
            return _refused(AdminUserPayload, exc)
        detail = selectors.get_user(info.context.user, user.pk)
        return AdminUserPayload(
            success=True,
            message='Account activated.' if input.is_active else 'Account deactivated.',
            user=AdminUserType.from_model(detail.user) if detail else None,
        )

    @strawberry.mutation(
        description=(
            "Assign one of an organization's roles to one of its active memberships, "
            "under the organization domain's own rules. Requires the organization-role "
            'permission; audited.'
        )
    )
    def admin_assign_membership_role(
        self, info: strawberry.Info, input: AdminMembershipRoleInput
    ) -> AdminMemberPayload:
        try:
            membership = services.assign_membership_role(
                info.context.user, input.membership_id, input.role_id, input.reason
            )
        except AdministrationError as exc:
            return _refused(AdminMemberPayload, exc)
        return AdminMemberPayload(
            success=True, message='Role assigned.', member=AdminMemberType.from_model(membership)
        )

    @strawberry.mutation(
        description=(
            'Remove a role from a membership. The last active Owner keeps the Owner role. '
            'Requires the organization-role permission; audited.'
        )
    )
    def admin_remove_membership_role(
        self, info: strawberry.Info, input: AdminMembershipRoleInput
    ) -> AdminMemberPayload:
        try:
            membership = services.remove_membership_role(
                info.context.user, input.membership_id, input.role_id, input.reason
            )
        except AdministrationError as exc:
            return _refused(AdminMemberPayload, exc)
        return AdminMemberPayload(
            success=True, message='Role removed.', member=AdminMemberType.from_model(membership)
        )

    @strawberry.mutation(
        description='Create a category. Requires the category permission; audited.'
    )
    def admin_create_category(
        self, info: strawberry.Info, input: AdminCreateCategoryInput
    ) -> AdminCategoryPayload:
        try:
            category = services.create_category(
                info.context.user,
                services.CategoryInput(name=input.name, description=input.description),
            )
        except AdministrationError as exc:
            return _refused(AdminCategoryPayload, exc)
        return AdminCategoryPayload(
            success=True,
            message='Category created.',
            category=AdminCategoryType.from_model(category),
        )

    @strawberry.mutation(description='Rename or re-describe a category. Audited.')
    def admin_update_category(
        self, info: strawberry.Info, input: AdminUpdateCategoryInput
    ) -> AdminCategoryPayload:
        try:
            category = services.update_category(
                info.context.user,
                input.id,
                services.CategoryInput(name=input.name, description=input.description),
            )
        except AdministrationError as exc:
            return _refused(AdminCategoryPayload, exc)
        return AdminCategoryPayload(
            success=True,
            message='Category updated.',
            category=AdminCategoryType.from_model(category),
        )

    @strawberry.mutation(
        description=(
            'Retire a category from new ideas, or bring it back. Existing ideas keep '
            'their category either way. Audited.'
        )
    )
    def admin_set_category_active(
        self, info: strawberry.Info, input: AdminSetCategoryActiveInput
    ) -> AdminCategoryPayload:
        try:
            category = services.set_category_active(info.context.user, input.id, input.is_active)
        except AdministrationError as exc:
            return _refused(AdminCategoryPayload, exc)
        return AdminCategoryPayload(
            success=True,
            message='Category reactivated.' if input.is_active else 'Category retired.',
            category=AdminCategoryType.from_model(category),
        )
