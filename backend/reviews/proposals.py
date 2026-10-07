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
from django.db.models import Q
from django.utils import timezone

from administration import authorization as admin_authorization
from administration import services as admin_services
from administration.models import AdminAuditEntry
from ideas.models import Idea
from identity.models import User
from reviews.models import IdeaProposal, IdeaProposalView, Review, ReviewAssignment

logger = logging.getLogger(__name__)

FIELDS = (
    'title',
    'executive_summary',
    'problem',
    'proposed_solution',
    'requirements_summary',
    'scope',
    'deliverables',
    'risks',
    'assumptions',
    'estimated_effort',
    'estimated_timeline',
    'acceptance_criteria',
)
_REQUIRED = (
    ('executive_summary', 'executive summary'),
    ('problem', 'problem'),
    ('proposed_solution', 'proposed solution'),
    ('scope', 'scope'),
    ('deliverables', 'deliverables'),
    ('estimated_timeline', 'timeline'),
    ('acceptance_criteria', 'acceptance criteria'),
)
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


# --- writing ---------------------------------------------------------------------------------


@transaction.atomic
def start_proposal(user: User | None, idea_id: object) -> IdeaProposal:
    """Begin the proposal for an approved idea, pre-filled from what is already known."""
    idea = _idea(idea_id)
    writers = _require_writer(user, idea)
    if idea.status != Idea.Status.APPROVED:
        raise ProposalError('A proposal is written once the idea has been approved.')
    if IdeaProposal.objects.filter(idea=idea).exists():
        raise ProposalError('This idea already has a proposal.')

    proposal = IdeaProposal.objects.create(
        idea=idea,
        team_id=writers.team_id,
        title=idea.title,
        problem=idea.description,
        created_by=user,
    )
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
        limit = {'title': 200, 'estimated_effort': 120, 'estimated_timeline': 120}.get(field)
        text = _clean(value, field, limit=limit)
        if field == 'title' and not text:
            raise ProposalError('Title is required.', field='title')
        setattr(proposal, field, text)
        changed.append(field)
    if changed:
        proposal.save(update_fields=[*changed, 'updated_at'])
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
    for field, label in _REQUIRED:
        if not getattr(proposal, field).strip():
            raise ProposalError(f'Fill in the {label} before sending it.', field=field)

    proposal.status = 'submitted'
    proposal.submitted_by = user
    proposal.submitted_at = timezone.now()
    proposal.review_feedback = ''
    proposal.save(update_fields=['status', 'submitted_by', 'submitted_at', 'review_feedback'])
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
    """Approve the proposal and make it readable to the idea's owner."""
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
        'admin': proposal.status != 'draft',
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
