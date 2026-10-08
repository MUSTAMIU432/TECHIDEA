"""
The owner's answers to a released proposal: go ahead with development or not, and on what terms.

The last step before delivery. The owner reads the proposal the admin released and answers
a short set of questions:

- **Go ahead?** Yes or no.
- If yes: do they accept the **timeline**, and - only when they pay - the **payment plan**
  (agreed, or needs discussion); a **preferred start**; and any **conditions** for the
  delivery team.
- If no: **why**, which is required - the team and the admin should know.

A *yes* is the owner's go-ahead itself (`ideas.go_ahead.confirm_go_ahead`), in the same
transaction: the opportunity opens ready for assignment and the delivery managers are
told, so the platform admin assigns developers knowing the owner's terms. A *no* records
the decision, tells the admin and the review team, and the go-ahead is refused from then on.

Who reads the answers: the owner, the review team that wrote the proposal, the platform
admins who release proposals, the delivery managers, and the developers assigned to it.
"""

from datetime import date

from django.db import transaction

from automation import authorization as automation_authorization
from ideas.models import Idea
from identity.models import User
from reviews import proposals
from reviews.models import IdeaProposal, ProposalAnswer

TEXT_MAX_LENGTH = 2000
_AGREEMENTS = {choice.value for choice in ProposalAnswer.Agreement}


class ProposalAnswerError(Exception):
    """A refused answer, in the project's `(message, field)` shape."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _text(value: str | None, field: str) -> str:
    text = (value or '').strip()
    if len(text) > TEXT_MAX_LENGTH:
        raise ProposalAnswerError(
            f'Keep this to {TEXT_MAX_LENGTH} characters or fewer.', field=field
        )
    return text


def _unanswered_proposal(user: User | None, idea_id: object) -> tuple[Idea, IdeaProposal]:
    """The owner's released, unanswered proposal, locked; refused for anybody else."""
    if user is None or not user.is_active:
        raise ProposalAnswerError('Sign in to continue.')
    idea = Idea.objects.filter(pk=proposals._as_int(idea_id)).first()
    if idea is None or proposals.viewer_role(user, idea) != 'owner':
        raise ProposalAnswerError('This proposal is not yours to answer.')
    proposal = IdeaProposal.objects.select_for_update().filter(idea=idea).first()
    if proposal is None or proposal.status != 'released':
        raise ProposalAnswerError('There is no released proposal to answer.')
    if ProposalAnswer.objects.filter(proposal=proposal).exists():
        raise ProposalAnswerError('You have already answered this proposal.')
    return idea, proposal


def _decline(user: User, idea: Idea, proposal: IdeaProposal, reason: str) -> ProposalAnswer:
    reason = _text(reason, 'declineReason')
    if not reason:
        raise ProposalAnswerError(
            'Tell the team why, so they understand your decision.', field='declineReason'
        )
    record = ProposalAnswer.objects.create(
        proposal=proposal,
        idea=idea,
        decision=ProposalAnswer.Decision.DECLINE,
        decline_reason=reason,
        answered_by=user,
    )
    record_pk = record.pk
    transaction.on_commit(lambda: _tell_about_the_decline(record_pk))
    return record


def _proceed(
    user: User,
    idea: Idea,
    proposal: IdeaProposal,
    *,
    timeline: str,
    payment: str,
    preferred_start: date | None,
    conditions: str,
) -> ProposalAnswer:
    if timeline not in _AGREEMENTS:
        raise ProposalAnswerError('Say whether you accept the timeline.', field='timeline')
    owner_pays = proposal.payment_required == 'yes'
    if owner_pays and payment not in _AGREEMENTS:
        raise ProposalAnswerError('Say whether you accept the payment plan.', field='payment')
    if preferred_start is not None and preferred_start < date.today():
        raise ProposalAnswerError(
            'Choose a start date from today onwards, or leave it empty.', field='preferredStart'
        )

    record = ProposalAnswer.objects.create(
        proposal=proposal,
        idea=idea,
        decision=ProposalAnswer.Decision.PROCEED,
        timeline=timeline,
        payment=payment if owner_pays else '',
        preferred_start=preferred_start,
        conditions=_text(conditions, 'conditions'),
        answered_by=user,
    )

    from ideas import go_ahead
    from ideas.services import IdeaError

    try:
        go_ahead.confirm_go_ahead(user, idea.pk)
    except IdeaError as exc:
        # Rolls the answers back with it: no terms without a go-ahead.
        raise ProposalAnswerError(exc.message) from None
    return record


@transaction.atomic
def answer(
    user: User | None,
    idea_id: object,
    *,
    decision: str,
    timeline: str = '',
    payment: str = '',
    preferred_start: date | None = None,
    conditions: str = '',
    decline_reason: str = '',
) -> ProposalAnswer:
    """Record the owner's answers; a *proceed* is also their go-ahead, in this transaction."""
    idea, proposal = _unanswered_proposal(user, idea_id)
    if decision == ProposalAnswer.Decision.DECLINE:
        return _decline(user, idea, proposal, decline_reason)
    if decision == ProposalAnswer.Decision.PROCEED:
        return _proceed(
            user,
            idea,
            proposal,
            timeline=timeline,
            payment=payment,
            preferred_start=preferred_start,
            conditions=conditions,
        )
    raise ProposalAnswerError('Choose whether to go ahead.', field='decision')


def _tell_about_the_decline(record_pk: int) -> None:
    from notifications import services as notification_services

    record = ProposalAnswer.objects.select_related('idea').filter(pk=record_pk).first()
    if record is None:
        return
    writers = proposals.writers_for(record.idea)
    team = list(User.objects.filter(pk__in=writers.member_ids, is_active=True)) if writers else []
    notification_services.deliver(
        recipients=[*proposals.release_managers(), *team],
        kind='proposal.owner_declined',
        title=f'The owner decided not to go ahead with "{record.idea.title}"',
        body='They read the proposal and chose not to proceed with development. Their reason '
        'is on the proposal.',
        idea=record.idea,
        send_email=False,
    )


def answer_for(user: User | None, idea_id: object) -> ProposalAnswer | None:
    """The owner's answers on this idea's proposal, for those who act on them; else None."""
    if user is None or not user.is_active:
        return None
    idea = Idea.objects.filter(pk=proposals._as_int(idea_id)).first()
    if idea is None:
        return None
    record = ProposalAnswer.objects.select_related('answered_by').filter(idea=idea).first()
    if record is None:
        return None
    if proposals.viewer_role(user, idea) in ('owner', 'writer', 'admin'):
        return record
    if automation_authorization.is_delivery_manager(user):
        return record
    opportunity = idea.automation_opportunities.exclude(status='cancelled').first()
    if opportunity is not None and automation_authorization.is_assigned_to(user, opportunity):
        return record
    return None
