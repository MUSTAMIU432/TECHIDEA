"""
Reading ideas (S2-002).

Every read of an idea goes through this module, and the reason is structural
rather than stylistic: `ideas/services.py` decides who may *write*, and
nothing in it decides who may *read*. A caller that reached for
`Idea.objects` directly would get every idea in the table, including other
tenants' - so the only way to obtain an idea is through a function here that
has already applied the rules.

The rules applied, in this order:

1. **Tenancy, which is now one of three shapes.** An idea is filed by one person,
   by a team, or by an organization - `Idea.submission_context` says which - so
   "whose idea is this" is answered from the idea rather than assumed from a
   non-null organization. An `INDIVIDUAL` idea has no tenant at all beyond its
   author, which is the case that makes "organization is optional" real rather
   than aspirational. Each context's read rule below says which of the three it
   is, and none of them falls through to another.
2. **Visibility.** `Idea.visibility` narrows that further. This is the one
   genuinely Ideas-specific decision in the whole authorization story, and it
   lives here - as a *read* filter - rather than as a second permission
   hierarchy, exactly as `docs/ideas-domain.md` specified in S2-001.

**Team visibility is membership, not organization visibility.**
`visibility=ORGANIZATION` on a team idea means "visible to my team", because
that is the only audience a team has: there is no organization to widen to, and
treating it as author-only would make a team idea unreadable by the very people
it was filed with. `visibility=PRIVATE` remains the author's alone.

**A platform reviewer can read a locked submission.**
The platform track has to be able to review what it was sent, and it is
independent of every organization - so a reviewer who is not a member of the
idea's organization must still be able to read the submission they were asked to
decide. That is granted by `platform_reviewer_filter`, and it is deliberately
narrow: only an idea that has actually been submitted to the platform
(`platform_version >= 1`), and only to an account holding the platform-scoped
`administration.review_platform_submissions` permission. It cannot expose a
draft, an idea still in the organization track, or anything at all to an
organization role or a team role.

`DEPARTMENT` fails closed
-------------------------
`DEPARTMENT` is a reserved value with no Department model behind it yet, so
there is nothing that could honour it. It is therefore treated exactly like
`PRIVATE` here: author-only. The alternative - ignoring the field, or
treating it as `ORGANIZATION` - would mean an idea an author deliberately
scoped to their department was readable by the whole organization, which is
the failure mode a reserved value exists to prevent. When the department tier
arrives this is the one function that has to change.

Every function here returns `None`, `[]` or an empty `QuerySet` for anything
the reader may not have. Nothing raises and nothing distinguishes "does not
exist" from "exists but is not yours", so a caller cannot use these to probe
for the existence of another tenant's ideas.
"""

from dataclasses import dataclass

from django.db.models import Count, Exists, IntegerField, OuterRef, Q, QuerySet, Subquery
from django.db.models.functions import Coalesce

from ideas.models import Attachment, Category, Comment, Idea, IdeaTransition, Vote
from ideas.pagination import Page, empty_page, paginate
from identity.models import User
from organizations import authorization

# Per-request memoisation of the three facts the visibility filter needs.
#
# `_visibility_filter` runs inside every queryset a request builds - and a single
# request usually builds several (a page of ideas, then its attachments, then its
# comments). Without this, one request would ask "which organizations am I in?"
# once per queryset and "do I hold the platform review permission?" once per
# queryset, which is the difference between a fixed query count and one that
# grows with the number of reads in a page.
#
# Stored on the user instance, like Django's own `_perm_cache`, so the cache has
# exactly the lifetime Django's does: the request. Nothing else may read or write
# these attributes, and they are only ever *filled* from the database.
_ORGANIZATION_IDS_CACHE = '_active_organization_ids_cache'
_TEAM_IDS_CACHE = '_active_team_ids_cache'
_PLATFORM_REVIEWER_CACHE = '_is_platform_reviewer_cache'


def _is_platform_reviewer(user: User | None) -> bool:
    """
    Whether `user` holds the platform review permission.

    Asked through `user.has_perm` - Django's own permission backend, and the same
    cache Django keeps - and memoised here too, because the visibility filter needs
    the answer once per queryset rather than once per idea.
    """
    if user is None or not user.is_active:
        return False

    cached = getattr(user, _PLATFORM_REVIEWER_CACHE, None)
    if cached is not None:
        return cached

    from administration.authorization import REVIEW_PLATFORM_SUBMISSIONS

    answer = user.has_perm(REVIEW_PLATFORM_SUBMISSIONS)
    setattr(user, _PLATFORM_REVIEWER_CACHE, answer)
    return answer


def _organization_ids(user: User) -> list[int]:
    """The caller's active organization ids, once per request."""
    cached = getattr(user, _ORGANIZATION_IDS_CACHE, None)
    if cached is None:
        cached = authorization.active_organization_ids(user)
        setattr(user, _ORGANIZATION_IDS_CACHE, cached)
    return cached


def _team_ids(user: User) -> list[int]:
    """The caller's active team ids, once per request."""
    from teams import authorization as team_authorization

    cached = getattr(user, _TEAM_IDS_CACHE, None)
    if cached is None:
        cached = team_authorization.active_team_ids(user)
        setattr(user, _TEAM_IDS_CACHE, cached)
    return cached


def platform_reviewer_filter(user: User) -> Q:
    """
    The `Q` that lets a platform reviewer read a submission they must decide.

    **Only ideas that have been submitted to the platform.** `platform_version
    >= 1` is set by `ideas.versions.freeze_submission` in the same transaction
    as the move to `SUBMITTED`, so this cannot match a draft, an idea still with
    an organization, or anything an author has not deliberately put forward. An
    empty `Q(pk__in=<none>)` for anybody without the permission, which matches
    nothing rather than everything.
    """
    if not _is_platform_reviewer(user):
        return Q(pk__in=Idea.objects.none().values('pk'))

    return Q(platform_version__gte=1)


def can_view_idea(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` may read `idea`.

    Exposed as its own predicate rather than only as a filter because a
    caller needs to answer the same question in Python - the frontend is told
    "you may edit this" from exactly this, so the rule that decides it is the
    same rule that filtered the list it came from.
    """
    if user is None or not user.is_active or idea is None:
        return False

    if idea.visibility == Idea.Visibility.PUBLIC:
        # Platform-wide by definition. Still requires an authenticated,
        # active account: this is a signed-in product, not a public board.
        return True

    if idea.author_id == user.pk:
        return True

    # A submission the platform was actually sent. Checked before the tenant
    # branches, because a platform reviewer is deliberately not a member of
    # every idea's organization - see the module docstring.
    if idea.platform_version >= 1 and _is_platform_reviewer(user):
        return True

    # Everything below is tenant-scoped, and `DEPARTMENT` is deliberately
    # absent: with no department tier it is author-only, so it falls through to
    # "must be a member of the idea's tenant" being false. See the module
    # docstring.
    if idea.visibility == Idea.Visibility.ORGANIZATION:
        if idea.submission_context == Idea.SubmissionContext.TEAM:
            from teams import authorization as team_authorization

            return team_authorization.is_member_of(user, idea.team_id)
        return authorization.is_member_of(user, idea.organization_id)

    return False


def _visibility_filter(user: User) -> Q:
    """
    The `Q` object equivalent of `can_view_idea`, for use in a queryset.

    Kept in the same module and in the same shape as the predicate above on
    purpose. Two implementations of one rule is the failure mode this module
    exists to prevent, so the two are written to be visibly parallel:
    `PUBLIC` is readable by anyone authenticated, an author always sees
    their own, and `ORGANIZATION` additionally requires membership.
    """
    return (
        Q(visibility=Idea.Visibility.PUBLIC)
        | Q(author=user)
        | Q(
            visibility=Idea.Visibility.ORGANIZATION,
            organization_id__in=_organization_ids(user),
        )
        | Q(visibility=Idea.Visibility.ORGANIZATION, team_id__in=_team_ids(user))
        | platform_reviewer_filter(user)
    )


def _base_queryset() -> QuerySet[Idea]:
    return Idea.objects.select_related('organization', 'team', 'author', 'category')


def get_idea(user: User | None, idea_id: object) -> Idea | None:
    """
    One idea `user` may read, or `None`.

    `None` covers all three unreadable cases identically - no such id, another
    tenant's idea, or an idea that exists but is not shared with this reader -
    so the answer cannot be used to discover that an id is real.
    """
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(idea_id))
    except (TypeError, ValueError):
        return None

    return _base_queryset().filter(_visibility_filter(user), pk=normalized_id).first()


def get_idea_for_update(user: User | None, idea_id: object) -> Idea | None:
    """
    One idea the reader may see, locked for update. Caller must be in a transaction.

    Exists for exactly one caller - `ideas.lifecycle.transition_idea` - and
    deliberately a *selector* rather than a query inside the lifecycle, so that
    a transition is refused for precisely the ideas a read would have hidden.
    Two implementations of the visibility rule, one in the filter and one in
    the write path, is how "you may not see it but you may approve it" happens.

    `select_for_update` is what makes two concurrent transitions of the same
    idea safe: the second waits for the first to commit and then re-reads the
    *new* status, rather than both having read the old one.
    """
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(idea_id))
    except (TypeError, ValueError):
        return None

    return (
        _base_queryset()
        .filter(_visibility_filter(user), pk=normalized_id)
        # `of=('self',)`, and not a bare `select_for_update()`: the queryset
        # select-relates `category`, which is nullable, so PostgreSQL builds a
        # LEFT OUTER JOIN and refuses `FOR UPDATE` on the nullable side of one.
        # Naming the table restricts the lock to the idea row, which is the
        # only row whose state the transition is about - the joined rows are
        # read-only context and are not being changed here.
        .select_for_update(of=('self',))
        .first()
    )


def list_ideas(user: User | None) -> QuerySet[Idea]:
    """
    Every idea `user` may read, across all their organizations, newest first.

    Returns a `QuerySet` rather than a list on purpose: a list would be an
    already-materialized result that a caller could append to or filter again
    by hand, and the whole point of this module is that the filter cannot be
    forgotten. The `PUBLIC` branch is platform-wide, so this is not the same
    as "my organization's ideas" - see `list_organization_ideas`.
    """
    if user is None or not user.is_active:
        return Idea.objects.none()

    return _base_queryset().filter(_visibility_filter(user)).order_by('-created_at')


def list_organization_ideas(
    user: User | None,
    organization_id: object,
) -> QuerySet[Idea]:
    """
    The ideas in one organization that `user` may read.

    Returns empty for an organization `user` is not an active member of,
    without confirming that the organization exists. A `PUBLIC` idea in a
    tenant the reader is not a member of is therefore *not* returned here even
    though `list_ideas` would include it: this is the organization's feed, and
    a tenant's feed is not a listing of another tenant's contents.
    """
    if user is None or not user.is_active:
        return Idea.objects.none()

    try:
        normalized_id = int(str(organization_id))
    except (TypeError, ValueError):
        return Idea.objects.none()

    if authorization.get_membership(user, normalized_id) is None:
        return Idea.objects.none()

    return (
        _base_queryset()
        .filter(_visibility_filter(user), organization_id=normalized_id)
        .order_by('-created_at')
    )


def list_team_ideas(user: User | None, team_id: object) -> QuerySet[Idea]:
    """
    The ideas one team has filed that `user` may read.

    The team counterpart of `list_organization_ideas`, with the same two
    properties that make it safe: empty for a team `user` is not an active
    member of, and empty for a team that does not exist, without confirming
    either. `Team` is a collaboration boundary rather than a tenant, so this is
    "what my team is putting forward", never "a tenant's private feed" - which is
    why it is a separate function rather than an `organization_id` argument that
    happens to hold a team.
    """
    if user is None or not user.is_active:
        return Idea.objects.none()

    try:
        normalized_id = int(str(team_id))
    except (TypeError, ValueError):
        return Idea.objects.none()

    from teams import authorization as team_authorization

    if team_authorization.get_membership(user, normalized_id) is None:
        return Idea.objects.none()

    return (
        _base_queryset()
        .filter(_visibility_filter(user), team_id=normalized_id)
        .order_by('-created_at')
    )


def list_own_ideas(user: User | None) -> QuerySet[Idea]:
    """
    Everything `user` has written, whatever state it is in.

    The author's own view is deliberately *not* visibility-filtered: an author
    can always see their own idea, and filtering their own work by who else
    might read it would be a strange thing to do to a person. It is the one
    place in this module that is a scope rather than a filter, and the only
    reason it is safe is that the author set is exactly the caller's own id.
    """
    if user is None or not user.is_active:
        return Idea.objects.none()

    return _base_queryset().filter(author=user).order_by('-created_at')


@dataclass(frozen=True)
class IdeaFilters:
    """
    Everything a caller may narrow a discovery query by.

    Note what is *not* here: there is no `visibility` field, and no `author_id`,
    and no free-form field mapping. Those are exactly the arguments that would
    turn a read into a way of asking for somebody else's rows — `visibility=public`
    reads like a filter and behaves like a grant. Visibility is decided by
    `_visibility_filter` and by nothing else; an author filter is dropped for
    the same reason (nobody needs "ideas by X" in discovery, and it is one more
    surface to keep honest).

    `organization_id` is the exception that looks like a grant and is not: it
    can only ever *narrow*, because `list_discoverable_ideas` refuses it
    outright unless the caller holds an active membership of that
    organization.
    """

    organization_id: object | None = None
    category_id: object | None = None
    status: str | None = None
    search: str | None = None


def _normalize_id(value: object) -> int | None:
    """`None` for an unusable id, so a bad filter empties the result set."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _search_filter(search: str | None) -> Q:
    """
    Case-insensitive substring match over the two fields a reader scans.

    `icontains` builds a `LIKE` in which the term is a *bound parameter*, so
    the text can never be interpreted as SQL — which is the only reason this is
    safe to expose to a client at all. Django also escapes the pattern
    metacharacters: a search for `%` compiles to a `LIKE` with that character
    escaped, so a reader who types a wildcard searches for the character
    rather than matching everything. Worth stating explicitly because it is a property of
    the ORM's escaping, not of this module: swapping `icontains` for `raw` to
    gain trigram search later would quietly turn "a % sign" into "everything",
    so `test_a_wildcard_character_is_searched_for_literally` pins it.

    Deliberately title and description only. `problem_statement` and friends
    are not exposed anywhere yet, and searching a field the API does not
    return would let a reader confirm the presence of a phrase in text they
    cannot otherwise see.
    """
    term = (search or '').strip()
    if not term:
        return Q()
    return Q(title__icontains=term) | Q(description__icontains=term)


def _apply_filters(queryset: QuerySet[Idea], filters: IdeaFilters) -> QuerySet[Idea]:
    """
    Narrow an already-visibility-filtered queryset.

    Order matters and is the whole point: the visibility filter is applied by
    the caller *before* this, and everything here can only remove rows from
    what that filter already allowed. A filter therefore cannot widen the
    result, whatever it is set to.

    An unusable filter value empties the result rather than being ignored. A
    client that asked for category "abc" and got the unfiltered list would
    read that as "no ideas in this category" when it means "that category does
    not exist" — an empty page says the same thing and cannot be mistaken for
    data.
    """
    queryset = queryset.filter(_search_filter(filters.search))

    if filters.category_id is not None:
        category_pk = _normalize_id(filters.category_id)
        if category_pk is None:
            return queryset.none()
        queryset = queryset.filter(category_id=category_pk)

    if filters.status is not None and str(filters.status).strip():
        status = str(filters.status).strip().lower()
        if status not in Idea.Status.values:
            return queryset.none()
        queryset = queryset.filter(status=status)

    return queryset


def list_discoverable_ideas(
    user: User | None,
    filters: IdeaFilters | None = None,
    *,
    offset: object = 0,
    limit: object = None,
) -> Page[Idea]:
    """
    One page of ideas `user` may see, newest first, narrowed by `filters`.

    The single entry point for discovery, and the only place the visibility
    filter, the discovery filters and pagination meet. It is the read path
    `organizationIdeas` and `ideas` both go through, so there is no second
    queryset an unfiltered idea could escape through.

    An `organization_id` filter the caller has no active membership of yields
    an empty page, not an error and not somebody else's ideas — the same
    answer as for an organization that does not exist, so the argument cannot
    be used to probe for either.
    """
    applied = filters or IdeaFilters()

    if user is None or not user.is_active:
        return empty_page(offset, limit)

    queryset = _base_queryset().filter(_visibility_filter(user))

    if applied.organization_id is not None:
        organization_pk = _normalize_id(applied.organization_id)
        if organization_pk is None:
            return empty_page(offset, limit)
        if authorization.get_membership(user, organization_pk) is None:
            return empty_page(offset, limit)
        queryset = queryset.filter(organization_id=organization_pk)

    queryset = _apply_filters(queryset, applied)

    # Ordering: `-pk` as the tie-breaker is not decoration: `created_at` is
    # microsecond-resolution, so two ideas filed in the same instant would
    # otherwise come back in an arbitrary order and a page boundary could
    # show the same row twice and skip another. Ordering by the primary key as
    # well makes every page deterministic, which is what lets `offset` be a
    # correct way to page at all.
    queryset = queryset.order_by('-created_at', '-pk')

    # Vote state rides along on the rows this query already fetches (S2-006).
    # Annotated here rather than in the caller so that every path into a page
    # of ideas reports the same numbers, and so no caller can forget: the
    # alternative is a resolver looping and counting, which is an N+1 the
    # annotations make unnecessary.
    return paginate(annotate_vote_state(queryset, user), offset=offset, limit=limit)


def list_active_categories() -> QuerySet[Category]:
    """
    Categories available to file a new idea under.

    `is_active` is retirement, not deletion (`Idea.category` is `PROTECT`, so a
    used category can never be deleted) - so a retired category keeps its
    history and disappears from the picker. Read-only and unfiltered by
    tenancy on purpose: `Category` is platform-wide by design, which is what
    makes ideas comparable across organizations.
    """
    return Category.objects.filter(is_active=True).order_by('name')


# --- comments (S2-005) -------------------------------------------------------------


def _visible_idea_ids(user: User) -> QuerySet[Idea]:
    """
    The ids of every idea `user` may read, as a queryset.

    A helper rather than an inline filter because comments are authorized
    through their idea and nothing else: a comment has no tenant of its own, no
    visibility of its own, and no membership check that would answer the
    question on its own. Deriving it from `_visibility_filter` is what makes
    "you may read a comment" and "you may read the idea it is on" the same
    statement by construction - a second, comment-specific version of the rule
    is exactly the drift this module exists to prevent.
    """
    return _base_queryset().filter(_visibility_filter(user)).values('pk')


def get_comment(user: User | None, comment_id: object) -> Comment | None:
    """
    One comment `user` may read, or `None`.

    `None` covers no such id, an id belonging to an idea in another tenant, and
    a comment on an idea that is not shared with this reader - identically, so
    a comment id cannot be used to find out which ideas exist.

    Read through the idea rather than through the comment, which is the whole
    authorization story for this model: `idea__in=_visible_idea_ids(user)` is
    `can_view_idea` applied to the parent, so a comment can never be readable
    on an idea that is not.
    """
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(comment_id))
    except (TypeError, ValueError):
        return None

    return (
        Comment.objects.select_related('author', 'idea')
        .filter(pk=normalized_id, idea__in=_visible_idea_ids(user))
        .first()
    )


def list_comments(
    user: User | None,
    idea_id: object,
    *,
    offset: object = 0,
    limit: object = None,
) -> Page[Comment]:
    """
    One page of an idea's discussion, oldest first.

    Discussion order is chronological, which is the order a conversation is
    read in, and the tie-breaker is the primary key for the same reason ideas
    order by `-created_at, -pk`: `created_at` is microsecond-resolution, so two
    comments written in the same instant would otherwise come back in an
    arbitrary order, and a page boundary could then show one of them twice and
    skip the other. `Meta.ordering` is `['created_at']` alone, so the tie-break
    is stated here rather than inherited.

    An idea the caller may not read is an **empty page**, not an error and not a
    refusal: the same answer as for an idea that does not exist, and the same
    answer this module already gives for an organization the caller does not
    belong to. A discussion is a corollary of an idea being readable, so it
    cannot be a better oracle than the idea itself.
    """
    if user is None or not user.is_active:
        return empty_page(offset, limit)

    # Resolved through `get_idea`, so the visibility rule is not restated. A
    # null idea here means "not readable", and the queryset below is then
    # filtered on a null pk, which matches nothing.
    idea = get_idea(user, idea_id)

    queryset = (
        Comment.objects.select_related('author', 'idea')
        .filter(idea_id=idea.pk if idea is not None else None)
        .order_by('created_at', 'pk')
    )

    return paginate(queryset, offset=offset, limit=limit)


# --- votes (S2-006) -----------------------------------------------------------------


@dataclass(frozen=True)
class IdeaVoteState:
    """
    How an idea is voted on, as one reader sees it.

    A value rather than a queryset, because the two numbers a client needs -
    how many people voted, and whether *I* did - are two different questions
    and the second one is only answerable for a known reader. Returning them
    together is what lets a card render its vote control from a single row it
    already has.

    Frozen for the same reason `IdeaFilters` is: a caller that could mutate the
    state it was handed would be holding a second version of the truth.
    """

    idea_id: int
    vote_count: int
    viewer_has_voted: bool


def annotate_vote_state(queryset: QuerySet[Idea], user: User) -> QuerySet[Idea]:
    """
    Add `vote_count` and `viewer_has_voted` to a queryset of ideas.

    **Why annotations rather than a loop.** A discovery page is up to 50 ideas
    and each needs a count and a yes/no. Counting them in Python would be 50
    queries per page; the obvious fix - a grouped aggregate - would change the
    query's shape and add a `GROUP BY` to a query that also has to be sliced
    and counted. Two correlated subqueries keep it to the one row-fetch the
    page already made, and `paginate`'s `COUNT(*)` is unaffected: Django does
    not carry annotations into `.count()`, which is pinned by
    `test_discovery_still_costs_the_same_three_queries`.

    **`Coalesce` is load-bearing.** A `COUNT` subquery over a group with no
    rows returns SQL `NULL`, not `0`, and `NULL` into a non-nullable
    `Int!` field is a GraphQL error. The overwhelmingly common case - an idea
    nobody has voted on - would fail to serialize. So the zero is supplied
    here, once, rather than in every consumer.

    `Exists` rather than a count for the viewer's own vote, because the answer
    is a boolean and the database can stop at the first row. It uses
    `unique_vote_per_user_idea`, so it is an index probe.
    """
    return queryset.annotate(
        vote_count=Coalesce(
            Subquery(
                Vote.objects.filter(idea=OuterRef('pk'))
                .order_by()
                .values('idea')
                .annotate(total=Count('*'))
                .values('total')[:1],
                output_field=IntegerField(),
            ),
            0,
        ),
        viewer_has_voted=Exists(Vote.objects.filter(idea=OuterRef('pk'), user=user)),
    )


def vote_state_for(user: User | None, idea: Idea) -> IdeaVoteState | None:
    """
    The vote state of one *already authorized* idea, or `None`.

    `None` for an absent or inactive reader, and for a deactivated one - the
    same answer the rest of this module gives.

    Deliberately takes an `Idea` rather than an id: the caller has already
    resolved the idea through `get_idea`, so the visibility filter has already
    run and re-running it here would be a second answer to "may this reader see
    it". A count for an idea nobody was shown is never produced by this
    function, which is the whole of the read-side authorization story for
    votes.

    Two queries rather than one annotated row, because a single idea that came
    from `get_idea` has no annotations on it. That is fine here and would not
    be for a page of fifty, which is why the list path uses
    `annotate_vote_state`.
    """
    if user is None or not user.is_active:
        return None

    return IdeaVoteState(
        idea_id=idea.pk,
        vote_count=vote_count_for(idea),
        viewer_has_voted=Vote.objects.filter(idea=idea, user=user).exists(),
    )


# --- attachments (S2-007) ------------------------------------------------------------


def get_attachment(user: User | None, attachment_id: object) -> Attachment | None:
    """
    One attachment `user` may read, or `None`.

    Read through the attachment's idea, exactly the way `get_comment` reads
    through a comment's: `idea__in=_visible_idea_ids(user)` is
    `can_view_idea` applied to the parent, so an attachment can never be
    readable on an idea that is not. `None` covers no such id, an id
    belonging to an idea in another tenant, and an attachment on an idea not
    shared with this reader - identically, so an attachment id cannot be used
    to find out which ideas exist.
    """
    if user is None or not user.is_active:
        return None

    try:
        normalized_id = int(str(attachment_id))
    except (TypeError, ValueError):
        return None

    return (
        Attachment.objects.select_related('idea', 'idea__organization', 'uploaded_by')
        .filter(pk=normalized_id, idea__in=_visible_idea_ids(user))
        .first()
    )


def get_idea_attachment(
    user: User | None, idea_id: object, attachment_id: object
) -> Attachment | None:
    """
    One attachment `user` may read, *and* that names `idea_id` as its idea.

    The one caller of this is `ideas/views.py`'s download view, which is
    reached with both ids in the URL - so "this attachment exists, but
    belongs to a different idea from the one in the URL" must not resolve to
    the attachment anyway (the download link for one idea's evidence must not
    also work as a link for another attachment sharing its id space by
    accident, and no idea's page ever renders a link naming a mismatched
    pair). Resolving `idea_id` through `get_idea` first, then filtering
    `idea_id=idea.pk` rather than trusting the caller's pairing, is what
    makes that structural rather than a check to remember.
    """
    idea = get_idea(user, idea_id)
    if idea is None:
        return None

    try:
        normalized_id = int(str(attachment_id))
    except (TypeError, ValueError):
        return None

    return (
        Attachment.objects.select_related('idea').filter(pk=normalized_id, idea_id=idea.pk).first()
    )


def list_attachments(
    user: User | None,
    idea_id: object,
    *,
    offset: object = 0,
    limit: object = None,
) -> Page[Attachment]:
    """
    One page of an idea's supporting evidence, oldest first.

    Exactly `list_comments`' shape, for exactly the same reason: an idea the
    caller may not read is an **empty page**, not an error, so an attachment
    listing cannot be a better oracle than the idea it hangs from. Ordered by
    `created_at, pk` - upload order, not a ranking - with the primary key as
    tie-breaker for the same microsecond-collision reason every other list in
    this module uses one.

    `select_related('uploaded_by')` is what keeps a page of many attachments
    at one query for the page plus one for the count, rather than one extra
    query per row to resolve who uploaded each one.
    """
    if user is None or not user.is_active:
        return empty_page(offset, limit)

    idea = get_idea(user, idea_id)

    queryset = (
        Attachment.objects.select_related('uploaded_by')
        .filter(idea_id=idea.pk if idea is not None else None)
        .order_by('created_at', 'pk')
    )

    return paginate(queryset, offset=offset, limit=limit)


def vote_count_for(idea: Idea) -> int:
    """
    How many people have voted on one already authorized idea.

    Split out from `vote_state_for` because the total is **not** a per-reader
    fact, and only the "did *I* vote" half needs a reader. Three idea-writing
    mutations (`createIdea`, `updateIdea`, `submitIdea`) build their payload
    from an `Idea` without naming a viewer - that is S2-002's shape for
    `availableTransitions` and is deliberately left exactly as it shipped - and
    a count that fell back to `0` there would report a wrong number on an idea
    that other people had already voted for. So the total is counted from the
    idea itself in that case, and only the viewer's own answer goes unanswered.

    Takes the same already authorized `Idea` for the same reason
    `vote_state_for` does: authorization happened when the idea was resolved,
    and this function is not a second place where it could be forgotten.
    """
    return Vote.objects.filter(idea=idea).aggregate(total=Count('*'))['total'] or 0


def list_idea_transitions(user: User | None, idea_id: object) -> list[IdeaTransition]:
    """
    One idea's lifecycle history, oldest first, for whoever may read it.

    Every row names the member who made the move, so this is shown to

    - the idea's **author**, whose idea it is,
    - **organization reviewers of the idea's organization** (`lifecycle.
      is_organization_reviewer`: an active member holding `idea.review` there,
      not the author), and
    - **platform reviewers** (`lifecycle.is_platform_reviewer`), because the
      platform track makes several of these moves and its reviewers need to see
      that they happened.

    and to nobody else - an empty list, which is also the answer for an idea the
    caller cannot read or that does not exist, so the id reveals nothing.

    Both reviewer kinds are listed because the idea now has **two** tracks' worth
    of moves in its history - the organization stage and the platform track - and
    a reader of either one needs the whole chain to make sense of where the idea
    is. They are still different permissions, so this is not a way to see an
    idea's history without being able to read the idea.
    """
    # Imported here: `ideas.lifecycle` imports this module.
    from ideas import lifecycle

    idea = get_idea(user, idea_id)
    if idea is None:
        return []
    if (
        idea.author_id != user.pk
        and not lifecycle.is_organization_reviewer(user, idea)
        and not lifecycle.is_platform_reviewer(user, idea)
    ):
        return []
    return list(IdeaTransition.objects.filter(idea=idea).order_by('created_at', 'pk'))
