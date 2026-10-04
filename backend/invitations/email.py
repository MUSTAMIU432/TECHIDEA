"""
The invitation email.

One message, to one address that may or may not have an account - so unlike
`identity.email._send`, which mails a `User`, this one takes the `Invitation` row
and the plaintext token and addresses them to the address **on the invitation**.
That is not a relaxation: the address is validated and normalized when the
invitation is created and is exactly the address acceptance requires, so mailing
it is the only address that could possibly work.

Same contract as every other transactional message in the project
(`identity.email`, `reviews.notifications`), and the same three properties,
because an invitation is the one message where getting this wrong has a security
consequence rather than an annoyance:

- **Plain text**, from `DEFAULT_FROM_EMAIL`, to the invitation's stored address.
- **After the transaction commits** (`services._send_invitation_email` registers
  it with `transaction.on_commit`), so an invitation that was rolled back sends
  nothing.
- **Never raises.** A delivery failure is logged; the invitation still stands and
  the inviter can revoke it and send another.

**What the message does and does not contain.** It names the tenant, the role,
the inviter, when it expires, and a link to accept. It does **not** contain the
organization's ideas, its members, or anything else the recipient is not yet
entitled to - an inbox is readable by more than its owner and outlives the
account's access, so the body is a statement about the invitation and nothing
else. Every link is a frontend URL with the token in the query string, exactly
like the password-reset link, so the token travels over the same TLS the rest of
the app does and is never logged by this module.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from invitations.models import Invitation

logger = logging.getLogger(__name__)

INVITATION_PATH = '/invitations/accept'

# No per-recipient or per-tenant data in the subject, for the reason on
# `identity.email.PASSWORD_RESET_SUBJECT`: a lock-screen preview is not private.
ORGANIZATION_INVITATION_SUBJECT = 'You have been invited to join an organization'
TEAM_INVITATION_SUBJECT = 'You have been invited to join a team'


def subject_for(invitation: Invitation) -> str:
    return (
        ORGANIZATION_INVITATION_SUBJECT
        if invitation.scope == Invitation.Scope.ORGANIZATION
        else TEAM_INVITATION_SUBJECT
    )


def send_invitation_email(invitation_id: int, raw_token: str) -> None:
    """
    Mail the invitation `raw_token` belongs to.

    Takes an id and re-reads the row, because it runs after the commit: the row
    it describes is the committed one. Does nothing if the invitation is no
    longer `PENDING` - a revoked or already-accepted invitation must not produce
    a working-looking link, and the token in that message is worthless anyway
    (`status` is checked on acceptance), but there is no reason to send it.
    """
    invitation = (
        Invitation.objects.select_related('organization', 'team', 'invited_by')
        .filter(pk=invitation_id)
        .first()
    )
    if invitation is None or invitation.status != Invitation.Status.PENDING:
        return

    # `build_action_url` raises ImproperlyConfigured when FRONTEND_URL is unset,
    # which every environment sets. Caught here rather than allowed to escape,
    # because the module's contract is that it never raises - a misconfigured
    # deployment should log an invitation it could not deliver, not turn sending
    # one into a 500 that the inviter sees as a failure to invite.
    try:
        from identity.email import build_action_url, format_lifetime

        action_url = build_action_url(INVITATION_PATH, raw_token)
        lifetime = format_lifetime(settings.INVITATION_TOKEN_LIFETIME)
    except Exception:
        logger.exception(
            'Could not build the invitation link (invitation=%s). The invitation '
            'stands; the recipient was not notified.',
            invitation.pk,
        )
        return

    body = render_to_string(
        'invitations/email/invitation.txt',
        {
            'app_name': 'Automation Platform',
            'tenant_name': invitation.tenant_label,
            'tenant_kind': (
                'organization' if invitation.scope == Invitation.Scope.ORGANIZATION else 'team'
            ),
            'role_name': invitation.role_slug.replace('_', ' ').capitalize(),
            'invited_by_first_name': invitation.invited_by.first_name,
            'action_url': action_url,
            'lifetime': lifetime,
        },
    )

    message = EmailMessage(
        subject=subject_for(invitation),
        body=body,
        to=[invitation.email],
        from_email=settings.DEFAULT_FROM_EMAIL,
    )

    try:
        message.send(fail_silently=False)
    except Exception:
        # Broad for the reason in `identity.email._send`: every backend raises its
        # own type, and the invitation is committed either way.
        logger.exception(
            'Could not send the invitation email (invitation=%s, email=%s). The '
            'invitation stands; the recipient was not notified.',
            invitation.pk,
            invitation.email,
        )
