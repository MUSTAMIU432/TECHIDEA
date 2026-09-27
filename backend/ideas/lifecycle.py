"""
The idea lifecycle: which status may follow which, and who may make it so
(S2-003).

One table, in one place, that answers two questions at once:

    (current status, target status) -> who is allowed to make that move

Everything about a status change goes through `transition_idea` here. There
is no other way to reach a non-`DRAFT` status, and there is deliberately no
`status` field on any write input, so "set my idea to Approved" is not an
operation this codebase has the vocabulary for. A client that sends a status
anywhere gets a GraphQL error or a refusal; it cannot talk past the matrix.

Why a table rather than a chain of `if`s
----------------------------------------
The permitted pairs are a property of the *domain*, not of any one operation,
and there are seven of them across two kinds of actor. Written as scattered
conditionals they would be spread over several methods, each with its own idea
of who may act, and a pair added to one would be missing from another - the
failure mode being a lifecycle that accepts a move in one code path and refuses
it in another. As a table it can be read top to bottom, asserted in one test
(`test_the_matrix_is_exactly_the_documented_one`), and rendered to the client
(`available_transitions`) from the same source that enforces it.

The two kinds of actor
----------------------
- **AUTHOR** - the person who wrote the idea, who must still hold an active
  membership. Before review begins the lifecycle belongs to them: they submit
  a finished draft, and they re-submit after a reviewer asks for changes.
- **REVIEWER** - an active member who holds a *system* role in the idea's own
  organization, and who is **not** the author. Everything from `SUBMITTED`
  onward belongs to them.

The author exclusion is the point of the split. Without it, an author who also
holds the Owner role - the common case, since bootstrap makes everybody's
first organization their own - would review and approve their own idea, and no
amount of role checking would catch it, because the role is genuinely held.
Self-review is refused structurally rather than by hoping the two roles go to
different people.

A custom, non-system "Reviewer" role can be granted later without touching
this module: see `organizations.authorization.membership_holds_system_role`
for why the gate is a system role today, and for the one function to replace
if that trade changes.

`submitted_at` is written exactly once
---------------------------------------
On the first `DRAFT → SUBMITTED`, and never again. A re-submission after
`CHANGES_REQUESTED` keeps the original timestamp, because "when was this first
put forward" is an audit fact a second submission does not change - and
because the model enforces that anything past `DRAFT` has a timestamp, so
clearing it on the way back would be an inconsistent row. The model's own
`clean()` enforces both halves of that invariant on this write path, which is
why the transition writes `status` and `submitted_at` together and lets
`save()` check the pair.
"""

from django.db import transaction
from django.utils import timezone

from ideas import selectors
from ideas.models import Idea
from ideas.services import (
    IdeaError,
    _require_active_user,
    _require_membership,
    _validate_for_submission,
)
from identity.models import User
from organizations import authorization

# The two kinds of actor, named so the matrix below reads as a rule rather than
# as a bare boolean.
AUTHOR = 'author'
REVIEWER = 'reviewer'

# (from, to) -> required actor. This table *is* the lifecycle; anything not
# listed here is not a transition, and `transition_idea` refuses it.
TRANSITIONS: dict[tuple[str, str], str] = {
    # The author puts a finished idea forward.
    (Idea.Status.DRAFT, Idea.Status.SUBMITTED): AUTHOR,
    # A reviewer picks it up, then resolves it one of three ways.
    (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW): REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED): REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED): REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED): REVIEWER,
    # The author answers the requested changes and puts it forward again.
    (Idea.Status.CHANGES_REQUESTED, Idea.Status.SUBMITTED): AUTHOR,
    # An approved idea is handed to the automation-opportunity track. The
    # transition exists so the lifecycle is complete and so a later sprint is
    # behaviour rather than a migration; nothing in this repository acts on an
    # idea once it is in that state, and no UI offers the move.
    (Idea.Status.APPROVED, Idea.Status.AUTOMATION_PROPOSAL): REVIEWER,
}

# Every status a client may *name* as a target. All of them are accepted here
# and then checked against the matrix, so an unreachable target is refused by
# the same code path as an illegal pair - which means the error can say which
# move was refused, and a client cannot use a type error to map the states
# that exist.
TARGET_STATUSES = tuple(Idea.Status.values)


def _status_label(status: str) -> str:
    return Idea.Status(status).label


def is_reviewer(user: User | None, idea: Idea) -> bool:
    """
    Whether `user` may act as a reviewer on `idea`.

    Convenience predicate with the same meaning as the REVIEWER half of
    `_actor_may`, for callers that want the answer on its own. Both go through
    `authorization`, so "active member" and "holds a system role" mean exactly
    what they mean everywhere else in the platform.
    """
    if user is None or not user.is_active:
        return False
    if idea.author_id == getattr(user, 'pk', None):
        return False
    membership = authorization.get_membership(user, idea.organization_id)
    return authorization.membership_holds_system_role(membership)


def _actor_may(
    user: User, idea: Idea, membership, to_status: str, *, require_legal_pair: bool = True
) -> bool:
    """
    Whether this actor may make *this particular* move. The single place the
    actor rule is written; `can_transition` and `transition_idea` both defer to
    it, so "what the UI offers" and "what the server accepts" cannot diverge.

    `require_legal_pair=False` asks the actor question alone, which is how
    `transition_idea` tells "you are the wrong actor for this move" apart from
    "this move does not exist" - two different refusals that deserve two
    different messages, and which must not be collapsed: the first is about the
    caller and the second is about the idea.
    """
    required_actor = TRANSITIONS.get((idea.status, to_status))
    if required_actor is None:
        # Not a legal move at all.
        return not require_legal_pair

    if required_actor == AUTHOR:
        return idea.author_id == user.pk

    if idea.author_id == user.pk:
        # A person never reviews their own idea, even holding the role that
        # would otherwise let them.
        return False

    return authorization.membership_holds_system_role(membership)


def _illegal_transition_error(idea: Idea, to_status: str) -> IdeaError:
    """
    The refusal for a move the lifecycle does not contain.

    One case keeps S2-002's exact wording. `submitIdea` shipped the string
    "Only a draft can be edited." for an idea that was not a draft, the
    frontend shows the backend's message to the user verbatim, and a plain
    status-pair sentence ("An idea cannot go from Submitted to Submitted") is a
    worse thing to put in front of somebody who has just clicked Submit twice.
    It is also true: only a draft is editable, and only a draft or a
    changes-requested idea can be put forward.
    """
    if to_status == Idea.Status.SUBMITTED and idea.status != Idea.Status.DRAFT:
        return IdeaError('Only a draft can be edited.')
    return IdeaError(
        f'An idea cannot go from {_status_label(idea.status)} to {_status_label(to_status)}.'
    )


def can_transition(user: User | None, idea: Idea, to_status: str) -> bool:
    """
    Whether `user` may move `idea` to `to_status` from where it is now.

    The whole rule, without side effects and without a write - so it is
    `available_transitions`' only input and the reason the UI's idea of what is
    possible is the server's.
    """
    if user is None or not user.is_active:
        return False

    membership = authorization.get_membership(user, idea.organization_id)
    if membership is None:
        return False

    return _actor_may(user, idea, membership, str(to_status or '').strip().lower())


def available_transitions(user: User | None, idea: Idea) -> list[str]:
    """
    The statuses `user` may move this idea to right now, in lifecycle order.

    Rendered to the client so the UI offers exactly the moves the server would
    accept. A convenience, never a control: the same matrix is enforced again
    inside `transition_idea`, and a client that ignores this and posts a
    different target gets the refusal it would have got anyway.
    """
    return [to_status for to_status in TARGET_STATUSES if can_transition(user, idea, to_status)]


def transition_idea(user: User | None, idea_id: object, to_status: str) -> Idea:
    """
    Move an idea to `to_status`, if the lifecycle permits it and this actor may.

    Every check runs inside one transaction with the row locked, so two
    concurrent transitions of the same idea cannot both read the same starting
    status and both succeed: the second waits, then re-reads a status that no
    longer permits its move and is refused. Without the lock, "approve" and
    "reject" fired at once would both apply and the last write would silently
    win - which is exactly the sort of thing that is invisible in review and
    obvious afterwards.

    The order of the refusals is deliberate, and it is what keeps the operation
    from being an existence oracle:

    1. an unauthenticated or deactivated caller is refused as such;
    2. an idea the caller cannot *see* is refused exactly like one that does
       not exist - which also means a reviewer cannot act on a `PRIVATE` idea
       they were never shown, because lifecycle and visibility agree on what
       exists as far as this caller is concerned;
    3. the actor check, and only then
    4. whether the move itself is in the matrix.
    """
    active_user = _require_active_user(user)

    normalized_target = str(to_status or '').strip().lower()
    if normalized_target not in TARGET_STATUSES:
        raise IdeaError('That is not a state an idea can be in.')

    with transaction.atomic():
        # A locked read *inside* the transaction, through the same visibility
        # filter every other read uses.
        idea = selectors.get_idea_for_update(active_user, idea_id)
        if idea is None:
            raise IdeaError('Idea is unavailable.', reason='forbidden')

        # Membership is re-checked per transition rather than taken from the
        # selector: leaving the organization has to take the ability to act in
        # it with you, and the selector decides what is *readable*, not what is
        # *writable*.
        membership = _require_membership(active_user, idea.organization_id)

        # Actor first, then the matrix - but each is asked *alone*, so the two
        # refusals stay distinguishable: an ordinary member pressing Approve on
        # a colleague's submitted idea is told they may not, and only somebody
        # who could legitimately have made the move is told the move does not
        # exist. Neither message reveals anything about ideas the caller cannot
        # see, because the visibility check above already answered those.
        if not _actor_may(
            active_user, idea, membership, normalized_target, require_legal_pair=False
        ):
            raise IdeaError('You are not allowed to make that change to this idea.')

        if (idea.status, normalized_target) not in TRANSITIONS:
            raise _illegal_transition_error(idea, normalized_target)

        if normalized_target == Idea.Status.SUBMITTED:
            # Applies to the first submission and to a re-submission alike: an
            # idea whose description was emptied while addressing review
            # feedback is no more ready the second time than the first.
            _validate_for_submission(idea)

        if idea.status == Idea.Status.DRAFT and idea.submitted_at is None:
            idea.submitted_at = timezone.now()

        idea.status = normalized_target
        # `save()` runs `full_clean()`, so the model's own
        # status/submitted_at invariant is checked on this path like every
        # other write.
        idea.save()

    return idea
