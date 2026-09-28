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
- **REVIEWER** - an active member who holds the `idea.review` permission in
  the idea's own organization, and who is **not** the author. Everything from
  `SUBMITTED` onward belongs to them.

The author exclusion is the point of the split. Without it, an author who also
holds the Owner role - the common case, since bootstrap makes everybody's
first organization their own - would review and approve their own idea, and no
amount of role checking would catch it, because the role is genuinely held.
Self-review is refused structurally rather than by hoping the two roles go to
different people.

Until S3-002 the gate was "holds a system role", which meant only an Owner
could review. It is now the `idea.review` permission
(`organizations.authorization.IDEA_REVIEW`), checked through the same
`membership_has_permission` every other capability uses. The Owner role holds
it, so nobody who could review before lost the ability, and the non-system
Reviewer role holds it, so reviewing can be granted without ownership. See
`docs/reviews-domain.md` §6.

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

import logging

from django.db import transaction
from django.utils import timezone

from ideas import selectors
from ideas.models import Idea, IdeaTransition
from ideas.services import (
    IdeaError,
    _require_active_user,
    _require_membership,
    _validate_for_submission,
)
from identity.models import User
from organizations import authorization

logger = logging.getLogger(__name__)

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

# The moves a review makes (S3-004). Starting a review and deciding one are
# *review operations*: each must leave a `Review` record, written in the same
# transaction as the status change. So these pairs are refused by the public
# `transition_idea` and are reachable only through `apply_review_transition`,
# which the Reviews domain calls inside that transaction. The matrix above is
# unchanged - these are still the lifecycle's moves and still need a reviewer;
# what changes is that they can no longer be made without a review.
# `APPROVED -> AUTOMATION_PROPOSAL` is not here: no review is open by then.
REVIEW_OWNED_TRANSITIONS = frozenset(
    {
        (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW),
        (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED),
        (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED),
        (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED),
    }
)

# Every status a client may *name* as a target. All of them are accepted here
# and then checked against the matrix, so an unreachable target is refused by
# the same code path as an illegal pair - which means the error can say which
# move was refused, and a client cannot use a type error to map the states
# that exist.
TARGET_STATUSES = tuple(Idea.Status.values)

# The states in which an idea's discussion is closed (S2-005).
#
# One entry, and it is here because of what the table above already says:
# `REJECTED` has no outgoing transition, so it is a state the lifecycle cannot
# leave. A comment on a rejected idea has no future move to inform - the idea
# will not be reviewed again and nothing in the domain can put it back - so
# accepting one is accepting discussion of a decision that is already final.
#
# The invariant, asserted in
# `test_every_closed_state_is_a_terminal_one`, is that a state the lifecycle
# can still leave is never closed. The converse deliberately does not hold:
# `AUTOMATION_PROPOSAL` is terminal here too, and stays **open**, because being
# terminal within this app is not the same as being finished with - that idea
# was handed to the automation-opportunity track, where it is very much alive,
# and closing its discussion would cut off exactly the conversation a handoff
# invites. `REJECTED` is closed because it is a verdict, not because it is
# terminal; the two are related here but they are not the same claim.
#
# Every other state is open, deliberately including `DRAFT`. A draft is the
# author's working copy and participation in it is participation in writing it,
# not in reviewing it; refusing comments there would be inventing a policy this
# domain has never stated, and would break the ordinary case of a colleague
# asking a question while an idea is still being written. The gate that always
# exists is the one that always has: the caller must be able to *read* the
# idea.
#
# This is a set rather than a flag on the idea, so "which states are open" has
# exactly one answer in the codebase.
DISCUSSION_CLOSED_STATUSES = frozenset({Idea.Status.REJECTED})


def discussion_is_open(idea: Idea) -> bool:
    """
    Whether `idea`'s discussion accepts a new comment right now.

    The rule for *adding* to a discussion, and nothing else. Retracting or
    editing a comment somebody already wrote stays available in a closed
    discussion: taking something back is not participating in the discussion,
    and a reader must never be able to lock a colleague out of their own words.
    """
    return idea.status not in DISCUSSION_CLOSED_STATUSES


def _status_label(status: str) -> str:
    return Idea.Status(status).label


def is_reviewer(user: User | None, idea: Idea) -> bool:
    """
    Whether `user` may act as a reviewer on `idea`.

    Convenience predicate with the same meaning as the REVIEWER half of
    `_actor_may`, for callers that want the answer on its own. Both go through
    `authorization`, so "active member" and "holds `idea.review`" mean exactly
    what they mean everywhere else in the platform.
    """
    if user is None or not user.is_active:
        return False
    if idea.author_id == getattr(user, 'pk', None):
        return False
    membership = authorization.get_membership(user, idea.organization_id)
    return authorization.membership_has_permission(membership, authorization.IDEA_REVIEW)


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
        # A person never reviews their own idea, even holding the permission
        # that would otherwise let them.
        return False

    return authorization.membership_has_permission(membership, authorization.IDEA_REVIEW)


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

    Review-owned moves are never listed: `transitionIdea` refuses them, and a
    client offers them from the review capability fields instead.
    """
    return [
        to_status
        for to_status in TARGET_STATUSES
        if (idea.status, to_status) not in REVIEW_OWNED_TRANSITIONS
        and can_transition(user, idea, to_status)
    ]


def _transition_idea(user: User | None, idea_id: object, to_status: str) -> Idea:
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

        # After the actor check, so only somebody who could have made the move
        # learns that it is made elsewhere.
        if (idea.status, normalized_target) in REVIEW_OWNED_TRANSITIONS:
            raise IdeaError(REVIEW_OWNED_MESSAGE)

        if (idea.status, normalized_target) not in TRANSITIONS:
            raise _illegal_transition_error(idea, normalized_target)

        if normalized_target == Idea.Status.SUBMITTED:
            # Applies to the first submission and to a re-submission alike: an
            # idea whose description was emptied while addressing review
            # feedback is no more ready the second time than the first.
            _validate_for_submission(idea)

        if idea.status == Idea.Status.DRAFT and idea.submitted_at is None:
            idea.submitted_at = timezone.now()

        from_status = idea.status
        idea.status = normalized_target
        # `save()` runs `full_clean()`, so the model's own
        # status/submitted_at invariant is checked on this path like every
        # other write.
        idea.save()
        _record_transition(idea, from_status, active_user)

    return idea


def _record_transition(idea: Idea, from_status: str, actor: User) -> None:
    """
    Append the audit row for a status change that has just been saved (S3-007).

    Called by both write paths, inside the transaction that saved the status,
    so the row and the change commit together or not at all. `actor` is the
    caller the lifecycle has just authorized - never a value from a request.
    """
    IdeaTransition.objects.create(
        idea=idea, from_status=from_status, to_status=idea.status, actor=actor
    )


def _log_refusal(user: User | None, idea_id: object, to_status: str, exc: IdeaError) -> None:
    """
    A refused move, recorded in the log rather than the database (D-10): ids and
    the refusal reason only, never content, so the log is safe to ship.
    """
    logger.info(
        'Refused idea transition (user=%s, idea=%s, to=%s, reason=%s).',
        getattr(user, 'pk', None),
        idea_id,
        to_status,
        exc.reason,
    )


REVIEW_OWNED_MESSAGE = (
    'Reviews are started and decided in the review workspace, not by changing the status directly.'
)


def _apply_review_transition(user: User, idea: Idea, to_status: str) -> Idea:
    """
    Make one review-owned move on an idea the caller has already locked.

    The only way to reach the pairs in `REVIEW_OWNED_TRANSITIONS`, and it has
    exactly one caller: `reviews.services`, which writes the `Review` record in
    the same transaction. It opens no transaction of its own and refuses to run
    outside one, because its whole contract is "the review and the status
    commit together or not at all" - the caller's transaction is that contract.

    `idea` must have been read with `selectors.get_idea_for_update` inside the
    caller's transaction, so the status checked here is the locked one. The
    rules are the same as `transition_idea`'s: an active membership, the
    REVIEWER actor rule (which refuses the author), and a pair in the matrix.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('apply_review_transition must run inside a transaction.')

    normalized_target = str(to_status or '').strip().lower()
    if (idea.status, normalized_target) not in REVIEW_OWNED_TRANSITIONS:
        raise _illegal_transition_error(idea, normalized_target)

    membership = _require_membership(user, idea.organization_id)
    if not _actor_may(user, idea, membership, normalized_target):
        raise IdeaError('You are not allowed to make that change to this idea.')

    from_status = idea.status
    idea.status = normalized_target
    idea.save()
    # In the caller's transaction, alongside the `Review` it writes: a
    # completion that rolls back takes this row with it.
    _record_transition(idea, from_status, user)
    return idea


def transition_idea(user: User | None, idea_id: object, to_status: str) -> Idea:
    """
    Move an idea to `to_status`, if the lifecycle permits it and this actor may.

    See `_transition_idea` for the rules and the order of the refusals. This
    wrapper adds only the audit of refusals: a successful move is recorded as
    an `IdeaTransition` inside the transaction, a refused one in the log.
    """
    try:
        return _transition_idea(user, idea_id, to_status)
    except IdeaError as exc:
        _log_refusal(user, idea_id, to_status, exc)
        raise


def apply_review_transition(user: User, idea: Idea, to_status: str) -> Idea:
    """
    Make one review-owned move on an idea the caller has already locked.

    See `_apply_review_transition` for the contract. Like `transition_idea`,
    a refusal is logged; a success is recorded as an `IdeaTransition` in the
    caller's transaction.
    """
    try:
        return _apply_review_transition(user, idea, to_status)
    except IdeaError as exc:
        _log_refusal(user, idea.pk, to_status, exc)
        raise
