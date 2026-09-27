"""
Offset pagination for the read side (S2-004).

The project had no pagination abstraction before this: every list query
returned the whole queryset, which is fine for a user's own handful of
memberships and not fine for discovery, where "every idea the caller may see"
is unbounded and grows with the platform. So this is the small, explicit thing
the task calls for, kept deliberately plain.

**Offset, not cursor.** A cursor is the better answer for a large,
append-heavy, continuously-ordered dataset. This is not that dataset: the
ordering is stable (`-created_at, -pk`), the result set is small enough that a
page count is cheap, and the UI needs "page 2 of 7" more than it needs a
stable cursor across an insert. Offset also composes with the filters the
discovery query already has, which a keyset cursor would have to be taught
separately. If a future dataset needs cursors, this module is the one place
that changes and the payload shape can grow a `cursor` field alongside
`offset`.

**Clamped, not rejected.** An out-of-range limit is brought inside the maximum
rather than refused. A client asking for 1000 rows is asking for a page, not
for an error, and a hard maximum is only meaningful if the server is the one
that enforces it — `MAX_PAGE_SIZE` below is that enforcement, and a client
that ignores it gets 50 rows, not the table.

Two queries per page, and why that is the right trade here: one `COUNT(*)` for
the total and one page fetch. A cursor design would need neither the count nor
the offset, but it would also stop the UI saying "showing 1-20 of 137", which
is the number people use to decide whether refining the search beats paging.
Discovery is a `COUNT` over a filtered, indexed, already-visibility-restricted
set; anything hotter belongs in a cached count, and nothing here is hot yet.

Generic over the model so the same helper serves comments, votes and proposals
when they arrive: `paginate(queryset, ...)` knows nothing about ideas.
"""

from dataclasses import dataclass

from django.db.models import QuerySet

# A page is what an unfiltered browsing UI wants, and it is the size the ideas
# index is designed around (`ideas_org_created_idx` leads on the tenant filter
# that every discovery query applies).
DEFAULT_PAGE_SIZE = 20

# A ceiling, not a target. A client may ask for less; asking for more gets
# this. It exists so a single request cannot ask the database for the whole
# table.
MAX_PAGE_SIZE = 50


@dataclass(frozen=True)
class Page[T]:
    """
    One page of results, plus what a UI needs to offer the next one.

    `total_count` is the number of rows matching the filters *before* paging,
    so "showing 1-20 of 137" is answerable from one response. `offset` and
    `limit` are echoed back as applied rather than as requested, so a client
    that asked for 500 and got 50 can tell that from its own numbers without
    knowing the server's maximum.
    """

    items: list[T]
    total_count: int
    offset: int
    limit: int

    @property
    def has_next_page(self) -> bool:
        return self.offset + len(self.items) < self.total_count

    @property
    def has_previous_page(self) -> bool:
        return self.offset > 0


def clamp_limit(limit: object) -> int:
    """
    Coerce a requested page size into something sane.

    Non-numeric, zero and negative values become the default rather than an
    error: this is a hint about how much to send, and refusing the request
    because of it would make a cosmetic mistake look like a broken client.
    Above the maximum is clamped, not refused - see the module docstring.

    A float is *rejected* rather than truncated, and that is a deliberate
    asymmetry with "non-numeric". `3.5` is not a page size somebody meant to
    send, and quietly rounding it to 3 invents an answer to a question the
    caller did not ask. `3.0` and `"3"` are also rejected: the type says
    integer, so anything else is treated as a caller that got it wrong, and the
    default is the safe thing to do instead. `int(str(limit))` would accept all
    of them, which is why this is explicit about the shape it wants.
    """
    if isinstance(limit, bool) or not isinstance(limit, (int, str)):
        return DEFAULT_PAGE_SIZE

    try:
        requested = int(limit.strip() if isinstance(limit, str) else limit)
    except ValueError:
        return DEFAULT_PAGE_SIZE

    if requested <= 0:
        return DEFAULT_PAGE_SIZE
    return min(requested, MAX_PAGE_SIZE)


def clamp_offset(offset: object) -> int:
    """
    A negative offset is zero. See `clamp_limit` for the reasoning, including
    why a float is refused rather than truncated.
    """
    if isinstance(offset, bool) or not isinstance(offset, (int, str)):
        return 0

    try:
        requested = int(offset.strip() if isinstance(offset, str) else offset)
    except ValueError:
        return 0
    return max(requested, 0)


def paginate[T](
    queryset: QuerySet[T],
    *,
    offset: object = 0,
    limit: object = None,
) -> Page[T]:
    """
    Slice `queryset` into one page and count what it matched.

    The queryset is expected to be ordered and already filtered — by the
    caller, which in this project means the visibility filter has been applied
    *before* this function is reached. Pagination deliberately knows nothing
    about that: if it did, there would be a second code path that could skip
    it.
    """
    applied_offset = clamp_offset(offset)
    applied_limit = clamp_limit(limit)

    total_count = queryset.count()
    items = list(queryset[applied_offset : applied_offset + applied_limit])

    return Page(
        items=items,
        total_count=total_count,
        offset=applied_offset,
        limit=applied_limit,
    )


def empty_page[T](offset: object = 0, limit: object = None) -> Page[T]:
    """
    An empty page with the same shape as a real one.

    Returned instead of an empty list wherever the answer is "you may not see
    this" rather than "there is nothing here" — a caller that could tell the
    two apart from the response could probe for the existence of ideas it
    cannot read.
    """
    return Page(
        items=[],
        total_count=0,
        offset=clamp_offset(offset),
        limit=clamp_limit(limit),
    )
