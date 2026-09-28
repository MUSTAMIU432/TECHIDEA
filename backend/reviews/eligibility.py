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

What is not here: claiming a review, completing one, taking one over, or
checking for a review already in progress. Those are operations (S3-004), and
they call into this module rather than living in it.
"""

from ideas import lifecycle, selectors
from ideas.models import Idea
from identity.models import User
from organizations import authorization


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


def can_start_review(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` is currently eligible to start a review of `idea`:
    eligible to review it, and the idea is waiting in `SUBMITTED`.

    A capability predicate only, reported to clients as
    `IdeaType.viewerCanStartReview`. Nothing in S3-003 starts a review; the
    start-review operation, when it becomes available (S3-004), locks the idea
    and asks this question again, because this answer can be stale by the time
    a request arrives. No in-progress check is needed here: a review is only
    ever open while its idea is `UNDER_REVIEW`, so a `SUBMITTED` idea has none.
    """
    return idea is not None and idea.status == Idea.Status.SUBMITTED and can_review(user, idea)
