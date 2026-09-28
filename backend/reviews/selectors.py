"""
Review reads (S3-003): the review queue, an idea's review history, and the
viewer's own in-progress review.

Every read here starts from an *authorized idea*: the queue is built on
`ideas.selectors.list_organization_ideas` (membership plus the visibility
filter), and history and the active review both resolve the idea through
`ideas.selectors.get_idea` before any review row is touched. There is
deliberately no way to fetch a review by its own id: a review is only ever
reached through an idea the caller may read, so a guessed id is not an
entry point (`docs/reviews-domain.md` §12).

Who is a reviewer is `reviews.eligibility`'s answer, which in turn is the
lifecycle's REVIEWER rule over `organizations.authorization`. Nothing here
checks a role or a membership by hand.

Nothing here writes. Listing the queue never starts a review, creates a review
row or moves a status; the start-review operation is S3-004.
"""

from dataclasses import dataclass, field

from ideas import selectors as idea_selectors
from ideas.models import Idea
from ideas.pagination import Page, empty_page, paginate
from identity.models import User
from reviews import eligibility
from reviews.models import Review


def review_queue(
    user: User | None,
    organization_id: object,
    *,
    offset: object = 0,
    limit: object = None,
) -> Page[Idea]:
    """
    One page of the ideas waiting for review in `organization_id`, oldest
    submission first.

    Empty - not an error, and not distinguishable from an empty queue - for
    anybody who is not a reviewer in that organization: an anonymous caller,
    a non-member, a member without `idea.review`, an inactive membership, or a
    reviewer of some *other* organization. So `organizationId` cannot be used
    to probe which organizations exist or who reviews them.

    What is in the queue, and why:

    - **that organization's ideas only**, read through
      `list_organization_ideas`, which is membership-gated and
      visibility-filtered. Another tenant's `PUBLIC` idea is readable
      platform-wide but is never in this organization's queue;
    - **`SUBMITTED` only**. An `UNDER_REVIEW` idea has already been taken up
      by a reviewer, so it is not waiting for anybody; drafts and decided
      ideas are not reviewable at all;
    - **never the caller's own ideas**, which they could not review anyway;
    - **readable ones only**, so a submitted `PRIVATE` idea is not here - the
      reviewer could not open it (`docs/reviews-domain.md` D-6).

    Oldest submission first, because a queue is served in order of arrival;
    `pk` breaks ties so every page is deterministic and `offset` pages
    correctly.

    The caller is eligible to start a review of every idea on the page, once
    the start-review operation exists (S3-004), so each is marked
    `viewer_can_start_review = True` - the answer
    `eligibility.can_start_review` would give, established for the whole page
    by the filters above instead of re-derived per idea with its own queries.
    """
    if not eligibility.is_reviewer_in(user, organization_id):
        return empty_page(offset, limit)

    queryset = (
        idea_selectors.list_organization_ideas(user, organization_id)
        .filter(status=Idea.Status.SUBMITTED)
        .exclude(author=user)
        .order_by('submitted_at', 'pk')
    )

    page = paginate(
        idea_selectors.annotate_vote_state(queryset, user),
        offset=offset,
        limit=limit,
    )
    for idea in page.items:
        idea.viewer_can_start_review = True
    return page


@dataclass(frozen=True)
class ReviewHistory:
    """
    What `user` may see of one idea's reviews, and in which capacity.

    `viewer_is_reviewer` travels with the rows because it decides what the
    adapter may show of them (the submission snapshot is for reviewers only),
    and asking it a second time in the resolver would be a second place to
    decide it.
    """

    reviews: list[Review] = field(default_factory=list)
    viewer_is_reviewer: bool = False


def list_idea_reviews(user: User | None, idea_id: object) -> ReviewHistory:
    """
    The reviews of one idea `user` may see, in round order.

    - A **reviewer** of the idea's organization sees every review, including
      one still in progress: they are the people who act on it.
    - The idea's **author** sees completed reviews only. Those carry the
      decision and the feedback addressed to them; a review still being
      written is not yet anything the reviewer has said.
    - **Anybody else** sees nothing, including every platform-wide reader of
      a `PUBLIC` idea. Review history is not public because the idea is.

    An idea the caller cannot read answers an empty history, which is also the
    answer for an idea that does not exist and for one with no reviews, so the
    id cannot be used to learn anything.
    """
    idea = idea_selectors.get_idea(user, idea_id)
    if idea is None:
        return ReviewHistory()

    reviews = Review.objects.filter(idea=idea).prefetch_related('assessments').order_by('round')

    if eligibility.can_review(user, idea):
        return ReviewHistory(reviews=list(reviews), viewer_is_reviewer=True)
    if idea.author_id == user.pk:
        return ReviewHistory(reviews=list(reviews.filter(completed_at__isnull=False)))
    return ReviewHistory()


def active_review_id_for(user: User | None, idea: Idea | None) -> int | None:
    """
    The id of the review of `idea` that `user` has in progress, or `None`.

    Only ever the caller's *own* open review, and only while they are still
    eligible: a reviewer who has left the organization no longer has an
    active review as far as this answer is concerned, and nobody can learn
    about another reviewer's claim through it.

    A review is only open while its idea is `UNDER_REVIEW` (it is opened by
    the move into that state and completed by the move out of it, S3-004), so
    every other status answers `None` without a query. Until S3-004 ships
    there is no code path that opens a review, and this is always `None`.
    """
    if idea is None or idea.status != Idea.Status.UNDER_REVIEW:
        return None
    if not eligibility.can_review(user, idea):
        return None
    return (
        Review.objects.filter(idea=idea, reviewer=user, completed_at__isnull=True)
        .values_list('pk', flat=True)
        .first()
    )
