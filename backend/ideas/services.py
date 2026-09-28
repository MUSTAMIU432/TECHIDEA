"""
Writing ideas: create, edit, submit (S2-002), commenting on them (S2-005),
voting on them (S2-006), and attaching supporting evidence to them (S2-007).

The review workflow's queue is a later sprint; this module does not stub it,
does not half-implement it, and does not define the vocabulary it would use.
S2-006 added the vote operations, which follow this module's existing rule
that the client is never trusted with ownership: the voter is the
authenticated user and the idea is authorized and then used. S2-007's
attachment operations follow the same rule for the uploader, and additionally
never trust the client with the storage key (`ideas/storage.py` generates
it) or with the file's declared type (`ideas/attachments.py` derives and
confirms it from the bytes instead).

Who may do what
---------------
- **create_idea** requires an authenticated, active user with an **active
  membership** in the target organization. There is no idea without a tenant,
  and no tenant without a membership, so that single check is what makes
  `Idea.organization` trustworthy on every row.
- **update_idea** and **submit_idea** require the caller to be **the author**
  *and* to still hold an active membership of the idea's organization. Both
  halves matter and for different reasons: authorship is who owns the
  content, and membership is whether they may still act in that tenant at
  all. A member who leaves an organization must not be able to keep editing
  ideas in it, and an author who is somehow not a member has no business
  writing into it.

There is deliberately no `ideas/permissions.py` and no Ideas-specific
permission code. S2-001 committed to that: authorization stays in
`organizations.authorization`, which is write/act-shaped, and the one
Ideas-specific question - *who may read this?* - is a read filter in
`ideas/selectors.py` built on the same membership. Adding a parallel
`idea.create` code here would have been a second answer to a question the
platform already answers.

Commenting (S2-005)
--------------------
- **add_comment** requires an authenticated, active user who may **read** the
  idea, and an idea whose discussion is open (`lifecycle.discussion_is_open`).
  Deliberately *read*, not *member*: participation follows readability, which
  is the same rule `docs/ideas-domain.md` set for voting, and it is what lets
  anybody in the platform discuss a `PUBLIC` idea without being made a member
  of someone else's organization first. An idea is `PUBLIC` precisely because
  it is meant to be read and answered across tenants.
- **update_comment** and **delete_comment** require the caller to be **the
  author**, and the comment's idea to still be readable to them. There is no
  elevated path: no administrator, moderator or Owner may edit or delete
  somebody else's comment, because no such capability exists in
  `organizations.authorization` to draw it from, and inventing one here would
  be a second authorization system (see below).
- Retraction stays available in a closed discussion. Being able to take back
  what you wrote is not participating in the discussion, and a state change
  must not be able to strand a comment nobody may ever remove.

Every refusal below is one message per kind, because each is one answer. An
unknown comment id, another author's comment, and a comment on an idea the
caller may not read are all `"Comment is unavailable."`; an unknown idea and
an unreadable one are both `"Idea is unavailable."` So a comment id is not an
oracle for which ideas or people exist, and an idea id is not an oracle for
which comments exist. The one refusal that names itself is a closed
discussion, because the caller can already see the idea and its status - see
`_load_discussable_idea`.

What the client is never trusted with
-------------------------------------
`organization_id` and the author are **inputs to a decision, not values to be
written**. The organization is authorized and then used; the author is always
the authenticated user and is never read from the request. Neither can be
supplied, overridden, or "corrected" by a caller - which is why neither
appears in any input dataclass here. `update_idea`'s input likewise has no
`organization`, `author` or `status` field, so "change the tenant", "become
somebody else" and "mark this submitted by hand" are not operations this
module has; they are operations it does not have the vocabulary for. Status
changes in general belong to `ideas.lifecycle`, which `submit_idea` below
delegates to rather than reimplementing.

Refusal never confirms existence
--------------------------------
An idea the caller may not touch - another author's, another tenant's, or one
that does not exist - produces the same `IdeaError` with the same message
("Idea is unavailable."). Distinguishing them would turn every one of these
three operations into a probe for which idea ids are real.
"""

import logging
from dataclasses import dataclass

from django.db import DatabaseError, IntegrityError, transaction

from ideas import attachments as attachment_rules
from ideas import selectors, storage
from ideas.models import Attachment, Category, Comment, Idea, Vote
from identity.models import User
from organizations import authorization
from organizations.authorization import AuthorizationError
from organizations.models import Membership

logger = logging.getLogger(__name__)


class IdeaError(AuthorizationError):
    """
    Any refusal from this module.

    Carries the same `(message, field, reason)` triple as
    `organizations.services.OrganizationError` and is rendered the same way
    by `ideas/schema.py` - one failure convention for the whole API, so the
    frontend has a single shape to handle rather than one per domain.

    `field` names the input at fault so the resolver can put the message next
    to it; it is `None` for anything that is not a field-level problem (a
    throttle, a tenancy refusal, an already-submitted idea).
    """

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message, reason=reason, field=field)


@dataclass(frozen=True)
class IdeaInput:
    """
    The writable content of an idea.

    Exactly the four fields S2-002's form collects, and nothing else - see the
    module docstring for why ownership, tenant and status are absent. The
    model's optional `problem_statement`, `proposed_solution` and
    `expected_benefit` are also absent: they are content for a later slice,
    and adding them to a public input type before anything writes them would
    be an API nobody can yet fill in.
    """

    title: str
    description: str = ''
    category_id: object | None = None
    visibility: str | None = None


# The rules below are the "what must be filled in to submit" that
# `ideas/models.py` deliberately did *not* express on the model: a DRAFT is
# incomplete by definition, so completeness is a property of the transition,
# not of the schema. See that module's docstring.
MIN_DESCRIPTION_LENGTH = 20
MAX_TITLE_LENGTH = Idea._meta.get_field('title').max_length

# A comment has a maximum, and it is a judgement call of the same kind as
# MIN_DESCRIPTION_LENGTH: `Comment.content` is a `TextField`, so the database
# will happily store a novel. The number is generous because a discussion reply
# is a paragraph rather than a sentence - someone answering a question with
# detail is the point - and bounded because an unbounded write on a table every
# member can read is a storage and rendering hazard, and because the frontend
# renders the content into a card that has to stay legible.
#
# Enforced by the service, not by a `MaxLengthValidator`, for the same reason
# the other content rules are: the rule is a product decision rather than a
# schema fact, and it is refused rather than truncated. Silently shortening
# somebody's comment would store text they did not write and return success -
# the caller would have no way to know the last line was lost.
MAX_COMMENT_LENGTH = 2000

# `DEPARTMENT` is reserved vocabulary (see `Idea.Visibility`) with no
# Department model behind it, so nothing could honour it. Accepting it would
# mean storing an idea its author believes is department-scoped while every
# reader of this code treats it as private or, later, organization-wide.
# Refusing it is the fail-closed choice; the value becomes selectable the day
# the department tier does.
SELECTABLE_VISIBILITIES = (
    Idea.Visibility.PUBLIC,
    Idea.Visibility.ORGANIZATION,
    Idea.Visibility.PRIVATE,
)


def _require_active_user(user: User | None) -> User:
    if user is None or not user.is_active:
        raise IdeaError('You must be signed in to work with ideas.', reason='unauthenticated')
    return user


def _require_membership(user: User, organization_id: object) -> Membership:
    """
    Refuse unless `user` is an active member of `organization_id`, and return
    that membership.

    The same predicate `organizations.authorization` authorizes writes with,
    used directly rather than through a permission code - see the module
    docstring for why Ideas adds none.

    Returning the membership rather than a boolean is deliberate: it is the
    authorization decision itself, and it carries the very organization row the
    decision was made about (`get_membership` select-relates it). So the
    caller writes the idea into the organization that was just authorized
    instead of looking the id up a second time - which is both one query
    cheaper and impossible to get subtly wrong, since a second lookup could
    in principle resolve to something else.
    """
    try:
        normalized_id = int(str(organization_id))
    except (TypeError, ValueError):
        raise IdeaError('Idea is unavailable.', reason='forbidden') from None

    membership = authorization.get_membership(user, normalized_id)
    if membership is None:
        raise IdeaError(
            'You must be an active member of this organization to file ideas here.',
            reason='membership_required',
        )
    return membership


def _validate_title(title: str | None) -> str:
    normalized = (title or '').strip()
    if not normalized:
        raise IdeaError('Give the idea a title.', field='title')
    if len(normalized) > MAX_TITLE_LENGTH:
        raise IdeaError(
            f'The title must be {MAX_TITLE_LENGTH} characters or fewer.',
            field='title',
        )
    return normalized


def _validate_description(description: str | None) -> str:
    return (description or '').strip()


def _resolve_category(category_id: object | None) -> Category | None:
    """
    The category to file under, or `None` for an unclassified idea.

    A category is optional on a draft - an idea can be written before it is
    classified - but a *bad* one is refused rather than silently dropped,
    because silently dropping it would hand back a success the caller did not
    ask for. A retired (`is_active=False`) category is refused for the same
    reason: filing something new under a category the organization has
    retired is almost certainly a mistake, and the picker does not offer it.
    """
    if category_id is None or (isinstance(category_id, str) and not category_id.strip()):
        return None

    try:
        normalized_id = int(str(category_id))
    except (TypeError, ValueError):
        raise IdeaError('Choose a valid category.', field='category') from None

    category = selectors.list_active_categories().filter(pk=normalized_id).first()
    if category is None:
        raise IdeaError('Choose a valid category.', field='category')
    return category


def _resolve_visibility(visibility: str | None) -> str | None:
    """
    The visibility to store, or `None` to keep the model default.

    Case-normalized, because the value arrives from a GraphQL enum or a
    `<select>` and neither is worth rejecting over casing - but validated
    against the *selectable* set rather than the full enum, so `department`
    cannot be stored by sending it directly.
    """
    if visibility is None or not str(visibility).strip():
        return None

    normalized = str(visibility).strip().lower()
    if normalized not in SELECTABLE_VISIBILITIES:
        raise IdeaError('Choose who can see this idea.', field='visibility')
    return normalized


def _load_owned_draft(user: User, idea_id: object) -> Idea:
    """
    The idea identified by `idea_id`, if `user` may edit it right now.

    Every refusal below produces the same message, because they are the same
    answer: this operation is not available to you on this id. Covered:
    no such id, another tenant's idea, somebody else's idea in your own
    organization, and your own idea that is past the draft phase.
    """
    # Read through the public selector rather than reaching for a queryset
    # here: this is the only place in the codebase that needs the row behind a
    # refusal, and `get_idea` already returns exactly the ideas this caller
    # may see. An author can always see their own idea (`can_view_idea` says
    # so unconditionally), so a row that comes back is either the caller's or
    # nothing - which is the same answer either way.
    idea = selectors.get_idea(user, idea_id)
    if idea is None or idea.author_id != user.pk:
        raise IdeaError('Idea is unavailable.', reason='forbidden')

    # Membership is re-checked on every write, not only at creation: leaving
    # an organization has to take the ability to write into it with you.
    if authorization.get_membership(user, idea.organization_id) is None:
        raise IdeaError(
            'You must be an active member of this organization to work with ideas here.',
            reason='membership_required',
        )

    if idea.status != Idea.Status.DRAFT:
        # S2-002 owns DRAFT -> SUBMITTED and nothing else, so a submitted
        # idea is not editable here. The Sprint 3 review transitions
        # (CHANGES_REQUESTED -> a new draft, for instance) will widen this.
        raise IdeaError('Only a draft can be edited.', reason='forbidden')

    return idea


def _validate_for_submission(idea: Idea) -> None:
    """
    What a draft must contain before it can be submitted.

    A rule of the transition, so it lives here rather than on the model: a
    draft is *supposed* to be incomplete, and a `null=False` column or a
    database CHECK would make a half-written idea unsaveable.

    Reported as a whole-form error (`field=None`) rather than per field: this
    runs only on submit, where the author's next action is to go and fill the
    gaps in the form above them, not to have one input highlighted.
    """
    if not idea.title.strip():
        raise IdeaError('Add a title before submitting this idea.')
    if len(idea.description.strip()) < MIN_DESCRIPTION_LENGTH:
        raise IdeaError(
            f'Describe the problem in at least {MIN_DESCRIPTION_LENGTH} characters '
            'before submitting.'
        )
    if idea.category_id is None:
        raise IdeaError('Choose a category before submitting this idea.')


def create_idea(user: User | None, organization_id: object, data: IdeaInput) -> Idea:
    """
    File a new idea as a DRAFT.

    The author is `user` - resolved from the access token by
    `identity.authentication` and passed in by the resolver, never read from
    `data`. The status is the model default (`DRAFT`) and the visibility is
    the model default (`PRIVATE`, fail closed) unless the caller chose one, so
    a new idea is visible to nobody but its author until somebody deliberately
    widens it.
    """
    active_user = _require_active_user(user)
    membership = _require_membership(active_user, organization_id)

    title = _validate_title(data.title)
    description = _validate_description(data.description)
    category = _resolve_category(data.category_id)
    visibility = _resolve_visibility(data.visibility)

    return Idea.objects.create(
        organization=membership.organization,
        author=active_user,
        title=title,
        description=description,
        category=category,
        **({'visibility': visibility} if visibility else {}),
    )


def update_idea(user: User | None, idea_id: object, data: IdeaInput) -> Idea:
    """
    Edit the author's own draft.

    `data` carries content only. `organization`, `author` and `status` are
    not fields on `IdeaInput`, so there is no code path here that could move
    an idea to another tenant, hand it to another author, or mark it
    submitted by hand - the only way to submit is `submit_idea`.
    """
    active_user = _require_active_user(user)
    idea = _load_owned_draft(active_user, idea_id)

    idea.title = _validate_title(data.title)
    idea.description = _validate_description(data.description)
    idea.category = _resolve_category(data.category_id)

    visibility = _resolve_visibility(data.visibility)
    if visibility:
        idea.visibility = visibility

    # `save()` runs `full_clean()`, so the model's own invariants - the
    # status/`submitted_at` pairing included - hold on this write path too.
    idea.save()
    return idea


def submit_idea(user: User | None, idea_id: object) -> Idea:
    """
    DRAFT -> SUBMITTED, by its author.

    A thin delegation to `ideas.lifecycle.transition_idea` rather than a second
    implementation of the same move. S2-002 owned this transition outright;
    S2-003 made it one entry in a seven-pair matrix, and leaving the original
    body in place would have given the domain two answers to "may this idea be
    submitted?" - free to drift, and free to disagree about who may ask. The
    name is kept because it is what the `submitIdea` mutation and the S2-002
    tests call, and because "submit my idea" is the operation's name in the
    product regardless of which module implements it.

    `CHANGES_REQUESTED -> SUBMITTED` now goes through the same call, so a
    re-submission after review feedback is the same validated move rather than a
    second, slightly different one.
    """
    # Imported here rather than at module scope: `ideas.lifecycle` imports the
    # validators above from this module, so a top-level import in both
    # directions would be a cycle. One direction at import time is enough.
    from ideas import lifecycle

    return lifecycle.transition_idea(user, idea_id, Idea.Status.SUBMITTED)


# --- comments (S2-005) -------------------------------------------------------------


def _validate_comment_content(content: str | None) -> str:
    """
    The content to store, or a refusal.

    Normalized by stripping the ends and folding CRLF to LF, and nothing else.
    The strip is the same normalization every other content field gets, and it
    is what makes a whitespace-only comment detectable at all - `"   "` is
    truthy, so a blank check on the raw value would accept it. The newline fold
    is because the frontend's control is a `<textarea>`, and a browser on
    Windows submits `\\r\\n`; storing that unchanged means the same comment
    renders with stray carriage returns everywhere else.

    Internal whitespace is **not** touched. Collapsing runs of spaces would
    destroy the indentation of a pasted code block or a bulleted list, and
    rewriting somebody's comment into tidier prose is not this module's
    decision to make.

    No truncation, ever - see `MAX_COMMENT_LENGTH`.
    """
    normalized = (content or '').replace('\r\n', '\n').replace('\r', '\n').strip()

    if not normalized:
        raise IdeaError('Write something before posting.', field='content')
    if len(normalized) > MAX_COMMENT_LENGTH:
        raise IdeaError(
            f'A comment must be {MAX_COMMENT_LENGTH} characters or fewer.',
            field='content',
        )
    return normalized


def _discussion_is_open(idea: Idea) -> bool:
    """
    Whether `idea` accepts a new comment, asked of `ideas.lifecycle`.

    A one-line forwarder, and it exists only because of the import direction:
    `ideas.lifecycle` imports this module's validators, so importing it back at
    module scope would be a cycle. Same pattern and same reasoning as
    `submit_idea` below, and the rule itself stays in the lifecycle next to the
    transition matrix it is derived from - this is a bridge, not a second
    answer to "is this discussion open?".
    """
    from ideas import lifecycle

    return lifecycle.discussion_is_open(idea)


def _load_discussable_idea(user: User, idea_id: object) -> Idea:
    """
    The idea to comment on, or a refusal.

    Two gates, in this order, and the order is the security property: the
    caller must be able to **read** the idea (so a comment can never be attached
    to an idea the author of the comment was never shown), and only then may
    the idea's discussion state be consulted.

    Every refusal is "Idea is unavailable." for the same reason the idea
    operations use one message: no such id, another tenant's idea and a
    private idea are one answer, so the argument is not a probe for which idea
    ids exist.
    """
    # Through the public selector, not a raw lookup: this operation must not be
    # able to comment on an idea `get_idea` would not return.
    idea = selectors.get_idea(user, idea_id)
    if idea is None:
        raise IdeaError('Idea is unavailable.', reason='forbidden')

    if not _discussion_is_open(idea):
        # Named plainly rather than as "unavailable": the reader can already
        # see the idea, so a generic refusal would tell somebody they already
        # know less than the truth, and the state that closed the discussion is
        # public information carried by the idea itself.
        raise IdeaError(
            'This idea is no longer open for discussion.',
            reason='discussion_closed',
        )

    return idea


def _load_owned_comment(user: User, comment_id: object) -> Comment:
    """
    The comment identified by `comment_id`, if `user` may change it right now.

    Covered by one refusal message: no such id, a comment on an idea the caller
    may not read, and somebody else's comment. Authorship is the only way past.

    No membership re-check, unlike `_load_owned_draft`, and the difference is
    deliberate rather than an oversight. Editing an *idea* is writing into a
    tenant, so leaving that tenant has to end it. A comment was never a write
    into a tenant in the first place - it follows the idea's own visibility -
    so the same gate would be inventing a rule and would strand a comment on a
    `PUBLIC` idea that its author may still read after leaving an organization.
    """
    comment = selectors.get_comment(user, comment_id)
    if comment is None or comment.author_id != user.pk:
        raise IdeaError('Comment is unavailable.', reason='forbidden')
    return comment


def add_comment(user: User | None, idea_id: object, content: str) -> Comment:
    """
    Post a comment on an idea the caller may read.

    The author is `user` and the idea is resolved, authorized and used by this
    module - neither is an input, so no caller can post as somebody else or
    attach a comment to an idea it was not allowed to name. The status is not
    touched: a comment does not move the idea it is on.
    """
    active_user = _require_active_user(user)
    idea = _load_discussable_idea(active_user, idea_id)
    normalized = _validate_comment_content(content)

    return Comment.objects.create(idea=idea, author=active_user, content=normalized)


def update_comment(user: User | None, comment_id: object, content: str) -> Comment:
    """
    Edit the caller's own comment.

    The idea, the author and `created_at` are not inputs and are not assigned,
    so an edit cannot move a comment between ideas, reattribute it, or rewrite
    when it was posted. The visible edit time moves, and `updated_at` records
    it.
    """
    active_user = _require_active_user(user)
    comment = _load_owned_comment(active_user, comment_id)
    comment.content = _validate_comment_content(content)

    # `save()` runs `full_clean()`, so the model's own rule - no contentless
    # row - holds on this write path as well as on create.
    comment.save()
    return comment


def delete_comment(user: User | None, comment_id: object) -> None:
    """
    Remove the caller's own comment.

    A hard delete. There is no soft delete and no moderation state on the model
    (S2-001 declined to model a policy that does not exist), and a `CASCADE`
    from the idea takes the discussion with it. If moderation arrives it will
    arrive as a state, not as a repair of this row.
    """
    active_user = _require_active_user(user)
    comment = _load_owned_comment(active_user, comment_id)
    comment.delete()


# --- votes (S2-006) -----------------------------------------------------------------


def _require_readable_idea(user: User, idea_id: object) -> Idea:
    """
    The idea to act on, for an operation that only needs to *read* it.

    Shared by the vote operations, and it is the same gate the comment
    operations apply first - the visibility filter, resolved through
    `selectors.get_idea` so the rule is not restated here.

    Deliberately **no lifecycle check**, and the difference from
    `_load_discussable_idea` is a decision rather than an oversight. This
    domain's own rule for votes is that "a user may not vote on an idea they
    cannot read" (`docs/ideas-domain.md`), full stop - and there is no vote
    equivalent of `lifecycle.DISCUSSION_CLOSED_STATUSES`. So a rejected idea
    can still be voted on, and an approved one handed to the opportunities
    track most certainly can.

    The reason the two rules differ: a comment is participation in a
    *decision*, and a decision that is finished has nothing left to discuss;
    a vote is a statement of interest in the *idea*, which is a thing that
    outlives its own review state. Closing discussion on rejection while
    accepting votes on it is not an inconsistency - they answer different
    questions about the same object - but it is exactly the kind of thing that
    looks like an oversight in review, so it is written down here and pinned
    by `test_a_rejected_idea_can_still_be_voted_on`.
    """
    idea = selectors.get_idea(user, idea_id)
    if idea is None:
        raise IdeaError('Idea is unavailable.', reason='forbidden')
    return idea


def vote_for_idea(user: User | None, idea_id: object) -> Vote:
    """
    Record that `user` finds `idea` worth doing. One vote per user per idea.

    The voter is `user` and is never an input, so there is no request that can
    vote on somebody else's behalf. The idea is resolved, authorized and then
    used, so there is no request that can name an idea the voter was never
    shown.

    **Idempotent at the service level, enforced at the database level** - both
    halves, because they answer different races. The `get` below is the
    common case: a double-clicked button, a retried request, a client that
    replays on a timeout. The `unique_vote_per_user_idea` constraint is what
    answers the case a check-then-insert cannot: two *simultaneous* requests
    that both read "no vote" and both try to insert. The constraint is the
    final integrity boundary because it is the only thing that cannot be
    raced - a service-level check is a read followed by a write, and two of
    those interleave.

    So the loser of that race catches `IntegrityError` and reads the row the
    winner inserted, and both callers get the same vote back rather than one
    of them seeing an error for a state they asked for. `transaction.atomic()`
    around the insert is what makes that readable: without the inner block the
    failed statement would poison the outer transaction and the follow-up read
    would fail too.
    """
    active_user = _require_active_user(user)
    idea = _require_readable_idea(active_user, idea_id)

    existing = Vote.objects.filter(idea=idea, user=active_user).first()
    if existing is not None:
        return existing

    try:
        with transaction.atomic():
            return Vote.objects.create(idea=idea, user=active_user)
    except IntegrityError:
        # Lost the race to a concurrent insert. The constraint did its job, so
        # the state the caller asked for now exists - which makes this a
        # success, not a failure. Re-read rather than re-raise, and if the row
        # genuinely is not there then something other than the constraint
        # failed and the error belongs to the caller.
        concurrent = Vote.objects.filter(idea=idea, user=active_user).first()
        if concurrent is None:
            raise
        return concurrent


def remove_vote(user: User | None, idea_id: object) -> None:
    """
    Withdraw `user`'s vote on `idea`, if they have one.

    **Idempotent**: removing a vote that is not there succeeds and does
    nothing. A vote control is a toggle that a reader may well double-click,
    and a second click reporting "you have no vote to remove" would be an
    error about a state the reader already achieved. It leaks nothing either
    way, because "no vote of mine" is the same fact whether the row was never
    written or was written and removed.

    The visibility gate still applies in full, and that is the important part:
    this is idempotent *within* the set of ideas the caller may read. An idea
    they cannot read is refused, exactly as it is for voting - otherwise the
    idempotent branch would turn "unreadable idea" into a success and quietly
    confirm that the operation is available there.
    """
    active_user = _require_active_user(user)
    idea = _require_readable_idea(active_user, idea_id)

    # Scoped to the authenticated user rather than deleting by idea id: this
    # removes *your* vote and can never touch anybody else's, so a vote for
    # another user on the same idea is not even a row this statement can name.
    Vote.objects.filter(idea=idea, user=active_user).delete()


# --- attachments (S2-007) -----------------------------------------------------------


def _load_attachable_idea(user: User, idea_id: object) -> Idea:
    """
    The idea `user` may attach supporting evidence to right now.

    Authorship and an active membership - the same two conditions
    `_load_owned_draft` requires for editing a draft's content - but
    deliberately **no lifecycle gate**, and that is a considered choice, not
    an omission. Evidence is not the draft content itself; it accumulates
    while an idea is discussed and reviewed, not only while it is being
    written, so restricting uploads to `DRAFT` (as `_load_owned_draft` does
    for `update_idea`) would refuse the case this feature mostly exists for -
    attaching a screenshot or a spreadsheet once an idea is already under
    review. This follows the shape S2-006 established for votes
    (`_require_readable_idea`: readability with no status condition) rather
    than the shape S2-005 established for comments (closed on rejection):
    an idea's own evidence, like interest in it, outlives its review state.

    Resolved through `get_idea` first, so an idea the caller cannot even see
    is refused identically to one that does not exist, and only *then* is
    authorship checked - the same two-step order `_load_owned_draft` and
    `_load_discussable_idea` use, so a reviewer or a colleague cannot learn
    "this idea exists but isn't yours to attach to" about something they
    were never shown in the first place.
    """
    idea = selectors.get_idea(user, idea_id)
    if idea is None or idea.author_id != user.pk:
        raise IdeaError('Idea is unavailable.', reason='forbidden')

    # Re-checked on every write, not only at creation: leaving the
    # organization has to take the ability to attach evidence to it with you,
    # the same reasoning `_load_owned_draft` and `create_idea` apply.
    if authorization.get_membership(user, idea.organization_id) is None:
        raise IdeaError(
            'You must be an active member of this organization to work with ideas here.',
            reason='membership_required',
        )

    return idea


def _load_owned_attachment(user: User, attachment_id: object) -> Attachment:
    """
    The attachment `user` may delete right now, or a refusal.

    The gate is the *idea's* authorship, not the attachment's uploader,
    though today the two are always the same person: `upload_attachment`
    only ever accepts a file from an idea's own author (see
    `_load_attachable_idea`), so there is no second contributor whose
    uploads this idea's author would otherwise be deleting. Written this way
    rather than checking `attachment.uploaded_by_id` so the rule has one
    statement - "the author of this idea controls its evidence" - instead of
    two that happen to agree only because of how upload is gated.

    One refusal message for no such id, an attachment on another tenant's
    idea, and somebody else's idea's attachment alike - the same shape
    `_load_owned_comment` uses, so an attachment id is not an oracle for any
    of the three.
    """
    attachment = selectors.get_attachment(user, attachment_id)
    if attachment is None or attachment.idea.author_id != user.pk:
        raise IdeaError('Attachment is unavailable.', reason='forbidden')

    if authorization.get_membership(user, attachment.idea.organization_id) is None:
        raise IdeaError(
            'You must be an active member of this organization to work with ideas here.',
            reason='membership_required',
        )

    return attachment


def upload_attachment(user: User | None, idea_id: object, uploaded_file) -> Attachment:
    """
    Attach `uploaded_file` to an idea `user` owns, as supporting evidence.

    `uploaded_file` is a Django `UploadedFile` (from `request.FILES`,
    `ideas/views.py`'s only caller) - its `.name` is the display filename the
    browser sent, `.size` is measured by Django from the actual request body
    (not a header the client could lie about), and `.content_type` is **never
    read here**: this domain's own content type is derived from the file's
    validated extension and confirmed against its leading bytes, exactly the
    way `ideas.attachments`'s module docstring explains, so a browser's
    `multipart/form-data` claim never becomes stored data.

    The storage key is generated here, from the idea's own id, and is never a
    value `uploaded_file` or its caller supplies - see
    `ideas.storage.generate_storage_key`. That is the actual defense against
    a client choosing an arbitrary key or walking one attachment's bytes onto
    another's.

    **Ordering, and what happens if a step fails.** Validation runs entirely
    before anything touches storage, so a rejected file never reaches it.
    The object is then written to storage *before* the database row is
    created: if the row's own validation or the database itself then fails,
    the just-written object is deleted so storage and the database do not
    end up disagreeing about an attachment that was never actually recorded.
    The reverse order - row first, object second - would leave a *readable*
    attachment whose bytes do not exist the moment the storage write failed,
    which is a worse inconsistency than a storage object nothing points to.
    """
    active_user = _require_active_user(user)
    idea = _load_attachable_idea(active_user, idea_id)

    try:
        display_name = attachment_rules.safe_display_filename(uploaded_file.name)
        extension = attachment_rules.resolve_extension(display_name)
        attachment_rules.validate_size(uploaded_file.size)

        head = uploaded_file.read(attachment_rules.SNIFF_BYTES)
        uploaded_file.seek(0)
        attachment_rules.validate_content(extension, head)
    except attachment_rules.AttachmentValidationError as exc:
        raise IdeaError(exc.message, field=exc.field) from exc

    content_type = attachment_rules.canonical_content_type(extension)
    storage_key = storage.generate_storage_key(idea.pk, extension)

    try:
        saved_key = storage.save_object(storage_key, uploaded_file)
    except storage.AttachmentStorageError as exc:
        raise IdeaError('The file could not be stored. Please try again.') from exc

    try:
        return Attachment.objects.create(
            idea=idea,
            uploaded_by=active_user,
            filename=display_name,
            content_type=content_type,
            size=uploaded_file.size,
            storage_key=saved_key,
        )
    except DatabaseError:
        # The row never made it in, so nothing refers to the object that was
        # just written - clean it up rather than leaving an orphan storage
        # never has to know it must reconcile.
        storage.delete_object(saved_key)
        raise


def delete_attachment(user: User | None, attachment_id: object) -> None:
    """
    Remove one of the caller's own idea's attachments - both the metadata row
    and its storage object.

    **Not idempotent**, deliberately matching `delete_comment`'s shape rather
    than the votes' toggle shape: an attachment is a distinct piece of
    evidence, not a per-user flag, so "delete it again" names something that
    is no longer there, and `_load_owned_attachment` refuses it the same way
    a second `deleteComment` on an already-deleted comment is refused.

    **Database row first, storage object second.** Deleting the row and then
    best-effort deleting the object (`storage.delete_object` never raises for
    a missing or already-gone key, and logs rather than raising for a genuine
    backend failure) means a storage-side failure here leaves an orphaned
    object with nothing pointing to it - wasted space, cleaned up later - not
    a database row that still claims to exist while its bytes are already
    gone. The reverse order would risk exactly that worse outcome if the
    database delete then failed after the object was already removed.
    """
    active_user = _require_active_user(user)
    attachment = _load_owned_attachment(active_user, attachment_id)

    storage_key = attachment.storage_key
    attachment.delete()
    storage.delete_object(storage_key)
