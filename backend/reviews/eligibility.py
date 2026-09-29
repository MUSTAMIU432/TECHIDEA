"""
Who may review an idea (S3-002).

The Reviews domain's single answer to "may this user act as a reviewer on
this idea?". Every review read and write in later work items goes through it,
so the rule is written once.

It adds no authorization of its own. It composes the two rules that already
exist and that must agree about what a reviewer can reach:

- **Can they read it?** `ideas.selectors.can_view_idea`, the visibility rule
  every Ideas read uses. A reviewer is never eligible for an idea they cannot
  see, which is also why a submitted `PRIVATE` idea cannot be reviewed.
- **Are they a reviewer of it?** `ideas.lifecycle.is_reviewer`, the REVIEWER
  actor rule the lifecycle enforces: an active member of the idea's *own*
  organization, holding `idea.review` there, who is not its author. It goes
  through `organizations.authorization`, so a grant in organization A never
  makes anyone eligible in organization B, and an inactive membership counts
  as no membership.

Delegating to `is_reviewer` rather than re-deriving it is deliberate: the
lifecycle decides who may move an idea through review, and if this module kept
its own copy of that rule the two could drift, and Reviews would offer a
review the lifecycle then refuses to record, or the reverse.

What is not here: claiming a review, completing one or taking one over.
Those are operations (`reviews.services`), and they call into this module
rather than living in it. `review_is_stalled` answers only whether a take-over
would be permitted, for the capability flag; the operation asks again under
its lock.
"""

from ideas import lifecycle, selectors
from ideas.models import Idea
from identity.models import User
from organizations import authorization
from reviews.models import Review


def is_reviewer_in(user: User | None, organization_id: object) -> bool:
    """
    Whether `user` reviews for `organization_id` at all: an active user with
    an active membership there that holds `idea.review`.

    The organization-level half of `can_review`, for the questions that are
    about an organization rather than one idea - whether to show somebody the
    review queue, and whether to fill it. Per-idea rules (authorship,
    visibility) are not here, because there is no idea: the queue applies them
    as filters, and `can_review` applies them to a single idea.

    `False` for a malformed or unknown organization id, exactly as for one the
    user is not in, so the answer cannot be used to probe for organizations.
    """
    membership = authorization.get_membership(user, organization_id)
    return authorization.membership_has_permission(membership, authorization.IDEA_REVIEW)


def can_review(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` is a reviewer of `idea`, whatever state the idea is in.

    Deliberately independent of status: it also decides who may read an idea's
    review history (S3-003), which exists in every state after the first
    review. The status an *operation* needs is that operation's check; see
    `can_start_review`.
    """
    if idea is None:
        return False
    return selectors.can_view_idea(user, idea) and lifecycle.is_reviewer(user, idea)


def review_is_stalled(idea: Idea) -> bool:
    """
    Whether `idea` has an open review whose reviewer can no longer review it
    (`docs/reviews-domain.md` §5.3, D-2): they left the organization, lost
    `idea.review`, were deactivated, or can no longer read the idea.

    Nobody can decide such a review - `complete_review` refuses its own
    reviewer too - so it is the one open review another reviewer may take
    over. An open review held by an eligible reviewer is never stalled.
    """
    open_review = (
        Review.objects.select_related('reviewer')
        .filter(idea=idea, completed_at__isnull=True)
        .first()
    )
    return open_review is not None and not can_review(open_review.reviewer, idea)


def can_start_review(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` is currently eligible to start a review of `idea`:
    eligible to review it, and either the idea is waiting in `SUBMITTED` or it
    is `UNDER_REVIEW` with a stalled review they may take over (S3-008).

    A capability predicate only, reported to clients as
    `IdeaType.viewerCanStartReview`. `start_review` locks the idea and asks
    every part of this again, because this answer can be stale by the time a
    request arrives. A `SUBMITTED` idea needs no in-progress check: a review
    is only ever open while its idea is `UNDER_REVIEW`.
    """
    # Status first: every other state answers without a query.
    if idea is None or idea.status not in (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW):
        return False
    if not can_review(user, idea):
        return False
    if idea.status == Idea.Status.SUBMITTED:
        return True
    return idea.status == Idea.Status.UNDER_REVIEW and review_is_stalled(idea)
