"""
Writing ideas: create, edit, submit (S2-002).

The three operations that make up the first vertical slice of the Ideas
domain, and nothing else. `add_comment`, `vote_for_idea` and the review
transitions are later sprints; this module does not stub them, does not
half-implement them, and does not define the vocabulary they would use.

Who may do what
---------------
- **create_idea** requires an authenticated, active user with an **active
  membership** in the target organization. There is no idea without a tenant,
  and no tenant without a membership, so that single check is what makes
  `Idea.organization` trustworthy on every row.
- **update_idea** and **submit_idea** require the caller to be **the author**
  *and* to still hold an active membership of the idea's organization. Both
  halves matter and for different reasons: authorship is who owns the
  content, and membership is whether they may still act in that tenant at
  all. A member who leaves an organization must not be able to keep editing
  ideas in it, and an author who is somehow not a member has no business
  writing into it.

There is deliberately no `ideas/permissions.py` and no Ideas-specific
permission code. S2-001 committed to that: authorization stays in
`organizations.authorization`, which is write/act-shaped, and the one
Ideas-specific question - *who may read this?* - is a read filter in
`ideas/selectors.py` built on the same membership. Adding a parallel
`idea.create` code here would have been a second answer to a question the
platform already answers.

What the client is never trusted with
-------------------------------------
`organization_id` and the author are **inputs to a decision, not values to be
written**. The organization is authorized and then used; the author is always
the authenticated user and is never read from the request. Neither can be
supplied, overridden, or "corrected" by a caller - which is why neither
appears in any input dataclass here. `update_idea`'s input likewise has no
`organization`, `author` or `status` field, so "change the tenant", "become
somebody else" and "mark this submitted by hand" are not operations this
module has; they are operations it does not have the vocabulary for.

Refusal never confirms existence
--------------------------------
An idea the caller may not touch - another author's, another tenant's, or one
that does not exist - produces the same `IdeaError` with the same message
("Idea is unavailable."). Distinguishing them would turn every one of these
three operations into a probe for which idea ids are real.
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from ideas import selectors
from ideas.models import Category, Idea
from identity.models import User
from organizations import authorization
from organizations.authorization import AuthorizationError
from organizations.models import Membership


class IdeaError(AuthorizationError):
    """
    Any refusal from this module.

    Carries the same `(message, field, reason)` triple as
    `organizations.services.OrganizationError` and is rendered the same way
    by `ideas/schema.py` - one failure convention for the whole API, so the
    frontend has a single shape to handle rather than one per domain.

    `field` names the input at fault so the resolver can put the message next
    to it; it is `None` for anything that is not a field-level problem (a
    throttle, a tenancy refusal, an already-submitted idea).
    """

    def __init__(self, message: str, field: str | None = None, reason: str = 'forbidden'):
        super().__init__(message, reason=reason, field=field)


@dataclass(frozen=True)
class IdeaInput:
    """
    The writable content of an idea.

    Exactly the four fields S2-002's form collects, and nothing else - see the
    module docstring for why ownership, tenant and status are absent. The
    model's optional `problem_statement`, `proposed_solution` and
    `expected_benefit` are also absent: they are content for a later slice,
    and adding them to a public input type before anything writes them would
    be an API nobody can yet fill in.
    """

    title: str
    description: str = ''
    category_id: object | None = None
    visibility: str | None = None


# The rules below are the "what must be filled in to submit" that
# `ideas/models.py` deliberately did *not* express on the model: a DRAFT is
# incomplete by definition, so completeness is a property of the transition,
# not of the schema. See that module's docstring.
MIN_DESCRIPTION_LENGTH = 20
MAX_TITLE_LENGTH = Idea._meta.get_field('title').max_length

# `DEPARTMENT` is reserved vocabulary (see `Idea.Visibility`) with no
# Department model behind it, so nothing could honour it. Accepting it would
# mean storing an idea its author believes is department-scoped while every
# reader of this code treats it as private or, later, organization-wide.
# Refusing it is the fail-closed choice; the value becomes selectable the day
# the department tier does.
SELECTABLE_VISIBILITIES = (
    Idea.Visibility.PUBLIC,
    Idea.Visibility.ORGANIZATION,
    Idea.Visibility.PRIVATE,
)


def _require_active_user(user: User | None) -> User:
    if user is None or not user.is_active:
        raise IdeaError('You must be signed in to work with ideas.', reason='unauthenticated')
    return user


def _require_membership(user: User, organization_id: object) -> Membership:
    """
    Refuse unless `user` is an active member of `organization_id`, and return
    that membership.

    The same predicate `organizations.authorization` authorizes writes with,
    used directly rather than through a permission code - see the module
    docstring for why Ideas adds none.

    Returning the membership rather than a boolean is deliberate: it is the
    authorization decision itself, and it carries the very organization row the
    decision was made about (`get_membership` select-relates it). So the
    caller writes the idea into the organization that was just authorized
    instead of looking the id up a second time - which is both one query
    cheaper and impossible to get subtly wrong, since a second lookup could
    in principle resolve to something else.
    """
    try:
        normalized_id = int(str(organization_id))
    except (TypeError, ValueError):
        raise IdeaError('Idea is unavailable.', reason='forbidden') from None

    membership = authorization.get_membership(user, normalized_id)
    if membership is None:
        raise IdeaError(
            'You must be an active member of this organization to file ideas here.',
            reason='membership_required',
        )
    return membership


def _validate_title(title: str | None) -> str:
    normalized = (title or '').strip()
    if not normalized:
        raise IdeaError('Give the idea a title.', field='title')
    if len(normalized) > MAX_TITLE_LENGTH:
        raise IdeaError(
            f'The title must be {MAX_TITLE_LENGTH} characters or fewer.',
            field='title',
        )
    return normalized


def _validate_description(description: str | None) -> str:
    return (description or '').strip()


def _resolve_category(category_id: object | None) -> Category | None:
    """
    The category to file under, or `None` for an unclassified idea.

    A category is optional on a draft - an idea can be written before it is
    classified - but a *bad* one is refused rather than silently dropped,
    because silently dropping it would hand back a success the caller did not
    ask for. A retired (`is_active=False`) category is refused for the same
    reason: filing something new under a category the organization has
    retired is almost certainly a mistake, and the picker does not offer it.
    """
    if category_id is None or (isinstance(category_id, str) and not category_id.strip()):
        return None

    try:
        normalized_id = int(str(category_id))
    except (TypeError, ValueError):
        raise IdeaError('Choose a valid category.', field='category') from None

    category = selectors.list_active_categories().filter(pk=normalized_id).first()
    if category is None:
        raise IdeaError('Choose a valid category.', field='category')
    return category


def _resolve_visibility(visibility: str | None) -> str | None:
    """
    The visibility to store, or `None` to keep the model default.

    Case-normalized, because the value arrives from a GraphQL enum or a
    `<select>` and neither is worth rejecting over casing - but validated
    against the *selectable* set rather than the full enum, so `department`
    cannot be stored by sending it directly.
    """
    if visibility is None or not str(visibility).strip():
        return None

    normalized = str(visibility).strip().lower()
    if normalized not in SELECTABLE_VISIBILITIES:
        raise IdeaError('Choose who can see this idea.', field='visibility')
    return normalized


def _load_owned_draft(user: User, idea_id: object) -> Idea:
    """
    The idea identified by `idea_id`, if `user` may edit it right now.

    Every refusal below produces the same message, because they are the same
    answer: this operation is not available to you on this id. Covered:
    no such id, another tenant's idea, somebody else's idea in your own
    organization, and your own idea that is past the draft phase.
    """
    # Read through the public selector rather than reaching for a queryset
    # here: this is the only place in the codebase that needs the row behind a
    # refusal, and `get_idea` already returns exactly the ideas this caller
    # may see. An author can always see their own idea (`can_view_idea` says
    # so unconditionally), so a row that comes back is either the caller's or
    # nothing - which is the same answer either way.
    idea = selectors.get_idea(user, idea_id)
    if idea is None or idea.author_id != user.pk:
        raise IdeaError('Idea is unavailable.', reason='forbidden')

    # Membership is re-checked on every write, not only at creation: leaving
    # an organization has to take the ability to write into it with you.
    if authorization.get_membership(user, idea.organization_id) is None:
        raise IdeaError(
            'You must be an active member of this organization to work with ideas here.',
            reason='membership_required',
        )

    if idea.status != Idea.Status.DRAFT:
        # S2-002 owns DRAFT -> SUBMITTED and nothing else, so a submitted
        # idea is not editable here. The Sprint 3 review transitions
        # (CHANGES_REQUESTED -> a new draft, for instance) will widen this.
        raise IdeaError('Only a draft can be edited.', reason='forbidden')

    return idea


def _validate_for_submission(idea: Idea) -> None:
    """
    What a draft must contain before it can be submitted.

    A rule of the transition, so it lives here rather than on the model: a
    draft is *supposed* to be incomplete, and a `null=False` column or a
    database CHECK would make a half-written idea unsaveable.

    Reported as a whole-form error (`field=None`) rather than per field: this
    runs only on submit, where the author's next action is to go and fill the
    gaps in the form above them, not to have one input highlighted.
    """
    if not idea.title.strip():
        raise IdeaError('Add a title before submitting this idea.')
    if len(idea.description.strip()) < MIN_DESCRIPTION_LENGTH:
        raise IdeaError(
            f'Describe the problem in at least {MIN_DESCRIPTION_LENGTH} characters '
            'before submitting.'
        )
    if idea.category_id is None:
        raise IdeaError('Choose a category before submitting this idea.')


def create_idea(user: User | None, organization_id: object, data: IdeaInput) -> Idea:
    """
    File a new idea as a DRAFT.

    The author is `user` - resolved from the access token by
    `identity.authentication` and passed in by the resolver, never read from
    `data`. The status is the model default (`DRAFT`) and the visibility is
    the model default (`PRIVATE`, fail closed) unless the caller chose one, so
    a new idea is visible to nobody but its author until somebody deliberately
    widens it.
    """
    active_user = _require_active_user(user)
    membership = _require_membership(active_user, organization_id)

    title = _validate_title(data.title)
    description = _validate_description(data.description)
    category = _resolve_category(data.category_id)
    visibility = _resolve_visibility(data.visibility)

    return Idea.objects.create(
        organization=membership.organization,
        author=active_user,
        title=title,
        description=description,
        category=category,
        **({'visibility': visibility} if visibility else {}),
    )


def update_idea(user: User | None, idea_id: object, data: IdeaInput) -> Idea:
    """
    Edit the author's own draft.

    `data` carries content only. `organization`, `author` and `status` are
    not fields on `IdeaInput`, so there is no code path here that could move
    an idea to another tenant, hand it to another author, or mark it
    submitted by hand - the only way to submit is `submit_idea`.
    """
    active_user = _require_active_user(user)
    idea = _load_owned_draft(active_user, idea_id)

    idea.title = _validate_title(data.title)
    idea.description = _validate_description(data.description)
    idea.category = _resolve_category(data.category_id)

    visibility = _resolve_visibility(data.visibility)
    if visibility:
        idea.visibility = visibility

    # `save()` runs `full_clean()`, so the model's own invariants - the
    # status/`submitted_at` pairing included - hold on this write path too.
    idea.save()
    return idea


def submit_idea(user: User | None, idea_id: object) -> Idea:
    """
    DRAFT -> SUBMITTED, once, by its author.

    Stamps `submitted_at` in the same transaction as the status change, so
    there is no window in which an idea is `SUBMITTED` with no timestamp -
    which the model itself treats as an inconsistent row. The transition is
    not reversible here and nothing else advances the lifecycle: review
    (`UNDER_REVIEW`, `CHANGES_REQUESTED`, `REJECTED`, `APPROVED`) and
    `AUTOMATION_PROPOSAL` are Sprint 3's.
    """
    active_user = _require_active_user(user)
    idea = _load_owned_draft(active_user, idea_id)

    _validate_for_submission(idea)

    with transaction.atomic():
        idea.status = Idea.Status.SUBMITTED
        idea.submitted_at = timezone.now()
        idea.save()

    return idea
