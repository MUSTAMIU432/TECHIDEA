"""
Who may review an idea, and which review.

Two questions that used to be one:

    reviews.eligibility        may this user act as a reviewer on this idea?
    reviews.models.Review.scope is this the organization's review or the
                               platform's?

The split matters because the two tracks have nothing in common except the shape
of a review round. An **organization** reviewer is an active member of the idea's
own organization holding `idea.review` there, confirming what that organization
wants to submit. A **platform** reviewer is an independent account holding the
platform-scoped `administration.review_platform_submissions` permission, and
nothing else - no organization membership, no organization role, no team role.

That asymmetry is the whole of "platform reviewers are independent" and "an
organization reviewer cannot platform-approve". It is structural rather than
careful: the platform answer is a Django permission on a platform-scoped model,
and no organization role can produce it, so there is no code path in which
holding `idea.review` anywhere also confers platform review.

Both tracks compose two rules that already exist and that must agree about what
a reviewer can reach:

- **Can they read it?** `ideas.selectors.can_view_idea`, the visibility rule
  every Ideas read uses - which now includes the narrow platform-reviewer
  branch that lets a reviewer outside the idea's organization read a submission
  that was actually sent to the platform. A reviewer is never eligible for an
  idea they cannot see.
- **Are they the right reviewer for it?** `ideas.lifecycle.is_organization_reviewer`
  or `ideas.lifecycle.is_platform_reviewer`. Delegating rather than re-deriving
  is deliberate: the lifecycle decides who may move an idea through review, and
  if this module kept its own copy of that rule the two could drift, and Reviews
  would offer a review the lifecycle then refuses to record.

Neither predicate forgets the author. Both delegate to the lifecycle predicates,
which refuse the author structurally, so "authors cannot review their own ideas"
is not a check somebody could forget to add to a new code path.

What is not here: claiming a review, completing one, taking one over, assigning
one. Those are operations (`reviews.services` for the platform track,
`reviews.organization_review` for the organization's), and they call into this
module rather than living in it. `review_is_stalled` answers only whether a
take-over would be permitted, for the capability flag; the operation asks again
under its lock.
"""

from ideas import lifecycle, selectors
from ideas.models import Idea
from identity.models import User
from organizations import authorization
from reviews.models import Review


def is_organization_reviewer_in(user: User | None, organization_id: object) -> bool:
    """
    Whether `user` reviews for `organization_id` at all: an active user with an
    active membership there that holds `idea.review`.

    The organization-level half of `can_organization_review`, for the questions
    that are about an organization rather than one idea - whether to show
    somebody the organization review queue, and whether to fill it. Per-idea
    rules (authorship, visibility, submission context) are not here, because
    there is no idea: the queue applies them as filters, and the per-idea
    predicate applies them to a single idea.

    `False` for a malformed or unknown organization id, exactly as for one the
    user is not in, so the answer cannot be used to probe for organizations.
    """
    membership = authorization.get_membership(user, organization_id)
    return authorization.membership_has_permission(membership, authorization.IDEA_REVIEW)


def is_platform_reviewer(user: User | None) -> bool:
    """
    Whether `user` reviews for the platform.

    Organization-free and idea-free by construction: this is the permission
    check and nothing else. A platform admin who also owns an organization is
    still only a platform reviewer because of the permission, and an organization
    Reviewer is never one.
    """
    return lifecycle.is_platform_reviewer(user)


# Retained for the callers written before the two reviewer kinds were split:
# this was always the organization-level question ("do they review for this
# organization?"), so it keeps the organization meaning. Anything that means
# "may they review *this idea*" should call `can_organization_review` or
# `can_review` explicitly, because which one it is now matters.
is_reviewer_in = is_organization_reviewer_in


def can_review(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` is a **platform** reviewer of `idea`, whatever state it is in.

    Deliberately independent of status: it also decides who may read a platform
    submission's review history, which exists in every state after the platform
    saw it. The status an *operation* needs is that operation's check; see
    `can_start_review`.
    """
    if idea is None:
        return False
    return selectors.can_view_idea(user, idea) and lifecycle.is_platform_reviewer(user, idea)


def can_organization_review(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` is an **organization** reviewer of `idea`.

    Only ever true for an organization-context idea the caller's own
    organization owns. An individual or team idea has no organization to confirm
    it, so this is `False` for one even for a user who holds `idea.review` in
    every tenant on the platform - which is what makes "only organization-context
    ideas require organization review" a rule rather than a UI condition.
    """
    if idea is None:
        return False
    return selectors.can_view_idea(user, idea) and lifecycle.is_organization_reviewer(user, idea)


def review_is_stalled(idea: Idea, scope: str = 'platform') -> bool:
    """
    Whether `idea` has an open review of `scope` whose reviewer can no longer
    review it: they lost the permission, were deactivated, can no longer read
    the idea, or - for the organization track - left the organization.

    Nobody can decide such a review - `complete_review` refuses its own reviewer
    too - so it is the one open review another eligible reviewer may take over.
    An open review held by an eligible reviewer is never stalled.

    Scoped, because an idea can legitimately have an organization review open
    while a platform review is being routed, and "is this stalled" asked without a
    scope would answer for the wrong one.
    """
    open_review = (
        Review.objects.select_related('reviewer')
        .filter(idea=idea, scope=scope, completed_at__isnull=True)
        .first()
    )
    if open_review is None:
        return False

    eligible = can_review if scope == 'platform' else can_organization_review
    return not eligible(open_review.reviewer, idea)


def can_start_review(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` is currently eligible to start a **platform** review of
    `idea`: eligible to review it, and either the idea is waiting in `SUBMITTED`
    or it is `UNDER_REVIEW` with a stalled review they may take over.

    A capability predicate only, reported to clients as
    `IdeaType.viewer_can_start_review`. `start_review` locks the idea and asks
    every part of this again, because this answer can be stale by the time a
    request arrives. A `SUBMITTED` idea needs no in-progress check: a review is
    only ever open while its idea is `UNDER_REVIEW`.
    """
    if idea is None or idea.status not in (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW):
        return False
    if not can_review(user, idea):
        return False
    if idea.status == Idea.Status.SUBMITTED:
        return True
    return idea.status == Idea.Status.UNDER_REVIEW and review_is_stalled(idea, 'platform')


def can_start_organization_review(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` may open the organization review of `idea` right now.

    `SUBMITTED_TO_ORGANIZATION` opens a round; `ORGANIZATION_CHANGES_REQUESTED`
    is the author's move to resubmit, so a reviewer starts nothing there - the
    resubmission is what puts it back in front of them.
    """
    if idea is None or idea.status != Idea.Status.SUBMITTED_TO_ORGANIZATION:
        return False
    return can_organization_review(user, idea)


def can_complete_organization_review(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` may decide the organization review of `idea` - the round is
    open, and they are its reviewer.

    The author check is already inside `can_organization_review`, so this cannot
    be satisfied by an author who also holds `idea.review`. Reported to clients so
    the review workspace can offer the decision buttons to exactly the right
    person.
    """
    if idea is None or idea.status != Idea.Status.SUBMITTED_TO_ORGANIZATION:
        return False
    return can_organization_review(user, idea)


def queue_stages() -> dict[str, tuple[str, ...]]:
    """
    The statuses each review queue is built from. Reported to the client so the
    queue tabs and the server's queries cannot name different states.
    """
    return {
        'organization': (Idea.Status.SUBMITTED_TO_ORGANIZATION,),
        'platform': (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW),
    }


# Imported at the bottom to keep the module's import graph readable: `models`
# imports `ideas.models`, and the predicates above only need it for the type
# hints and the queue stages, all of which run at call time.
