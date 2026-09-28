"""
The author's email when a review is decided (docs/reviews-domain.md §15).

One message, to one person: the idea's author, when a reviewer completes a
review with any decision. Not a notification system - no model, no inbox, no
queue - and deliberately the same contract as `identity/email.py`:

- **Plain text**, from `DEFAULT_FROM_EMAIL`, to the *stored* address of the
  author - never an address from a request.
- **Sent after the transaction commits** (`complete_review` registers it with
  `transaction.on_commit`), so a completion that rolls back sends nothing, and
  nobody is told about a decision that does not exist.
- **Never raises.** A delivery failure is logged and the decision stands:
  the review is recorded either way, and the author can read it in the app.
- **Carries the decision and a link, not the feedback or the criteria.** An
  inbox is readable by more than its owner and outlives the account's access;
  the review's content stays behind authentication, where the author's review
  history already shows it. The idea's title is included - it is the author's
  own text and the only way to tell two notifications apart.

Synchronous, because there is no worker infrastructure and this is one small
message per decision; `docs/reviews-domain.md` §15 records that trade.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from reviews.models import Review

logger = logging.getLogger(__name__)

# No per-recipient or per-idea data in the subject, for the reason on
# `identity.email.PASSWORD_RESET_SUBJECT`: a lock-screen preview is not private.
REVIEW_DECISION_SUBJECT = 'Your idea has been reviewed'
IDEAS_PATH = '/app/ideas'

NEXT_STEPS = {
    Review.Decision.CHANGES_REQUESTED: (
        'The reviewer asked for changes. Revise the idea and submit it again when it is ready.'
    ),
    Review.Decision.APPROVED: None,
    Review.Decision.REJECTED: None,
}


def send_review_decision_email(review_id: int) -> None:
    """
    Tell the author of `review_id`'s idea that it was decided.

    Takes an id and reads the review again, because it runs after the commit:
    the row it describes is the committed one. Does nothing for a review that
    is not completed - there is no decision to report.
    """
    review = Review.objects.select_related('idea__author').filter(pk=review_id).first()
    if review is None or review.completed_at is None:
        return

    author = review.idea.author
    body = render_to_string(
        'reviews/email/review_decision.txt',
        {
            'first_name': author.first_name,
            'app_name': 'Automation Platform',
            'idea_title': review.idea.title,
            'decision': review.get_decision_display(),
            'next_step': NEXT_STEPS.get(review.decision),
            'ideas_url': f'{settings.FRONTEND_URL}{IDEAS_PATH}' if settings.FRONTEND_URL else '',
        },
    )
    message = EmailMessage(
        subject=REVIEW_DECISION_SUBJECT,
        body=body,
        to=[author.email],
        from_email=settings.DEFAULT_FROM_EMAIL,
    )

    try:
        message.send(fail_silently=False)
    except Exception:
        # Broad for the reason in `identity.email._send`: every backend raises
        # its own type, and the decision is already committed either way.
        logger.exception(
            'Could not send the review decision email (review=%s, author=%s). The '
            'decision is recorded; the author was not notified.',
            review.pk,
            author.pk,
        )
