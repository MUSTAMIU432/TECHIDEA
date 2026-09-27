"""
Reading ideas (S2-002).

Every read of an idea goes through this module, and the reason is structural
rather than stylistic: `ideas/services.py` decides who may *write*, and
nothing in it decides who may *read*. A caller that reached for
`Idea.objects` directly would get every idea in the table, including other
tenants' - so the only way to obtain an idea is through a function here that
has already applied the rules.

The two rules applied, in this order:

1. **Tenancy.** An idea belongs to an organization, and a reader may only see
   an idea belonging to an organization they are an *active* member of -
   except for a `PUBLIC` idea, which is platform-wide by definition. The
   membership predicate is `organizations.authorization.get_membership`'s, so
   a read and a write can never disagree about who belongs where.
2. **Visibility.** `Idea.visibility` narrows that further. This is the one
   genuinely Ideas-specific decision in the whole authorization story, and it
   lives here - as a *read* filter - rather than as a second permission
   hierarchy, exactly as `docs/ideas-domain.md` specified in S2-001.

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

from django.db.models import Q, QuerySet

from ideas.models import Category, Idea
from ideas.pagination import Page, empty_page, paginate
from identity.models import User
from organizations import authorization


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

    # Everything below is tenant-scoped, and `DEPARTMENT` is deliberately
    # absent: with no department tier it is author-only, so it falls through to
    # "must be a member of the idea's organization" being false. See the
    # module docstring.
    if idea.visibility == Idea.Visibility.ORGANIZATION:
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
            organization_id__in=authorization.active_organization_ids(user),
        )
    )


def _base_queryset() -> QuerySet[Idea]:
    return Idea.objects.select_related('organization', 'author', 'category')


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

    # `-pk` as the tie-breaker is not decoration: `created_at` is
    # microsecond-resolution, so two ideas filed in the same instant would
    # otherwise come back in an arbitrary order and a page boundary could
    # show the same row twice and skip another. Ordering by the primary key as
    # well makes every page deterministic, which is what lets `offset` be a
    # correct way to page at all.
    queryset = queryset.order_by('-created_at', '-pk')

    return paginate(queryset, offset=offset, limit=limit)


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
