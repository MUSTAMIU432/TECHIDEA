"""
The proposal for an approved idea.

After a review team approves an idea it writes the proposal: what the platform would build,
for whom, how long it would take and how it will be judged done. A platform admin then decides
whether it goes to the owner; the owner reads it on a view-only page and only then can give the
go-ahead; a delivery manager assigns a developer. Four different people, in that order, and
this module is where the first three meet:

- **Writers** are the members of the team the idea was routed to (or, when one reviewer did the
  work unrouted, that reviewer). Any writer edits; only the **lead** submits, so the hand-off to
  the admin has one accountable name.
- The **admin** (`administration.release_proposals`) releases it, sends it back with feedback,
  or declines it. The people who write a proposal never approve it.
- The **owner** sees it only once it is released, and never edits it. Each time they open it is
  recorded.

Every operation re-asks everything under a row lock, writes its audit entry in the same
transaction, and notifies afterwards. Nothing takes a status from a client.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from administration import authorization as admin_authorization
from administration import services as admin_services
from administration.models import AdminAuditEntry
from ideas.models import Idea
from identity.models import User
from reviews.models import (
    IdeaProposal,
    IdeaProposalContribution,
    IdeaProposalView,
    Review,
    ReviewAssignment,
    ReviewTeam,
)

logger = logging.getLogger(__name__)

FIELDS = (
    'title',
    'executive_summary',
    'problem',
    'feasibility',
    'proposed_solution',
    'requirements_summary',
    'scope',
    'deliverables',
    'estimated_timeline',
    'milestones',
    'estimated_effort',
    'financial_requirements',
    'payment_required',
    'payment_plan',
    'risks',
    'assumptions',
    'acceptance_criteria',
)
#: What a proposal must say before the lead can send it: complete enough for the admin to
#: judge and for the owner to agree to - what, whether it can be done, what it needs, how long,
#: what it costs, and who pays how. The payment plan is required only when the owner pays.
_REQUIRED = (
    ('executive_summary', 'executive summary'),
    ('problem', 'problem'),
    ('feasibility', 'feasibility'),
    ('proposed_solution', 'proposed solution'),
    ('requirements_summary', 'requirements'),
    ('scope', 'scope'),
    ('deliverables', 'deliverables'),
    ('estimated_timeline', 'overall timeline'),
    ('milestones', 'timeline and milestones'),
    ('financial_requirements', 'financial requirements'),
    ('payment_required', 'answer to whether the owner pays'),
    ('payment_plan', 'payment plan'),
    ('acceptance_criteria', 'acceptance criteria'),
)
_LIMITS = {'title': 200, 'estimated_effort': 120, 'estimated_timeline': 120}
_EDITABLE = frozenset({'draft', 'changes_requested'})
UNAVAILABLE = 'This proposal is not available.'


class ProposalError(Exception):
    """A refused proposal operation, in the project's `(message, field, reason)` shape."""

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message)
        self.message = message
        self.field = field
        self.reason = reason


@dataclass(frozen=True)
class Writers:
    """Who may write this idea's proposal, and who among them submits it."""

    team_id: int | None
    member_ids: frozenset[int]
    lead_id: int


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _approving_review(idea: Idea) -> Review | None:
    return (
        Review.objects.filter(
            idea=idea, scope=Review.Scope.PLATFORM, decision=Review.Decision.APPROVED
        )
        .order_by('-completed_at', '-pk')
        .first()
    )


def writers_for(idea: Idea) -> Writers | None:
    """
    The team (or the one reviewer) that approved `idea`, or `None` if it has not been approved.
    """
    review = _approving_review(idea)
    if review is None:
        return None
    assignment = (
        ReviewAssignment.objects.select_related('team__lead')
        .filter(idea=idea, team__isnull=False)
        .order_by('-created_at', '-pk')
        .first()
    )
    if assignment is not None:
        team = assignment.team
        ids = set(team.members.values_list('user_id', flat=True)) | {team.lead_id}
        return Writers(team.pk, frozenset(ids), team.lead_id)
    return Writers(None, frozenset({review.reviewer_id}), review.reviewer_id)


def release_managers() -> list[User]:
    """Active accounts holding the release permission, by group, directly, or as superuser."""
    codename = admin_authorization.RELEASE_PROPOSALS.split('.', 1)[1]
    return list(
        User.objects.filter(is_active=True)
        .filter(
            Q(
                user_permissions__content_type__app_label='administration',
                user_permissions__codename=codename,
            )
            | Q(
                groups__permissions__content_type__app_label='administration',
                groups__permissions__codename=codename,
            )
            | Q(is_superuser=True)
        )
        .distinct()
    )


def _idea(idea_id: object) -> Idea:
    idea = Idea.objects.select_for_update().filter(pk=_as_int(idea_id)).first()
    if idea is None:
        raise ProposalError(UNAVAILABLE)
    return idea


def _clean(value: str | None, field: str, *, limit: int | None = None) -> str:
    text = (value or '').strip()
    if limit is not None and len(text) > limit:
        raise ProposalError(f'{field.replace("_", " ").capitalize()} is too long.', field=field)
    return text


def _audit(actor, action, proposal: IdeaProposal, **meta) -> None:
    admin_services.record_event(
        actor, action, 'idea', proposal.idea_id, proposal.title, metadata=meta or None
    )


def _notify(recipients, kind, title, body, idea) -> None:
    from notifications import services as notifications

    people = [p for p in recipients if p is not None and p.is_active]
    if people:
        notifications.deliver(
            recipients=people, kind=kind, title=title, body=body, idea=idea, send_email=False
        )


def _require_writer(user: User | None, idea: Idea) -> Writers:
    if user is None or not user.is_active:
        raise ProposalError('Sign in to continue.', reason='unauthenticated')
    writers = writers_for(idea)
    if writers is None or user.pk not in writers.member_ids or idea.author_id == user.pk:
        raise ProposalError(UNAVAILABLE)
    return writers


def _required_for(proposal: IdeaProposal | None) -> list[tuple[str, str]]:
    # The payment plan is asked for only once the team has said the owner pays.
    pays = proposal is not None and proposal.payment_required == 'yes'
    return [(f, label) for f, label in _REQUIRED if f != 'payment_plan' or pays]


def missing_required(proposal: IdeaProposal | None) -> list[tuple[str, str]]:
    """The required sections still empty, as `(field, label)`, in reading order."""
    required = _required_for(proposal)
    if proposal is None:
        return required
    return [(f, label) for f, label in required if not getattr(proposal, f).strip()]


def applicable_fields(proposal: IdeaProposal | None) -> tuple[str, ...]:
    """The sections this proposal has: no payment plan when the owner is not charged."""
    if proposal is not None and proposal.payment_required == 'no':
        return tuple(f for f in FIELDS if f != 'payment_plan')
    return FIELDS


def _contributed(proposal: IdeaProposal, user: User, action: str, sections=()) -> None:
    IdeaProposalContribution.objects.create(
        proposal=proposal, contributor=user, action=action, sections=list(sections)
    )


# --- writing ---------------------------------------------------------------------------------


@transaction.atomic
def start_proposal(user: User | None, idea_id: object) -> IdeaProposal:
    """Begin the proposal for an approved idea, pre-filled from what is already known."""
    idea = _idea(idea_id)
    writers = _require_writer(user, idea)
    if idea.status != Idea.Status.APPROVED:
        raise ProposalError('A proposal is written once the idea has been approved.')
    from reviews import decision_letters

    if decision_letters.pending_letter(idea) is not None:
        # The platform admin confirms the approval - sending it to the owner - first.
        raise ProposalError(
            'The platform admin has not confirmed this approval yet. You can start the '
            'proposal as soon as they do - you will be notified.'
        )
    if IdeaProposal.objects.filter(idea=idea).exists():
        raise ProposalError('This idea already has a proposal.')

    proposal = IdeaProposal.objects.create(
        idea=idea,
        team_id=writers.team_id,
        title=idea.title,
        problem=idea.description,
        created_by=user,
    )
    _contributed(proposal, user, IdeaProposalContribution.Action.STARTED)
    return proposal


@transaction.atomic
def update_proposal(user: User | None, idea_id: object, values: dict) -> IdeaProposal:
    idea = _idea(idea_id)
    _require_writer(user, idea)
    proposal = IdeaProposal.objects.select_for_update().filter(idea=idea).first()
    if proposal is None:
        raise ProposalError('There is no proposal for this idea yet.')
    if proposal.status not in _EDITABLE:
        raise ProposalError('This proposal can no longer be edited.')
    unknown = set(values) - set(FIELDS)
    if unknown:
        raise ProposalError('Unknown proposal field.', field=sorted(unknown)[0])

    changed = []
    for field, value in values.items():
        if value is None:
            continue
        text = _clean(value, field, limit=_LIMITS.get(field))
        if field == 'title' and not text:
            raise ProposalError('Title is required.', field='title')
        if field == 'payment_required' and text not in ('', 'yes', 'no'):
            raise ProposalError(
                "Say whether the owner pays: 'yes' or 'no'.", field='payment_required'
            )
        if getattr(proposal, field) == text:
            continue  # the editor sends every section; only real changes count as a hand in it
        setattr(proposal, field, text)
        changed.append(field)
    if changed:
        proposal.save(update_fields=[*changed, 'updated_at'])
        _contributed(proposal, user, IdeaProposalContribution.Action.EDITED, changed)
    return proposal


@transaction.atomic
def submit_proposal(user: User | None, idea_id: object) -> IdeaProposal:
    """The team's lead sends the finished proposal to the platform admin."""
    idea = _idea(idea_id)
    writers = _require_writer(user, idea)
    proposal = IdeaProposal.objects.select_for_update().filter(idea=idea).first()
    if proposal is None:
        raise ProposalError('There is no proposal for this idea yet.')
    if user.pk != writers.lead_id:
        raise ProposalError("Only the team's lead can send the proposal to the admin.")
    if proposal.status not in _EDITABLE:
        raise ProposalError('This proposal has already been sent.')
    missing = missing_required(proposal)
    if missing:
        field, label = missing[0]
        raise ProposalError(f'Fill in the {label} before sending it.', field=field)

    proposal.status = 'submitted'
    proposal.submitted_by = user
    proposal.submitted_at = timezone.now()
    proposal.review_feedback = ''
    proposal.save(update_fields=['status', 'submitted_by', 'submitted_at', 'review_feedback'])
    _contributed(proposal, user, IdeaProposalContribution.Action.SUBMITTED)
    _audit(user, AdminAuditEntry.Action.PROPOSAL_SUBMITTED, proposal)
    _notify(
        release_managers(),
        'proposal.submitted',
        f'A proposal for "{idea.title}" is waiting for you',
        'A review team has finished a proposal. Read it, then release it to the owner, '
        'send it back, or decline it.',
        idea,
    )
    return proposal


# --- the admin's decision -----------------------------------------------------------------------


def _decide(admin, idea_id, to_status: str, action, *, feedback: str | None = None):
    admin_authorization.require_release_proposals(admin)
    idea = _idea(idea_id)
    proposal = IdeaProposal.objects.select_for_update().filter(idea=idea).first()
    if proposal is None or proposal.status != 'submitted':
        raise ProposalError('There is no proposal waiting for a decision on this idea.')
    if proposal.created_by_id == admin.pk or proposal.submitted_by_id == admin.pk:
        # The people who wrote it never approve it, even holding the permission.
        raise ProposalError('You helped write this proposal, so somebody else has to decide it.')
    if feedback is not None:
        proposal.review_feedback = _clean(feedback, 'feedback')
        if not proposal.review_feedback:
            raise ProposalError('Say why, so the team knows what to do.', field='feedback')
    proposal.status = to_status
    proposal.decided_by = admin
    proposal.decided_at = timezone.now()
    proposal.save(update_fields=['status', 'decided_by', 'decided_at', 'review_feedback'])
    _audit(admin, action, proposal)
    return idea, proposal


@transaction.atomic
def release(admin: User | None, idea_id: object) -> IdeaProposal:
    """
    Approve the proposal and make it readable to the idea's owner.

    Refused until the approval itself has reached the owner: they hear "your idea was
    approved" in the decision letter first, and only then receive the proposal.
    """
    from reviews import decision_letters

    if decision_letters.pending_letter(_idea(idea_id)) is not None:
        raise ProposalError(
            'Send the approval letter to the owner first, from the Decisions page. The owner '
            'hears that their idea was approved before they receive its proposal.'
        )
    idea, proposal = _decide(admin, idea_id, 'released', AdminAuditEntry.Action.PROPOSAL_RELEASED)
    _notify(
        [idea.author],
        'proposal.released',
        f'Your proposal for "{idea.title}" is ready to read',
        'The platform has prepared a proposal for your idea. Read it carefully; if it looks '
        'right, give your go-ahead.',
        idea,
    )
    return proposal


@transaction.atomic
def request_changes(admin: User | None, idea_id: object, feedback: str) -> IdeaProposal:
    idea, proposal = _decide(
        admin,
        idea_id,
        'changes_requested',
        AdminAuditEntry.Action.PROPOSAL_CHANGES_REQUESTED,
        feedback=feedback,
    )
    team = proposal.team
    recipients = [m.user for m in team.members.select_related('user')] if team else []
    if not recipients and proposal.created_by:
        recipients = [proposal.created_by]
    _notify(
        recipients,
        'proposal.changes_requested',
        f'The proposal for "{idea.title}" was sent back',
        proposal.review_feedback,
        idea,
    )
    return proposal


@transaction.atomic
def decline(admin: User | None, idea_id: object, reason: str) -> IdeaProposal:
    """The platform will not develop this idea. The owner is told why; the idea stays approved."""
    idea, proposal = _decide(
        admin, idea_id, 'declined', AdminAuditEntry.Action.PROPOSAL_DECLINED, feedback=reason
    )
    _notify(
        [idea.author],
        'proposal.declined',
        f'"{idea.title}" will not be developed',
        proposal.review_feedback,
        idea,
    )
    return proposal


# --- reading --------------------------------------------------------------------------------------


def viewer_role(user: User | None, idea: Idea) -> str | None:
    """
    How `user` relates to this idea's proposal: 'writer', 'admin', 'owner', or `None`.

    Decided here and nowhere else, so what the list, the page and the mutation each think
    "may see it" cannot drift apart.
    """
    if user is None or not user.is_active:
        return None
    writers = writers_for(idea)
    if writers is not None and user.pk in writers.member_ids and idea.author_id != user.pk:
        return 'writer'
    if admin_authorization.capabilities_for(user).can_release_proposals:
        return 'admin'
    if idea.author_id == user.pk:
        return 'owner'
    return None


def proposal_for(user: User | None, idea_id: object) -> tuple[IdeaProposal, str] | None:
    """The proposal and the reader's role, or `None` for every reason it cannot be shown."""
    idea = Idea.objects.filter(pk=_as_int(idea_id)).first()
    if idea is None:
        return None
    role = viewer_role(user, idea)
    proposal = IdeaProposal.objects.select_related('team', 'idea').filter(idea=idea).first()
    if role is None or proposal is None:
        return None
    visible = {
        'writer': True,
        # The admin follows a draft as it is written - read-only: deciding still waits for the
        # lead to send it.
        'admin': True,
        'owner': proposal.status == 'released',
    }[role]
    return (proposal, role) if visible else None


def is_writer_lead(user: User | None, idea: Idea) -> bool:
    writers = writers_for(idea)
    return writers is not None and user is not None and user.pk == writers.lead_id


@transaction.atomic
def record_view(user: User | None, idea_id: object) -> IdeaProposalView:
    """Note that the owner opened the released proposal. Refused for anyone else."""
    found = proposal_for(user, idea_id)
    if found is None or found[1] != 'owner':
        raise ProposalError(UNAVAILABLE)
    return IdeaProposalView.objects.create(proposal=found[0], viewer=user)


def decided_proposals(admin: User | None) -> list[IdeaProposal]:
    """Proposals that have reached the admin, newest first. Empty without the permission."""
    if not admin_authorization.capabilities_for(admin).can_release_proposals:
        return []
    return list(
        IdeaProposal.objects.exclude(status='draft')
        .select_related('idea', 'team')
        .order_by('-submitted_at', '-pk')[:200]
    )


# --- progress, for the admin -----------------------------------------------------------------

#: Where a row sits on the board: the work still moving first, the decided last.
_BOARD_ORDER = {
    'submitted': 0,
    'changes_requested': 1,
    'draft': 2,
    'not_started': 3,
    'released': 4,
    'declined': 5,
}


@dataclass(frozen=True)
class Participant:
    """Someone with a hand, or a seat, in writing one proposal."""

    user: User
    role: str  # 'lead', 'member', or 'former' (contributed, no longer on the team)
    contributions: int
    sections: tuple[str, ...]
    last_contributed_at: object  # datetime | None


@dataclass(frozen=True)
class ProgressRow:
    idea: Idea
    proposal: IdeaProposal | None
    status: str  # a proposal status, or 'not_started'
    team_name: str | None
    participants: tuple[Participant, ...]
    filled: tuple[str, ...]
    total: int
    missing_required: tuple[str, ...]
    activity: tuple[IdeaProposalContribution, ...]


def _participants(writers: Writers | None, contributions) -> tuple[Participant, ...]:
    seats = dict.fromkeys(writers.member_ids, 'member') if writers else {}
    if writers is not None:
        seats[writers.lead_id] = 'lead'
    by_person: dict[int, list[IdeaProposalContribution]] = {}
    for contribution in contributions:
        by_person.setdefault(contribution.contributor_id, []).append(contribution)
    people = User.objects.in_bulk(set(seats) | set(by_person))

    rows = []
    for pk, user in people.items():
        mine = by_person.get(pk, [])
        sections = sorted({s for c in mine for s in c.sections}, key=FIELDS.index)
        rows.append(
            Participant(
                user=user,
                role=seats.get(pk, 'former'),
                contributions=len(mine),
                sections=tuple(sections),
                last_contributed_at=max((c.created_at for c in mine), default=None),
            )
        )
    rank = {'lead': 0, 'member': 1, 'former': 2}
    rows.sort(key=lambda p: (rank[p.role], -p.contributions, p.user.email))
    return tuple(rows)


def progress_board(admin: User | None) -> list[ProgressRow]:
    """
    Every approved idea's proposal, as far as it has got - including ideas whose team has not
    started one yet, and drafts still being written - with who is on the team and what each
    of them has actually done. Empty without the release permission.
    """
    if not admin_authorization.capabilities_for(admin).can_release_proposals:
        return []
    written = list(
        IdeaProposal.objects.select_related('idea', 'team')
        .prefetch_related(
            Prefetch(
                'contributions',
                queryset=IdeaProposalContribution.objects.select_related('contributor'),
            )
        )
        .order_by('-updated_at', '-pk')[:200]
    )
    waiting = list(
        Idea.objects.filter(status=Idea.Status.APPROVED, proposal__isnull=True).order_by(
            '-updated_at', '-pk'
        )[:200]
    )

    rows = []
    for proposal in written:
        contributions = list(proposal.contributions.all())
        rows.append(
            ProgressRow(
                idea=proposal.idea,
                proposal=proposal,
                status=proposal.status,
                team_name=proposal.team.name if proposal.team_id else None,
                participants=_participants(writers_for(proposal.idea), contributions),
                filled=tuple(
                    f for f in applicable_fields(proposal) if getattr(proposal, f).strip()
                ),
                total=len(applicable_fields(proposal)),
                missing_required=tuple(f for f, _ in missing_required(proposal)),
                activity=tuple(contributions[:10]),
            )
        )
    for idea in waiting:
        writers = writers_for(idea)
        team = None
        if writers is not None and writers.team_id is not None:
            team = ReviewTeam.objects.filter(pk=writers.team_id).values_list('name', flat=True)
        rows.append(
            ProgressRow(
                idea=idea,
                proposal=None,
                status='not_started',
                team_name=team.first() if team is not None else None,
                participants=_participants(writers, []),
                filled=(),
                total=len(FIELDS),
                missing_required=tuple(f for f, _ in missing_required(None)),
                activity=(),
            )
        )
    rows.sort(key=lambda r: _BOARD_ORDER.get(r.status, 9))  # stable: newest first within
    return rows
