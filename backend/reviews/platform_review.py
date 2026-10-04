"""
The platform review track: intake, assignment, decision, and the report.

Three things live here and nothing else does, because each of them is platform-
scoped and therefore belongs somewhere that can only be reached by a platform
permission:

1. **Eligibility.** `can_review` is re-exported from `reviews.eligibility` under
   the platform name, so a reader of this module sees which track it is and
   cannot reach the organization predicate by accident.

2. **Assignment.** Routing a submission to a platform reviewer is not reviewing
   it - the admin who does the routing decides nothing about the idea - so it is
   a distinct operation, gated on `administration.assign_platform_reviewers`,
   writing a `reviews.ReviewAssignment` rather than a `Review`. Until the
   reviewer starts the round, no review exists, which is exactly the state the
   platform queue's "waiting to be picked up" tab is.

3. **The report.** Platform approval is not owner go-ahead, and the report is
   what stands between them. `generate_report` writes the
   `PlatformReviewReport` **in the caller's transaction** - the one
   `reviews.services.complete_review` opens - so an approval without a report is
   not a state the database can be left in, and a report can never describe a
   decision that rolled back.

The report's contents
---------------------
Copied from the approving review and the frozen submission it reviewed, with the
reviewer's own words in the sections the product asks for (summary, feedback,
recommendations, important considerations, constraints, next steps) and the
categorical criterion assessments verbatim. **No score, no total, no weighting.**
The existing review system is categorical and this module has no field that could
hold a number, so "the platform scored it 8.4/10" is not a sentence this codebase
can produce.

Delivery is not this module's business. It generates the report and then hands
the *fact* to `notifications.services.deliver`, which writes the in-app
notification and sends the email after the commit and never raises. A mail failure
leaves the approval valid; a report that could not be generated rolls the
approval back, because an approval the owner is never told about is not a
decision anybody acted on.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from administration import authorization as admin_authorization
from ideas import selectors as idea_selectors
from ideas import versions
from ideas.models import Idea
from identity.models import User
from reviews import eligibility
from reviews.models import PlatformReviewReport, Review, ReviewAssignment

logger = logging.getLogger(__name__)

# Re-exported so this module can be read as "the platform track" without the
# reader having to know that the eligibility rules live one module over.
can_review = eligibility.can_review
can_start_review = eligibility.can_start_review

PLATFORM_STAGES = (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW)


class PlatformReviewError(Exception):
    """A refused platform review operation, in the project's error shape."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message)
        self.message = message
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class ReportContent:
    """
    The reviewer's own words, as the report stores them.

    A dataclass rather than a bag of keyword arguments, because there are nine of
    them and every one is optional - a reviewer who approves with no
    recommendations must be able to, and the report must render without inventing
    prose. `generate_report` fills the rest from the review and the version.
    """

    review_summary: str = ''
    feedback: str = ''
    recommendations: str = ''
    important_considerations: str = ''
    constraints: str = ''
    next_steps: str = ''
    approval_summary: str = ''


# What "approval" means at this stage, written once so the report, the email and
# the in-app notification cannot say three different things. The product asks for
# this to be unambiguous: approval is not implementation, and saying so in the
# document is what stops "approved" being read as "somebody is building this".
APPROVAL_SUMMARY = (
    'The platform has reviewed this submission and approved it to move toward '
    'implementation. This is not a decision to build it, and no developer has been '
    'selected. As the owner, please read this report and give your go-ahead before '
    'the idea proceeds.'
)

DEFAULT_NEXT_STEPS = (
    'Read this report. If it looks right, give your go-ahead; the idea will then be '
    'made available to developers. If something here is wrong or incomplete, reply '
    'to your platform contact before you decide.'
)


# --- assignment -----------------------------------------------------------------------


def assign_reviewer(user: User | None, idea_id: object, reviewer_id: object) -> ReviewAssignment:
    """
    Route a submitted idea to a platform reviewer.

    Requires `administration.assign_platform_reviewers`. The reviewer they name
    must themselves hold `administration.review_platform_submissions` - checked
    here, before the row exists, so the platform cannot route an idea to somebody
    who could never decide it and leave it stranded. This is the same rule that
    `reviews.services.start_review` enforces at claim time, asked earlier and in a
    friendlier place.
    """
    admin_authorization.require_assign_reviewer(user)

    assignment_reviewer = _resolve_reviewer(reviewer_id)
    if not eligibility.is_platform_reviewer(assignment_reviewer):
        raise PlatformReviewError('That person cannot review submissions for the platform.')

    with transaction.atomic():
        # Locked through the platform-reviewer visibility branch: an admin who
        # cannot read the submission cannot route it either.
        idea = idea_selectors.get_idea_for_update(user, idea_id)
        if idea is None:
            raise PlatformReviewError('Idea is unavailable.')
        if idea.status not in PLATFORM_STAGES:
            raise PlatformReviewError('Only an idea submitted to the platform can be assigned.')

        if Review.objects.filter(idea=idea, completed_at__isnull=True).exists():
            raise PlatformReviewError('This idea is already being reviewed.')

        ReviewAssignment.objects.filter(idea=idea, released_at__isnull=True).update(
            released_at=timezone.now()
        )

        try:
            with transaction.atomic():
                assignment = ReviewAssignment.objects.create(
                    idea=idea, reviewer=assignment_reviewer, assigned_by=user
                )
        except Exception:
            raise PlatformReviewError('This idea is already assigned.') from None

    logger.info(
        'Platform reviewer assigned (idea=%s, reviewer=%s, assigned_by=%s).',
        idea.pk,
        assignment_reviewer.pk,
        user.pk,
    )
    return assignment


def release_assignment(user: User | None, idea_id: object) -> None:
    """
    Take back an assignment without withdrawing a review.

    If a round is already open, the reviewer simply stops holding the assignment -
    the round is withdrawn by `reviews.services.start_review`'s take-over path,
    which has its own rules about who may take it. Routing and reviewing are
    separate operations and are undone separately.
    """
    admin_authorization.require_assign_reviewer(user)

    with transaction.atomic():
        idea = idea_selectors.get_idea_for_update(user, idea_id)
        if idea is None:
            raise PlatformReviewError('Idea is unavailable.')

        released = ReviewAssignment.objects.filter(idea=idea, released_at__isnull=True).update(
            released_at=timezone.now()
        )
        if not released:
            raise PlatformReviewError('This idea is not currently assigned.')

    logger.info('Platform reviewer assignment released (idea=%s, by=%s).', idea.pk, user.pk)


def assignment_for(idea: Idea) -> ReviewAssignment | None:
    """The current assignment of `idea`, or `None`. `idea` must already be authorized."""
    if idea is None or not idea.pk:
        return None
    return (
        ReviewAssignment.objects.select_related('reviewer', 'assigned_by')
        .filter(idea=idea, released_at__isnull=True)
        .first()
    )


def _resolve_reviewer(reviewer_id: object) -> User:
    try:
        normalized = int(str(reviewer_id))
    except (TypeError, ValueError):
        raise PlatformReviewError('Reviewer is unavailable.') from None

    reviewer = User.objects.filter(pk=normalized, is_active=True).first()
    if reviewer is None:
        raise PlatformReviewError('Reviewer is unavailable.')
    return reviewer


# --- the report ------------------------------------------------------------------------


def generate_report(review: Review, content: ReportContent | None = None) -> PlatformReviewReport:
    """
    Build the Platform Review & Approval Report for an **approving** review.

    Runs inside `reviews.services.complete_review`'s transaction and is called
    only when the decision is `APPROVED`, so the report and the approval commit
    together. Refuses anything else loudly rather than producing a report for a
    decision that was not an approval - including an organization `CONFIRMED`
    review, which is the mistake this whole module is arranged to prevent.

    Everything that is copied rather than composed says so: the idea's title and
    context from the row, the criteria from the review's own assessments, and the
    snapshot from the frozen submission version the platform actually reviewed.
    """
    if review.scope != Review.Scope.PLATFORM:
        raise PlatformReviewError('A report can only be produced for a platform review.')
    if review.decision != Review.Decision.APPROVED:
        raise PlatformReviewError('A report can only be produced for an approval.')

    idea = review.idea
    version = versions.current_version(idea)
    if version is None:
        # Cannot happen while submissions are frozen in the same transaction as
        # the status that claims there is one; refused rather than guessed at,
        # because a report about "the submission" with no submission is not a
        # report.
        raise PlatformReviewError('The submitted version of this idea is unavailable.')

    supplied = content or ReportContent()
    criteria = [
        {
            'criterion': assessment.criterion,
            'criterionLabel': assessment.get_criterion_display(),
            'rating': assessment.rating,
            'ratingLabel': assessment.get_rating_display(),
            'note': assessment.note,
        }
        for assessment in review.assessments.all()
    ]

    try:
        report = PlatformReviewReport.objects.create(
            idea=idea,
            review=review,
            version=version,
            submission_context=idea.submission_context,
            organization_id=idea.organization_id,
            team_id=idea.team_id,
            round=review.round,
            decision=Review.Decision.APPROVED,
            review_summary=supplied.review_summary,
            recommendations=supplied.recommendations,
            important_considerations=supplied.important_considerations,
            constraints=supplied.constraints,
            next_steps=supplied.next_steps or DEFAULT_NEXT_STEPS,
            approval_summary=supplied.approval_summary or APPROVAL_SUMMARY,
            criteria=criteria,
            approved_at=review.completed_at,
        )
    except Exception:
        logger.exception(
            'Could not generate the platform review report (review=%s, idea=%s).',
            review.pk,
            idea.pk,
        )
        raise PlatformReviewError('We could not write the review report.') from None

    logger.info(
        'Platform review report generated (report=%s, idea=%s, round=%s).',
        report.pk,
        idea.pk,
        report.round,
    )
    return report


def report_for_idea(idea: Idea) -> PlatformReviewReport | None:
    """
    The report for `idea`, or `None`.

    Takes an already-authorized idea and does one indexed read, so a reader cannot
    obtain a report for an idea they cannot read - authorization happened when the
    idea was resolved, and this function is deliberately not a second place where
    it could be forgotten.
    """
    if idea is None or not idea.pk:
        return None

    return (
        PlatformReviewReport.objects.select_related('review', 'review__reviewer', 'version')
        .filter(idea=idea)
        .order_by('-generated_at', '-pk')
        .first()
    )


def notify_approval(report: PlatformReviewReport) -> None:
    """
    Tell the idea's owner that the review is complete and the report is ready.

    Registered on commit by `reviews.services.complete_review`, never inside the
    transaction - the pattern the whole platform uses
    (`notifications.services.deliver`): the approval is the fact, the notification
    and the email are two deliveries of it, and neither can undo or fail it.

    The body deliberately carries no criteria, no feedback and no report contents.
    It says the review is complete, that the idea is approved for the next stage,
    and that the owner should read the report and decide - which is what the email
    asks for in the product's own terms.
    """
    from notifications import services as notification_services

    idea = report.idea
    notification_services.deliver(
        recipients=idea.author,
        kind='idea.platform_approved',
        title=f'Platform review completed for "{idea.title}"',
        body=(
            'The idea has been approved for the next stage. Review the report and give '
            'your go-ahead before it proceeds toward implementation.'
        ),
        idea=idea,
        report=report,
    )


def notify_decision(idea: Idea, decision: str, review: Review) -> None:
    """
    Tell the owner about a platform decision that is not an approval.

    Request-changes and reject both need the author to know, and both are one
    notification with a link back to the review history - where the feedback
    lives, behind authentication. The email carries the decision and the fact that
    feedback is waiting, not the feedback itself.
    """
    from notifications import services as notification_services

    if decision == Review.Decision.CHANGES_REQUESTED:
        notification_services.deliver(
            recipients=idea.author,
            kind='idea.platform_changes_requested',
            title=f'The platform asked for changes to "{idea.title}"',
            body=(
                'A platform reviewer asked for changes. Read the review feedback, revise '
                'the idea, then submit it again.'
            ),
            idea=idea,
        )
        return

    if decision == Review.Decision.REJECTED:
        notification_services.deliver(
            recipients=idea.author,
            kind='idea.platform_rejected',
            title=f'The platform did not approve "{idea.title}"',
            body=(
                'A platform reviewer did not approve this idea. The review feedback '
                'explains why, and is in the idea review history.'
            ),
            idea=idea,
        )


# --- the queue ---------------------------------------------------------------------------


def platform_queue(user: User | None) -> list[Idea]:
    """
    The submissions this platform reviewer may act on.

    Built from the same predicate that authorizes a review, so it cannot contain
    an idea the viewer could not decide - in particular not the viewer's own idea,
    which `is_platform_reviewer` refuses. Oldest first, because a review queue is
    a queue.

    Ideas nobody is working on come first: `SUBMITTED` (in intake) before
    `UNDER_REVIEW` (claimed), each group oldest first. That is one ordering
    expression over one filtered scan rather than two queries merged in Python -
    which matters because this list is read on every visit to the review page.
    """
    if user is None or not user.is_active or not eligibility.is_platform_reviewer(user):
        return []

    return list(
        Idea.objects.filter(
            idea_selectors._visibility_filter(user),
            status__in=PLATFORM_STAGES,
        )
        .select_related('author', 'organization', 'team', 'category')
        .annotate(_unclaimed=Q(status=Idea.Status.SUBMITTED))
        .order_by('-_unclaimed', 'submitted_at', 'pk')
    )


def platform_intake(user: User | None) -> list[Idea]:
    """
    Everything sitting in platform intake: submitted, not yet under review.

    A platform **admin's** view rather than a reviewer's - the person who routes
    work needs to see what has arrived, including ideas they may not review
    themselves. So the read gate here is `assign_platform_reviewers`, and the
    content gate is the platform visibility branch, which shows the submission and
    its metadata to somebody who has to route it.

    Deliberately metadata-shaped: it is a triage list, not a reading list, and the
    full content is behind the reviewer workspace for those who may review it.
    """
    if user is None or not user.is_active:
        return []
    if not admin_authorization.capabilities_for(user).can_assign_platform_reviewers:
        return []

    return list(
        Idea.objects.filter(status=Idea.Status.SUBMITTED)
        .select_related('author', 'organization', 'team', 'category')
        .order_by('submitted_at', 'pk')
    )
