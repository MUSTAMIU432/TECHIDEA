"""
The owner's response to a request for changes.

A changes request is the reviewers asking the owner for something. The owner answers by
revising the idea (the edit form), adding documents (the evidence panel), and - here -
saying what they changed, which `respond` records **and** resubmits in one transaction:
a response without a resubmission would tell the reviewers about changes they cannot
see, and a resubmission is refused here exactly as `submitIdea` would refuse it.

Who reads a response: the idea's author, and the reviewers of the track that asked -
platform reviewers for a platform round, the organization's or team's reviewers for
theirs. Nobody else, any more than they read the review it answers.
"""

from dataclasses import dataclass

from django.db import transaction

from ideas import selectors as idea_selectors
from ideas import services as idea_services
from ideas.models import Idea
from identity.models import User
from reviews import eligibility
from reviews.models import ChangeResponse, Review

MESSAGE_MAX_LENGTH = 3000
_ANSWERABLE = (Idea.Status.CHANGES_REQUESTED, Idea.Status.ORGANIZATION_CHANGES_REQUESTED)


class ChangeResponseError(Exception):
    """A refused response, in the project's `(message, field)` shape."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _round_that_asked(idea: Idea) -> Review | None:
    """The completed round whose request for changes the owner is answering."""
    scope = (
        Review.Scope.PLATFORM
        if idea.status == Idea.Status.CHANGES_REQUESTED
        else Review.Scope.ORGANIZATION
    )
    return (
        Review.objects.filter(
            idea=idea,
            scope=scope,
            decision=Review.Decision.CHANGES_REQUESTED,
            completed_at__isnull=False,
        )
        .order_by('-completed_at', '-pk')
        .first()
    )


@transaction.atomic
def respond(user: User | None, idea_id: object, message: str) -> tuple[ChangeResponse, Idea]:
    """
    Record what the owner changed, and resubmit the idea for the next round.

    The resubmission is `ideas.services.submit_idea` itself - the same permission, the
    same validation, the same next stage - so this cannot put forward an idea the
    ordinary resubmission would refuse; if it is refused, the response is not kept.
    """
    if user is None or not user.is_active:
        raise ChangeResponseError('Sign in to continue.')
    idea = idea_selectors.get_idea(user, idea_id)
    if idea is None:
        raise ChangeResponseError('Idea is unavailable.')
    if idea.status not in _ANSWERABLE:
        raise ChangeResponseError('Nobody is waiting for changes on this idea.')

    text = (message or '').strip()
    if not text:
        raise ChangeResponseError(
            'Tell the reviewers what you changed before resubmitting.', field='message'
        )
    if len(text) > MESSAGE_MAX_LENGTH:
        raise ChangeResponseError(
            f'Keep the response to {MESSAGE_MAX_LENGTH} characters or fewer.', field='message'
        )

    review = _round_that_asked(idea)
    if review is None:
        raise ChangeResponseError('There is no request for changes to answer.')
    if ChangeResponse.objects.filter(review=review).exists():
        raise ChangeResponseError('This request for changes has already been answered.')

    response = ChangeResponse.objects.create(idea=idea, review=review, message=text, author=user)
    try:
        moved = idea_services.submit_idea(user, idea.pk)
    except idea_services.IdeaError as exc:
        # Rolls the response back with the transaction: nothing is half-done.
        raise ChangeResponseError(exc.message, field=getattr(exc, 'field', None)) from None
    return response, moved


@dataclass(frozen=True)
class ResponseView:
    response: ChangeResponse
    review: Review


def responses_for(user: User | None, idea_id: object) -> list[ResponseView]:
    """The responses on an idea `user` may read, newest first; empty for anybody else."""
    idea = idea_selectors.get_idea(user, idea_id)
    if idea is None:
        return []
    responses = list(ChangeResponse.objects.filter(idea=idea).select_related('review', 'author'))
    is_author = idea.author_id == getattr(user, 'pk', None)
    platform_reviewer = eligibility.can_review(user, idea)
    other_reviewer = eligibility.can_organization_review(user, idea)
    return [
        ResponseView(response, response.review)
        for response in responses
        if is_author
        or (platform_reviewer and response.review.scope == Review.Scope.PLATFORM)
        or (other_reviewer and response.review.scope == Review.Scope.ORGANIZATION)
    ]
