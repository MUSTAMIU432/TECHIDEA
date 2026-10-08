"""
A platform reviewer's work: every idea they or their review team is handling, where it
stands, and what - if anything - is waiting on them.

The notifications tell a reviewer that something happened; this is where they see the
whole picture and act. One row per idea, from the review through the proposal to
delivery, each with the step of the journey it has reached, the next action when it is
the reviewer's, and the page where that action is taken.

Which ideas: those routed to a review team the reviewer belongs to, and those they
reviewed themselves. Read-only, and computed from the records that decide each step -
the idea's status, its open round, its decision letter, its proposal and the owner's
answers - so it cannot disagree with them.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q

from ideas.models import Idea
from identity.models import User
from reviews import eligibility
from reviews.models import (
    DecisionLetter,
    IdeaProposal,
    ProposalAnswer,
    Review,
    ReviewAssignment,
    ReviewTeamMember,
)

#: The journey's steps, in order, as the progress bar draws them.
STEPS = ('Review', 'Decision', 'Proposal', 'Platform admin', 'Owner', 'Delivery')


@dataclass(frozen=True)
class WorkItem:
    idea: Idea
    stage: str
    stage_label: str
    step: int
    action_label: str | None
    action_path: str | None
    team_name: str | None
    is_lead: bool
    updated_at: datetime

    @property
    def needs_me(self) -> bool:
        return self.action_label is not None


def _review_path(idea: Idea) -> str:
    return f'/app/reviews?idea={idea.pk}'


def _proposal_path(idea: Idea) -> str:
    return f'/app/reviews/proposals/{idea.pk}'


def _proposal_stage(idea: Idea, proposal: IdeaProposal | None, is_writer: bool, is_lead: bool):
    """Where an approved idea's proposal stands: (stage, label, step, action)."""
    if proposal is None:
        return (
            'write_proposal',
            'Approved - the proposal can be written',
            2,
            ('Start the proposal' if is_writer else None),
        )
    if proposal.status == 'draft':
        return (
            'writing',
            'Proposal being written',
            2,
            ('Continue the proposal' if is_writer else None),
        )
    if proposal.status == 'changes_requested':
        return (
            'sent_back',
            'The admin sent the proposal back',
            2,
            ('Revise the proposal' if is_writer else None),
        )
    if proposal.status == 'submitted':
        return 'with_admin', 'Proposal with the platform admin', 3, None
    if proposal.status == 'declined':
        return 'proposal_declined', 'The admin declined the proposal', 3, None
    answer = ProposalAnswer.objects.filter(proposal=proposal).first()
    if answer is None:
        return 'with_owner', 'Proposal with the owner - waiting for their answer', 4, None
    if answer.decision == ProposalAnswer.Decision.DECLINE:
        return 'owner_declined', 'The owner decided not to go ahead', 4, None
    return 'in_delivery', 'The owner gave the go-ahead', 5, None


def _item_for(user: User, idea: Idea, team_name, is_lead, is_writer) -> WorkItem | None:
    status = idea.status
    pending_letter = DecisionLetter.objects.filter(
        idea=idea, status=DecisionLetter.Status.PENDING
    ).exists()
    action_path = _review_path(idea)
    if status == Idea.Status.SUBMITTED:
        stage, label, step, action = 'to_review', 'Waiting for review', 0, 'Start the review'
    elif status == Idea.Status.UNDER_REVIEW:
        mine = Review.objects.filter(
            idea=idea, scope=Review.Scope.PLATFORM, completed_at__isnull=True, reviewer=user
        ).exists()
        stage, label, step = 'reviewing', 'Under review', 0
        action = 'Continue the review' if mine or eligibility.can_review(user, idea) else None
    elif status == Idea.Status.CHANGES_REQUESTED:
        stage, label, step, action = (
            'with_owner_changes',
            "Sent back - waiting for the owner's changes",
            0,
            None,
        )
    elif status in (Idea.Status.APPROVED, Idea.Status.REJECTED) and pending_letter:
        verdict = 'Approved' if status == Idea.Status.APPROVED else 'Rejected'
        stage, label, step, action = (
            'awaiting_admin',
            f'{verdict} - waiting for the platform admin to tell the owner',
            1,
            None,
        )
    elif status == Idea.Status.REJECTED:
        stage, label, step, action = 'rejected', 'Rejected', 1, None
    elif status == Idea.Status.APPROVED:
        proposal = IdeaProposal.objects.filter(idea=idea).first()
        stage, label, step, action = _proposal_stage(idea, proposal, is_writer, is_lead)
        action_path = _proposal_path(idea)
    elif status in (Idea.Status.READY_FOR_IMPLEMENTATION, Idea.Status.AUTOMATION_PROPOSAL):
        stage, label, step, action = 'in_delivery', 'In delivery', 5, None
        action_path = _proposal_path(idea)
    else:
        return None
    return WorkItem(
        idea=idea,
        stage=stage,
        stage_label=label,
        step=step,
        action_label=action,
        action_path=action_path,
        team_name=team_name,
        is_lead=is_lead,
        updated_at=idea.updated_at,
    )


def workboard_for(user: User | None) -> list[WorkItem]:
    """The reviewer's ideas, those waiting on them first, then the most recently changed."""
    if user is None or not user.is_active or not eligibility.is_platform_reviewer(user):
        return []
    from reviews.proposals import writers_for

    team_ids = set(ReviewTeamMember.objects.filter(user=user).values_list('team_id', flat=True))
    routed = ReviewAssignment.objects.filter(team_id__in=team_ids, released_at__isnull=True)
    ideas = (
        Idea.objects.filter(
            Q(pk__in=routed.values('idea_id'))
            | Q(reviews__reviewer=user, reviews__scope=Review.Scope.PLATFORM)
        )
        .exclude(author=user)
        .distinct()
        .select_related('organization', 'team', 'author')
    )

    items = []
    for idea in ideas:
        assignment = (
            ReviewAssignment.objects.select_related('team')
            .filter(idea=idea, team__isnull=False, released_at__isnull=True)
            .first()
        )
        team = assignment.team if assignment else None
        writers = writers_for(idea) if idea.status != Idea.Status.SUBMITTED else None
        item = _item_for(
            user,
            idea,
            team.name if team else None,
            bool(team and team.lead_id == user.pk),
            bool(writers and user.pk in writers.member_ids),
        )
        if item is not None:
            items.append(item)
    items.sort(key=lambda item: (not item.needs_me, -item.updated_at.timestamp()))
    return items
