"""
Decision letters: how an approval or a rejection reaches the idea's owner.

The path, and who acts at each step
-----------------------------------
1. A platform reviewer (the review team's lead) records **Approved** or **Rejected**.
   The decision is recorded as it always was - the idea moves, the report is written -
   and `draft` writes a letter from a template in the same transaction. The owner is
   **not** told; the administrators who send letters are.
2. An administrator holding `release_proposals` reads it on the console's *Decisions*
   page, edits it if they want, and `send`s it. Never somebody who decided the review.
   For an approval this is also the go-ahead for the review team to write the proposal:
   `proposals.start_proposal` is refused until it is sent, and the team is notified.
3. Only then does the owner learn the outcome: a notification, an email, and the letter
   at the top of their idea's page. Until then `is_withheld_from` makes the idea read
   as still under review to them, and the last round and the report stay hidden.

A **changes request** never has a letter. It is the reviewers asking the owner for
something, so it goes to them directly (`reviews.platform_review.notify_decision`).

The email
---------
The letter itself can quote the reviewer's feedback, and an inbox is readable by more
than its owner (see `notifications.email`). So the email is a styled announcement - a
congratulation or a gentle "we have news" - with a button to read the letter in the
app, and the letter stays behind authentication.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from administration import authorization as admin_authorization
from administration import services as admin_services
from administration.authorization import AdministrationError
from administration.models import AdminAuditEntry
from ideas.models import Idea
from identity.models import User
from reviews import eligibility
from reviews.models import DecisionLetter, Review

logger = logging.getLogger(__name__)

PLATFORM_NAME = 'MUSTANET'
MESSAGE_MAX_LENGTH = 5000

# Each paragraph is one line, joined by blank lines: the letter is shown as written,
# so a line break inside a paragraph would show up as one.
_APPROVED_TEMPLATE = '\n\n'.join(
    (
        'Dear {first_name},',
        'Congratulations! We are delighted to tell you that your idea "{title}" has been '
        'approved by the {platform} review team.',
        'Your idea stood out for the clear way it describes a real problem, and for the '
        'difference automating it can make to the people who live with it every day.',
        'What happens next: our review team is now preparing a proposal for you - the '
        'solution, the timeline and what it will take. You will be notified as soon as it is '
        'ready for you to read, and nothing goes ahead until you give your go-ahead.',
        'Thank you for putting this forward. We look forward to building it with you.',
        'Warm regards,\nThe {platform} team',
    )
)

_REJECTED_TEMPLATE = '\n\n'.join(
    (
        'Dear {first_name},',
        'Thank you for submitting your idea "{title}", and for the time and care you put into it.',
        'After a careful review, we are sorry to tell you that we are not able to take it '
        'forward at this time.{feedback_section}',
        'This decision is about this idea, at this time - not about the value of your '
        'contribution. Many of the best ideas we see come from people who have submitted '
        'before, and we would genuinely welcome your next one.',
        'With kind regards,\nThe {platform} team',
    )
)


class DecisionLetterError(AdministrationError):
    """A refused letter operation, in the project's `(message, field)` shape."""


def _first_name(user: User) -> str:
    return user.first_name or user.get_full_name() or 'there'


def default_message(idea: Idea, decision: str, feedback: str = '') -> str:
    """The template for this decision, filled in for this idea and its owner."""
    context = {
        'first_name': _first_name(idea.author),
        'title': idea.title,
        'platform': PLATFORM_NAME,
    }
    if decision == DecisionLetter.Decision.APPROVED:
        return _APPROVED_TEMPLATE.format(**context)
    feedback = (feedback or '').strip()
    feedback_section = (
        f'\n\nThe review team shared the following with you:\n\n{feedback}' if feedback else ''
    )
    return _REJECTED_TEMPLATE.format(**context, feedback_section=feedback_section)


# --- drafting, inside the reviewer's transaction ---------------------------------------


def draft(review: Review) -> DecisionLetter | None:
    """
    Write the letter for a platform approval or rejection. Called by
    `reviews.services.complete_review` in its transaction; anything else returns None.
    """
    if review.scope != Review.Scope.PLATFORM or review.decision not in (
        Review.Decision.APPROVED,
        Review.Decision.REJECTED,
    ):
        return None
    letter = DecisionLetter.objects.create(
        idea=review.idea,
        review=review,
        decision=review.decision,
        message=default_message(review.idea, review.decision, review.feedback),
    )
    transaction.on_commit(lambda: _tell_the_senders(letter.pk))
    return letter


def _tell_the_senders(letter_pk: int) -> None:
    from notifications import services as notification_services
    from reviews.proposals import release_managers

    letter = DecisionLetter.objects.select_related('idea').filter(pk=letter_pk).first()
    if letter is None:
        return
    verdict = 'approved' if letter.decision == DecisionLetter.Decision.APPROVED else 'rejected'
    notification_services.deliver(
        recipients=release_managers(),
        kind='review.decision_awaiting_release',
        title=f'"{letter.idea.title}" was {verdict} - the owner is waiting to hear',
        body='A review team reached a decision. Read it, adjust the letter if you want, and '
        'send it to the owner from the Decisions page.',
        idea=letter.idea,
        send_email=False,
    )


# --- what the owner may see before the letter is sent -----------------------------------


def pending_letter(idea: Idea) -> DecisionLetter | None:
    """The letter for this idea's current decision, if it has not been sent yet."""
    if idea.status not in (Idea.Status.APPROVED, Idea.Status.REJECTED):
        return None
    return DecisionLetter.objects.filter(idea=idea, status=DecisionLetter.Status.PENDING).first()


def is_withheld_from(user: User | None, idea: Idea) -> bool:
    """
    Whether `user` must still see `idea` as under review: a decision is waiting to be
    sent, and they are not on the platform side that made it.
    """
    if idea.status not in (Idea.Status.APPROVED, Idea.Status.REJECTED):
        return False
    if eligibility.is_platform_reviewer(user):
        return False
    return pending_letter(idea) is not None


def withheld_review_ids(idea: Idea) -> set[int]:
    """The reviews whose outcome the owner has not been told yet."""
    return set(
        DecisionLetter.objects.filter(idea=idea, status=DecisionLetter.Status.PENDING).values_list(
            'review_id', flat=True
        )
    )


def sent_letter_for(user: User | None, idea_id: object) -> DecisionLetter | None:
    """The latest sent letter of an idea `user` may read, or None."""
    from ideas import selectors as idea_selectors

    idea = idea_selectors.get_idea(user, idea_id)
    if idea is None:
        return None
    return (
        DecisionLetter.objects.select_related('sent_by')
        .filter(idea=idea, status=DecisionLetter.Status.SENT)
        .order_by('-sent_at', '-pk')
        .first()
    )


# --- the administrator's side ------------------------------------------------------------


def list_letters(actor: User | None, status: str | None = None) -> list[DecisionLetter]:
    """Letters for the console, newest first. Empty without `release_proposals`."""
    if not admin_authorization.capabilities_for(actor).can_release_proposals:
        return []
    letters = DecisionLetter.objects.select_related(
        'idea', 'idea__author', 'review', 'review__reviewer', 'review__decided_by', 'sent_by'
    )
    if status:
        letters = letters.filter(status=status)
    return list(letters.order_by('status', '-created_at', '-pk'))


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise DecisionLetterError('That letter is not available.') from None


@transaction.atomic
def send(actor: User | None, letter_id: object, message: str | None = None) -> DecisionLetter:
    """
    Send the letter to the idea's owner: the moment the owner learns the outcome.

    Refused for anybody who decided the review - the reviewer, or whoever recorded the
    decision - so the people who judged an idea are not also the ones who tell its owner.
    """
    admin = admin_authorization.require_release_proposals(actor)
    letter = (
        DecisionLetter.objects.select_for_update()
        .select_related('idea', 'idea__author', 'review')
        .filter(pk=_as_int(letter_id))
        .first()
    )
    if letter is None:
        raise DecisionLetterError('That letter is not available.')
    if letter.status == DecisionLetter.Status.SENT:
        raise DecisionLetterError('This letter has already been sent.')
    if admin.pk in {letter.review.reviewer_id, letter.review.decided_by_id}:
        raise DecisionLetterError(
            'You decided this review, so somebody else has to send its letter.'
        )

    text = (message if message is not None else letter.message).strip()
    if not text:
        raise DecisionLetterError('Write the letter before sending it.', field='message')
    if len(text) > MESSAGE_MAX_LENGTH:
        raise DecisionLetterError(
            f'The letter must be {MESSAGE_MAX_LENGTH} characters or fewer.', field='message'
        )

    letter.message = text
    letter.status = DecisionLetter.Status.SENT
    letter.sent_at = timezone.now()
    letter.sent_by = admin
    letter.save(update_fields=['message', 'status', 'sent_at', 'sent_by'])

    admin_services.record_event(
        admin,
        AdminAuditEntry.Action.DECISION_LETTER_SENT,
        'idea',
        letter.idea_id,
        letter.idea.title,
        metadata={'decision': letter.decision, 'review': letter.review_id},
    )
    letter_pk = letter.pk
    transaction.on_commit(lambda: _tell_the_owner(letter_pk))
    if letter.decision == DecisionLetter.Decision.APPROVED:
        transaction.on_commit(lambda: _open_the_proposal(letter_pk))
    return letter


def _open_the_proposal(letter_pk: int) -> None:
    """
    Tell the review team they can write the proposal: sending the approval is the
    platform admin's go-ahead for it, as much as it is the owner's news.
    """
    from notifications import services as notification_services
    from reviews.proposals import writers_for

    letter = DecisionLetter.objects.select_related('idea').filter(pk=letter_pk).first()
    if letter is None:
        return
    writers = writers_for(letter.idea)
    if writers is None:
        return
    notification_services.deliver(
        recipients=list(User.objects.filter(pk__in=writers.member_ids, is_active=True)),
        kind='proposal.writing_opened',
        title=f'You can now write the proposal for "{letter.idea.title}"',
        body='The platform admin confirmed your approval and told the owner. Start the '
        'proposal from the idea page.',
        idea=letter.idea,
        # In the app only, like every other nudge to reviewers: the work is waiting for
        # them there, and mailing a whole team for it is how a notification stops being read.
        send_email=False,
    )


def _tell_the_owner(letter_pk: int) -> None:
    from notifications import services as notification_services

    letter = (
        DecisionLetter.objects.select_related('idea', 'idea__author').filter(pk=letter_pk).first()
    )
    if letter is None:
        return
    idea = letter.idea
    approved = letter.decision == DecisionLetter.Decision.APPROVED
    notification_services.deliver(
        recipients=idea.author,
        kind='idea.platform_approved' if approved else 'idea.platform_rejected',
        title=(
            f'Congratulations - "{idea.title}" has been approved'
            if approved
            else f'An update on your idea "{idea.title}"'
        ),
        body=(
            'Your idea has been approved by the platform. A letter from the team is waiting '
            'for you on the idea page.'
            if approved
            else 'The platform has reached a decision on your idea. A letter from the team is '
            'waiting for you on the idea page.'
        ),
        # No report: the notification opens the idea, where the letter is - the report
        # is one link away from there.
        idea=idea,
        # The letter has its own, better email below; the generic one would be a second.
        send_email=False,
    )
    _email_the_owner(letter)


def _email_the_owner(letter: DecisionLetter) -> None:
    """The styled announcement. Never raises: the letter is sent whatever the mail does."""
    from notifications.email import build_action_url_path

    idea = letter.idea
    owner = idea.author
    approved = letter.decision == DecisionLetter.Decision.APPROVED
    context = {
        'platform': PLATFORM_NAME,
        'first_name': _first_name(owner),
        'title': idea.title,
        'approved': approved,
        'idea_url': build_action_url_path(f'/app/ideas/{idea.pk}'),
    }
    subject = (
        f'Congratulations - your idea "{idea.title}" has been approved'
        if approved
        else f'An update on your idea "{idea.title}"'
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string('reviews/email/decision_letter.txt', context),
        to=[owner.email],
        from_email=settings.DEFAULT_FROM_EMAIL,
    )
    message.attach_alternative(
        render_to_string('reviews/email/decision_letter.html', context), 'text/html'
    )
    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception(
            'Could not email the decision letter (letter=%s). It is on the idea page.', letter.pk
        )
