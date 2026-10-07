"""
Writing ideas: create, edit, submit (S2-002), commenting on them (S2-005),
voting on them (S2-006), and attaching supporting evidence to them (S2-007).

The review workflow's queue is a later sprint; this module does not stub it,
does not half-implement it, and does not define the vocabulary it would use.
S2-006 added the vote operations, which follow this module's existing rule
that the client is never trusted with ownership: the voter is the
authenticated user and the idea is authorized and then used. S2-007's
attachment operations follow the same rule for the uploader, and additionally
never trust the client with the storage key (`ideas/storage.py` generates
it) or with the file's declared type (`ideas/attachments.py` derives and
confirms it from the bytes instead).

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

Commenting (S2-005)
--------------------
- **add_comment** requires an authenticated, active user who may **read** the
  idea, and an idea whose discussion is open (`lifecycle.discussion_is_open`).
  Deliberately *read*, not *member*: participation follows readability, which
  is the same rule `docs/ideas-domain.md` set for voting, and it is what lets
  anybody in the platform discuss a `PUBLIC` idea without being made a member
  of someone else's organization first. An idea is `PUBLIC` precisely because
  it is meant to be read and answered across tenants.
- **update_comment** and **delete_comment** require the caller to be **the
  author**, and the comment's idea to still be readable to them. There is no
  elevated path: no administrator, moderator or Owner may edit or delete
  somebody else's comment, because no such capability exists in
  `organizations.authorization` to draw it from, and inventing one here would
  be a second authorization system (see below).
- Retraction stays available in a closed discussion. Being able to take back
  what you wrote is not participating in the discussion, and a state change
  must not be able to strand a comment nobody may ever remove.
- **add_comment** also takes an optional `parent_id`, which makes the comment a
  *reply* to another comment on the same idea. It is resolved through the same
  readable-comment selector as everything else, so a reply can only ever hang
  off a comment the caller was shown; the model refuses a reply to a reply, so
  a thread is one level deep and needs no depth field to render. Deleting a
  comment takes its replies with it (`CASCADE` on the model) rather than
  promoting them, because a reply whose question is gone has nothing to answer.

Every refusal below is one message per kind, because each is one answer. An
unknown comment id, another author's comment, and a comment on an idea the
caller may not read are all `"Comment is unavailable."`; an unknown idea and
an unreadable one are both `"Idea is unavailable."` So a comment id is not an
oracle for which ideas or people exist, and an idea id is not an oracle for
which comments exist. The one refusal that names itself is a closed
discussion, because the caller can already see the idea and its status - see
`_load_discussable_idea`.

What the client is never trusted with
-------------------------------------
`organization_id` and the author are **inputs to a decision, not values to be
written**. The organization is authorized and then used; the author is always
the authenticated user and is never read from the request. Neither can be
supplied, overridden, or "corrected" by a caller - which is why neither
appears in any input dataclass here. `update_idea`'s input likewise has no
`organization`, `author` or `status` field, so "change the tenant", "become
somebody else" and "mark this submitted by hand" are not operations this
module has; they are operations it does not have the vocabulary for. Status
changes in general belong to `ideas.lifecycle`, which `submit_idea` below
delegates to rather than reimplementing.

Refusal never confirms existence
--------------------------------
An idea the caller may not touch - another author's, another tenant's, or one
that does not exist - produces the same `IdeaError` with the same message
("Idea is unavailable."). Distinguishing them would turn every one of these
three operations into a probe for which idea ids are real.
"""

import logging
from dataclasses import dataclass, field

from django.db import DatabaseError, IntegrityError, models, transaction

from ideas import attachments as attachment_rules
from ideas import selectors, storage
from ideas.models import Attachment, Category, Comment, Idea, Vote
from identity.models import User
from organizations import authorization
from organizations.authorization import AuthorizationError
from organizations.models import Membership

logger = logging.getLogger(__name__)


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

    The title, the problem description, its classification and visibility,
    and the problem story the guided intake form collects - and nothing else.
    See the module docstring for why ownership, tenant and status are absent.
    The model's `problem_statement` and `proposed_solution` are absent too:
    the first would duplicate `description`, and the second is deliberately
    never asked of the author (see `Idea`).

    Every story field defaults to "not answered", and an update writes the
    whole input: a field left out is cleared, exactly as an omitted
    `category_id` already was. The form always sends every field back.
    """

    title: str
    description: str = ''
    category_id: object | None = None
    visibility: str | None = None
    current_process: str = ''
    current_tools: list[str] = field(default_factory=list)
    current_tools_other: str = ''
    performed_by: str = ''
    affected_people: str = ''
    frequency: str | None = None
    time_required: str = ''
    people_involved: int | None = None
    impacts: list[str] = field(default_factory=list)
    impact_details: str = ''
    improvement_goal: str = ''
    desired_outcome: str = ''
    easier_for_people: str = ''
    expected_benefit: str = ''
    important_considerations: str = ''


# The rules below are the "what must be filled in to submit" that
# `ideas/models.py` deliberately did *not* express on the model: a DRAFT is
# incomplete by definition, so completeness is a property of the transition,
# not of the schema. See that module's docstring.
MIN_DESCRIPTION_LENGTH = 20
MAX_TITLE_LENGTH = Idea._meta.get_field('title').max_length

# A comment has a maximum, and it is a judgement call of the same kind as
# MIN_DESCRIPTION_LENGTH: `Comment.content` is a `TextField`, so the database
# will happily store a novel. The number is generous because a discussion reply
# is a paragraph rather than a sentence - someone answering a question with
# detail is the point - and bounded because an unbounded write on a table every
# member can read is a storage and rendering hazard, and because the frontend
# renders the content into a card that has to stay legible.
#
# Enforced by the service, not by a `MaxLengthValidator`, for the same reason
# the other content rules are: the rule is a product decision rather than a
# schema fact, and it is refused rather than truncated. Silently shortening
# somebody's comment would store text they did not write and return success -
# the caller would have no way to know the last line was lost.
MAX_COMMENT_LENGTH = 2000

# **The audience of an idea is not a choice; it follows the level it is filed at.**
#
# An individual idea is visible to its author (and, once submitted, to the
# platform's reviewers); a team idea to the team's members; an organization idea
# to the organization's members. There is no "everyone" - an idea never reaches
# people outside the tenant it belongs to except through the platform review
# track, which has its own read grant (`selectors.platform_reviewer_filter`).
#
# Kept as a column rather than computed on read, because every read path already
# filters on it and a stored value keeps those queries index-friendly. The
# service is the only writer and always writes the value below.
AUDIENCE_BY_CONTEXT = {
    Idea.SubmissionContext.INDIVIDUAL: Idea.Visibility.PRIVATE,
    Idea.SubmissionContext.TEAM: Idea.Visibility.TEAM,
    Idea.SubmissionContext.ORGANIZATION: Idea.Visibility.ORGANIZATION,
}


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


# --- the problem story ---------------------------------------------------------------
#
# Every story answer is optional, so the only rules are about shape: a known
# value for the closed vocabularies, a sensible bound for everything else.
# Refused rather than truncated, for the reason `MAX_COMMENT_LENGTH` gives -
# silently shortening somebody's answer would store text they did not write.

# A long answer is a few paragraphs; the cap keeps a text column every reader
# of the idea is served from being a storage and rendering hazard.
MAX_STORY_ANSWER_LENGTH = 5000
# "About how many people" is a rough headcount, and a number beyond this is a
# typo rather than a process.
MAX_PEOPLE_INVOLVED = 1_000_000

# (field, max length) for the free-text answers. The short ones take their
# bound from the column so the two cannot disagree.
_STORY_TEXT_FIELDS: tuple[tuple[str, int], ...] = (
    ('current_process', MAX_STORY_ANSWER_LENGTH),
    ('current_tools_other', Idea._meta.get_field('current_tools_other').max_length),
    ('performed_by', Idea._meta.get_field('performed_by').max_length),
    ('affected_people', Idea._meta.get_field('affected_people').max_length),
    ('time_required', Idea._meta.get_field('time_required').max_length),
    ('impact_details', MAX_STORY_ANSWER_LENGTH),
    ('improvement_goal', MAX_STORY_ANSWER_LENGTH),
    ('desired_outcome', MAX_STORY_ANSWER_LENGTH),
    ('easier_for_people', MAX_STORY_ANSWER_LENGTH),
    ('expected_benefit', MAX_STORY_ANSWER_LENGTH),
    ('important_considerations', MAX_STORY_ANSWER_LENGTH),
)


def _normalize_text(value: str | None) -> str:
    """Strip the ends and fold CRLF to LF - see `_validate_comment_content`."""
    return (value or '').replace('\r\n', '\n').replace('\r', '\n').strip()


def _validate_story_text(field_name: str, value: str | None, max_length: int) -> str:
    normalized = _normalize_text(value)
    if len(normalized) > max_length:
        raise IdeaError(
            f'This answer must be {max_length} characters or fewer.',
            field=field_name,
        )
    return normalized


def _validate_choice_list(field_name: str, values: list[str] | None, choices) -> list[str]:
    """
    A list of known values, de-duplicated, in the vocabulary's own order.

    Case-normalized for the same reason `_resolve_visibility` is. Stored in a
    canonical order so the same answer is always the same value - "email,
    paper" and "paper, email" are one answer, and comparing or querying them
    should not have to know that.
    """
    allowed = list(choices.values)
    chosen = set()
    for value in values or []:
        normalized = str(value).strip().lower()
        if normalized not in allowed:
            raise IdeaError('Choose from the options offered.', field=field_name)
        chosen.add(normalized)
    return [value for value in allowed if value in chosen]


def _validate_frequency(frequency: str | None) -> str:
    if frequency is None or not str(frequency).strip():
        return ''
    normalized = str(frequency).strip().lower()
    if normalized not in Idea.Frequency.values:
        raise IdeaError('Choose how often this happens.', field='frequency')
    return normalized


def _validate_people_involved(people_involved: object | None) -> int | None:
    if people_involved is None or (isinstance(people_involved, str) and not people_involved):
        return None
    try:
        normalized = int(str(people_involved))
    except (TypeError, ValueError):
        raise IdeaError('Enter a number of people.', field='people_involved') from None
    if normalized < 0 or normalized > MAX_PEOPLE_INVOLVED:
        raise IdeaError(
            f'Enter a number of people between 0 and {MAX_PEOPLE_INVOLVED:,}.',
            field='people_involved',
        )
    return normalized


def _validate_story(data: IdeaInput) -> dict[str, object]:
    """The problem-story columns to write, validated and normalized."""
    story: dict[str, object] = {
        name: _validate_story_text(name, getattr(data, name), max_length)
        for name, max_length in _STORY_TEXT_FIELDS
    }
    story['current_tools'] = _validate_choice_list(
        'current_tools', data.current_tools, Idea.CurrentTool
    )
    story['impacts'] = _validate_choice_list('impacts', data.impacts, Idea.Impact)
    story['frequency'] = _validate_frequency(data.frequency)
    story['people_involved'] = _validate_people_involved(data.people_involved)
    return story


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


def _resolve_visibility(visibility: str | None, context: str) -> str:
    """
    The audience to store for an idea at `context` level.

    Always the one the level dictates. A client that names an audience is not
    ignored: naming a *different* one is refused, in words about the level, so a
    caller that believes it is making an idea public finds out instead of getting
    a private one back. Case-normalized because the value arrives from a GraphQL
    enum.
    """
    audience = AUDIENCE_BY_CONTEXT[context]
    named = str(visibility).strip().lower() if visibility is not None else ''
    if named and named != audience:
        raise IdeaError(AUDIENCE_FOLLOWS_LEVEL[context], field='visibility')
    return audience


# The states in which the author may edit an idea's content (S3-005): while it
# is being written, and while a reviewer has sent it back for changes. Every
# other state is somebody else's to act on - a submitted or under-review idea
# is what the reviewer is looking at, and a decided one is history.
#
# Both changes-requested states are in it, because both mean the same thing to an
# author: somebody has read it and written down what to change. Which track asked
# is a question the status label answers, not a question about whether the author
# may fix it.
#
# **This is the working copy, and editing it does not touch the submission.** An
# idea in `CHANGES_REQUESTED` has already been submitted to the platform and its
# content frozen into an `IdeaSubmissionVersion`; editing here changes the row the
# author will resubmit from, and the frozen version the platform holds is a
# different row that no edit path can reach. That is what makes "answer the
# feedback without unlocking the official submission" true rather than aspirational.
EDITABLE_STATUSES = frozenset(
    {
        Idea.Status.DRAFT,
        Idea.Status.CHANGES_REQUESTED,
        Idea.Status.ORGANIZATION_CHANGES_REQUESTED,
    }
)


def can_edit_idea(user: User | None, idea: Idea | None) -> bool:
    """
    Whether `user` may edit `idea` right now - the predicate form of
    `_load_editable_idea`, for the client to ask instead of inferring.

    It answers by attempting the same refusals in the same order and reporting
    which one applied, so `viewerCanEdit` cannot disagree with `updateIdea`: a
    field that said yes for an idea the server would refuse would be worse than
    no field, because a client would offer a button and then report a failure.

    Deliberately a *convenience*. Every check here is repeated by
    `update_idea`; nothing is authorized by this function, and hiding a control
    is not a security property.
    """
    if idea is None:
        return False
    try:
        _load_editable_idea(_require_active_user(user), idea.pk)
    except IdeaError:
        return False
    return True


def _load_editable_idea(user: User, idea_id: object) -> Idea:
    """
    The idea identified by `idea_id`, if `user` may edit it right now.

    Every refusal below produces the same message, because they are the same
    answer: this operation is not available to you on this id. Covered:
    no such id, another tenant's idea, somebody else's idea in your own
    organization, and your own idea in a state that is not editable.
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

    # Standing in the idea's tenant is re-checked on every write, not only at
    # creation: leaving has to take the ability to write into that tenant with
    # you. **One branch per context**, because the three proofs differ - an
    # organization membership, a team membership, or nothing at all, since an
    # individual idea's author *is* its tenant.
    #
    # This used to ask for an organization membership unconditionally, which
    # meant a team or individual idea could not be edited by anybody, its author
    # included: `organization_id` is null for those two, and a null tenant is not
    # a membership. The branch is what `lifecycle._require_tenant_standing`
    # already used, and the two must agree - otherwise one of them refuses an
    # operation the other allows.
    if idea.submission_context == Idea.SubmissionContext.TEAM:
        from teams import authorization as team_authorization

        if not team_authorization.is_member_of(user, idea.team_id):
            raise IdeaError(
                'You must be an active member of this team to work with this idea.',
                reason='membership_required',
            )
    elif idea.submission_context == Idea.SubmissionContext.ORGANIZATION:
        if authorization.get_membership(user, idea.organization_id) is None:
            raise IdeaError(
                'You must be an active member of this organization to work with ideas here.',
                reason='membership_required',
            )

    if idea.status not in EDITABLE_STATUSES:
        # The S2-002 wording is kept: the frontend shows it verbatim, and it
        # still names the common case. A changes-requested idea is editable
        # too (S3-005) - the author's answer to the review.
        raise IdeaError('Only a draft can be edited.', reason='forbidden')

    if idea.is_locked and idea.status != Idea.Status.CHANGES_REQUESTED:
        # A locked idea is never editable, whatever its status. Unreachable while
        # `EDITABLE_STATUSES` and the lifecycle agree - a locked idea is at least
        # `SUBMITTED` - and kept as a named refusal rather than an implicit
        # consequence of the set above, because "the official submission is locked"
        # is a security property of the platform and a security property should
        # not be an emergent behaviour of an unrelated constant.
        raise IdeaError(
            'This idea has been submitted to the platform and can no longer be edited.',
            reason='forbidden',
        )

    return idea


# Who must be able to read an idea for it to be put forward (S3-008,
# `docs/reviews-domain.md` D-6). A submitted idea is waiting for a reviewer of
# its organization, and a reviewer can only review what they can read: a
# `PRIVATE` idea is readable by its author alone, so submitting one would park
# it in `SUBMITTED` where no reviewer could ever reach it. `DEPARTMENT` reads
# as author-only too, and is not selectable anyway. The meaning of `PRIVATE`
# is unchanged - a private draft stays private - and nothing widens visibility
# on the author's behalf: they choose, then submit.
REVIEWABLE_VISIBILITIES = frozenset({Idea.Visibility.ORGANIZATION, Idea.Visibility.PUBLIC})

SUBMISSION_VISIBILITY_MESSAGE = (
    'An organization idea has to be visible to its organization so its reviewers '
    'can read it before it is submitted.'
)

# The refusal for naming an audience the idea's level does not have. Written per
# level, in the author's terms, because the fix is one sentence.
AUDIENCE_FOLLOWS_LEVEL = {
    Idea.SubmissionContext.INDIVIDUAL: (
        'An individual idea is seen only by you until it reaches the platform. '
        'File it at team or organization level to share it with people.'
    ),
    Idea.SubmissionContext.TEAM: (
        'A team idea is seen by your team. There is no wider audience; the '
        'platform reviews it once you submit it.'
    ),
    Idea.SubmissionContext.ORGANIZATION: (
        'An organization idea is seen by your organization. There is no wider '
        'audience; the platform reviews it once your organization has confirmed it.'
    ),
}


def _validate_submission_visibility(idea: Idea) -> None:
    """
    The visibility a submission needs: organization reviewers must be able to read it.

    An individual or team idea has no such constraint - its reviewers are the
    platform's, who read a submission through `platform_reviewer_filter`
    whatever audience the author chose.
    """
    if idea.submission_context != Idea.SubmissionContext.ORGANIZATION:
        return
    if idea.visibility not in REVIEWABLE_VISIBILITIES:
        raise IdeaError(SUBMISSION_VISIBILITY_MESSAGE)


def _validate_for_submission(idea: Idea) -> None:
    """
    What a draft must contain before it can be submitted: its content, and a
    visibility its organization's reviewers can read.

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
    _validate_submission_visibility(idea)


def _resolve_context(
    user: User,
    submission_context: str | None,
    organization_id: object | None,
    team_id: object | None,
):
    """
    The tenant an idea is being filed for, given the caller's chosen context.

    Returns `(context, organization, team)` with exactly the tenants that context
    requires, having proved the caller belongs to each one. This is the write-side
    counterpart of `Idea._clean_submission_context`, and the two must agree - the
    model refuses a row they disagree about, and this function makes the refusal a
    clear message *before* there is a row.

    The three branches, and why each proves something different:

    - `INDIVIDUAL` needs **nothing**. This is the branch that makes "an
      organization is optional" true: a user with no organization and no team
      files an idea, and the only authorization is that they are an authenticated
      user filing their own idea. The two tenant ids must both be absent, so a
      caller cannot smuggle an organization in and be quietly given an
      organization-context idea.
    - `TEAM` needs an **active team membership**. Not `team.ideas.submit` -
      filing is not submitting, and a plain member can file; the membership is
      what proves they belong to the team they are filing for.
    - `ORGANIZATION` needs an **active organization membership**, through
      `_require_membership`, which is the same predicate every other write in the
      platform authorizes with.
    """
    normalized = str(submission_context or Idea.SubmissionContext.ORGANIZATION).strip().lower()

    if normalized == Idea.SubmissionContext.INDIVIDUAL:
        if organization_id not in (None, '') or team_id not in (None, ''):
            raise IdeaError(
                'A submission of your own does not belong to an organization or a team.',
                field='submissionContext',
            )
        return normalized, None, None

    if normalized == Idea.SubmissionContext.TEAM:
        if organization_id not in (None, ''):
            raise IdeaError(
                'A team submission does not belong to an organization.', field='submissionContext'
            )
        team = _resolve_team(user, team_id)
        return normalized, None, team

    if normalized == Idea.SubmissionContext.ORGANIZATION:
        if team_id not in (None, ''):
            raise IdeaError(
                'An organization submission does not belong to a team.',
                field='submissionContext',
            )
        membership = _require_membership(user, organization_id)
        return normalized, membership.organization, None

    raise IdeaError('Choose how you are submitting this idea.', field='submissionContext')


def _resolve_team(user: User, team_id: object):
    """The team `user` is an active member of, or a refusal."""
    from teams import authorization as team_authorization
    from teams.models import Team

    try:
        normalized_id = int(str(team_id))
    except (TypeError, ValueError):
        raise IdeaError('Team is unavailable.', reason='forbidden') from None

    if not team_authorization.is_member_of(user, normalized_id):
        raise IdeaError(
            'You must be an active member of this team to file an idea for it.',
            reason='membership_required',
        )

    team = Team.objects.filter(pk=normalized_id).first()
    if team is None:  # pragma: no cover - is_member_of already proved it exists
        raise IdeaError('Team is unavailable.', reason='forbidden')
    return team


def create_idea_in_context(
    user: User | None,
    data: IdeaInput,
    *,
    submission_context: str | None = None,
    organization_id: object | None = None,
    team_id: object | None = None,
) -> Idea:
    """
    File a new idea as a DRAFT, in whichever of the three contexts was chosen.

    The one way an idea comes into existence. The author is `user` - resolved from
    the access token by `identity.authentication` and passed in by the resolver,
    never read from `data` - and the tenant is whatever `_resolve_context` proved
    they belong to. The status is the model default (`DRAFT`) and the visibility
    is the model default (`PRIVATE`, fail closed) unless the caller chose one, so
    a new idea is visible to nobody but its author until somebody deliberately
    widens it.

    `create_idea` below is this function with the organization spelled out, kept
    because the `createIdea` mutation's original signature named an organization
    and the S2 tests call it. Both go through the same validation, so the older
    entry point gains the context rules rather than bypassing them.
    """
    active_user = _require_active_user(user)
    context, organization, team = _resolve_context(
        active_user, submission_context, organization_id, team_id
    )

    title = _validate_title(data.title)
    description = _validate_description(data.description)
    story = _validate_story(data)
    category = _resolve_category(data.category_id)
    visibility = _resolve_visibility(data.visibility, context)

    return Idea.objects.create(
        organization=organization,
        team=team,
        submission_context=context,
        author=active_user,
        title=title,
        description=description,
        category=category,
        **story,
        **({'visibility': visibility} if visibility else {}),
    )


def create_idea(user: User | None, organization_id: object, data: IdeaInput) -> Idea:
    """
    File a new organization-context idea as a DRAFT.

    Kept as the named organization entry point the `createIdea` mutation and the
    S2 tests use. It is a delegation, not a second implementation: everything the
    function used to do - the membership proof, the title, description, story,
    category and visibility validation, and the fail-closed visibility default -
    happens in `create_idea_in_context`, so there is one place where "what must a
    new idea satisfy" is written down.
    """
    return create_idea_in_context(
        user,
        data,
        submission_context=Idea.SubmissionContext.ORGANIZATION,
        organization_id=organization_id,
    )


def update_idea(user: User | None, idea_id: object, data: IdeaInput) -> Idea:
    """
    Edit the author's own draft, or their own idea a reviewer sent back with
    `CHANGES_REQUESTED` (S3-005).

    `data` carries content only. `organization`, `author` and `status` are
    not fields on `IdeaInput`, so there is no code path here that could move
    an idea to another tenant, hand it to another author, or mark it
    submitted by hand - the only way to submit is `submit_idea`.

    Editing never touches a review. The completed review that asked for the
    changes keeps its own snapshot of what it reviewed, and the next round is
    opened only when a reviewer starts it.

    **Visibility is fixed once an idea has been submitted**
    (`docs/reviews-domain.md` §10, D-6). While changes are requested the
    author edits the content, not who can read it: narrowing an idea under
    review to `PRIVATE` would hide it, and its own review history, from the
    reviewers the resubmission is for. The same value is accepted, so a form
    that sends every field back is not refused for it.
    """
    active_user = _require_active_user(user)
    idea = _load_editable_idea(active_user, idea_id)

    idea.title = _validate_title(data.title)
    idea.description = _validate_description(data.description)
    for name, value in _validate_story(data).items():
        setattr(idea, name, value)
    idea.category = _resolve_category(data.category_id)

    # The audience follows the idea's level, which never changes, so there is
    # nothing to edit here; a value that disagrees with it is still refused.
    if data.visibility is not None:
        _resolve_visibility(data.visibility, idea.submission_context)

    # `save()` runs `full_clean()`, so the model's own invariants - the
    # status/`submitted_at` pairing included - hold on this write path too.
    idea.save()
    return idea


def submission_target(user: User | None, idea: Idea) -> str:
    """
    The status "Submit" would move this idea to right now.

    **Context-aware, and it is the author's context that decides.** An
    organization-context idea is put in front of its organization first; an
    individual or team idea has no organization to confirm it and goes straight to
    the platform. Both are the same button - which is the point: the author does
    not choose a workflow, they choose who they are submitting with, and the
    platform decides what "submit" means.

    A team submission is additionally checked for `team.ideas.submit`, so an
    ordinary member can file an idea for their team but the *submission* is a
    permission the team's roles decide (an owner or any member with it). A team
    cannot be granted an approval permission, so this is the furthest a team can
    go in the lifecycle.

    Refuses when there is nothing to submit to, which is what keeps a client from
    rendering a button whose only possible outcome is an error.
    """
    from ideas import lifecycle

    if idea is None:
        raise IdeaError('Idea is unavailable.', reason='forbidden')

    if user is None or not user.is_active:
        raise IdeaError('You must be signed in to work with ideas.', reason='unauthenticated')

    target = lifecycle.resubmission_target(idea)
    if target is None:
        raise IdeaError('Only a draft can be edited.', reason='forbidden')

    if idea.submission_context == Idea.SubmissionContext.TEAM and target in (
        Idea.Status.SUBMITTED,
        Idea.Status.SUBMITTED_TO_ORGANIZATION,
    ):
        from teams import authorization as team_authorization

        if not team_authorization.can_submit_for(user, idea.team_id):
            raise IdeaError(
                'You do not have permission to submit this idea for your team.',
                reason='forbidden',
            )

    return target


def submit_idea(user: User | None, idea_id: object) -> Idea:
    """
    Put this idea forward, to whichever stage its context names.

    A thin delegation to `ideas.lifecycle.transition_idea` rather than a second
    implementation of the same move - which decides the target from the idea's own
    context, so there is still exactly one answer to "may this idea be submitted,
    and to what". For an organization idea that is
    `SUBMITTED_TO_ORGANIZATION`; for an individual or team idea it is `SUBMITTED`.

    The same call serves a resubmission after either track's changes request,
    because both resolve through the lifecycle's `RESUBMISSION_TARGET`. The name
    is kept because it is what the `submitIdea` mutation and the S2-002 tests
    call, and because "submit my idea" is the operation's name in the product
    regardless of which stage it reaches.
    """
    # Imported here rather than at module scope: `ideas.lifecycle` imports the
    # validators above from this module, so a top-level import in both
    # directions would be a cycle. One direction at import time is enough.
    from ideas import lifecycle

    active_user = _require_active_user(user)
    idea = selectors.get_idea(active_user, idea_id)
    if idea is None:
        raise IdeaError('Idea is unavailable.', reason='forbidden')

    moved = lifecycle.transition_idea(active_user, idea_id, submission_target(active_user, idea))
    _notify_the_reviewers_waiting_on(moved)
    return moved


def _notify_the_reviewers_waiting_on(idea: Idea) -> None:
    """
    Tell the reviewers who now have something to do that they do.

    A submission changes nothing about who is *allowed* to review - the queues are
    permission-scoped queries either way - so the only thing missing without this
    is that a reviewer has to go looking for work. And a queue nudge is the one
    notification that is deliberately not emailed: there may be many platform
    reviewers, "somebody has submitted something" is not addressed to anybody in
    particular, and mailing every one of them for every submission is how a
    notification stops being read.

    Authoritative about *whose* queue: the organization's own reviewers for an
    organization submission, and the platform's reviewers for anything that reached
    the platform. Registered after the commit and never raising, like every other
    notification in the platform.
    """
    from notifications import services as notification_services

    if idea.status == Idea.Status.SUBMITTED_TO_ORGANIZATION:
        recipients = organization_reviewers_for(idea)
        kind = 'review.organization_queue'
        noun = tenant_noun(idea)
        title = f'"{idea.title}" is waiting for your {noun}'
        body = f'A member has submitted an idea for your {noun} to review.'
    elif idea.status == Idea.Status.SUBMITTED:
        recipients = platform_reviewers()
        kind = 'review.platform_queue'
        title = f'"{idea.title}" is waiting for platform review'
        body = 'An idea has been submitted to the platform and is waiting for a reviewer.'
    else:
        # A resubmission that went back to the organization, or any other
        # destination: the queue it landed in has already been told, by the
        # original submission. Saying it twice would make two submissions look
        # like two different ideas arriving.
        return

    # Never the author. They know they submitted it; being told "somebody's idea
    # is waiting" about your own is the kind of notification that teaches people
    # to ignore the badge. (An organization's Owner can be both, which is exactly
    # when this matters.)
    recipients = [user for user in recipients if user.pk != idea.author_id]
    if not recipients:
        return

    notification_services.deliver(
        recipients=recipients,
        kind=kind,
        title=title,
        body=body,
        idea=idea,
        send_email=False,
    )


def tenant_noun(idea: Idea) -> str:
    """The word for the body that checks this idea first: 'team' or 'organization'."""
    return 'team' if idea.submission_context == Idea.SubmissionContext.TEAM else 'organization'


def organization_reviewers_for(idea: Idea) -> list[User]:
    """
    The active people who check this idea before the platform: its team's reviewers
    for a team idea, its organization's reviewers for an organization one, nobody for
    an individual idea. (Named for the organization, which came first.)
    """
    if idea.submission_context == Idea.SubmissionContext.TEAM:
        if idea.team_id is None:
            return []
        from teams import authorization as team_authorization
        from teams.models import TeamMembership

        members = TeamMembership.objects.filter(
            team_id=idea.team_id, status=TeamMembership.Status.ACTIVE
        ).select_related('user')
        return [
            m.user
            for m in members
            if m.user.is_active and team_authorization.can_review_for(m.user, idea.team_id)
        ]

    if idea.organization_id is None:
        return []

    from organizations import authorization as organization_authorization
    from organizations.models import Membership
    from organizations.services import IDEA_REVIEW

    memberships = Membership.objects.filter(
        organization_id=idea.organization_id, status=Membership.Status.ACTIVE
    ).select_related('user')

    return [
        membership.user
        for membership in memberships
        if membership.user.is_active
        and organization_authorization.membership_has_permission(membership, IDEA_REVIEW)
    ]


def platform_reviewers() -> list[User]:
    """
    The active accounts holding the platform review permission.

    Resolved by one query rather than by asking the permission framework per
    account: this runs on every submission, and "who is on the platform side" is a
    question the database can answer once.
    """
    from administration.authorization import REVIEW_PLATFORM_SUBMISSIONS

    codename = REVIEW_PLATFORM_SUBMISSIONS.split('.', 1)[1]
    return list(
        User.objects.filter(is_active=True)
        .filter(
            models.Q(
                user_permissions__content_type__app_label='administration',
                user_permissions__codename=codename,
            )
            | models.Q(
                groups__permissions__content_type__app_label='administration',
                groups__permissions__codename=codename,
            )
        )
        .distinct()
    )


def submit_to_platform(user: User | None, idea_id: object) -> Idea:
    """
    `ORGANIZATION_CONFIRMED -> SUBMITTED`: the owner puts a confirmed idea in
    front of the platform.

    Named separately from `submit_idea` because it is a genuinely different act,
    not a variant of the same one: the organization has already said this is what
    it wants to submit, and now the *owner* is the one choosing to submit it. The
    distinction is what the product asks for - organization confirmation is not
    platform submission - and a separate name is what stops a reader of this file
    from assuming "submit" covers both.
    """
    from ideas import lifecycle

    active_user = _require_active_user(user)
    idea = selectors.get_idea(active_user, idea_id)
    if idea is not None and idea.submission_context == Idea.SubmissionContext.TEAM:
        from teams import authorization as team_authorization

        if not team_authorization.can_submit_for(active_user, idea.team_id):
            raise IdeaError(
                'You do not have permission to submit this idea for your team.',
                reason='forbidden',
            )

    moved = lifecycle.transition_idea(active_user, idea_id, Idea.Status.SUBMITTED)
    _notify_the_reviewers_waiting_on(moved)
    return moved


# --- comments (S2-005) -------------------------------------------------------------


def _validate_comment_content(content: str | None) -> str:
    """
    The content to store, or a refusal.

    Normalized by stripping the ends and folding CRLF to LF, and nothing else.
    The strip is the same normalization every other content field gets, and it
    is what makes a whitespace-only comment detectable at all - `"   "` is
    truthy, so a blank check on the raw value would accept it. The newline fold
    is because the frontend's control is a `<textarea>`, and a browser on
    Windows submits `\\r\\n`; storing that unchanged means the same comment
    renders with stray carriage returns everywhere else.

    Internal whitespace is **not** touched. Collapsing runs of spaces would
    destroy the indentation of a pasted code block or a bulleted list, and
    rewriting somebody's comment into tidier prose is not this module's
    decision to make.

    No truncation, ever - see `MAX_COMMENT_LENGTH`.
    """
    normalized = (content or '').replace('\r\n', '\n').replace('\r', '\n').strip()

    if not normalized:
        raise IdeaError('Write something before posting.', field='content')
    if len(normalized) > MAX_COMMENT_LENGTH:
        raise IdeaError(
            f'A comment must be {MAX_COMMENT_LENGTH} characters or fewer.',
            field='content',
        )
    return normalized


def _discussion_is_open(idea: Idea) -> bool:
    """
    Whether `idea` accepts a new comment, asked of `ideas.lifecycle`.

    A one-line forwarder, and it exists only because of the import direction:
    `ideas.lifecycle` imports this module's validators, so importing it back at
    module scope would be a cycle. Same pattern and same reasoning as
    `submit_idea` below, and the rule itself stays in the lifecycle next to the
    transition matrix it is derived from - this is a bridge, not a second
    answer to "is this discussion open?".
    """
    from ideas import lifecycle

    return lifecycle.discussion_is_open(idea)


def _load_discussable_idea(user: User, idea_id: object) -> Idea:
    """
    The idea to comment on, or a refusal.

    Two gates, in this order, and the order is the security property: the
    caller must be able to **read** the idea (so a comment can never be attached
    to an idea the author of the comment was never shown), and only then may
    the idea's discussion state be consulted.

    Every refusal is "Idea is unavailable." for the same reason the idea
    operations use one message: no such id, another tenant's idea and a
    private idea are one answer, so the argument is not a probe for which idea
    ids exist.
    """
    # Through the public selector, not a raw lookup: this operation must not be
    # able to comment on an idea `get_idea` would not return.
    idea = selectors.get_idea(user, idea_id)
    if idea is None:
        raise IdeaError('Idea is unavailable.', reason='forbidden')

    if not _discussion_is_open(idea):
        # Named plainly rather than as "unavailable": the reader can already
        # see the idea, so a generic refusal would tell somebody they already
        # know less than the truth, and the state that closed the discussion is
        # public information carried by the idea itself.
        raise IdeaError(
            'This idea is no longer open for discussion.',
            reason='discussion_closed',
        )

    return idea


def _load_owned_comment(user: User, comment_id: object) -> Comment:
    """
    The comment identified by `comment_id`, if `user` may change it right now.

    Covered by one refusal message: no such id, a comment on an idea the caller
    may not read, and somebody else's comment. Authorship is the only way past.

    No membership re-check, unlike `_load_editable_idea`, and the difference is
    deliberate rather than an oversight. Editing an *idea* is writing into a
    tenant, so leaving that tenant has to end it. A comment was never a write
    into a tenant in the first place - it follows the idea's own visibility -
    so the same gate would be inventing a rule and would strand a comment on a
    `PUBLIC` idea that its author may still read after leaving an organization.
    """
    comment = selectors.get_comment(user, comment_id)
    if comment is None or comment.author_id != user.pk:
        raise IdeaError('Comment is unavailable.', reason='forbidden')
    return comment


def add_comment(
    user: User | None, idea_id: object, content: str, parent_id: object | None = None
) -> Comment:
    """
    Post a comment on an idea the caller may read, optionally as a reply to
    another comment on the same idea.

    The author is `user` and the idea is resolved, authorized and used by this
    module - neither is an input, so no caller can post as somebody else or
    attach a comment to an idea it was not allowed to name. The status is not
    touched: a comment does not move the idea it is on.

    `parent_id` is the one thing the client names about the *shape* of its
    comment, and it is resolved here rather than trusted:

    - it goes through `selectors.get_comment`, so a parent the caller cannot
      read is not a parent they may reply to. Without that, a reply could hang
      off a comment on a private idea and be read by somebody who was never
      shown either of them;
    - the resolved parent is checked against the already-authorized idea, so a
      reply cannot cross from one idea's thread into another's;
    - the model refuses a reply to a reply (`Comment.clean`), which is one
      level of nesting as a rule of the row rather than as a value.

    A parent that does not exist, is unreadable, belongs to another idea, or is
    itself a reply all raise `IdeaError` naming the input at fault. The client
    is told *why* rather than being left to draw a reply that the server would
    have to drop: this is a form mistake, not an existence oracle, because the
    caller had to be able to read the idea first to get here.
    """
    active_user = _require_active_user(user)
    idea = _load_discussable_idea(active_user, idea_id)
    normalized = _validate_comment_content(content)

    parent: Comment | None = None
    if parent_id is not None:
        parent = selectors.get_comment(active_user, parent_id)
        if parent is None:
            raise IdeaError(
                'The comment you are replying to is unavailable.',
                field='parentId',
            )
        if parent.idea_id != idea.pk:
            raise IdeaError(
                'A reply must be on the same idea as the comment it answers.',
                field='parentId',
            )
        if parent.parent_id is not None:
            raise IdeaError(
                'You can only reply to a top-level comment.',
                field='parentId',
            )

    # `Comment.save` runs `full_clean`, so the model's own rules - content, no
    # self-parenting, one level of nesting - hold on this write path as well as
    # on any other.
    return Comment.objects.create(
        idea=idea,
        author=active_user,
        content=normalized,
        parent=parent,
    )


def update_comment(user: User | None, comment_id: object, content: str) -> Comment:
    """
    Edit the caller's own comment.

    The idea, the author and `created_at` are not inputs and are not assigned,
    so an edit cannot move a comment between ideas, reattribute it, or rewrite
    when it was posted. The visible edit time moves, and `updated_at` records
    it.
    """
    active_user = _require_active_user(user)
    comment = _load_owned_comment(active_user, comment_id)
    comment.content = _validate_comment_content(content)

    # `save()` runs `full_clean()`, so the model's own rule - no contentless
    # row - holds on this write path as well as on create.
    comment.save()
    return comment


def delete_comment(user: User | None, comment_id: object) -> None:
    """
    Remove the caller's own comment.

    A hard delete. There is no soft delete and no moderation state on the model
    (S2-001 declined to model a policy that does not exist), and a `CASCADE`
    from the idea takes the discussion with it. If moderation arrives it will
    arrive as a state, not as a repair of this row.

    Any replies to it go with it, and that is the model's `CASCADE` rather than
    a decision taken here. A reply is *about* its parent, so once the parent is
    retracted the reply has no subject left; promoting it to a top-level comment
    would quietly republish somebody's words as though they had been their own.
    The consequence worth knowing is that an author can remove a thread by
    removing its first comment, and a reply to a reply is not a thing - so
    replies to one's own comment cannot exist.
    """
    active_user = _require_active_user(user)
    comment = _load_owned_comment(active_user, comment_id)
    comment.delete()


# --- votes (S2-006) -----------------------------------------------------------------


def _require_readable_idea(user: User, idea_id: object) -> Idea:
    """
    The idea to act on, for an operation that only needs to *read* it.

    Shared by the vote operations, and it is the same gate the comment
    operations apply first - the visibility filter, resolved through
    `selectors.get_idea` so the rule is not restated here.

    Deliberately **no lifecycle check**, and the difference from
    `_load_discussable_idea` is a decision rather than an oversight. This
    domain's own rule for votes is that "a user may not vote on an idea they
    cannot read" (`docs/ideas-domain.md`), full stop - and there is no vote
    equivalent of `lifecycle.DISCUSSION_CLOSED_STATUSES`. So a rejected idea
    can still be voted on, and an approved one handed to the opportunities
    track most certainly can.

    The reason the two rules differ: a comment is participation in a
    *decision*, and a decision that is finished has nothing left to discuss;
    a vote is a statement of interest in the *idea*, which is a thing that
    outlives its own review state. Closing discussion on rejection while
    accepting votes on it is not an inconsistency - they answer different
    questions about the same object - but it is exactly the kind of thing that
    looks like an oversight in review, so it is written down here and pinned
    by `test_a_rejected_idea_can_still_be_voted_on`.
    """
    idea = selectors.get_idea(user, idea_id)
    if idea is None:
        raise IdeaError('Idea is unavailable.', reason='forbidden')
    return idea


def vote_for_idea(user: User | None, idea_id: object) -> Vote:
    """
    Record that `user` finds `idea` worth doing. One vote per user per idea.

    The voter is `user` and is never an input, so there is no request that can
    vote on somebody else's behalf. The idea is resolved, authorized and then
    used, so there is no request that can name an idea the voter was never
    shown.

    **Idempotent at the service level, enforced at the database level** - both
    halves, because they answer different races. The `get` below is the
    common case: a double-clicked button, a retried request, a client that
    replays on a timeout. The `unique_vote_per_user_idea` constraint is what
    answers the case a check-then-insert cannot: two *simultaneous* requests
    that both read "no vote" and both try to insert. The constraint is the
    final integrity boundary because it is the only thing that cannot be
    raced - a service-level check is a read followed by a write, and two of
    those interleave.

    So the loser of that race catches `IntegrityError` and reads the row the
    winner inserted, and both callers get the same vote back rather than one
    of them seeing an error for a state they asked for. `transaction.atomic()`
    around the insert is what makes that readable: without the inner block the
    failed statement would poison the outer transaction and the follow-up read
    would fail too.
    """
    active_user = _require_active_user(user)
    idea = _require_readable_idea(active_user, idea_id)

    existing = Vote.objects.filter(idea=idea, user=active_user).first()
    if existing is not None:
        return existing

    try:
        with transaction.atomic():
            return Vote.objects.create(idea=idea, user=active_user)
    except IntegrityError:
        # Lost the race to a concurrent insert. The constraint did its job, so
        # the state the caller asked for now exists - which makes this a
        # success, not a failure. Re-read rather than re-raise, and if the row
        # genuinely is not there then something other than the constraint
        # failed and the error belongs to the caller.
        concurrent = Vote.objects.filter(idea=idea, user=active_user).first()
        if concurrent is None:
            raise
        return concurrent


def remove_vote(user: User | None, idea_id: object) -> None:
    """
    Withdraw `user`'s vote on `idea`, if they have one.

    **Idempotent**: removing a vote that is not there succeeds and does
    nothing. A vote control is a toggle that a reader may well double-click,
    and a second click reporting "you have no vote to remove" would be an
    error about a state the reader already achieved. It leaks nothing either
    way, because "no vote of mine" is the same fact whether the row was never
    written or was written and removed.

    The visibility gate still applies in full, and that is the important part:
    this is idempotent *within* the set of ideas the caller may read. An idea
    they cannot read is refused, exactly as it is for voting - otherwise the
    idempotent branch would turn "unreadable idea" into a success and quietly
    confirm that the operation is available there.
    """
    active_user = _require_active_user(user)
    idea = _require_readable_idea(active_user, idea_id)

    # Scoped to the authenticated user rather than deleting by idea id: this
    # removes *your* vote and can never touch anybody else's, so a vote for
    # another user on the same idea is not even a row this statement can name.
    Vote.objects.filter(idea=idea, user=active_user).delete()


# --- attachments (S2-007) -----------------------------------------------------------


def _load_attachable_idea(user: User, idea_id: object) -> Idea:
    """
    The idea `user` may attach supporting evidence to right now.

    Authorship and an active membership - the same two conditions
    `_load_editable_idea` requires for editing an idea's content - but
    deliberately **no lifecycle gate**, and that is a considered choice, not
    an omission. Evidence is not the content itself; it accumulates
    while an idea is discussed and reviewed, not only while it is being
    written, so restricting uploads to the editable states (as
    `_load_editable_idea` does for `update_idea`) would refuse the case this
    feature mostly exists for -
    attaching a screenshot or a spreadsheet once an idea is already under
    review. This follows the shape S2-006 established for votes
    (`_require_readable_idea`: readability with no status condition) rather
    than the shape S2-005 established for comments (closed on rejection):
    an idea's own evidence, like interest in it, outlives its review state.

    Resolved through `get_idea` first, so an idea the caller cannot even see
    is refused identically to one that does not exist, and only *then* is
    authorship checked - the same two-step order `_load_editable_idea` and
    `_load_discussable_idea` use, so a reviewer or a colleague cannot learn
    "this idea exists but isn't yours to attach to" about something they
    were never shown in the first place.
    """
    idea = selectors.get_idea(user, idea_id)
    if idea is None or idea.author_id != user.pk:
        raise IdeaError('Idea is unavailable.', reason='forbidden')

    # Re-checked on every write, not only at creation: leaving the
    # organization has to take the ability to attach evidence to it with you,
    # the same reasoning `_load_editable_idea` and `create_idea` apply.
    if authorization.get_membership(user, idea.organization_id) is None:
        raise IdeaError(
            'You must be an active member of this organization to work with ideas here.',
            reason='membership_required',
        )

    return idea


def _load_owned_attachment(user: User, attachment_id: object) -> Attachment:
    """
    The attachment `user` may delete right now, or a refusal.

    The gate is the *idea's* authorship, not the attachment's uploader,
    though today the two are always the same person: `upload_attachment`
    only ever accepts a file from an idea's own author (see
    `_load_attachable_idea`), so there is no second contributor whose
    uploads this idea's author would otherwise be deleting. Written this way
    rather than checking `attachment.uploaded_by_id` so the rule has one
    statement - "the author of this idea controls its evidence" - instead of
    two that happen to agree only because of how upload is gated.

    One refusal message for no such id, an attachment on another tenant's
    idea, and somebody else's idea's attachment alike - the same shape
    `_load_owned_comment` uses, so an attachment id is not an oracle for any
    of the three.
    """
    attachment = selectors.get_attachment(user, attachment_id)
    if attachment is None or attachment.idea.author_id != user.pk:
        raise IdeaError('Attachment is unavailable.', reason='forbidden')

    if authorization.get_membership(user, attachment.idea.organization_id) is None:
        raise IdeaError(
            'You must be an active member of this organization to work with ideas here.',
            reason='membership_required',
        )

    return attachment


def authorize_attachment_upload(user: User | None, idea_id: object) -> Idea:
    """
    Whether `user` may attach evidence to `idea_id`, decided *before* the
    upload itself is looked at (S2-008).

    Exactly the gate `upload_attachment` applies - it calls the same two
    functions - exposed on its own so `ideas/views.py` can refuse an
    unauthenticated or unauthorized caller before it touches
    `request.FILES`. Reading that attribute is what makes Django parse the
    multipart body (and spool a large file to temporary disk), so asking this
    first means a caller who could never have uploaded does not get to make
    the server do that work. `upload_attachment` still re-applies the gate
    itself: this is an early exit, not a replacement for it.
    """
    active_user = _require_active_user(user)
    return _load_attachable_idea(active_user, idea_id)


def upload_attachment(user: User | None, idea_id: object, uploaded_file) -> Attachment:
    """
    Attach `uploaded_file` to an idea `user` owns, as supporting evidence.

    `uploaded_file` is a Django `UploadedFile` (from `request.FILES`,
    `ideas/views.py`'s only caller) - its `.name` is the display filename the
    browser sent, `.size` is measured by Django from the actual request body
    (not a header the client could lie about), and `.content_type` is **never
    read here**: this domain's own content type is derived from the file's
    validated extension and confirmed against its leading bytes, exactly the
    way `ideas.attachments`'s module docstring explains, so a browser's
    `multipart/form-data` claim never becomes stored data.

    The storage key is generated here, from the idea's own id, and is never a
    value `uploaded_file` or its caller supplies - see
    `ideas.storage.generate_storage_key`. That is the actual defense against
    a client choosing an arbitrary key or walking one attachment's bytes onto
    another's.

    **Ordering, and what happens if a step fails.** Validation runs entirely
    before anything touches storage, so a rejected file never reaches it.
    The object is then written to storage *before* the database row is
    created: if the row's own validation or the database itself then fails,
    the just-written object is deleted so storage and the database do not
    end up disagreeing about an attachment that was never actually recorded.
    The reverse order - row first, object second - would leave a *readable*
    attachment whose bytes do not exist the moment the storage write failed,
    which is a worse inconsistency than a storage object nothing points to.
    """
    active_user = _require_active_user(user)
    idea = _load_attachable_idea(active_user, idea_id)

    try:
        display_name = attachment_rules.safe_display_filename(uploaded_file.name)
        extension = attachment_rules.resolve_extension(display_name)
        attachment_rules.validate_size(uploaded_file.size)

        head = uploaded_file.read(attachment_rules.SNIFF_BYTES)
        uploaded_file.seek(0)
        attachment_rules.validate_content(extension, head)
    except attachment_rules.AttachmentValidationError as exc:
        raise IdeaError(exc.message, field=exc.field) from exc

    content_type = attachment_rules.canonical_content_type(extension)
    storage_key = storage.generate_storage_key(idea.pk, extension)

    try:
        saved_key = storage.save_object(storage_key, uploaded_file)
    except storage.AttachmentStorageError as exc:
        # An explicit reason, not `IdeaError`'s `'forbidden'` default: this is
        # the storage tier failing, not a refusal, and `ideas/views.py` maps it
        # to a server error rather than to the 404 an authorization refusal
        # gets (S2-008).
        raise IdeaError(
            'The file could not be stored. Please try again.', reason='storage_unavailable'
        ) from exc

    try:
        return Attachment.objects.create(
            idea=idea,
            uploaded_by=active_user,
            filename=display_name,
            content_type=content_type,
            size=uploaded_file.size,
            storage_key=saved_key,
        )
    except DatabaseError:
        # The row never made it in, so nothing refers to the object that was
        # just written - clean it up rather than leaving an orphan storage
        # never has to know it must reconcile.
        storage.delete_object(saved_key)
        raise


def delete_attachment(user: User | None, attachment_id: object) -> None:
    """
    Remove one of the caller's own idea's attachments - both the metadata row
    and its storage object.

    **Not idempotent**, deliberately matching `delete_comment`'s shape rather
    than the votes' toggle shape: an attachment is a distinct piece of
    evidence, not a per-user flag, so "delete it again" names something that
    is no longer there, and `_load_owned_attachment` refuses it the same way
    a second `deleteComment` on an already-deleted comment is refused.

    **Database row first, storage object second.** Deleting the row and then
    best-effort deleting the object (`storage.delete_object` never raises for
    a missing or already-gone key, and logs rather than raising for a genuine
    backend failure) means a storage-side failure here leaves an orphaned
    object with nothing pointing to it - wasted space, cleaned up later - not
    a database row that still claims to exist while its bytes are already
    gone. The reverse order would risk exactly that worse outcome if the
    database delete then failed after the object was already removed.
    """
    active_user = _require_active_user(user)
    attachment = _load_owned_attachment(active_user, attachment_id)

    storage_key = attachment.storage_key
    attachment.delete()
    storage.delete_object(storage_key)
