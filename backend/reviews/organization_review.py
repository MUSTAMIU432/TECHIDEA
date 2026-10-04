"""
Organization review: "does this idea accurately represent what we want to submit?"

A separate module from `reviews.services` for a reason that is not tidiness. The
two tracks share the *shape* of a review round and nothing else:

    organization review                  platform review
    ---------------------------          ---------------------------
    who: an org member with              who: an independent account
         `idea.review` in the idea's          with
         own organization                       `administration.review_
         platform_submissions`
    answers: "is this what our            answers: "should the platform
         organization wants to put              accept this idea?"
         forward?"
    outcomes: confirm, or request        outcomes: approve, request changes,
         changes                               reject
    then: the OWNER submits to the       then: the platform generates a report
         platform                                and the owner gives a go-ahead

Two modules rather than one parameterised service, because every question a
review operation has to ask is different between them - who may start it, what
decisions exist, what the decision means, what happens afterwards - and a shared
implementation would need a `scope` argument threaded through all of it and a
branch at every step. The `Review` model is shared (one table, two kinds of row,
distinguished by `Review.scope`), because the row itself genuinely is the same
thing: a round, a reviewer, a snapshot, a decision.

What an organization review is **not**
-------------------------------------
It is not an approval, it is not a platform decision, and it is not a vote. The
decisions available here are `CHANGES_REQUESTED` and `CONFIRMED` - deliberately
not `APPROVED`, which `reviews.models.Review.clean()` refuses for this scope, so
"the organization approved it" is not a state any code path can produce.

The author is refused by `reviews.eligibility.can_organization_review`, which
delegates to `ideas.lifecycle.is_organization_reviewer`. It is not merely
"preferable" that an author does not appear in their own organization's review
queue: the queue is built from the same predicate, so their own idea is not in it.

Concurrency and refusal order follow `reviews.services` exactly - lock the idea,
then ask everything on the locked row - so the two tracks cannot deadlock each
other and neither can be raced. Refusals are reported as one message for
"unavailable" whether the idea does not exist, belongs to another organization,
or is simply not in the organization track yet, so the queue is not an existence
oracle.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from ideas import lifecycle
from ideas import selectors as idea_selectors
from ideas.models import Idea
from reviews import eligibility
from reviews.models import Review, ReviewCriterionAssessment

logger = logging.getLogger(__name__)

SCOPE = Review.Scope.ORGANIZATION

MAX_FEEDBACK_LENGTH = 5000
MAX_NOTE_LENGTH = 1000

# A changes request must explain itself; a confirmation need not. Same reasoning
# as `reviews.services.FEEDBACK_REQUIRED_DECISIONS`, and for the same reason: an
# author sent back needs to know what to change.
FEEDBACK_REQUIRED_DECISIONS = frozenset({Review.Decision.CHANGES_REQUESTED})

# The two decisions this track can record, and where each leaves the idea. Read
# from `reviews.models.SCOPE_DECISIONS` rather than restated, so the table that
# stops an organization review recording an approval is the same table the
# lifecycle consults when it moves the idea.
ORGANIZATION_DECISIONS: dict[str, str] = {
    Review.Decision.CHANGES_REQUESTED: Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
    Review.Decision.CONFIRMED: Idea.Status.ORGANIZATION_CONFIRMED,
}


class OrganizationReviewError(Exception):
    """A refused organization review operation, in the project's error shape."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message)
        self.message = message
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class CompleteOrganizationReviewInput:
    idea_id: object
    review_id: object
    decision: str
    feedback: str = ''
    assessments: tuple = ()


def _require_active_user(user) -> object:
    if user is None or not user.is_active:
        raise OrganizationReviewError(
            'You must be signed in to review ideas.', reason='unauthenticated'
        )
    return user


def _lock_organization_idea(user, idea_id: object) -> Idea:
    """
    The idea, locked, as an organization reviewer may see it.

    Read through the shared visibility-filtered selector rather than a query of
    this module's own, so an idea this caller may not read is refused exactly as
    one that does not exist.
    """
    idea = idea_selectors.get_idea_for_update(user, idea_id)
    if idea is None:
        raise OrganizationReviewError('Idea is unavailable.')
    return idea


def _require_reviewer(user, idea: Idea) -> None:
    """
    Refuse unless `user` may organization-review `idea`, asked again on the
    locked row.

    The state may have changed since the client was rendered, and eligibility
    depends on the idea's submission context as well as on the caller's
    membership - so both are re-asked here rather than trusted.
    """
    if not eligibility.can_organization_review(user, idea):
        raise OrganizationReviewError('You are not allowed to review this idea.')


def organization_snapshot(idea: Idea) -> dict:
    """
    What the organization reviewer is looking at, frozen at the moment they start.

    The **complete** idea, because the organization reviewer is confirming that
    this is what the organization wants to submit - which is a judgement about the
    whole submission, not about a summary of it. So this is the full problem
    story, not `ideas.lifecycle`'s leaner idea-level snapshot: a reviewer asked to
    confirm a submission has to be able to read the submission.
    """
    from ideas.versions import SNAPSHOT_FIELDS, build_snapshot

    snapshot = build_snapshot(idea)
    snapshot['snapshot_fields'] = list(SNAPSHOT_FIELDS)
    return snapshot


@transaction.atomic
def start_organization_review(user, idea_id: object) -> Review:
    """
    Open the organization review round for an idea the organization is reviewing.

    `SUBMITTED_TO_ORGANIZATION -> ORGANIZATION_CHANGES_REQUESTED | ORGANIZATION_CONFIRMED`
    is review-owned, so the status move is made by
    `ideas.lifecycle.apply_review_transition` inside this transaction, and the
    `Review` row commits with it.

    Two organization reviewers pressing Start on the same idea at once: the second
    waits for the first, re-reads an idea that is no longer
    `SUBMITTED_TO_ORGANIZATION`, and is refused. The one-open-review-per-scope
    constraint is the database backstop.
    """
    active_user = _require_active_user(user)

    with transaction.atomic():
        idea = _lock_organization_idea(active_user, idea_id)
        _require_reviewer(active_user, idea)

        if idea.status != Idea.Status.SUBMITTED_TO_ORGANIZATION:
            # Also the losing side of two concurrent starts: it read the status
            # after the winner committed.
            raise OrganizationReviewError('This idea is not waiting for organization review.')

        existing = (
            Review.objects.select_for_update(of=('self',))
            .filter(idea=idea, scope=SCOPE, completed_at__isnull=True)
            .first()
        )
        if existing is not None:
            raise OrganizationReviewError('This idea is already being reviewed.')

        try:
            with transaction.atomic():
                review = Review.objects.create(
                    idea=idea,
                    reviewer=active_user,
                    scope=SCOPE,
                    round=_next_round(idea),
                    submission_snapshot=organization_snapshot(idea),
                )
        except Exception:
            raise OrganizationReviewError('This idea is already being reviewed.') from None

    logger.info(
        'Organization review opened (idea=%s, review=%s, reviewer=%s).',
        idea.pk,
        review.pk,
        active_user.pk,
    )
    return review


def _next_round(idea: Idea) -> int:
    """
    Round n+1 for `idea`, counting this scope's reviews only.

    One sequence per track, so "platform review round 2" in the report means the
    platform's second look at the idea rather than the third time anybody looked
    at it. `Review.Meta`'s `(idea, scope, round)` uniqueness is the backstop.
    """
    from django.db.models import Max

    last = Review.objects.filter(idea=idea, scope=SCOPE).aggregate(last=Max('round'))['last'] or 0
    return last + 1


def _validate_completion(data: CompleteOrganizationReviewInput) -> tuple[str, str]:
    """Everything about the input that needs no database, checked up front."""
    decision = str(data.decision or '').strip().lower()
    if decision not in ORGANIZATION_DECISIONS:
        # The message names the two real choices rather than the enum, because
        # "organization confirmation" and "changes requested" are the two things
        # a person can be asked for and "confirmed"/"organization_confirmed" are
        # not.
        raise OrganizationReviewError(
            'Choose whether to confirm this idea or request changes.', field='decision'
        )

    feedback = (data.feedback or '').strip()
    if len(feedback) > MAX_FEEDBACK_LENGTH:
        raise OrganizationReviewError(
            f'Feedback must be at most {MAX_FEEDBACK_LENGTH} characters.', field='feedback'
        )
    if decision in FEEDBACK_REQUIRED_DECISIONS and not feedback:
        raise OrganizationReviewError('Explain what needs to change.', field='feedback')

    return decision, feedback


@transaction.atomic
def complete_organization_review(user, data: CompleteOrganizationReviewInput) -> Review:
    """
    Record the organization reviewer's decision and move the idea.

    One transaction: the decision, the feedback, the criterion assessments and
    the status change commit together or not at all. The status move is the
    lifecycle's, through `apply_review_transition`, which refuses this caller
    unless they are the *organization* reviewer for this pair - so an
    organization reviewer cannot reach a platform decision through this function
    even by naming one.

    Nothing is emailed here. The notification is a separate, post-commit concern
    (`notifications.services.deliver`), because an organization decision that
    rolls back must not tell the author it happened.
    """
    active_user = _require_active_user(user)
    decision, feedback = _validate_completion(data)

    try:
        review_pk = int(str(data.review_id))
    except (TypeError, ValueError):
        raise OrganizationReviewError('Review is unavailable.') from None

    with transaction.atomic():
        idea = _lock_organization_idea(active_user, data.idea_id)
        _require_reviewer(active_user, idea)

        review = (
            Review.objects.select_for_update().filter(pk=review_pk, idea=idea, scope=SCOPE).first()
        )
        if review is None or review.reviewer_id != active_user.pk:
            # Not a review of this idea in this track, or somebody else's: the
            # same answer, so a guessed id reveals nothing and nobody completes
            # another reviewer's review.
            raise OrganizationReviewError('Review is unavailable.')
        if review.completed_at is not None:
            raise OrganizationReviewError('This review has already been completed.')
        if idea.status != Idea.Status.SUBMITTED_TO_ORGANIZATION:
            raise OrganizationReviewError('This idea is not waiting for organization review.')

        for item in data.assessments or ():
            ReviewCriterionAssessment.objects.create(
                review=review,
                criterion=item.criterion,
                rating=item.rating,
                note=(item.note or '').strip()[:MAX_NOTE_LENGTH],
            )

        review.decision = decision
        review.feedback = feedback
        review.completed_at = timezone.now()
        review.save()

        lifecycle.apply_review_transition(active_user, idea, ORGANIZATION_DECISIONS[decision])

    _notify_author(idea.pk, decision)
    return Review.objects.prefetch_related('assessments').get(pk=review.pk)


def _notify_author(idea_id: int, decision: str) -> None:
    """
    Tell the author what the organization decided.

    After the transaction has committed - registered from inside it with
    `on_commit`, so a rollback notifies nobody - and never raising, because the
    decision is recorded whether or not anybody was told.
    """
    from notifications import services as notification_services

    idea = Idea.objects.filter(pk=idea_id).select_related('author', 'organization').first()
    if idea is None:
        return

    if decision == Review.Decision.CHANGES_REQUESTED:
        notification_services.deliver(
            recipients=idea.author,
            kind='idea.organization_changes_requested',
            title=f'"{idea.title}" needs changes before {idea.tenant_label} can submit it',
            body=(
                'Your organization asked for changes. Review the feedback, edit the idea, '
                'then resubmit it to your organization.'
            ),
            idea=idea,
        )
        return

    notification_services.deliver(
        recipients=idea.author,
        kind='idea.organization_confirmed',
        title=f'Your organization confirmed "{idea.title}"',
        body=(
            'Your organization confirmed this idea. Review it once more, then submit it '
            'to the platform when you are ready.'
        ),
        idea=idea,
    )


def organization_queue_for(user, organization_id: object | None = None) -> list[Idea]:
    """
    The ideas waiting for `user` to review for their organization, oldest first.

    Built from the same predicate that authorizes a review, so the queue cannot
    contain an idea its viewer could not act on. Two consequences worth stating,
    because the product asks for both:

    - **The viewer's own ideas are never in it.** `can_organization_review`
      refuses the author structurally, and a queue filtered only by "readable"
      would still show them their own submission - which is how a person ends up
      trying to confirm an idea they wrote, and being refused at the worst moment.
      The `exclude(author=user)` below makes that a property of the list rather
      than a refusal at the click.
    - **Only organization-context ideas are in it.** A team or individual idea has
      no organization to confirm it, so it is not in any organization's queue at
      all - not even one the author happens to belong to.

    Oldest-first rather than newest-first: a review queue is a queue, and the
    idea that has been waiting longest belongs at the top.
    """
    if user is None or not user.is_active:
        return []

    queryset = (
        Idea.objects.filter(
            idea_selectors._visibility_filter(user),
            status=Idea.Status.SUBMITTED_TO_ORGANIZATION,
            submission_context=Idea.SubmissionContext.ORGANIZATION,
        )
        .exclude(author=user)
        .select_related('author', 'organization', 'team', 'category')
    )

    if organization_id is not None:
        try:
            queryset = queryset.filter(organization_id=int(str(organization_id)))
        except (TypeError, ValueError):
            # An unusable id yields nothing, which is also the answer for an
            # organization that does not exist.
            return []

    return list(queryset.order_by('submitted_at', 'pk'))
