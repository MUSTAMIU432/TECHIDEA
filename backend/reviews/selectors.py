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
    One page of the ideas waiting for **organization** review in
    `organization_id`, oldest submission first.

    **This is the organization's queue, not the platform's.** The platform has its
    own, and it is not tenant-scoped at all (`reviews.platform_review.platform_queue`
    - the platform reviews submissions from every organization, so a queue keyed by
    organization would be the wrong shape). This one is the organization's
    reviewers' work: ideas its members have put forward for the organization to
    confirm. Keeping the name and the shape is deliberate - it has always meant
    "this organization's queue" - and the *status* it filters on is what changed.

    Empty - not an error, and not distinguishable from an empty queue - for anybody
    who is not a reviewer in that organization: an anonymous caller, a non-member,
    a member without `idea.review`, an inactive membership, or a reviewer of some
    *other* organization. So `organizationId` cannot be used to probe which
    organizations exist or who reviews them.

    What is in the queue, and why:

    - **that organization's ideas only**, read through
      `list_organization_ideas`, which is membership-gated and
      visibility-filtered. Another tenant's `PUBLIC` idea is readable
      platform-wide but is never in this organization's queue;
    - **organization-context ideas only**. A team or individual idea has no
      organization to confirm it, so it is in no organization's queue - and the
      `submission_context` filter is what says so, rather than the idea simply
      failing a status check;
    - **`SUBMITTED_TO_ORGANIZATION` only**. An idea the organization has already
      confirmed has left the tenant; a draft has not been put forward; and a
      decided one is history;
    - **never the caller's own ideas**, which they could not review anyway - a
      queue that showed somebody their own submission would invite them to try,
      at the worst possible moment;
    - **readable ones only**, so a `PRIVATE` idea is not here - the reviewer could
      not open it.

    Oldest submission first, because a queue is served in order of arrival; `pk`
    breaks ties so every page is deterministic and `offset` pages correctly.

    Every idea on the page is marked `viewer_can_start_organization_review = True`,
    established by the filters above rather than re-derived per idea with its own
    queries - so a page of fifty costs the same two queries a page of one does.
    """
    if not eligibility.is_organization_reviewer_in(user, organization_id):
        return empty_page(offset, limit)

    queryset = (
        idea_selectors.list_organization_ideas(user, organization_id)
        .filter(
            status=Idea.Status.SUBMITTED_TO_ORGANIZATION,
            submission_context=Idea.SubmissionContext.ORGANIZATION,
        )
        .exclude(author=user)
        .order_by('submitted_at', 'pk')
    )

    page = paginate(idea_selectors.annotate_vote_state(queryset, user), offset=offset, limit=limit)
    for idea in page.items:
        idea.viewer_can_start_organization_review = True
    return page


def platform_queue(
    user: User | None,
    *,
    offset: object = 0,
    limit: object = None,
) -> Page[Idea]:
    """
    One page of the submissions waiting for **platform** review, oldest first.

    Not keyed by organization - platform review is cross-tenant - and paged through
    the same `ideas.pagination.Page` so the client handles one page shape.
    """
    from reviews import platform_review

    if not eligibility.is_platform_reviewer(user):
        return empty_page(offset, limit)

    queryset = (
        idea_selectors._base_queryset()
        .filter(
            idea_selectors.platform_reviewer_filter(user),
            status__in=platform_review.PLATFORM_STAGES,
        )
        .exclude(author=user)
        .order_by('submitted_at', 'pk')
    )
    return paginate(idea_selectors.annotate_vote_state(queryset, user), offset=offset, limit=limit)


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
        # A decided round the owner has not been sent the letter for is not theirs yet.
        from reviews import decision_letters

        completed = reviews.filter(completed_at__isnull=False).exclude(
            pk__in=decision_letters.withheld_review_ids(idea)
        )
        return ReviewHistory(reviews=list(completed))
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
