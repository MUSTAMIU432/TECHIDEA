"""
Administrative operations.

Every operation here follows the same four steps, in this order, and none of
them is skippable:

    platform permission   (`authorization.require_admin`, with the operation's
          |                own capability - never ACCESS_CONSOLE alone)
    lock the target       (select_for_update, inside the transaction)
          |
    the owning domain's   (e.g. `organizations.services.grant_membership_role`,
    rules                  `Category.full_clean`) - applied, not bypassed
          |
    audit                 (`AdminAuditEntry`, in the same transaction for a
                           success; after the rollback for a refusal)

What is deliberately *not* here:

- **No review or approval override.** Reviews are append-only and completed
  reviews immutable (`reviews.models`); an idea's status moves only through
  `ideas.lifecycle`. An administrator observes both. An override would be a
  separate, explicitly designed and audited operation - not a shortcut in this
  module.
- **No password, token or session material** is read or returned. Deactivating
  an account revokes its refresh sessions; nothing here can see one.
- **No granting of platform administration.** A console that could mint
  administrators would turn one compromised administrator account into many;
  that stays with the `grant_platform_admin` command (and Django's own admin
  for superusers).
- **No deletion.** Accounts are deactivated, categories retired - the rest of
  the platform's records (`Idea.category` is PROTECT, reviews PROTECT their
  reviewer) depend on both surviving.

Refusals raise `AdministrationError` (`(message, field, reason)`, like every
other domain's error). For the account and role operations - the ones that
change who can do what - a refusal of an operation the caller was *allowed to
attempt* (a rule said no: their own account, the last Owner) is audited as
`refused`, so repeated attempts are visible. A caller without the permission
is not recorded in the table (anybody can send a request, and the table is an
administrators' record) but is logged.
"""

import logging
from dataclasses import dataclass
from typing import NoReturn

from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission as AuthPermission
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from administration import authorization
from administration.authorization import AdministrationError
from administration.models import AdminAuditEntry
from ideas.models import Attachment, Category
from identity.models import RefreshSession, User
from organizations import services as organization_services
from organizations.models import Membership
from organizations.services import OrganizationError

logger = logging.getLogger(__name__)

MAX_REASON_LENGTH = 500
Action = AdminAuditEntry.Action


class TargetNotFoundError(AdministrationError):
    """
    The target does not exist. Not audited as a refusal: there is nothing it
    was done to, and an id that names nothing is not an administrative act.
    """


@dataclass(frozen=True)
class _Target:
    type: str
    id: object
    label: str = ''
    organization_id: int | None = None


def _record(
    actor: User | None,
    action: str,
    target: _Target,
    *,
    result: str = AdminAuditEntry.Result.SUCCEEDED,
    message: str = '',
    metadata: dict | None = None,
) -> AdminAuditEntry:
    return AdminAuditEntry.objects.create(
        actor=actor,
        action=action,
        result=result,
        target_type=target.type,
        target_id=str(target.id),
        target_label=target.label[:255],
        organization_id=target.organization_id,
        message=message[:255],
        metadata=metadata or {},
    )


def _authorize(user: User | None, permission: str, action: str) -> User:
    try:
        return authorization.require_admin(user, permission)
    except AdministrationError:
        logger.warning(
            'Refused administrative action %s for user %s: missing %s.',
            action,
            getattr(user, 'pk', None),
            permission,
        )
        raise


def _normalize_id(value: object, field: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise AdministrationError(f'Invalid {field}.', field=field) from None


def _normalize_reason(reason: str | None) -> str:
    normalized = (reason or '').strip()
    if len(normalized) > MAX_REASON_LENGTH:
        raise AdministrationError(
            f'Keep the reason under {MAX_REASON_LENGTH} characters.', field='reason'
        )
    return normalized


def _refuse(
    actor: User, action: str, target: _Target, exc: AdministrationError, metadata: dict
) -> NoReturn:
    """Audit a refusal the caller was entitled to attempt, then re-raise it."""
    _record(
        actor,
        action,
        target,
        result=AdminAuditEntry.Result.REFUSED,
        message=exc.message,
        metadata=metadata,
    )
    raise exc


# --- user accounts -------------------------------------------------------------


def set_user_active(
    user: User | None, user_id: object, is_active: bool, reason: str | None = None
) -> User:
    """
    Activate or deactivate an account.

    Deactivation is the platform's supported way to stop an account: the row,
    its ideas, its reviews and its audit trail all stay. Its effect is
    immediate - `get_authenticated_user` refuses an inactive user on the next
    request, whatever access token they hold - and every refresh session is
    revoked in the same transaction so none can be renewed. An open review the
    account held becomes *stalled* and another reviewer can take it over
    (`reviews.services.start_review`); nothing about the review is changed here.

    Rules: an administrator cannot change their own account (no locking
    yourself out, no re-enabling yourself), only a superuser can change a
    superuser's, and a no-op is refused rather than audited as a change.
    """
    action = Action.USER_ACTIVATED if is_active else Action.USER_DEACTIVATED
    actor = _authorize(user, authorization.MANAGE_USER_ACCOUNTS, action)
    target_id = _normalize_id(user_id, 'userId')
    normalized_reason = _normalize_reason(reason)
    metadata = {'reason': normalized_reason} if normalized_reason else {}

    try:
        with transaction.atomic():
            target = User.objects.select_for_update().filter(pk=target_id).first()
            if target is None:
                raise TargetNotFoundError('User not found.')
            audit_target = _Target('user', target.pk, target.email)

            if target.pk == actor.pk:
                raise AdministrationError('You cannot change the status of your own account.')
            if target.is_superuser and not actor.is_superuser:
                raise AdministrationError("Only a superuser can change a superuser's account.")
            if target.is_active == is_active:
                raise AdministrationError(
                    'This account is already active.'
                    if is_active
                    else 'This account is already deactivated.'
                )

            target.is_active = is_active
            target.save(update_fields=['is_active', 'updated_at'])
            revoked = 0
            if not is_active:
                revoked = RefreshSession.objects.filter(
                    user=target, revoked_at__isnull=True
                ).update(revoked_at=timezone.now())
            _record(actor, action, audit_target, metadata={**metadata, 'revoked_sessions': revoked})
            return target
    except TargetNotFoundError:
        raise
    except AdministrationError as exc:
        _refuse(actor, action, _Target('user', target_id), exc, metadata)


# --- organization roles ----------------------------------------------------------


def _change_membership_role(
    user: User | None, membership_id: object, role_id: object, reason: str | None, *, assign: bool
) -> Membership:
    action = Action.MEMBERSHIP_ROLE_ASSIGNED if assign else Action.MEMBERSHIP_ROLE_REMOVED
    actor = _authorize(user, authorization.MANAGE_ORGANIZATION_ROLES, action)
    normalized_membership_id = _normalize_id(membership_id, 'membershipId')
    normalized_role_id = _normalize_id(role_id, 'roleId')
    normalized_reason = _normalize_reason(reason)

    audit_target = _Target('membership', normalized_membership_id)
    metadata: dict = {'role_id': normalized_role_id}
    if normalized_reason:
        metadata['reason'] = normalized_reason

    try:
        with transaction.atomic():
            membership = (
                Membership.objects.select_for_update()
                .select_related('user', 'organization')
                .filter(pk=normalized_membership_id)
                .first()
            )
            role = (
                organization_services.lock_role_for_membership(membership, normalized_role_id)
                if membership is not None
                else None
            )
            if membership is None or role is None:
                raise TargetNotFoundError('Membership or role not found.')

            audit_target = _Target(
                'membership',
                membership.pk,
                f'{membership.user.email} in {membership.organization.name}',
                membership.organization_id,
            )
            metadata.update(role_slug=role.slug, user_id=membership.user_id)
            try:
                if assign:
                    organization_services.grant_membership_role(membership, role)
                else:
                    organization_services.revoke_membership_role(membership, role)
            except OrganizationError as exc:
                raise AdministrationError(exc.message, field=exc.field) from None

            _record(actor, action, audit_target, metadata=metadata)
    except TargetNotFoundError:
        raise
    except AdministrationError as exc:
        _refuse(actor, action, audit_target, exc, metadata)

    return (
        Membership.objects.select_related('user', 'organization')
        .prefetch_related('membership_roles__role')
        .get(pk=normalized_membership_id)
    )


def assign_membership_role(
    user: User | None, membership_id: object, role_id: object, reason: str | None = None
) -> Membership:
    """
    Give an organization member one of that organization's roles - for the
    support cases an organization cannot solve itself (its only owner left).
    The organization domain's rules apply unchanged (active memberships only,
    the role must be the membership's own organization's, no duplicates).
    """
    return _change_membership_role(user, membership_id, role_id, reason, assign=True)


def remove_membership_role(
    user: User | None, membership_id: object, role_id: object, reason: str | None = None
) -> Membership:
    """
    Take a role away from an organization member. The last active Owner still
    cannot lose the Owner role - no organization is left ownerless, whoever asks.
    """
    return _change_membership_role(user, membership_id, role_id, reason, assign=False)


# --- categories ------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryInput:
    name: str
    description: str = ''


def _category_error(exc: ValidationError) -> AdministrationError:
    errors = getattr(exc, 'message_dict', {})
    for field in ('name', 'slug', 'description'):
        if field in errors:
            message = errors[field][0]
            if 'already exists' in message:
                message = 'A category with this name already exists.'
            return AdministrationError(message, field='name' if field == 'slug' else field)
    for message in errors.get('__all__', []):
        if 'unique_category_name' in message or 'already exists' in message:
            return AdministrationError('A category with this name already exists.', field='name')
    return AdministrationError('Enter a valid category.')


def _validated_name(name: str | None) -> str:
    normalized = (name or '').strip()
    if not normalized:
        raise AdministrationError('Enter a category name.', field='name')
    return normalized


def create_category(user: User | None, data: CategoryInput) -> Category:
    """A new, active category, usable by every organization's idea form at once."""
    actor = _authorize(user, authorization.MANAGE_CATEGORIES, Action.CATEGORY_CREATED)
    name = _validated_name(data.name)

    with transaction.atomic():
        category = Category(name=name, description=(data.description or '').strip())
        try:
            category.save()
        except ValidationError as exc:
            raise _category_error(exc) from None
        _record(
            actor,
            Action.CATEGORY_CREATED,
            _Target('category', category.pk, category.name),
            metadata={'name': category.name, 'slug': category.slug},
        )
    return category


def update_category(user: User | None, category_id: object, data: CategoryInput) -> Category:
    """
    Rename or re-describe a category. The slug is kept: it is an identifier
    other records and links may already use, and every idea filed under the
    category keeps pointing at the same row either way.
    """
    actor = _authorize(user, authorization.MANAGE_CATEGORIES, Action.CATEGORY_UPDATED)
    normalized_id = _normalize_id(category_id, 'categoryId')
    name = _validated_name(data.name)
    description = (data.description or '').strip()

    with transaction.atomic():
        category = Category.objects.select_for_update().filter(pk=normalized_id).first()
        if category is None:
            raise TargetNotFoundError('Category not found.')

        changes = {}
        if category.name != name:
            changes['name'] = [category.name, name]
        if category.description != description:
            changes['description'] = [category.description, description]
        if not changes:
            raise AdministrationError('Nothing to change.')

        category.name = name
        category.description = description
        try:
            category.save()
        except ValidationError as exc:
            raise _category_error(exc) from None
        _record(
            actor,
            Action.CATEGORY_UPDATED,
            _Target('category', category.pk, category.name),
            metadata={'changes': changes},
        )
    return category


def set_category_active(user: User | None, category_id: object, is_active: bool) -> Category:
    """
    Retire a category from new ideas, or bring it back. Ideas already filed
    under it keep it - retirement never touches an existing idea, which is why
    it is the supported operation and deletion is not.
    """
    action = Action.CATEGORY_ACTIVATED if is_active else Action.CATEGORY_DEACTIVATED
    actor = _authorize(user, authorization.MANAGE_CATEGORIES, action)
    normalized_id = _normalize_id(category_id, 'categoryId')

    with transaction.atomic():
        category = Category.objects.select_for_update().filter(pk=normalized_id).first()
        if category is None:
            raise TargetNotFoundError('Category not found.')
        if category.is_active == is_active:
            raise AdministrationError(
                'This category is already active.'
                if is_active
                else 'This category is already retired.'
            )
        category.is_active = is_active
        category.save()
        _record(actor, action, _Target('category', category.pk, category.name))
    return category


# --- evidence --------------------------------------------------------------------


def record_attachment_download(user: User, attachment: Attachment) -> None:
    """
    Audit an administrator downloading an idea's evidence. Called by
    `administration.views` after authorizing, before streaming the file: a file
    read across organizations is exactly the kind of access that must leave a
    trace, and recording it first means a download that fails midway is still
    on the record.
    """
    _record(
        user,
        Action.ATTACHMENT_DOWNLOADED,
        _Target(
            'attachment',
            attachment.pk,
            attachment.filename,
            attachment.idea.organization_id,
        ),
        metadata={'idea_id': attachment.idea_id},
    )


# --- platform administration (command line only) ----------------------------------

PLATFORM_ADMIN_GROUP = 'Platform administrators'


def _platform_admin_group() -> Group:
    """
    The "Platform administrators" group, holding the **console** permissions.

    `CONSOLE_PERMISSIONS`, not `ALL_PERMISSIONS`: platform review and routing
    submissions are deliberately not in the administrator's hands. They belong to
    the "Platform reviewers" and whoever routes work (`grant_platform_reviewer`,
    and `grant_platform_intake`), because an administrator who could both hand an
    idea to a reviewer and approve it would be doing two different people's jobs
    with one permission - and no check inside the platform could notice, since
    both actions would be individually authorized.
    """
    group, _ = Group.objects.get_or_create(name=PLATFORM_ADMIN_GROUP)
    codenames = [code.split('.', 1)[1] for code in authorization.CONSOLE_PERMISSIONS]
    permissions = AuthPermission.objects.filter(
        content_type__app_label='administration', codename__in=codenames
    )
    if permissions.count() != len(codenames):
        raise AdministrationError('The administration permissions are missing. Run migrate first.')
    group.permissions.add(*permissions)
    return group


def grant_platform_admin(email: str) -> User:
    """
    Add an account to the "Platform administrators" group (every console
    permission). Command-line only - see the module docstring - and audited
    with no actor, which is what marks it as a command-line operation.
    """
    target = User.objects.filter(email=User.objects.normalize_email(email)).first()
    if target is None:
        raise AdministrationError('No account uses this email address.', field='email')
    if not target.is_active:
        raise AdministrationError('This account is deactivated.', field='email')

    with transaction.atomic():
        group = _platform_admin_group()
        target.groups.add(group)
        _record(None, Action.PLATFORM_ADMIN_GRANTED, _Target('user', target.pk, target.email))
    return target


def revoke_platform_admin(email: str) -> User:
    """
    Remove an account from the "Platform administrators" group. A superuser, or
    an account granted permissions directly, keeps what those give it - this
    reports the group change only.
    """
    target = User.objects.filter(email=User.objects.normalize_email(email)).first()
    if target is None:
        raise AdministrationError('No account uses this email address.', field='email')

    with transaction.atomic():
        group = _platform_admin_group()
        target.groups.remove(group)
        _record(None, Action.PLATFORM_ADMIN_REVOKED, _Target('user', target.pk, target.email))
    return target
