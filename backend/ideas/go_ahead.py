"""
The owner's go-ahead: platform approval is not permission to proceed.

Three facts this module exists to keep apart, which the product asks for
explicitly and which a single "approved" state would collapse:

    PLATFORM_APPROVED  the platform reviewer approved the submission and a
                       report was generated and delivered to the owner.
    GO-AHEAD           the **owner** has read that report and authorized this
                       idea to proceed toward implementation.
    READY              the idea is handed to the developer track (Sprint 4).

Only the second is the owner's to give, and only the author's. Everything about
it is deliberately unfriendly to automation:

- **Opening the report is not approval.** There is no read-tracking here that
  could be mistaken for a decision, and `confirm_go_ahead` does not care whether
  the report has been opened. A `platform_report_read_at` column would be a
  temptation to infer consent from a page view, and consent inferred from a page
  view is not consent.
- **Receiving the email or the notification is not approval.** Both are delivered
  by `notifications.services.deliver`, which runs after the commit and never
  raises - it is a side channel that cannot possibly have side effects on the
  lifecycle, and this module has no coupling to it.
- **The click must be the author's.** Not an organization Owner, not a team
  Owner, not a platform administrator: `idea.author_id`. A company cannot
  approve its own member's idea into implementation, and no role anywhere in the
  platform can stand in for the person who wrote it.

`confirm_go_ahead` is a thin, deliberate wrapper over
`lifecycle.transition_idea(..., READY_FOR_IMPLEMENTATION)`. The state change
itself is a lifecycle transition like any other - the matrix is the only place
that decides who may move an idea where - so this module adds the *operation's
meaning* (the wording, the report requirement, the audit entry) without becoming
a second way to reach the state. A caller that wanted to skip the report could
call the lifecycle directly, so `confirm_go_ahead` is the operation the UI uses
and `available_transitions` is what the UI reads; the security of the move itself
is the matrix's, which refuses anyone who is not the author.
"""

import logging

from django.db import transaction
from django.utils import timezone

from ideas.models import Idea
from reviews.models import IdeaProposal, PlatformReviewReport

logger = logging.getLogger(__name__)

# The wording of the confirmation dialog. Defined here rather than in the
# frontend so that the sentence the backend considers sufficient and the sentence
# the user reads are edited in the same commit - a dialog that asks for less than
# the backend requires is a consent problem, not a copy problem.
CONFIRMATION_PROMPT = 'Proceed to implementation?'
CONFIRMATION_STATEMENT = (
    'By continuing, you confirm that you have reviewed the platform review and '
    'approval report and authorize this idea to proceed toward implementation.'
)

NOT_READY = 'This idea is not waiting for your confirmation.'
NOT_THE_OWNER = 'Only the person who submitted this idea can give the go-ahead.'
NO_REPORT = 'Read the platform review report before deciding.'
NO_PROPOSAL = (
    'Your proposal has not been released to you yet. The platform will send it when it is ready.'
)


def can_give_go_ahead(user, idea: Idea) -> bool:
    """
    Whether `user` may give the go-ahead on `idea`.

    Three conditions, all cheap: the idea is approved, the user is its author,
    and a report exists for it. The last one is the interesting one - it is what
    stops a go-ahead on an approval whose report was never generated. It cannot
    normally happen (the report is written in the approval's transaction), so this
    is a backstop against a partial write rather than a rule somebody hits, and
    that is exactly why it belongs in the predicate: it costs one indexed read
    only on the approved path.
    """
    if user is None or not user.is_active or idea is None:
        return False
    if idea.status != Idea.Status.APPROVED:
        return False
    if idea.author_id != getattr(user, 'pk', None):
        return False
    return (
        PlatformReviewReport.objects.filter(idea=idea).exists()
        and IdeaProposal.objects.filter(idea=idea, status='released').exists()
    )


def report_for(idea: Idea) -> PlatformReviewReport | None:
    """The report the owner is being asked about, or `None` if there is not one."""
    if idea is None or not idea.pk:
        return None
    return (
        PlatformReviewReport.objects.select_related('review', 'review__reviewer', 'version')
        .filter(idea=idea)
        .order_by('-generated_at', '-pk')
        .first()
    )


@transaction.atomic
def confirm_go_ahead(user, idea_id: object) -> Idea:
    """
    The owner's explicit authorization: `APPROVED -> READY_FOR_IMPLEMENTATION`.

    Every check is asked again here, on the locked idea, whatever the client was
    shown - the state has not changed since, or has, and if it has this is a
    refusal rather than a duplicate transition.

    The transition is made by the lifecycle, which is the only code in the
    platform that writes `Idea.status`; this function owns the operation's
    wording, the report requirement, and the audit record of the decision
    (which is *not* an `IdeaTransition`, because that records a status change -
    the go-ahead is recorded on the idea row itself, as `owner_go_ahead_at` and
    `owner_go_ahead_by`, which is where a later reader looks for "who authorized
    this").
    """
    from ideas import lifecycle
    from ideas.services import IdeaError

    if user is None or not user.is_active:
        raise IdeaError('You must be signed in to do that.', reason='unauthenticated')

    with transaction.atomic():
        # The locked, visibility-filtered read the lifecycle itself uses, so a
        # go-ahead is refused for precisely the ideas a read would have hidden.
        idea = _locked_idea(user, idea_id)
        if idea is None:
            raise IdeaError('Idea is unavailable.', reason='forbidden')

        if idea.status != Idea.Status.APPROVED:
            raise IdeaError(NOT_READY, reason='forbidden')
        if idea.author_id != user.pk:
            raise IdeaError(NOT_THE_OWNER, reason='forbidden')
        if not PlatformReviewReport.objects.filter(idea=idea).exists():
            raise IdeaError(NO_REPORT, reason='forbidden')
        # The go-ahead is a decision about the *proposal*: the owner must have been sent one
        # and can only be answering a proposal an admin released to them.
        if not IdeaProposal.objects.filter(idea=idea, status='released').exists():
            raise IdeaError(NO_PROPOSAL, reason='forbidden')
        # An owner who answered the proposal "do not go ahead" has decided; it stands.
        from reviews.models import ProposalAnswer

        if ProposalAnswer.objects.filter(
            idea=idea, decision=ProposalAnswer.Decision.DECLINE
        ).exists():
            raise IdeaError('You decided not to go ahead with this proposal.', reason='forbidden')

        # Resolved *before* the transition, so the notification names the idea
        # by the title it had when it was approved.
        idea_pk = idea.pk
        title = idea.title

        idea = lifecycle.apply_owner_go_ahead(user, idea)

        # The delivery team sees the idea at once: its opportunity opens in the same transaction,
        # so an idea can never be ready for implementation without one.
        from automation import services as automation_services
        from automation.authorization import AutomationError

        try:
            automation_services.open_from_go_ahead(idea)
        except AutomationError as exc:
            raise IdeaError(exc.message, reason='forbidden') from None

    logger.info('Idea owner gave the go-ahead (idea=%s, owner=%s).', idea_pk, user.pk)

    transaction.on_commit(lambda: _notify_ready(idea_pk, title))
    return idea


def _locked_idea(user, idea_id: object) -> Idea | None:
    from ideas import selectors

    return selectors.get_idea_for_update(user, idea_id)


def _notify_ready(idea_id: int, title: str) -> None:
    """
    Tell the owner their idea is ready for implementation.

    After the commit, and never raising - the readiness is a fact whether or not
    anybody was told. The wording is deliberately about what the idea is *not*:
    a developer has not been selected, no project exists and no work has begun,
    because "ready for implementation" read without that caveat is the sentence
    that makes an owner think a developer is already on it.
    """
    from notifications import services as notification_services

    idea = Idea.objects.filter(pk=idea_id).select_related('author').first()
    if idea is None:
        return

    notification_services.deliver(
        recipients=idea.author,
        kind='idea.owner_go_ahead',
        title=f'"{title}" is ready for implementation',
        body=(
            'You gave the go-ahead. The idea now moves to the developer track. '
            'No developer has been selected and no work has started yet.'
        ),
        idea=idea,
    )


# Imported lazily by `lifecycle` to register the post-commit hook, which would
# otherwise be a cycle: lifecycle owns the status change, this module owns what it
# means.
def _notify_owner(idea_id: int) -> None:
    """The hook `lifecycle._transition_idea` registers for the go-ahead."""
    _notify_ready(idea_id, _title_for(idea_id))


def _title_for(idea_id: int) -> str:
    row = Idea.objects.filter(pk=idea_id).values_list('title', flat=True).first()
    return row or ''


def stamped_at(idea: Idea):
    """When the owner gave the go-ahead, or `None`. Read by the report page."""
    return idea.owner_go_ahead_at or None


def is_ready(idea: Idea) -> bool:
    return idea is not None and idea.status == Idea.Status.READY_FOR_IMPLEMENTATION


def _now():  # pragma: no cover - indirection kept for the test clock
    return timezone.now()
