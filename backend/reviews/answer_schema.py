"""
GraphQL for the owner's answers to a released proposal (`reviews.proposal_answers`).

Refusals are `success: false` payloads with the field they name, like every other domain.
"""

from datetime import date

import strawberry

from reviews import proposal_answers
from reviews.models import ProposalAnswer


@strawberry.type(description="The owner's answers to the released proposal.")
class ProposalAnswerType:
    decision: str = strawberry.field(description='`proceed` or `decline`.')
    timeline: str = strawberry.field(description='`yes`, `discuss`, or empty for a decline.')
    payment: str = strawberry.field(description='`yes`, `discuss`, or empty when not paying.')
    preferred_start: str | None
    conditions: str
    decline_reason: str
    answered_by_name: str
    answered_at: str

    @staticmethod
    def from_model(record: ProposalAnswer) -> 'ProposalAnswerType':
        who = record.answered_by
        return ProposalAnswerType(
            decision=record.decision,
            timeline=record.timeline,
            payment=record.payment,
            preferred_start=record.preferred_start.isoformat() if record.preferred_start else None,
            conditions=record.conditions,
            decline_reason=record.decline_reason,
            answered_by_name=who.get_full_name() or who.email,
            answered_at=record.answered_at.isoformat(),
        )


@strawberry.input(description="The owner's answers. Timeline and payment are `yes` or `discuss`.")
class ProposalAnswerInput:
    idea_id: strawberry.ID
    decision: str
    timeline: str = ''
    payment: str = ''
    preferred_start: date | None = None
    conditions: str = ''
    decline_reason: str = ''


@strawberry.type
class ProposalAnswerPayload:
    success: bool
    message: str
    field: str | None = None
    answer: ProposalAnswerType | None = None


@strawberry.type
class Query:
    @strawberry.field(
        description="The owner's answers to this idea's proposal, for the owner, the review "
        'team, the admins, the delivery managers and the assigned developers. Null otherwise, '
        'and until the owner has answered.'
    )
    def proposal_answer(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> ProposalAnswerType | None:
        record = proposal_answers.answer_for(info.context.user, idea_id)
        return ProposalAnswerType.from_model(record) if record else None


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description='Answer the released proposal. Going ahead is also the go-ahead itself: '
        'the idea is handed to the delivery team in the same step.'
    )
    def answer_proposal(
        self, info: strawberry.Info, input: ProposalAnswerInput
    ) -> ProposalAnswerPayload:
        try:
            record = proposal_answers.answer(
                info.context.user,
                input.idea_id,
                decision=input.decision,
                timeline=input.timeline,
                payment=input.payment,
                preferred_start=input.preferred_start,
                conditions=input.conditions,
                decline_reason=input.decline_reason,
            )
        except proposal_answers.ProposalAnswerError as exc:
            return ProposalAnswerPayload(success=False, message=exc.message, field=exc.field)
        proceed = record.decision == ProposalAnswer.Decision.PROCEED
        return ProposalAnswerPayload(
            success=True,
            message=(
                'Thank you - your go-ahead is recorded. The delivery team will assign a '
                'developer, with your answers in front of them.'
                if proceed
                else 'Thank you - your decision is recorded and the team has been told.'
            ),
            answer=ProposalAnswerType.from_model(record),
        )
