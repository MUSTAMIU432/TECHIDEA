"""
The Invitations GraphQL adapter.

Types with `from_model`, payloads carrying `(success, message, field)`, and
resolvers that move data between the schema and `invitations/services.py`. No
rule is decided here - the single-use, expiring, revocable, email-bound rules
live in the service and in `invitations/models.py`.

Two surfaces, deliberately separate
------------------------------------
- **Authenticated**: `sendOrganizationInvitation`, `sendTeamInvitation`,
  `revokeInvitation`, `organizationInvitations`, `teamInvitations`, and
  `invitation(token)` for looking at one you were sent.
- **Unauthenticated**: `invitationDetails(token)` and nothing else. A recipient
  who has no account yet has to be able to see *what* they were invited to before
  they register - that is the "Accept invitation -> Register -> Return to
  invitation -> Accept" journey. It returns the tenant's name, the role and
  whether the link is still usable, and **no token, no organization id and no
  list of members**, because the holder of a link may not be its recipient.

Acceptance itself (`acceptInvitation`) requires authentication, deliberately:
a membership must belong to an account, and creating one from inside an
invitation link would let an unauthenticated caller mint an account and a
membership from a forwarded email in one step.

**The token is never accepted as an argument to a read that returns it.** Every
resolver here takes a token and returns a description of the invitation; none of
them echoes the token back.
"""

import strawberry

from invitations import services
from invitations.models import Invitation

InvitationStatus = strawberry.enum(Invitation.Status, name='InvitationStatus')
InvitationScope = strawberry.enum(Invitation.Scope, name='InvitationScope')


@strawberry.type(
    description=(
        'One invitation: what it names, who it was sent to, and whether it can '
        'still be accepted. The token is never included - it exists in the email '
        'and nowhere else.'
    )
)
class InvitationType:
    id: strawberry.ID
    scope: InvitationScope
    status: InvitationStatus
    role_slug: str
    email: str
    tenant_name: str
    invited_by_id: strawberry.ID
    invited_by_first_name: str
    expires_at: str
    created_at: str
    accepted_at: str | None
    is_open: bool

    @staticmethod
    def from_model(invitation: Invitation) -> 'InvitationType':
        return InvitationType(
            id=strawberry.ID(str(invitation.pk)),
            scope=InvitationScope(invitation.scope),
            status=InvitationStatus(invitation.status),
            role_slug=invitation.role_slug,
            email=invitation.email,
            tenant_name=invitation.tenant_label,
            invited_by_id=strawberry.ID(str(invitation.invited_by_id)),
            invited_by_first_name=invitation.invited_by.first_name
            if invitation.invited_by_id
            else '',
            expires_at=invitation.expires_at.isoformat(),
            created_at=invitation.created_at.isoformat(),
            accepted_at=invitation.accepted_at.isoformat() if invitation.accepted_at else None,
            is_open=invitation.is_open,
        )


@strawberry.type(
    description=(
        'What an invitation link points at, for somebody who is not signed in '
        'yet. Enough to understand the invitation and nothing more: no token, no '
        'organization or team id, and nothing about the tenant other than its '
        'name.'
    )
)
class InvitationPreviewType:
    scope: InvitationScope
    tenant_name: str
    role_name: str
    email: str
    invited_by_first_name: str
    is_open: bool
    expired: bool
    accepted: bool
    revoked: bool
    declined: bool = strawberry.field(
        description='True when the recipient turned it down. Shown as its own state rather '
        'than folded into "expired", because the two mean opposite things to the person who '
        'sent it.'
    )

    @staticmethod
    def from_model(invitation: Invitation) -> 'InvitationPreviewType':
        return InvitationPreviewType(
            scope=InvitationScope(invitation.scope),
            tenant_name=invitation.tenant_label,
            role_name=invitation.role_slug.replace('_', ' ').capitalize(),
            email=invitation.email,
            invited_by_first_name=invitation.invited_by.first_name
            if invitation.invited_by_id
            else '',
            is_open=invitation.is_open,
            expired=not invitation.is_open and invitation.status == Invitation.Status.PENDING,
            accepted=invitation.status == Invitation.Status.ACCEPTED,
            revoked=invitation.status == Invitation.Status.REVOKED,
            declined=invitation.status == Invitation.Status.DECLINED,
        )


@strawberry.type(description='Result of sending or revoking an invitation.')
class InvitationPayload:
    success: bool
    message: str
    field: str | None = None
    invitation: InvitationType | None = None


@strawberry.type(description='Result of accepting an invitation.')
class AcceptInvitationPayload:
    success: bool
    message: str
    field: str | None = None
    invitation: InvitationType | None = None


@strawberry.input(description='Invite somebody to an organization, by email.')
class SendOrganizationInvitationInput:
    organization_id: strawberry.ID
    email: str
    role_slug: str = 'member'


@strawberry.input(description='Invite somebody to a team, by email.')
class SendTeamInvitationInput:
    team_id: strawberry.ID
    email: str
    role_slug: str = 'member'


def _safe(read):
    """
    Run a list-shaped read, turning a refusal into an empty list.

    The queries in this schema are *lists*, and a list that raises is a list the
    client has to handle as an error. A refusal here means "not your invitation",
    which for a list is the same as "no invitations" - and that is what the
    underlying selectors already promise, so this only normalises the error type.
    """
    try:
        return read()
    except services.InvitationError:
        return []


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            'The invitations you can see for an organization: what has been '
            'invited and what is still outstanding. Empty for anybody who cannot '
            "manage that organization's members, and empty for an organization "
            'that does not exist - the same answer either way.'
        )
    )
    def organization_invitations(
        self, info: strawberry.Info, organization_id: strawberry.ID
    ) -> list[InvitationType]:
        return [
            InvitationType.from_model(invitation)
            for invitation in _safe(
                lambda: services.list_for_organization(info.context.user, organization_id)
            )
        ]

    @strawberry.field(description='The invitations you can see for one of your teams.')
    def team_invitations(
        self, info: strawberry.Info, team_id: strawberry.ID
    ) -> list[InvitationType]:
        return [
            InvitationType.from_model(invitation)
            for invitation in _safe(lambda: services.list_for_team(info.context.user, team_id))
        ]

    @strawberry.field(
        description=(
            'What the invitation in this link is, for somebody who is not signed '
            'in. Returns null for a token that is unknown, expired, revoked or '
            'already accepted, so this cannot be used to confirm that a guess was '
            'ever valid.'
        )
    )
    def invitation_details(self, info: strawberry.Info, token: str) -> InvitationPreviewType | None:
        invitation = services.get_invitation_for_token(token)
        if invitation is None or not invitation.is_open:
            return None
        return InvitationPreviewType.from_model(invitation)


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            'Invite somebody to an organization by email, with a role, and email '
            'them a single-use link. No membership is created until they accept. '
            'Requires the permission to manage this organization`s members.'
        )
    )
    def send_organization_invitation(
        self, info: strawberry.Info, input: SendOrganizationInvitationInput
    ) -> InvitationPayload:
        try:
            issued = services.invite_to_organization(
                info.context.user, input.organization_id, input.email, input.role_slug
            )
        except services.InvitationError as exc:
            return InvitationPayload(success=False, message=exc.message, field=exc.field)
        return InvitationPayload(
            success=True,
            message=f'An invitation was sent to {issued.invitation.email}.',
            invitation=InvitationType.from_model(issued.invitation),
        )

    @strawberry.mutation(description='Invite somebody to a team by email, and email them a link.')
    def send_team_invitation(
        self, info: strawberry.Info, input: SendTeamInvitationInput
    ) -> InvitationPayload:
        try:
            issued = services.invite_to_team(
                info.context.user, input.team_id, input.email, input.role_slug
            )
        except services.InvitationError as exc:
            return InvitationPayload(success=False, message=exc.message, field=exc.field)
        return InvitationPayload(
            success=True,
            message=f'An invitation was sent to {issued.invitation.email}.',
            invitation=InvitationType.from_model(issued.invitation),
        )

    @strawberry.mutation(
        description=(
            'Withdraw an invitation that has not been accepted. Authorized '
            "against the invitation's own tenant, so an organization Owner "
            'cannot withdraw a team invitation.'
        )
    )
    def revoke_invitation(self, info: strawberry.Info, id: strawberry.ID) -> InvitationPayload:
        try:
            invitation = services.revoke_invitation(info.context.user, id)
        except services.InvitationError as exc:
            return InvitationPayload(success=False, message=exc.message, field=exc.field)
        return InvitationPayload(
            success=True,
            message='The invitation was withdrawn.',
            invitation=InvitationType.from_model(invitation),
        )

    @strawberry.mutation(
        description=(
            'Accept an invitation and join its organization or team. Requires '
            'you to be signed in with the address the invitation was sent to - '
            'signed in as somebody else is refused rather than adapted. Single '
            'use: a link that has been accepted, expired or withdrawn cannot be '
            'accepted again.'
        )
    )
    def accept_invitation(self, info: strawberry.Info, token: str) -> AcceptInvitationPayload:
        try:
            invitation = services.accept_invitation(info.context.user, token)
        except services.InvitationError as exc:
            return AcceptInvitationPayload(success=False, message=exc.message, field=exc.field)
        return AcceptInvitationPayload(
            success=True,
            message=f'You have joined {invitation.tenant_label}.',
            invitation=InvitationType.from_model(invitation),
        )

    @strawberry.mutation(
        description=(
            'Decline an invitation, closing it without joining. Requires you to be '
            'signed in with the address the invitation was sent to, for the same '
            'reason accepting does: a decline is a statement about the invitation, '
            'so only the person it was sent to may make one.\n\n'
            'Nothing is created and nothing is removed - a membership only ever '
            'comes from accepting - so this is safe to press and impossible to '
            'undo except by asking for another invitation.'
        )
    )
    def decline_invitation(self, info: strawberry.Info, token: str) -> AcceptInvitationPayload:
        try:
            invitation = services.decline_invitation(info.context.user, token)
        except services.InvitationError as exc:
            return AcceptInvitationPayload(success=False, message=exc.message, field=exc.field)
        return AcceptInvitationPayload(
            success=True,
            message=f'You have declined the invitation to {invitation.tenant_label}.',
            invitation=InvitationType.from_model(invitation),
        )
