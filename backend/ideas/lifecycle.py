"""
The idea lifecycle: which status may follow which, and who may make it so.

One table, in one place, that answers two questions at once:

    (current status, target status) -> who is allowed to make that move

Everything about a status change goes through `transition_idea` here. There is
no other way to reach a non-`DRAFT` status, and there is deliberately no
`status` field on any write input, so "set my idea to Approved" is not an
operation this codebase has the vocabulary for. A client that sends a status
anywhere gets a GraphQL error or a refusal; it cannot talk past the matrix.

Three kinds of actor
--------------------
The split the product asks for, and the reason each is separate:

- **AUTHOR** - the person who wrote the idea. Before a review begins the
  lifecycle belongs to them: they submit a finished draft, they answer review
  feedback and put it forward again, and they give the go-ahead on an approved
  idea. Nothing here ever lets them review it.
- **ORGANIZATION_REVIEWER** - an active member of the idea's *own organization*
  holding `idea.review` there, who is not its author. They may only confirm or
  ask the organization for changes, and only for an ORGANIZATION-context idea.
  Their decision is an organizational fact - "this is what we want to submit" -
  and it is deliberately *not* an approval.
- **PLATFORM_REVIEWER** - an independent account holding the platform-scoped
  `administration.review_platform_submissions` permission. They may approve,
  request changes or reject. **No organization role and no team role can reach
  this column**, which is the structural form of "platform reviewers are
  independent": it is not a check that could be forgotten, it is a permission
  that cannot be granted from an organization.

The author exclusion applies to both reviewer kinds. A person never reviews their
own idea, even holding every permission that would otherwise let them, because
bootstrap makes everybody's first organization their own and so the common case
is an Owner holding `idea.review` on the very idea they wrote.

Context-aware entry
-------------------
The matrix is context-free but the *first hop* is not, and `first_submission_target`
is the one place that knows it: an ORGANIZATION-context idea is validated by its
organization before the platform ever sees it, while an INDIVIDUAL or TEAM idea
has no organization to validate it and goes straight to the platform. Both enter
the platform track at exactly the same status, so nothing downstream has to ask
what kind of idea this is.

Locking and versions
--------------------
Entering `SUBMITTED` freezes the content into an `ideas.IdeaSubmissionVersion`
and sets `platform_locked_at`, in the same transaction as the status change
(`ideas.versions.freeze_submission`). From then on the working copy stays editable
while the author answers feedback, and the submission the platform is holding
does not move - which is what "the official submission is locked" means here.
"""

import logging

from django.db import transaction
from django.utils import timezone

from ideas import go_ahead, selectors, versions
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
# The three kinds of actor, named so the matrix below reads as a rule rather
# than as a bare boolean.
AUTHOR = 'author'
ORGANIZATION_REVIEWER = 'organization_reviewer'
PLATFORM_REVIEWER = 'platform_reviewer'

# (from, to) -> required actor. This table *is* the lifecycle; anything not
# listed here is not a transition, and `transition_idea` refuses it.
TRANSITIONS: dict[tuple[str, str], str] = {
    # --- the author puts a finished idea forward ------------------------------
    # Which of these two is available depends on the submission context, and
    # `first_submission_target` is the single place that knows it: an
    # organization validates its own ideas first.
    (Idea.Status.DRAFT, Idea.Status.SUBMITTED_TO_ORGANIZATION): AUTHOR,
    (Idea.Status.DRAFT, Idea.Status.SUBMITTED): AUTHOR,
    # The author answers the organization's requested changes and puts it back
    # in front of them.
    (Idea.Status.ORGANIZATION_CHANGES_REQUESTED, Idea.Status.SUBMITTED_TO_ORGANIZATION): AUTHOR,
    # The organization confirmed it; the *owner*, and only the owner, submits it
    # to the platform. This is the step that makes an organizational fact and a
    # platform submission two separate events.
    (Idea.Status.ORGANIZATION_CONFIRMED, Idea.Status.SUBMITTED): AUTHOR,
    # --- the organization reviewer --------------------------------------------
    # Two outcomes, and neither is an approval: the organization either asks for
    # changes or confirms that this is what it wants to submit.
    (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CHANGES_REQUESTED): (
        ORGANIZATION_REVIEWER
    ),
    (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CONFIRMED): (
        ORGANIZATION_REVIEWER
    ),
    # --- the platform reviewer ------------------------------------------------
    # Independent of every organization and team role; see the module docstring.
    (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW): PLATFORM_REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED): PLATFORM_REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED): PLATFORM_REVIEWER,
    (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED): PLATFORM_REVIEWER,
    # The author answers the platform's requested changes and resubmits. This
    # creates a NEW submission version rather than overwriting the frozen one -
    # see `ideas.versions`.
    (Idea.Status.CHANGES_REQUESTED, Idea.Status.SUBMITTED): AUTHOR,
    # --- the owner's go-ahead --------------------------------------------------
    # The only move out of `APPROVED`, and it is the author's alone. Platform
    # approval is not owner go-ahead, so this pair exists to make the difference
    # a state rather than an assumption.
    (Idea.Status.APPROVED, Idea.Status.READY_FOR_IMPLEMENTATION): AUTHOR,
    # An approved idea is handed to the automation-opportunity track. The
    # transition exists so the lifecycle is complete and so a later sprint is
    # behaviour rather than a migration; nothing in this repository acts on an
    # idea once it is in that state, and no UI offers the move. In Sprint 4 it
    # follows `READY_FOR_IMPLEMENTATION` rather than `APPROVED`.
    (Idea.Status.READY_FOR_IMPLEMENTATION, Idea.Status.AUTOMATION_PROPOSAL): PLATFORM_REVIEWER,
}

# The moves a review makes. Starting a review and deciding one are *review
# operations*: each must leave a `Review` record, written in the same transaction
# as the status change. So these pairs are refused by the public
# `transition_idea` and are reachable only through `apply_review_transition`,
# which the Reviews domain calls inside that transaction. The matrix above is
# unchanged - these are still the lifecycle's moves and still need a reviewer of
# the right kind; what changes is that they can no longer be made without a
# review.
# `APPROVED -> AUTOMATION_PROPOSAL` is not here: no review is open by then, and
# `APPROVED -> READY_FOR_IMPLEMENTATION` is the owner's, not a reviewer's.
REVIEW_OWNED_TRANSITIONS = frozenset(
    {
        (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CHANGES_REQUESTED),
        (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CONFIRMED),
        (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW),
        (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED),
        (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED),
        (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED),
    }
)

# Which review scope owns each review-owned move. Read by `apply_review_transition`
# so a platform decision can never be recorded as an organization one (or the
# reverse) even if a caller asks for it: the row the review writes carries the
# scope, and this is where the lifecycle and the review agree on it.
TRANSITION_REVIEW_SCOPE: dict[tuple[str, str], str] = {
    (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CHANGES_REQUESTED): (
        'organization'
    ),
    (Idea.Status.SUBMITTED_TO_ORGANIZATION, Idea.Status.ORGANIZATION_CONFIRMED): 'organization',
    (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW): 'platform',
    (Idea.Status.UNDER_REVIEW, Idea.Status.CHANGES_REQUESTED): 'platform',
    (Idea.Status.UNDER_REVIEW, Idea.Status.APPROVED): 'platform',
    (Idea.Status.UNDER_REVIEW, Idea.Status.REJECTED): 'platform',
}

# The status an idea enters the platform track at, by submission context.
#
# The one place that knows an organization validates its own ideas before the
# platform sees them. Everything from `SUBMITTED` onwards is identical for all
# three contexts, so no later code has to ask what kind of idea it is looking at.
FIRST_SUBMISSION_TARGET: dict[str, str] = {
    Idea.SubmissionContext.ORGANIZATION: Idea.Status.SUBMITTED_TO_ORGANIZATION,
    Idea.SubmissionContext.INDIVIDUAL: Idea.Status.SUBMITTED,
    Idea.SubmissionContext.TEAM: Idea.Status.SUBMITTED,
}

# Where an idea comes back to after each track asks for changes. Both are the
# author's, so a resubmission is one rule rather than two.
RESUBMISSION_TARGET: dict[str, str] = {
    Idea.Status.ORGANIZATION_CHANGES_REQUESTED: Idea.Status.SUBMITTED_TO_ORGANIZATION,
    Idea.Status.CHANGES_REQUESTED: Idea.Status.SUBMITTED,
}

# The target for "put this idea forward", whatever state it is in. The single
# answer to "what does Submit mean right now", which is what lets the frontend
# offer one button whose label comes from `ideas.states` and whose target comes
# from here.
RESUBMISSION_TARGETS: frozenset[str] = frozenset(RESUBMISSION_TARGET)

# Every status that means "this idea is now being put forward", and therefore
# every one that requires the content and visibility completeness rule. Grouping
# them here rather than testing for `SUBMITTED` alone is what keeps the
# organization stage from skipping a rule the platform stage applies - the two
# stages submit the same idea, so they must accept the same idea.
SUBMISSION_TARGETS: frozenset[str] = frozenset({Idea.Status.SUBMITTED}) | frozenset(
    FIRST_SUBMISSION_TARGET.values()
)

# The statuses that only exist because an organization validates its own ideas.
#
# A status is the wrong thing to ask "is this idea in the organization track?"
# about - `SUBMITTED` is shared by all three contexts, which is the whole point
# of the split - but these three are not shared by anything. They exist for the
# organization stage and mean nothing at all for a team or an individual idea,
# so an idea that finds itself in one of them is an organization idea by
# definition and any other context reaching one is a bug, not a choice.
ORGANIZATION_TRACK_STATUSES: frozenset[str] = frozenset(
    {
        Idea.Status.SUBMITTED_TO_ORGANIZATION,
        Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
        Idea.Status.ORGANIZATION_CONFIRMED,
    }
)

# The refusal for a move that is legal for one submission context and not for
# this one. Deliberately about the *idea* and not the caller: the person asking
# is the author, who is allowed to submit, and the thing they cannot do yet is
# skip a stage their organization owns.
CONTEXT_MISMATCH = 'Your organization has to confirm this idea before it can go to the platform.'


def context_allows(idea: Idea, to_status: str) -> bool:
    """
    Whether this idea's submission context permits this particular move.

    **Why the matrix alone is not enough.** The table above is decided over
    statuses, and `DRAFT -> SUBMITTED` appears in it once - as "an individual or
    team author submits their idea". For an *organization* author the same pair
    would be a way to put an idea in front of the platform without its
    organization ever seeing it, which would make the entire organization stage
    optional and every organization-review guarantee in this module decorative.
    So each pair is legal only for the contexts it means.

    Checked in the same two places as the actor rule - `can_transition`, which
    renders what the UI offers, and the two write paths, which decide what is
    accepted - so an idea cannot be offered a move that would be refused, and
    cannot be moved by one that would have been.
    """
    if idea is None:
        return False

    is_organization = idea.submission_context == Idea.SubmissionContext.ORGANIZATION
    target = str(to_status or '').strip().lower()

    if not is_organization and (
        idea.status in ORGANIZATION_TRACK_STATUSES or target in ORGANIZATION_TRACK_STATUSES
    ):
        return False

    if (idea.status, target) == (Idea.Status.DRAFT, Idea.Status.SUBMITTED):
        return not is_organization

    return True


def first_submission_target(idea: Idea) -> str:
    """The status this idea's first submission goes to, given its context."""
    return FIRST_SUBMISSION_TARGET.get(idea.submission_context, Idea.Status.SUBMITTED)


def resubmission_target(idea: Idea) -> str | None:
    """
    The status a resubmission of `idea` goes to, or `None` if it is not a
    resubmission.

    `DRAFT` is included as a resubmission so the existing "submit my idea"
    operation keeps working through one rule: an author always presses the same
    button, and the lifecycle decides which door it opens.
    """
    if idea.status == Idea.Status.DRAFT:
        return first_submission_target(idea)
    return RESUBMISSION_TARGET.get(idea.status)


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


def is_organization_reviewer(user: User | None, idea: Idea) -> bool:
    """
    Whether `user` may act as the idea's **organization** reviewer.

    An active member of the idea's own organization holding `idea.review` there,
    who is not the idea's author, and only ever for an idea their organization
    actually owns. The last clause is the point: an individual or a team idea has
    no organization to validate it, so no organization reviewer - not even one
    who holds `idea.review` in every tenant - can confirm or send it back. That
    is what makes "only organization-context ideas require organization review"
    a rule rather than a UI condition.

    Goes through `organizations.authorization`, so "active member" and
    "holds `idea.review`" mean exactly what they mean everywhere else in the
    platform, and a grant in one tenant authorizes nothing in another.
    """
    if user is None or not user.is_active or idea is None:
        return False
    if idea.submission_context != Idea.SubmissionContext.ORGANIZATION:
        return False
    if idea.organization_id is None:
        return False
    if idea.author_id == getattr(user, 'pk', None):
        return False

    membership = authorization.get_membership(user, idea.organization_id)
    return authorization.membership_has_permission(membership, authorization.IDEA_REVIEW)


def is_platform_reviewer(user: User | None, idea: Idea | None = None) -> bool:
    """
    Whether `user` may act as a **platform** reviewer.

    The answer does not depend on the idea at all, which is the point: platform
    review is authorized by a platform-scoped Django permission and by nothing
    else. No organization membership, no organization role, no team role and no
    team ownership can produce this answer - not because any of them is checked
    and found wanting, but because none of them is consulted. `idea` is accepted
    for symmetry with `is_organization_reviewer` and for the author check, which
    is applied here too: a platform administrator who wrote an idea cannot review
    it.
    """
    from administration import authorization as admin_authorization

    if user is None or not user.is_active:
        return False
    if idea is not None and idea.author_id == getattr(user, 'pk', None):
        return False
    return admin_authorization.capabilities_for(user).can_review_platform_submissions


# Retained as the name callers used before the two reviewer kinds were split
# apart, and as an alias for the organization rule - which is the one it always
# was. `reviews.eligibility` and the review-history selectors call the explicit
# names; anything older keeps working and means what it meant.
is_reviewer = is_organization_reviewer


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

    The three actor kinds are asked separately rather than through a lookup of
    which permission each needs, because the permissions live in three different
    mechanisms (the author is the row, the organization reviewer is an
    organization membership, the platform reviewer is a Django permission). What
    they share - the author exclusion - is written once, before the dispatch.
    """
    required_actor = TRANSITIONS.get((idea.status, to_status))
    if required_actor is None:
        # Not a legal move at all.
        return not require_legal_pair

    if required_actor == AUTHOR:
        return idea.author_id == user.pk

    if idea.author_id == user.pk:
        # A person never reviews their own idea, even holding the permission
        # that would otherwise let them, and in either reviewer capacity.
        return False

    if required_actor == ORGANIZATION_REVIEWER:
        return is_organization_reviewer(user, idea)

    return is_platform_reviewer(user, idea)


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

    target = str(to_status or '').strip().lower()
    if not context_allows(idea, target):
        return False

    return _actor_may(user, idea, _tenant_membership(user, idea), target)


def _tenant_membership(user: User, idea: Idea):
    """
    The caller's active membership of the idea's organization, or `None`.

    `None` for an individual or team idea, which have no organization - and
    which is exactly right: the *tenant* membership is what proves a caller may
    act inside this idea's tenant, and a team member's proof of that is their
    team membership, which `_actor_may` asks about through
    `teams.authorization` rather than here. Passing `None` therefore does not
    grant anything, because no actor rule reads it except the organization
    reviewer's, and that rule refuses anything without an organization.
    """
    if idea is None or idea.organization_id is None:
        return None
    return authorization.get_membership(user, idea.organization_id)


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

        # Tenant standing is re-checked per transition rather than taken from
        # the selector: leaving the organization (or the team) has to take the
        # ability to act with you, and the selector decides what is *readable*,
        # not what is *writable*. An individual idea's author belongs to no
        # organization, so the check is against whichever tenant the context
        # names - and there is none for an individual idea, which is why the
        # author being the author is the whole of what authorizes them.
        membership = _require_tenant_standing(active_user, idea, normalized_target)

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

        # Then the context, for the same reason it sits here and not inside the
        # matrix: an organization author pressing Submit is asking for something
        # they are allowed to ask for, and the answer is that their organization
        # has to confirm it first - not that the button is forbidden.
        if not context_allows(idea, normalized_target):
            raise IdeaError(CONTEXT_MISMATCH)

        # After the actor check, so only somebody who could have made the move
        # learns that it is made elsewhere.
        if (idea.status, normalized_target) in REVIEW_OWNED_TRANSITIONS:
            raise IdeaError(REVIEW_OWNED_MESSAGE)

        if (idea.status, normalized_target) not in TRANSITIONS:
            raise _illegal_transition_error(idea, normalized_target)

        if normalized_target in SUBMISSION_TARGETS:
            # Applies to the first submission and to a re-submission alike: an
            # idea whose description was emptied while addressing review
            # feedback is no more ready the second time than the first.
            #
            # `SUBMITTED_TO_ORGANIZATION` is in the set for the same reason
            # `SUBMITTED` is: an organization reviewer cannot read a `PRIVATE`
            # idea, so submitting one to the organization would park it in a
            # queue nobody in that organization could ever reach.
            _validate_for_submission(idea)

        if idea.status == Idea.Status.DRAFT and idea.submitted_at is None:
            idea.submitted_at = timezone.now()

        idea = _apply_transition_locked(active_user, idea, normalized_target)

    if normalized_target == Idea.Status.READY_FOR_IMPLEMENTATION:
        # After the commit: the handoff to Sprint 4 is a business fact worth a
        # notification, and a rollback must not tell anybody the idea is ready
        # for implementation. `apply_owner_go_ahead` registers its own hook for
        # the operation the UI actually calls.
        transitioned_pk = idea.pk
        transaction.on_commit(lambda: go_ahead._notify_owner(transitioned_pk))

    return idea


def apply_owner_go_ahead(user: User, idea: Idea) -> Idea:
    """
    Make the `APPROVED -> READY_FOR_IMPLEMENTATION` move on a **locked** idea.

    The lifecycle's own entry point for the owner's go-ahead, for exactly one
    caller: `ideas.go_ahead.confirm_go_ahead`, which owns the operation's meaning
    - the confirmation wording, the requirement that a report exists, and the
    "this is not a developer selection" note. What the *move* is stays here,
    because the matrix is the only thing in the platform that decides who may
    take an idea from one status to another, and a second implementation of that
    rule is how "the owner, and only the owner" quietly becomes "the owner, or
    whoever the frontend let press the button".

    Refuses to run outside a transaction, for the same reason
    `apply_review_transition` does: the status change and the go-ahead stamp
    must commit together or not at all.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('apply_owner_go_ahead must run inside a transaction.')

    target = Idea.Status.READY_FOR_IMPLEMENTATION
    if (idea.status, target) not in TRANSITIONS:
        raise _illegal_transition_error(idea, target)
    if TRANSITIONS[(idea.status, target)] != AUTHOR:
        raise IdeaError('You are not allowed to make that change to this idea.')
    if idea.author_id != user.pk:
        raise IdeaError('You are not allowed to make that change to this idea.')

    return _apply_transition_locked(user, idea, target)


def _apply_transition_locked(user: User, idea: Idea, to_status: str) -> Idea:
    """
    Save the status change, its side effects and its audit row, on a locked idea.

    The single place the *consequences* of a transition are written, so the two
    callers that make author moves - `transition_idea` and `apply_owner_go_ahead`
    - cannot drift on what a move records. Three side effects, each tied to one
    target and none of them optional:

    - `APPROVED -> READY_FOR_IMPLEMENTATION` stamps `owner_go_ahead_at/by`. The
      decision is recorded with the move, so an idea cannot be handed to
      implementation without an attributable moment and an attributable person -
      and so `idea_go_ahead_only_when_ready` has something true to check.
    - `-> SUBMITTED` stamps `platform_locked_at` and freezes a submission
      version, in the same transaction as the status that claims there is one, so
      "submitted to the platform without a locked submission" is not a state a
      crash between two statements can leave behind.
    - either way, an `IdeaTransition` row, which is the audit trail.

    `idea` must be locked by the caller. The actor check has already been made
    by that caller, because the two callers check different things (the public
    transition checks the matrix; the go-ahead checks that the caller is the
    author) and neither may re-ask in a way that could disagree.
    """
    from_status = idea.status
    idea.status = to_status

    if to_status == Idea.Status.READY_FOR_IMPLEMENTATION:
        idea.owner_go_ahead_at = timezone.now()
        idea.owner_go_ahead_by = user

    # `save()` runs `full_clean()`, so the model's own status/`submitted_at`,
    # context and go-ahead invariants are checked on this path like every other
    # write.
    idea.save()

    if to_status == Idea.Status.SUBMITTED:
        # `freeze_submission` stamps the lock and the version number **together**
        # and saves once. They cannot be written separately: the
        # `idea_platform_lock_matches_version` CHECK refuses a row that is locked
        # without a version (or versioned without a lock), so two saves would
        # have to pass through an inconsistent state that the database is
        # specifically built to reject.
        versions.freeze_submission(idea, user)

    _record_transition(idea, from_status, user)
    return idea


def _require_tenant_standing(user: User, idea: Idea, to_status: str):
    """
    Prove the caller still belongs to this idea's tenant, or refuse.

    One branch per context, because the three have genuinely different proofs: an
    organization membership for an organization idea, a team membership for a
    team idea, and nothing at all for an individual one - whose author *is* its
    tenant by definition. Returning `None` for the individual case is honest
    rather than permissive: no rule reads it, and the author check in
    `_actor_may` is the authorization.

    **A platform reviewer is exempt, and that exemption is the point.** Platform
    review is cross-tenant: the platform reviews submissions from organizations
    its reviewers are not members of. Requiring membership here would make the
    whole platform track unreachable for anybody outside the idea's tenant, which
    is not a restriction anybody wants - it is the track not working. The
    authorization for a platform move is the platform permission, checked in
    `_actor_may`, and the visibility filter in `ideas.selectors` has already
    established that this reviewer may read the submission.
    """
    if TRANSITIONS.get((idea.status, to_status)) == PLATFORM_REVIEWER:
        return None

    if idea.submission_context == Idea.SubmissionContext.TEAM:
        from teams import authorization as team_authorization

        if not team_authorization.is_member_of(user, idea.team_id):
            raise IdeaError(
                'You must be an active member of this team to work with this idea.',
                reason='membership_required',
            )
        return None

    if idea.submission_context == Idea.SubmissionContext.ORGANIZATION:
        return _require_membership(user, idea.organization_id)

    return None


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


def review_scope_for(idea: Idea, to_status: str) -> str:
    """
    Which review track owns the move from `idea.status` to `to_status`.

    `TRANSITION_REVIEW_SCOPE` answers it; `organization` and `platform` are the
    only values it contains, and a pair that is review-owned but absent from it
    raises rather than defaulting - because a default here would be a way for a
    platform decision to be recorded as an organization one.
    """
    scope = TRANSITION_REVIEW_SCOPE.get((idea.status, str(to_status or '').strip().lower()))
    if scope is None:
        raise IdeaError('That is not a review decision.')
    return scope


def _apply_review_transition(user: User, idea: Idea, to_status: str) -> Idea:
    """
    Make one review-owned move on an idea the caller has already locked.

    The only way to reach the pairs in `REVIEW_OWNED_TRANSITIONS`, and it has
    exactly two callers: `reviews.organization_review` and
    `reviews.platform_review`, each of which writes the `Review` record in the
    same transaction - and each of which is refused here unless its caller is the
    reviewer kind this pair belongs to. It opens no transaction of its own and
    refuses to run outside one, because its whole contract is "the review and the
    status commit together or not at all" - the caller's transaction is that
    contract.

    `idea` must have been read with `selectors.get_idea_for_update` inside the
    caller's transaction, so the status checked here is the locked one. The rules
    are the same as `transition_idea`'s: tenant standing, the right reviewer
    actor rule for this pair (either of which refuses the author), and a pair in
    the matrix.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError('apply_review_transition must run inside a transaction.')

    normalized_target = str(to_status or '').strip().lower()
    if (idea.status, normalized_target) not in REVIEW_OWNED_TRANSITIONS:
        raise _illegal_transition_error(idea, normalized_target)

    membership = _require_tenant_standing(user, idea, normalized_target)
    if not _actor_may(user, idea, membership, normalized_target):
        raise IdeaError('You are not allowed to make that change to this idea.')
    # The same context rule the public path applies. A review cannot be the way
    # round it: an organization review on a team idea, or a platform review
    # reached without the organization having confirmed it, would be a review
    # record about a journey that was never legal.
    if not context_allows(idea, normalized_target):
        raise IdeaError(CONTEXT_MISMATCH)

    from_status = idea.status
    idea.status = normalized_target
    if normalized_target == Idea.Status.APPROVED:
        # Stamped here, with the transition, rather than read back from the
        # report later: the report is generated in the review's transaction too,
        # and having the approval moment on the idea row is what lets the
        # author's report page say when this happened without a join.
        idea.platform_approved_at = timezone.now()
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
