"""
What an idea's state *means*, in words a person can act on.

`ideas/models.py` owns the vocabulary, `ideas/lifecycle.py` owns which move is
legal and who may make it, and this module owns the third question, which is the
one the product is actually judged on:

    Where is my idea? Who is reviewing it? What do I need to do next?

Every technical status maps here to exactly one **friendly label** and exactly one
**stage**, and every (idea, reader) pair resolves to at most one **primary
action**. Three deliberate properties:

**It reads the lifecycle, it does not re-decide it.** The action is derived from
`lifecycle.available_transitions` plus the two review-eligibility predicates, so
"what the UI offers" and "what the server accepts" come from one source. This
module can therefore never offer an action the server would refuse; if it could,
the two would drift the first time a rule moved.

**It answers for the reader, not in general.** An author and a reviewer looking at
the same approved idea see different things: the author is offered "Give
go-ahead", the reviewer is offered nothing. That is the whole point of a
state-aware UI, and it is why the first argument is always a user.

**Nothing here mutates, and nothing here authorizes.** It is presentation. The
security of every action it names is enforced by the service behind that action -
`ideas.lifecycle`, `reviews.organization_review`, `reviews.platform_review`,
`ideas.go_ahead` - and this module refusing to be the check is exactly why the
UI cannot be a security boundary.
"""

from dataclasses import dataclass
from enum import StrEnum

from ideas.models import Idea

# --- friendly labels (the vocabulary a person sees) ------------------------------------
#
# Not `Status.label`. Those are short admin-facing descriptions written when the
# lifecycle had seven values and one meaning ("Under review" was true of every
# review the platform did, which is no longer true). These are the labels the
# product asks for, and they are deliberately specific enough to answer "who is
# reviewing this?" without a second lookup - `PLATFORM_APPROVED` reading as
# "Approved" is what made a user think the idea was done when it was waiting on
# them.
FRIENDLY_LABELS: dict[str, str] = {
    Idea.Status.DRAFT: 'Draft',
    Idea.Status.SUBMITTED_TO_ORGANIZATION: 'Waiting for Organization Review',
    Idea.Status.ORGANIZATION_CHANGES_REQUESTED: 'Changes Requested by Organization',
    Idea.Status.ORGANIZATION_CONFIRMED: 'Organization Confirmed',
    Idea.Status.SUBMITTED: 'Submitted to Platform',
    Idea.Status.UNDER_REVIEW: 'Platform Review',
    Idea.Status.CHANGES_REQUESTED: 'Platform Changes Requested',
    Idea.Status.REJECTED: 'Not Approved',
    Idea.Status.APPROVED: 'Platform Approved — Your Confirmation Needed',
    Idea.Status.READY_FOR_IMPLEMENTATION: 'Ready for Implementation',
    Idea.Status.AUTOMATION_PROPOSAL: 'Automation Opportunity',
}

# One word per status for a badge, where the long label does not fit. The badge
# still spells the status out in its `title` attribute and in the surrounding
# card text; this is the short form for a chip, not a second meaning.
SHORT_LABELS: dict[str, str] = {
    Idea.Status.DRAFT: 'Draft',
    Idea.Status.SUBMITTED_TO_ORGANIZATION: 'Awaiting organization',
    Idea.Status.ORGANIZATION_CHANGES_REQUESTED: 'Changes requested',
    Idea.Status.ORGANIZATION_CONFIRMED: 'Organization confirmed',
    Idea.Status.SUBMITTED: 'Submitted',
    Idea.Status.UNDER_REVIEW: 'Under platform review',
    Idea.Status.CHANGES_REQUESTED: 'Changes requested',
    Idea.Status.REJECTED: 'Not approved',
    Idea.Status.APPROVED: 'Approved — your go-ahead needed',
    Idea.Status.READY_FOR_IMPLEMENTATION: 'Ready for implementation',
    Idea.Status.AUTOMATION_PROPOSAL: 'Automation opportunity',
}


class Stage(StrEnum):
    """
    The six steps of the journey, as a progress indicator shows them.

    A stage is a *bucket* of statuses, not a status: `SUBMITTED_TO_ORGANIZATION`
    and `ORGANIZATION_CHANGES_REQUESTED` are different states with different
    next actions, and a progress bar that treated them as two steps would be
    longer for some ideas than for others. So the bar is the journey, the badge is
    the state, and neither is derived from the other.
    """

    CREATED = 'created'
    ORGANIZATION_REVIEW = 'organization_review'
    ORGANIZATION_CONFIRMED = 'organization_confirmed'
    PLATFORM_REVIEW = 'platform_review'
    OWNER_CONFIRMATION = 'owner_confirmation'
    READY = 'ready'


# The order the progress indicator renders. `None` marks a step that does not
# apply to this idea's submission context: an individual or team idea has no
# organization in its journey, so those two steps are not drawn at all rather
# than drawn struck through.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.CREATED,
    Stage.ORGANIZATION_REVIEW,
    Stage.ORGANIZATION_CONFIRMED,
    Stage.PLATFORM_REVIEW,
    Stage.OWNER_CONFIRMATION,
    Stage.READY,
)

STAGES_FOR_ORGANIZATION_CONTEXT: tuple[Stage, ...] = STAGE_ORDER
STAGES_FOR_DIRECT_CONTEXT: tuple[Stage, ...] = (
    Stage.CREATED,
    Stage.PLATFORM_REVIEW,
    Stage.OWNER_CONFIRMATION,
    Stage.READY,
)

STATUS_STAGE: dict[str, Stage] = {
    Idea.Status.DRAFT: Stage.CREATED,
    Idea.Status.SUBMITTED_TO_ORGANIZATION: Stage.ORGANIZATION_REVIEW,
    Idea.Status.ORGANIZATION_CHANGES_REQUESTED: Stage.ORGANIZATION_REVIEW,
    Idea.Status.ORGANIZATION_CONFIRMED: Stage.ORGANIZATION_CONFIRMED,
    Idea.Status.SUBMITTED: Stage.PLATFORM_REVIEW,
    Idea.Status.UNDER_REVIEW: Stage.PLATFORM_REVIEW,
    Idea.Status.CHANGES_REQUESTED: Stage.PLATFORM_REVIEW,
    Idea.Status.REJECTED: Stage.PLATFORM_REVIEW,
    Idea.Status.APPROVED: Stage.OWNER_CONFIRMATION,
    Idea.Status.READY_FOR_IMPLEMENTATION: Stage.READY,
    Idea.Status.AUTOMATION_PROPOSAL: Stage.READY,
}

# Whether the idea is finished from the platform's point of view. `REJECTED` is
# the only terminal state the platform itself produces; everything else either
# moves on or is waiting for a person. Nothing else may be described as "done",
# because "done" is the word that made people think an approved idea was on its
# way to a developer when it was waiting for its owner.
TERMINAL_STATUSES = frozenset({Idea.Status.REJECTED})


# The tone of a badge, as a *name*. The frontend maps it to colour and to an icon,
# so the distinction never depends on colour alone - an approved-but-waiting idea
# and a rejected one must be told apart by their words first.
class Tone(StrEnum):
    NEUTRAL = 'neutral'
    INFO = 'info'
    WARNING = 'warning'
    SUCCESS = 'success'
    DANGER = 'danger'


STATUS_TONE: dict[str, Tone] = {
    Idea.Status.DRAFT: Tone.NEUTRAL,
    Idea.Status.SUBMITTED_TO_ORGANIZATION: Tone.INFO,
    Idea.Status.ORGANIZATION_CHANGES_REQUESTED: Tone.WARNING,
    Idea.Status.ORGANIZATION_CONFIRMED: Tone.INFO,
    Idea.Status.SUBMITTED: Tone.INFO,
    Idea.Status.UNDER_REVIEW: Tone.INFO,
    Idea.Status.CHANGES_REQUESTED: Tone.WARNING,
    Idea.Status.REJECTED: Tone.DANGER,
    Idea.Status.APPROVED: Tone.SUCCESS,
    Idea.Status.READY_FOR_IMPLEMENTATION: Tone.SUCCESS,
    Idea.Status.AUTOMATION_PROPOSAL: Tone.SUCCESS,
}


class Action(StrEnum):
    """
    The one thing the reader should do next.

    A closed set, because the product's rule is "one clear primary action" - not
    a menu the reader has to choose from. Each member names a *move or a page*,
    never a state, so a client cannot offer "set my idea to Organization
    Confirmed" even by accident.
    """

    CONTINUE_EDITING = 'continue_editing'
    REVIEW_FEEDBACK = 'review_feedback'
    SUBMIT_TO_ORGANIZATION = 'submit_to_organization'
    RESUBMIT_TO_ORGANIZATION = 'resubmit_to_organization'
    REVIEW_FOR_ORGANIZATION = 'review_for_organization'
    WAIT_FOR_ORGANIZATION = 'wait_for_organization'
    SUBMIT_TO_PLATFORM = 'submit_to_platform'
    REVIEW_ON_PLATFORM = 'review_on_platform'
    VIEW_STATUS = 'view_status'
    VIEW_REPORT = 'view_report'
    GIVE_GO_AHEAD = 'give_go_ahead'
    VIEW_IMPLEMENTATION_OPPORTUNITY = 'view_implementation_opportunity'
    NOTHING_TO_DO = 'nothing_to_do'


ACTION_LABELS: dict[str, str] = {
    Action.CONTINUE_EDITING: 'Continue Editing',
    Action.REVIEW_FEEDBACK: 'Review Changes',
    Action.SUBMIT_TO_ORGANIZATION: 'Submit to Organization',
    Action.RESUBMIT_TO_ORGANIZATION: 'Resubmit to Organization',
    Action.REVIEW_FOR_ORGANIZATION: 'Review Idea',
    Action.WAIT_FOR_ORGANIZATION: 'Waiting for Your Organization',
    Action.SUBMIT_TO_PLATFORM: 'Submit to Platform',
    Action.REVIEW_ON_PLATFORM: 'Review Idea',
    Action.VIEW_STATUS: 'View Status',
    Action.VIEW_REPORT: 'Review Report',
    Action.GIVE_GO_AHEAD: 'Give Go-Ahead',
    Action.VIEW_IMPLEMENTATION_OPPORTUNITY: 'View Implementation Opportunity',
    Action.NOTHING_TO_DO: '',
}


@dataclass(frozen=True)
class IdeaStateSummary:
    """
    Everything a card or a detail header needs to explain an idea's state to one
    reader, resolved once.

    Frozen, because it is a rendering of database state: a caller that could
    mutate it would be able to show a state the server does not hold.
    """

    status: str
    label: str
    short_label: str
    tone: Tone
    stage: Stage
    primary_action: Action
    primary_action_label: str
    is_locked: bool
    is_terminal: bool


def friendly_label(status: str) -> str:
    """The human wording for a status, falling back to Django's own label."""
    normalized = str(status or '').strip().lower()
    if normalized in FRIENDLY_LABELS:
        return FRIENDLY_LABELS[normalized]
    try:
        return Idea.Status(normalized).label
    except ValueError:
        return normalized


def stages_for(idea: Idea) -> tuple[Stage, ...]:
    """
    The progress steps to draw for this idea.

    An organization or team idea has the check-first steps (its organization's or
    team's review); an individual idea is not shown two steps it will never reach,
    because a progress bar that can never complete is worse than a shorter honest
    one.
    """
    if idea.submission_context in (
        Idea.SubmissionContext.ORGANIZATION,
        Idea.SubmissionContext.TEAM,
    ):
        return STAGES_FOR_ORGANIZATION_CONTEXT
    return STAGES_FOR_DIRECT_CONTEXT


def tone_for(status: str) -> Tone:
    return STATUS_TONE.get(str(status or '').strip().lower(), Tone.NEUTRAL)


def is_terminal(status: str) -> bool:
    return str(status or '').strip().lower() in TERMINAL_STATUSES


def stage_index(idea: Idea) -> tuple[int, int]:
    """
    `(current_step_index, total_steps)` for the progress indicator.

    One-based, and clamped: an unknown status reports the first step rather than
    an out-of-range number, so a client rendering a bar cannot be handed a
    position that does not exist.
    """
    stages = stages_for(idea)
    stage = STATUS_STAGE.get(idea.status)
    if stage not in stages:
        return 1, len(stages)
    return stages.index(stage) + 1, len(stages)


def primary_action_for(
    user,
    idea: Idea,
    *,
    capabilities: 'ActionCapabilities | None' = None,
) -> Action:
    """
    The single action `user` should take next on `idea`.

    A table read top to bottom rather than a chain of nested conditions, because
    the states are disjoint and the answer for each is a fact rather than a
    computation: `_BY_STATUS_AUTHOR` is what an *author* may do, and the two
    reviewer-driven rows are the only ones that need a capability answer. The
    journey it follows is the product's, in order: what am I writing, what did
    the organization say, has it been submitted, is it with the platform, is it
    approved, is it done.

    `capabilities` lets a caller pass eligibility answers it already has
    (`reviews.eligibility`, `reviews.platform_review`,
    `lifecycle.available_transitions`) so a detail page that has already fetched
    them does not repeat the queries. Omitted, they are asked - correctly, just
    less cheaply, and **always after** the status has been checked, so the common
    case of a finished idea costs nothing at all.
    """
    if idea is None or user is None:
        return Action.NOTHING_TO_DO

    # The two states where somebody else's eligibility, not the author's, decides
    # what is offered. Everything else is a function of the state and whether the
    # reader wrote it.
    if idea.status == Idea.Status.SUBMITTED_TO_ORGANIZATION:
        if _can_review_for_organization(user, idea, capabilities):
            return Action.REVIEW_FOR_ORGANIZATION
        return Action.WAIT_FOR_ORGANIZATION

    if idea.status in (Idea.Status.SUBMITTED, Idea.Status.UNDER_REVIEW):
        if _can_review_on_platform(user, idea, capabilities):
            return Action.REVIEW_ON_PLATFORM
        return Action.VIEW_STATUS

    if idea.author_id != getattr(user, 'pk', None):
        return _VIEW_FOR_EVERYONE
    return _BY_STATUS_AUTHOR.get(idea.status, _VIEW_FOR_EVERYONE)


# What each remaining state offers an **author**, and what it offers everybody
# else. The author column is the owner's side of the journey and the "everybody
# else" column is deliberately flat: a reader who is not the author is never
# offered a move, because none of these are moves - they are pages.
_BY_STATUS_AUTHOR: dict[str, Action] = {
    Idea.Status.DRAFT: Action.CONTINUE_EDITING,
    Idea.Status.ORGANIZATION_CHANGES_REQUESTED: Action.REVIEW_FEEDBACK,
    # Only the author submits a confirmed idea to the platform. An organization
    # confirming an idea is an organizational fact, not a submission - which is
    # why this is the author's row and not the organization's.
    Idea.Status.ORGANIZATION_CONFIRMED: Action.SUBMIT_TO_PLATFORM,
    Idea.Status.CHANGES_REQUESTED: Action.REVIEW_FEEDBACK,
    Idea.Status.REJECTED: Action.VIEW_STATUS,
    # Platform approval is not owner go-ahead, and only the owner may give it -
    # not an organization Owner, not a team Owner, not a platform
    # administrator. That is the reading this row encodes.
    Idea.Status.APPROVED: Action.GIVE_GO_AHEAD,
    Idea.Status.READY_FOR_IMPLEMENTATION: Action.VIEW_IMPLEMENTATION_OPPORTUNITY,
    Idea.Status.AUTOMATION_PROPOSAL: Action.VIEW_IMPLEMENTATION_OPPORTUNITY,
}

_VIEW_FOR_EVERYONE = Action.VIEW_STATUS


@dataclass(frozen=True)
class ActionCapabilities:
    """
    The eligibility answers `primary_action_for` needs, passed in by a caller
    that already has them.

    Grouped in one object rather than four booleans as arguments, because the
    argument list was unreadable and because it makes it obvious that these are
    *answers already computed*, not switches that turn an action on.
    """

    can_review_for_organization: bool = False
    can_review_on_platform: bool = False
    can_edit: bool = False
    can_submit_to_platform: bool = False
    can_give_go_ahead: bool = False


def _capabilities_for(user, idea: Idea) -> ActionCapabilities:
    """
    Ask every eligibility question this module would otherwise ask lazily.

    Imports are local because the domains it asks (`reviews`) import `ideas`, so
    a module-level import in both directions would be a cycle. The answers come
    from the same predicates the services enforce - never from a UI flag.
    """
    from ideas import lifecycle
    from reviews import eligibility, platform_review

    is_author = idea.author_id == getattr(user, 'pk', None)
    transitions = lifecycle.available_transitions(user, idea)

    return ActionCapabilities(
        can_review_for_organization=eligibility.can_organization_review(user, idea),
        can_review_on_platform=platform_review.can_review(user, idea),
        can_edit=idea.status in (Idea.Status.DRAFT, Idea.Status.CHANGES_REQUESTED) and is_author,
        can_submit_to_platform=Idea.Status.SUBMITTED in transitions,
        can_give_go_ahead=idea.status == Idea.Status.APPROVED and is_author,
    )


def _can_review_for_organization(user, idea: Idea, capabilities) -> bool:
    if capabilities is not None:
        return capabilities.can_review_for_organization
    try:
        from reviews import eligibility
    except ImportError:  # pragma: no cover - reviews is always installed
        return False
    return eligibility.can_organization_review(user, idea)


def _can_review_on_platform(user, idea: Idea, capabilities) -> bool:
    if capabilities is not None:
        return capabilities.can_review_on_platform
    try:
        from reviews import platform_review
    except ImportError:  # pragma: no cover - reviews is always installed
        return False
    return platform_review.can_review(user, idea)


def _for_level(text: str, idea: Idea) -> str:
    """
    The same words for the idea's level: the stage that checks an idea first is the
    organization's or the team's, and the status names say 'organization' because
    that level came first. A team's idea reads 'team'.
    """
    if idea is not None and idea.submission_context == Idea.SubmissionContext.TEAM:
        return text.replace('Organization', 'Team').replace('organization', 'team')
    return text


def summarize(
    user, idea: Idea, *, capabilities: 'ActionCapabilities | None' = None
) -> IdeaStateSummary:
    """
    One call for everything a header or a card needs.

    The three cheap things (label, tone, stage) come first and are answered from
    the row alone. The eligibility questions that decide the primary action are
    asked only for the statuses that can have one, so rendering a list of fifty
    approved ideas costs fifty dicts and no extra queries.
    """
    if (
        capabilities is None
        and idea is not None
        and idea.status
        in (
            Idea.Status.SUBMITTED_TO_ORGANIZATION,
            Idea.Status.SUBMITTED,
            Idea.Status.UNDER_REVIEW,
        )
    ):
        capabilities = _capabilities_for(user, idea)

    action = primary_action_for(user, idea, capabilities=capabilities)

    return IdeaStateSummary(
        status=idea.status,
        label=_for_level(friendly_label(idea.status), idea),
        short_label=_for_level(SHORT_LABELS.get(idea.status, friendly_label(idea.status)), idea),
        tone=tone_for(idea.status),
        stage=STATUS_STAGE.get(idea.status, Stage.CREATED),
        primary_action=action,
        primary_action_label=_for_level(ACTION_LABELS.get(action, ''), idea),
        is_locked=idea.is_locked,
        is_terminal=is_terminal(idea.status),
    )
