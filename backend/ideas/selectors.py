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

from django.db.models import Q, QuerySet

from ideas.models import Category, Idea
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
