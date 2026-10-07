"""
Invitation operations: send, revoke, accept.

The write side, in the same shape as `organizations.services` and
`teams.services` - one `(message, field, reason)` error class, one transaction
per operation - with one rule that shapes all of it:

    **Acceptance is the only thing that creates a membership.**

`invite_to_organization` and `invite_to_team` write an `Invitation` and send an
email. Neither touches `Membership` or `TeamMembership`. Only
`accept_invitation` does, and it does so inside the transaction that flips the
invitation from `PENDING` to `ACCEPTED`. So the question "is this person in my
organization" has exactly one possible answer at any moment: was an invitation
accepted by an account whose own address matches it.

Refusals on acceptance
----------------------
Every one of these is a refusal, and they are deliberately not distinguishable
from each other where they could be used to learn something:

- an unknown, revoked, expired, already-accepted or revoked token, and a token
  whose address does not match the signed-in account, all refuse. The messages
  are plain, and none of them says *which* half of the token failed, so the
  acceptance endpoint cannot be used to confirm that a given invitation
  existed.
- **Signed in with the wrong address** is refused rather than quietly accepted.
  The safe answer to "you are `a@example.com` and this invitation is for
  `b@example.com`" is to do nothing and say so. Switching accounts, or
  registering with the invited address, is the supported path.
- **No account yet** is not handled here. The frontend's invitation page sends
  an unauthenticated visitor to register first and returns them to the link; by
  the time `accept_invitation` runs there is always an authenticated user, which
  is why this function takes a `User` and not an email address.

Token handling
--------------
The plaintext token is minted with `secrets.token_urlsafe(32)` and returned to
the caller **once**, for the email. Only `sha256` of it is stored, so:

- it cannot be recovered from the database, so a dump of this table does not
  yield usable invitations;
- it cannot be guessed - 32 bytes of `secrets` output, and the lookup is by
  digest, so an attacker would need the token rather than the digest.

Acceptance claims the invitation with a **conditional UPDATE**
(`status=PENDING -> status=ACCEPTED`, returning the row count) rather than
reading then writing. That is what makes single-use hold under concurrency: two
clicks on the same link in two tabs produce two UPDATEs and exactly one of them
matches a `PENDING` row, so exactly one membership is created. A read-then-write
would let both see `PENDING`.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from identity.models import User
from invitations.models import Invitation
from organizations import services as organization_services
from organizations.authorization import (
    ORGANIZATION_MEMBERS_MANAGE,
    AuthorizationError,
)
from organizations.models import Membership, MembershipRole, Organization, Role
from teams import authorization as team_authorization
from teams import services as team_services
from teams.models import Team, TeamMembership, TeamMembershipRole, TeamRole

logger = logging.getLogger(__name__)

TOKEN_BYTES = 32
MAX_EMAIL_LENGTH = 254


class InvitationError(Exception):
    """A refused invitation operation, in the project's `(message, field, reason)` shape."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message)
        self.message = message
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class IssuedInvitation:
    """An invitation row and the plaintext token that was emailed with it."""

    invitation: Invitation
    raw_token: str


def _require_active_user(user: User | None) -> User:
    if user is None or not user.is_active:
        raise InvitationError('You must be signed in to do that.', reason='unauthenticated')
    return user


def _normalize_email(email: str | None) -> str:
    """
    The address as the identity app stores it - stripped and lowercased.

    `UserManager.normalize_email` rather than a local rule, so acceptance's
    comparison against `User.email` is a plain equality against the same
    normalization registration used. Two normalization rules that happened to
    agree today would be an invitation bound to an address nobody has.
    """
    # `User.objects.normalize_email` - the *manager*, where this platform's
    # case-folding rule lives. Called on the model it is an `AttributeError`, so
    # every invitation operation raised before it could compare anything.
    normalized = User.objects.normalize_email(email)
    if not normalized:
        raise InvitationError('Enter an email address.', field='email')
    if len(normalized) > MAX_EMAIL_LENGTH:
        raise InvitationError('That email address is too long.', field='email')
    return normalized


def _mint_token() -> tuple[str, str]:
    """A `(raw_token, token_digest)` pair. The raw half is returned once and never stored."""
    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    return raw_token, _digest(raw_token)


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _existing_open_invitation(tenant_field: str, tenant_id: int, email: str) -> Invitation | None:
    """An unspent, unexpired invitation for this exact (tenant, address) pair, if there is one."""
    return (
        Invitation.objects.filter(
            status=Invitation.Status.PENDING,
            expires_at__gt=timezone.now(),
            email=email,
            **{tenant_field: tenant_id},
        )
        .order_by('-created_at')
        .first()
    )


def _send_invitation_email(invitation: Invitation, raw_token: str) -> None:
    """
    Mail the invitation. After the commit, and never raising.

    Both halves are the contract `identity.email` and `reviews.notifications`
    already established, and they are load-bearing here: an invitation that
    cannot be delivered must not become an unusable pending row that blocks the
    inviter from trying again, and a mail failure must not roll back an
    invitation that is otherwise perfectly good - the inviter can revoke it and
    send another.
    """
    transaction.on_commit(lambda: _deliver(invitation.pk, raw_token))


def _deliver(invitation_id: int, raw_token: str) -> None:
    from invitations import email as invitation_email

    invitation_email.send_invitation_email(invitation_id, raw_token)


# --- sending -------------------------------------------------------------------------


def invite_to_organization(
    user: User | None,
    organization: Organization | object,
    email: str | None,
    role_slug: str = organization_services.MEMBER_ROLE_SLUG,
) -> IssuedInvitation:
    """
    Invite one email address to one organization, with one role, and email it.

    Requires `organization.members.manage` in that organization - which is what
    makes "the organization owner can invite members by email" a permission and
    not a role check somebody could forget. Owner is deliberately not an
    invitable role: handing out ownership is a role change
    (`organizations.services.assign_role_to_membership`), and letting an
    invitation create one would mean an Owner could mint a second Owner without
    the last-holder protection `revoke_membership_role` applies.

    The role is checked against the tenant *now*, so a typo cannot produce an
    invitation that fails at acceptance with a message the recipient cannot act
    on.
    """
    active_user = _require_active_user(user)

    membership = _require_organization_permission(
        active_user, organization, ORGANIZATION_MEMBERS_MANAGE
    )

    normalized_email = _normalize_email(email)

    if role_slug not in organization_services.INVITABLE_ORGANIZATION_ROLE_SLUGS:
        raise InvitationError('That role cannot be offered by invitation.', field='role')

    resolved = Organization.objects.filter(pk=membership.organization_id).first()
    if resolved is None:
        raise InvitationError('Organization is unavailable.')

    # Provision the Member role before offering it. Lazy and idempotent, so an
    # organization created before this role existed can still invite people with
    # the default role instead of failing with "that role cannot be offered".
    if role_slug == organization_services.MEMBER_ROLE_SLUG:
        organization_services.ensure_member_role(resolved)

    if normalized_email == active_user.email:
        raise InvitationError('You are already a member of this organization.', field='email')

    # Somebody who already accepted is a member; a member is not re-invited.
    if Membership.objects.filter(
        user__email=normalized_email,
        organization=resolved,
        status=Membership.Status.ACTIVE,
    ).exists():
        raise InvitationError(
            'That person is already a member of this organization.', field='email'
        )

    existing = _existing_open_invitation('organization', resolved.pk, normalized_email)
    if existing is not None:
        raise InvitationError('That person has already been invited.', field='email')

    raw_token, digest = _mint_token()

    with transaction.atomic():
        invitation = Invitation.objects.create(
            scope=Invitation.Scope.ORGANIZATION,
            organization=resolved,
            email=normalized_email,
            role_slug=role_slug,
            token_digest=digest,
            expires_at=timezone.now() + settings.INVITATION_TOKEN_LIFETIME,
            invited_by=active_user,
        )
        _send_invitation_email(invitation, raw_token)
        _notify_recipient(invitation.pk)

    logger.info(
        'Organization invitation created (organization=%s, invitation=%s, role=%s).',
        resolved.pk,
        invitation.pk,
        role_slug,
    )
    return IssuedInvitation(invitation=invitation, raw_token=raw_token)


def invite_to_team(
    user: User | None,
    team: Team | object,
    email: str | None,
    role_slug: str = team_services.MEMBER_ROLE_SLUG,
) -> IssuedInvitation:
    """
    Invite one email address to one team, with one role, and email it.

    Requires `team.members.manage`. There is no team "reviewer" role to offer
    and no way to invent one at this point: the invitable slugs are enumerated,
    and the Member role is the only one an invitation can grant besides Owner -
    which is offered because a team owner inviting a co-owner is the ordinary
    thing (unlike an organization, where ownership is a separate change).
    """
    active_user = _require_active_user(user)

    try:
        team_authorization.require_permission(
            active_user, team, team_authorization.TEAM_MEMBERS_MANAGE
        )
    except team_authorization.TeamAuthorizationError as exc:
        raise InvitationError(exc.message, field=exc.field, reason=exc.reason) from None

    normalized_email = _normalize_email(email)

    invitable = (team_services.OWNER_ROLE_SLUG, team_services.MEMBER_ROLE_SLUG)
    if role_slug not in invitable:
        raise InvitationError('That role cannot be offered by invitation.', field='role')

    resolved = Team.objects.filter(pk=getattr(team, 'pk', team)).first()
    if resolved is None:
        raise InvitationError('Team is unavailable.')

    if normalized_email == active_user.email:
        raise InvitationError('You are already a member of this team.', field='email')

    if TeamMembership.objects.filter(
        user__email=normalized_email,
        team=resolved,
        status=TeamMembership.Status.ACTIVE,
    ).exists():
        raise InvitationError('That person is already a member of this team.', field='email')

    existing = _existing_open_invitation('team', resolved.pk, normalized_email)
    if existing is not None:
        raise InvitationError('That person has already been invited.', field='email')

    raw_token, digest = _mint_token()

    with transaction.atomic():
        invitation = Invitation.objects.create(
            scope=Invitation.Scope.TEAM,
            team=resolved,
            email=normalized_email,
            role_slug=role_slug,
            token_digest=digest,
            expires_at=timezone.now() + settings.INVITATION_TOKEN_LIFETIME,
            invited_by=active_user,
        )
        _send_invitation_email(invitation, raw_token)
        _notify_recipient(invitation.pk)

    logger.info(
        'Team invitation created (team=%s, invitation=%s, role=%s).',
        resolved.pk,
        invitation.pk,
        role_slug,
    )
    return IssuedInvitation(invitation=invitation, raw_token=raw_token)


def _require_organization_permission(
    user: User, organization: Organization | object, permission_code: str
) -> Membership:
    """
    The caller's active membership of `organization`, having held
    `permission_code` there.

    Goes through `organizations.services`' own private wrapper so the refusal is
    that module's `OrganizationError` with its message, rather than a second
    phrasing of the same refusal from this module.
    """
    try:
        return organization_services._require_permission(user, organization, permission_code)
    except AuthorizationError as exc:
        raise InvitationError(exc.message, field=exc.field, reason=exc.reason) from None


def revoke_invitation(user: User | None, invitation_id: object) -> Invitation:
    """
    Revoke a `PENDING` invitation. The only way back from `REVOKED` is a new
    invitation.

    Authorized against the invitation's own tenant, so an organization Owner
    cannot revoke a team invitation and a team Owner cannot revoke an
    organization's - the permission required is looked up by scope, which is
    what stops a cross-tenant revocation.

    An invitation that is already `ACCEPTED` cannot be revoked, and this says so
    rather than pretending it worked: the membership it granted is a separate
    fact, and removing that is `organizations`/`teams` membership work, not
    invitation work.
    """
    active_user = _require_active_user(user)

    invitation = (
        Invitation.objects.select_related('organization', 'team')
        .filter(pk=_normalize_pk(invitation_id))
        .first()
    )
    if invitation is None:
        raise InvitationError('Invitation is unavailable.')

    if invitation.scope == Invitation.Scope.ORGANIZATION:
        _require_organization_permission(
            active_user, invitation.organization_id, ORGANIZATION_MEMBERS_MANAGE
        )
    else:
        try:
            team_authorization.require_permission(
                active_user, invitation.team_id, team_authorization.TEAM_MEMBERS_MANAGE
            )
        except team_authorization.TeamAuthorizationError as exc:
            raise InvitationError(exc.message, field=exc.field, reason=exc.reason) from None

    if invitation.status != Invitation.Status.PENDING:
        raise InvitationError('This invitation can no longer be revoked.')

    with transaction.atomic():
        claimed = Invitation.objects.filter(
            pk=invitation.pk, status=Invitation.Status.PENDING
        ).update(status=Invitation.Status.REVOKED, revoked_at=timezone.now())
        if not claimed:
            # Somebody accepted or revoked it between the read and here.
            raise InvitationError('This invitation can no longer be revoked.')
        invitation.refresh_from_db()

    logger.info('Invitation revoked (invitation=%s, actor=%s).', invitation.pk, active_user.pk)
    return invitation


def decline_invitation(user: User | None, raw_token: str) -> Invitation:
    """
    Decline an invitation addressed to `user`.

    The recipient's own act and only theirs. The address rule is the same one
    acceptance has: a signed-in account whose address is not the invitation's is
    refused rather than allowed to decline somebody else's invitation, because
    a decline is a statement *about* the invitation - "I do not want this" - and
    letting any account write one would turn the field into a way of denying
    another person their place.

    **Nothing is created and nothing is deactivated.** A membership only ever
    comes from acceptance, so there is no membership row to undo here; what the
    decline writes is the invitation's own terminal state, with who and when.
    That is the whole record, and it is the record an inviter is asking for when
    they wonder whether a link went stale or was turned down.

    Refuses anything that is not `PENDING`, with the same message acceptance
    uses for a spent token, so declining cannot be used to probe the state of an
    invitation nobody can act on. After the commit the inviter is told, and
    never inside this transaction - the same rule as every other notification.
    """
    active_user = _require_active_user(user)

    if not raw_token or not str(raw_token).strip():
        raise InvitationError('This invitation is not valid any more.')

    invitation = get_invitation_for_token(raw_token)
    if invitation is None or invitation.status != Invitation.Status.PENDING:
        raise InvitationError('This invitation is not valid any more.')

    if invitation.expires_at <= timezone.now():
        # Marked expired in its own transaction, exactly as acceptance does, so a
        # link that simply rotted does not keep saying "waiting to be accepted"
        # in somebody's list - and so the two agree about what a stale link is.
        Invitation.objects.filter(pk=invitation.pk, status=Invitation.Status.PENDING).update(
            status=Invitation.Status.EXPIRED
        )
        raise InvitationError('This invitation has expired.')

    # Refuse *before* the claim, exactly as acceptance does: an address mismatch
    # must not be able to burn the invitation for its real recipient.
    if invitation.email != active_user.email:
        raise InvitationError(
            'This invitation was sent to a different email address.',
            reason='forbidden',
        )

    with transaction.atomic():
        claimed = (
            Invitation.objects.filter(pk=invitation.pk, status=Invitation.Status.PENDING)
            .filter(email=active_user.email)
            .update(
                status=Invitation.Status.DECLINED,
                declined_by=active_user,
                declined_at=timezone.now(),
            )
        )
        if not claimed:
            # Accepted, revoked or expired between the read and here.
            raise InvitationError('This invitation is not valid any more.')
        invitation.refresh_from_db()

    _notify_inviter_of_decline(invitation.pk)

    logger.info('Invitation declined (invitation=%s, actor=%s).', invitation.pk, active_user.pk)
    return invitation


def _notify_inviter_of_decline(invitation_id: int) -> None:
    """
    Tell the inviter the invitation was turned down.

    Same reasoning as `_notify_inviter`, and the same reason it is not the
    recipient's notification: an outstanding invitation sits in the inviter's
    list looking live, and a decline is one of only two things that ends it.
    Before this existed, a decline had no notification at all and the inviter
    learned about it by noticing.
    """
    from notifications import services as notification_services

    invitation = (
        Invitation.objects.filter(pk=invitation_id)
        .select_related('invited_by', 'declined_by', 'organization', 'team')
        .first()
    )
    if invitation is None or invitation.invited_by_id == invitation.declined_by_id:
        return

    notification_services.deliver(
        recipients=invitation.invited_by,
        kind='invitation.declined',
        title=(
            f'{invitation.declined_by.first_name} declined the invitation to '
            f'{invitation.tenant_label}'
        ),
        body='Nothing was changed. You can send another invitation if you like.',
        send_email=True,
    )


def _normalize_pk(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise InvitationError('Invitation is unavailable.') from None


# --- accepting -----------------------------------------------------------------------


def get_invitation_for_token(raw_token: str) -> Invitation | None:
    """
    The invitation `raw_token` names, whatever its state, or `None`.

    A read-only inspection used by the frontend to show somebody *what* they were
    invited to before they sign in: the tenant's name and the role, and nothing
    that would be sensitive to a stranger holding a link. It reveals that a
    token is well-formed, which is why acceptance itself re-checks everything
    this returns.
    """
    if not raw_token:
        return None
    return (
        Invitation.objects.select_related('organization', 'team')
        .filter(token_digest=_digest(raw_token))
        .first()
    )


def accept_invitation(user: User | None, raw_token: str) -> Invitation:
    """
    Accept an invitation, creating the membership the caller does not yet have.

    The whole join, in one transaction: claim the invitation
    (`PENDING -> ACCEPTED`), create the membership, and attach the role. All of
    it or none - an accepted invitation with no membership is a broken state
    that nothing else would ever repair.

    The checks, in order, and the order is the security:

    1. an authenticated, active caller - there is no accepting an invitation as
       nobody, because a membership must belong to an account;
    2. the invitation exists, is `PENDING`, and has not expired - all reported
       with the same message, so acceptance is not an existence oracle for a
       token;
    3. **the invitation's address is the caller's own** - refused, not adapted;
    4. the tenant still exists and still has the role, so a role that was
       deleted or an organization that was removed fails at acceptance with a
       message rather than creating a membership with no role.

    Point 3 is checked *before* the claim, and the claim is conditional, so a
    mismatched address cannot even burn the invitation for its real recipient.
    """
    active_user = _require_active_user(user)

    if not raw_token:
        raise InvitationError('This invitation is not valid any more.')

    try:
        return _accept_invitation(active_user, raw_token)
    except _ExpiredInvitation as expired:
        # `PENDING -> EXPIRED`, conditionally: an acceptance that got there first
        # still wins and is not overwritten by a later refusal.
        Invitation.objects.filter(pk=expired.args[0], status=Invitation.Status.PENDING).update(
            status=Invitation.Status.EXPIRED
        )
        raise InvitationError('This invitation has expired.') from None


def _notify_recipient(invitation_id: int) -> None:
    """
    Tell a recipient who already has an account that they have been invited.

    **Only when the address belongs to an account.** An invitation is addressed to
    an email, and most of the time that address has never signed in - the email
    *is* the notification for those. But when the recipient already has an
    account, the invitation should also be waiting in their app: a person with a
    session open has no reason to be told twice, and a signed-out reader who is
    told nothing in-app cannot tell an invitation they declined from one that
    quietly expired.

    No token, and no accept link. `acceptInvitation` needs the plaintext token,
    which exists in exactly one place - the invitation email - because only its
    digest is stored. So this notification says what happened and where to look,
    and the link in it is the notifications list. Fabricating a link that cannot
    work would be worse than not having one.
    """
    from notifications import services as notification_services

    invitation = (
        Invitation.objects.filter(pk=invitation_id)
        .select_related('organization', 'team', 'invited_by')
        .first()
    )
    if invitation is None or invitation.status != Invitation.Status.PENDING:
        return

    recipient = User.objects.filter(email=invitation.email, is_active=True).first()
    if recipient is None or recipient.pk == invitation.invited_by_id:
        return

    tenant_kind = 'organization' if invitation.scope == Invitation.Scope.ORGANIZATION else 'team'
    notification_services.deliver(
        recipients=recipient,
        kind='invitation.received',
        title=f'You have been invited to join {invitation.tenant_label}',
        body=(
            f'{invitation.invited_by.first_name} invited you to this {tenant_kind} as '
            f'{invitation.role_slug}. Open the link in your email to accept or decline.'
        ),
    )


def _notify_inviter(invitation_id: int) -> None:
    """
    Tell the person who sent the invitation that it was accepted.

    They are the only one with a reason to want to know: an acceptance is the
    event that puts somebody on the roster, and until they hear it the invitation
    sits in their list looking outstanding. The recipient is not notified about
    their own acceptance - they performed it, and the invitation email is already
    the notification that they were invited at all.

    After the commit and never raising, like every other notification here.
    """
    from notifications import services as notification_services

    invitation = (
        Invitation.objects.filter(pk=invitation_id)
        .select_related('invited_by', 'accepted_by', 'organization', 'team')
        .first()
    )
    if invitation is None or invitation.invited_by_id == invitation.accepted_by_id:
        return

    notification_services.deliver(
        recipients=invitation.invited_by,
        kind='invitation.accepted',
        title=f'{invitation.accepted_by.first_name} joined {invitation.tenant_label}',
        body=f'Their {invitation.role_slug} role is now active.',
    )


class _ExpiredInvitation(Exception):
    """
    Internal control flow, not an error anybody handles.

    Thrown from inside the acceptance transaction and caught one frame out, so
    the refusal and the *record* of the expiry can be two separate transactions.
    Raising the refusal from inside would roll the record back along with
    everything else, which is the opposite of what it is for: the inviter's list
    has to be able to say "this one expired" rather than showing a pending
    invitation that will never work.
    """


def _accept_invitation(active_user: User, raw_token: str) -> Invitation:
    with transaction.atomic():
        # `of=('self',)`: both tenant columns are nullable, so `select_related`
        # joins them with LEFT OUTER JOIN and Postgres refuses to lock the
        # nullable side of one - `FOR UPDATE cannot be applied to the nullable
        # side of an outer join`. Locking only this row is what the single-use
        # guarantee needs; the tenant is re-read below for the membership, and
        # nothing here writes to it.
        invitation = (
            Invitation.objects.select_related('organization', 'team')
            .filter(token_digest=_digest(raw_token))
            .select_for_update(of=('self',))
            .first()
        )

        if invitation is None:
            raise InvitationError('This invitation is not valid any more.')
        if invitation.status != Invitation.Status.PENDING:
            raise InvitationError('This invitation is not valid any more.')
        if invitation.expires_at <= timezone.now():
            # The id travels out on the exception, so the transaction above can be
            # rolled back and the marking transaction that catches it can still
            # name the row. Raising the refusal from in here would take the record
            # of the expiry down with it.
            raise _ExpiredInvitation(invitation.pk)

        if invitation.email != active_user.email:
            # Safe refusal: nothing is revealed about whether the address is the
            # one that was invited, and nothing is changed.
            logger.info(
                'Refused invitation acceptance for a mismatched address (invitation=%s, user=%s).',
                invitation.pk,
                active_user.pk,
            )
            raise InvitationError(
                'This invitation was sent to a different email address.',
                reason='forbidden',
            )

        if invitation.scope == Invitation.Scope.ORGANIZATION:
            _accept_organization(invitation, active_user)
        else:
            _accept_team(invitation, active_user)

        # The conditional claim. `filter(status=PENDING)` is what makes the
        # invitation single-use under concurrency: whoever gets `claimed == 1`
        # is the one that created the membership above.
        claimed = Invitation.objects.filter(
            pk=invitation.pk, status=Invitation.Status.PENDING
        ).update(
            status=Invitation.Status.ACCEPTED,
            accepted_by=active_user,
            accepted_at=timezone.now(),
        )
        if not claimed:
            raise InvitationError('This invitation is not valid any more.')

        invitation.refresh_from_db()

    _notify_inviter(invitation.pk)

    logger.info(
        'Invitation accepted (invitation=%s, scope=%s, user=%s).',
        invitation.pk,
        invitation.scope,
        active_user.pk,
    )
    return invitation


def _accept_organization(invitation: Invitation, user: User) -> None:
    organization = invitation.organization
    if organization is None:
        raise InvitationError('The organization this invitation names no longer exists.')

    role = Role.objects.filter(organization=organization, slug=invitation.role_slug).first()
    if role is None:
        # The role was removed between the invitation being sent and it being
        # accepted. Say so rather than creating a member with no role, which
        # would look like an invitation that silently did nothing.
        raise InvitationError('That role is no longer available. Ask for a new invitation.')

    membership, created = Membership.objects.get_or_create(
        user=user,
        organization=organization,
        defaults={'status': Membership.Status.ACTIVE},
    )
    if not created and membership.status != Membership.Status.ACTIVE:
        # Re-accepting after having left: reactivate the row they already have
        # rather than creating a second one.
        membership.status = Membership.Status.ACTIVE
        membership.save()

    if not MembershipRole.objects.filter(membership=membership, role=role).exists():
        organization_services.grant_membership_role(membership, role)


def _accept_team(invitation: Invitation, user: User) -> None:
    team = invitation.team
    if team is None:
        raise InvitationError('The team this invitation names no longer exists.')

    role = TeamRole.objects.filter(team=team, slug=invitation.role_slug).first()
    if role is None:
        raise InvitationError('That role is no longer available. Ask for a new invitation.')

    membership, created = TeamMembership.objects.get_or_create(
        team=team,
        user=user,
        defaults={'status': TeamMembership.Status.ACTIVE},
    )
    if not created and membership.status != TeamMembership.Status.ACTIVE:
        membership.status = TeamMembership.Status.ACTIVE
        membership.save()

    if not TeamMembershipRole.objects.filter(membership=membership, role=role).exists():
        TeamMembershipRole.objects.create(membership=membership, role=role)


def expires_in(invitation: Invitation) -> timedelta:
    return invitation.expires_at - timezone.now()


# --- reading ---------------------------------------------------------------------------


def list_for_organization(user: User | None, organization_id: object) -> list[Invitation]:
    """
    The invitations an organization Owner can see: what has been sent and what is
    still outstanding.

    Empty - not an error - for anybody who cannot manage that organization's
    members, and empty for an organization that does not exist. The two are the
    same answer on purpose, so the argument cannot be used to discover
    organizations.
    """
    active_user = _require_active_user(user)
    try:
        organization_services._require_permission(
            active_user, organization_id, ORGANIZATION_MEMBERS_MANAGE
        )
    except AuthorizationError as exc:
        raise InvitationError(exc.message, field=exc.field, reason=exc.reason) from None

    return list(
        Invitation.objects.filter(organization_id=_normalize_pk(organization_id))
        .select_related('invited_by')
        .order_by('-created_at', '-pk')
    )


def list_for_team(user: User | None, team_id: object) -> list[Invitation]:
    """
    The invitations a team Owner can see. The team counterpart of
    `list_for_organization`, with the same empty answer for everybody else.
    """
    active_user = _require_active_user(user)
    try:
        team_authorization.require_permission(
            active_user, team_id, team_authorization.TEAM_MEMBERS_MANAGE
        )
    except team_authorization.TeamAuthorizationError as exc:
        raise InvitationError(exc.message, field=exc.field, reason=exc.reason) from None

    return list(
        Invitation.objects.filter(team_id=_normalize_pk(team_id))
        .select_related('invited_by')
        .order_by('-created_at', '-pk')
    )
