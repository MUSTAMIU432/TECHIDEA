"""
Outgoing transactional email: password-reset and account-activation links.

Transport only. Deciding *whether* to send one, minting its token and
recording it are `identity.services`' job; this module turns an already-decided
send into a message and hands it to Django's mail layer, which is where the
choice of SMTP server (or, in local development, printing to the console)
actually lives.

Plain SMTP, not a Google API client
----------------------------------
The only Google credential this project holds is a public OAuth *client id*,
used to verify sign-in ID tokens - it identifies the app and authorizes
nothing, so it cannot send mail. Sending through Google would need a different
credential entirely: a service-account key file (`GOOGLE_APPLICATION_CREDENTIALS`,
which this project deliberately has no setting for) plus domain-wide
delegation to impersonate a mailbox, or an OAuth client secret that the
ID-token flow was specifically designed to do without. Gmail's SMTP relay
needs neither: a mailbox address and an app password, configured as
EMAIL_HOST_USER/EMAIL_HOST_PASSWORD. Because the whole module goes through
`django.core.mail`, moving to a hosted provider later is a settings change
(EMAIL_BACKEND), not a rewrite.

Text-only, on purpose
---------------------
These messages carry a credential that resets a password or verifies an email
account, and the one thing they must do is get the reader to a real link.
That argues for the smallest possible markup: a plain-text body has no
remote images, no tracking pixel, no external stylesheet, and no HTML that
could render a subtly different link text from the link target - the class of
thing that makes a phishing mail convincing, and a real risk for a message
whose entire purpose is "click this link". The styled version of the auth
pages is already what the link leads to.

Failure is logged, never raised
-------------------------------
Every caller here is in the middle of an operation that reports a *generic*
result on purpose (`request_password_reset` answers identically whether or not
an account exists, so it cannot be used to probe for one). That discipline
means a broken mail configuration is invisible at the API boundary by
construction: the request "succeeds" and the user waits forever for a message
that was never sent. So a send that fails is logged at ERROR - loudly, with the
reason - and the operation continues to its normal outcome. The alternative
(raising) would either leak account existence to fix a delivery problem, or
force every caller to reimplement this catch. The cost of this choice is that
a broken mail setup is found in the logs rather than in a support ticket;
config/settings/production.py refuses to start on the settings that cause the
commonest version of it (the console backend) to reduce how long that lasts.
"""

import logging
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from identity.models import User

logger = logging.getLogger(__name__)

# The frontend routes a clicked link lands on. Both are the frontend's own
# paths, not API paths - the user is sent to a page in the browser app, which
# then calls the GraphQL mutation with the token from the URL.
# (`noqa: S105` throughout this module: ruff's hardcoded-password heuristic
# just pattern-matches the word "password" in a name. Nothing here is a
# password - see identity/schema.py's matching note.)
PASSWORD_RESET_PATH = '/reset-password'  # noqa: S105
ACTIVATION_PATH = '/activate-account'

# Subjects carry no per-recipient data on purpose: an inbox preview is visible
# to more than its owner (a lock-screen notification, a shared machine), and
# "Reset your ... password" tells the reader what to look for without naming
# the account it belongs to.
PASSWORD_RESET_SUBJECT = 'Reset your Automation Platform password'  # noqa: S105
ACTIVATION_SUBJECT = 'Confirm your email address'


def format_lifetime(lifetime: timedelta) -> str:
    """
    Render a token lifetime as words for the email body ("45 minutes").

    Derived from the setting rather than written into the template, so a
    deployment that shortens (or lengthens) the window cannot leave both
    telling the truth about the one number that decides whether a user's link
    still works.
    """
    total_minutes = int(lifetime.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f'{hours} hour{"s" if hours != 1 else ""} {minutes} minutes'
    if hours:
        return f'{hours} hour{"s" if hours != 1 else ""}'
    return f'{minutes} minute{"s" if minutes != 1 else ""}'


def build_action_url(path: str, token: str) -> str:
    """
    The absolute link a user clicks, from the token and the frontend origin.

    Raises ImproperlyConfigured rather than returning a relative path if
    FRONTEND_URL is unset: every environment sets it (local.py defaults it to
    the Vite dev server, production.py requires an https:// one), and a
    "correct-looking" relative link would sail through into an email that
    simply never resolves.
    """
    if not settings.FRONTEND_URL:
        raise ImproperlyConfigured(
            'FRONTEND_URL must be set to build an emailed action link (see backend/.env.example).'
        )

    return f'{settings.FRONTEND_URL}{path}?{urlencode({"token": token})}'


def send_password_reset_email(user: User, raw_token: str) -> None:
    """
    Mail `user` a single-use link that lets them choose a new password.

    Never raises - see this module's docstring. `raw_token` is the plaintext
    credential that exists nowhere else; it is written into the message and
    never logged, stored, or recoverable afterwards.
    """
    _send(
        user=user,
        subject=PASSWORD_RESET_SUBJECT,
        template_name='identity/email/password_reset.txt',
        context={
            'action_url': build_action_url(PASSWORD_RESET_PATH, raw_token),
            'lifetime': format_lifetime(settings.PASSWORD_RESET_TOKEN_LIFETIME),
        },
    )


def send_activation_email(user: User, raw_token: str) -> None:
    """
    Mail `user` a single-use link that marks their email address as verified.

    Never raises - see this module's docstring. `raw_token` is the plaintext
    credential that exists nowhere else; it is written into the message and
    never logged, stored, or recoverable afterwards.
    """
    _send(
        user=user,
        subject=ACTIVATION_SUBJECT,
        template_name='identity/email/activation.txt',
        context={
            'action_url': build_action_url(ACTIVATION_PATH, raw_token),
            'lifetime': format_lifetime(settings.ACTIVATION_TOKEN_LIFETIME),
        },
    )


def _send(*, user: User, subject: str, template_name: str, context: dict[str, str]) -> None:
    """
    Render and hand one message to the configured mail backend.

    The recipient is the *stored* `user.email`, never a value supplied by the
    request: that is what makes it safe to mail an arbitrary submitted address
    at all. `user.get_full_name()` is passed for the greeting but never
    included in the subject, for the reason on PASSWORD_RESET_SUBJECT.

    Django's template layer autoescapes, so a name containing an angle bracket
    is text rather than markup - one less thing to remember when a template is
    edited later.
    """
    body = render_to_string(
        template_name,
        {**context, 'first_name': user.first_name, 'app_name': 'Automation Platform'},
    )

    message = EmailMessage(
        subject=subject,
        body=body,
        to=[user.email],
        # The list form rather than `reply_to`: nothing in either message
        # expects a reply, and a user who does hit reply should not reach an
        # unattended mailbox that looks like a person.
        from_email=settings.DEFAULT_FROM_EMAIL,
    )

    try:
        message.send(fail_silently=False)
    except Exception:
        # Deliberately broad. Every backend raises its own type (SMTPException,
        # ConnectionRefusedError, TimeoutError, and whatever an SSL or DNS
        # failure surfaces as), and an enumerated allow-list would be a new
        # failure mode of its own: the one exception type not on the list
        # would escape this handler and turn a delivery problem into a 500
        # that *does* vary with whether the address has an account.
        logger.exception(
            'Could not send transactional email (user=%s). The link in it was '
            'never delivered; the requesting operation continued.',
            user.pk,
        )
