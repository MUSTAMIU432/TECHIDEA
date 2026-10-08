"""
GraphQL for decision letters (`reviews.decision_letters`).

Business refusals are `success: false` payloads with the field they name, like every
other domain. Who may do what is `decision_letters`' to decide; nothing here does.
"""

import strawberry

from administration.authorization import AdministrationError
from reviews import decision_letters
from reviews.models import DecisionLetter


@strawberry.type(description='An approval or rejection, as the console handles it.')
class DecisionLetterType:
    id: strawberry.ID
    decision: str = strawberry.field(description='`approved` or `rejected`.')
    status: str = strawberry.field(description='`pending` (waiting to be sent) or `sent`.')
    message: str
    created_at: str
    sent_at: str | None
    sent_by_name: str | None
    idea_id: strawberry.ID
    idea_title: str
    owner_name: str
    owner_email: str
    reviewer_name: str
    review_round: int
    review_feedback: str = strawberry.field(
        description="The reviewer's feedback on the deciding round, for the administrator "
        'to read before sending.'
    )

    @staticmethod
    def from_model(letter: DecisionLetter) -> 'DecisionLetterType':
        review = letter.review
        decider = review.decided_by or review.reviewer
        owner = letter.idea.author
        return DecisionLetterType(
            id=strawberry.ID(str(letter.pk)),
            decision=letter.decision,
            status=letter.status,
            message=letter.message,
            created_at=letter.created_at.isoformat(),
            sent_at=letter.sent_at.isoformat() if letter.sent_at else None,
            sent_by_name=(
                letter.sent_by.get_full_name() or letter.sent_by.email if letter.sent_by else None
            ),
            idea_id=strawberry.ID(str(letter.idea_id)),
            idea_title=letter.idea.title,
            owner_name=owner.get_full_name() or owner.email,
            owner_email=owner.email,
            reviewer_name=decider.get_full_name() or decider.email,
            review_round=review.round,
            review_feedback=review.feedback,
        )


@strawberry.type(description="The letter an idea's owner was sent about the platform's decision.")
class OwnerDecisionLetterType:
    decision: str
    message: str
    sent_at: str


@strawberry.type
class DecisionLetterPayload:
    success: bool
    message: str
    field: str | None = None
    letter: DecisionLetterType | None = None


@strawberry.type
class Query:
    @strawberry.field(
        description='Decision letters for the console, waiting ones first. Empty without the '
        'permission to release proposals.'
    )
    def decision_letters(
        self, info: strawberry.Info, status: str | None = None
    ) -> list[DecisionLetterType]:
        return [
            DecisionLetterType.from_model(letter)
            for letter in decision_letters.list_letters(info.context.user, status)
        ]

    @strawberry.field(
        description="The decision letter sent to this idea's owner. Null for anybody but the "
        "idea's author, and until a letter has been sent."
    )
    def idea_decision_letter(
        self, info: strawberry.Info, idea_id: strawberry.ID
    ) -> OwnerDecisionLetterType | None:
        user = info.context.user
        letter = decision_letters.sent_letter_for(user, idea_id)
        if letter is None or letter.idea.author_id != getattr(user, 'pk', None):
            return None
        return OwnerDecisionLetterType(
            decision=letter.decision, message=letter.message, sent_at=letter.sent_at.isoformat()
        )


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description="Send a decision letter to the idea's owner, with the text as edited."
    )
    def send_decision_letter(
        self, info: strawberry.Info, id: strawberry.ID, message: str | None = None
    ) -> DecisionLetterPayload:
        try:
            letter = decision_letters.send(info.context.user, id, message)
        except AdministrationError as exc:
            return DecisionLetterPayload(success=False, message=exc.message, field=exc.field)
        return DecisionLetterPayload(
            success=True,
            message='The letter was sent to the owner.',
            letter=DecisionLetterType.from_model(letter),
        )
