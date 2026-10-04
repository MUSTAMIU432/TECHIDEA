"""
Ideas domain models (S2-001).

This app owns the platform's first business domain beyond Identity &
Access: the **problem/idea submission foundation**. Its job, in the
product lifecycle

    PROBLEM -> IDEA -> VALIDATION -> AUTOMATION OPPORTUNITY -> ...

is to hold the second step only. Nothing here knows about validation
decisions, automation opportunities, requirements, proposals, developers,
projects, deployment or impact - those are later sprints, and a change to
their vocabulary must never require a migration of these tables.

Six entities:

    Category     a platform-wide, reusable classification of ideas.
    Idea         the submission itself, and the tenant boundary.
    Comment      a discussion reply on one idea.
    Vote         one member's single "this is worth doing" signal.
    Attachment    metadata for a file held in object storage.
    IdeaTransition  one recorded status change: the lifecycle's audit trail (S3-007).
    IdeaSubmissionVersion  one frozen copy of an idea as submitted to the platform.

Design notes that are not obvious from the field lists
-------------------------------------------------------

**Why there is no `ideas/permissions.py`.** Authorization is Sprint 1's and
stays there. `organizations.authorization` is the single mechanism that
answers "may this user act in this organization", derived from the caller's
active membership and the permissions on their roles; every Ideas operation
routes through it. The one genuinely Ideas-specific question - *which ideas
may this user read?*, i.e. `Idea.visibility` - is a **read** policy, and
`organizations.authorization` is deliberately write/act-shaped. It is
therefore specified (and will be implemented, in S2-002) as a selector-level
visibility filter in `ideas/selectors.py`, layered on top of
`organizations.authorization.get_membership` - never as a second
permission system, and never as a client-side filter.

**Why `visibility` defaults to `PRIVATE`.** Fail closed. A brand-new
submission is visible to nobody but its author until someone deliberately
widens it, so the worst outcome of a bug that ignores this field is that a
draft stays hidden - not that an internal idea leaks.

**Why the free-text fields are blank-able.** A `DRAFT` is incomplete by
definition: the whole point of the draft state is that the author can save
part-way. "What must be filled in to submit" is therefore *not* expressed as
`null=False` here (which would make a draft unsavable) and is not a database
CHECK (which would constrain later review states the domain does not own
yet). It belongs to the `submit_idea` service, which is Sprint 2's
submission work. The database's job is to store the vocabulary of the
domain faithfully, not to enforce the workflow.

**Why `status` and `visibility` carry database CHECK constraints as well as
`choices`.** Django's `choices` are enforced by `full_clean()`/forms, not by
the database, so a `bulk_create`, a management command or a future app could
write a value outside the enum and the row would be silently unvalidatable
ever after. The CHECK constraints below are *derived* from the same enums, so
the two can never drift apart.

**Deliberately not modelled here.** `DEPARTMENT` is a `Visibility` value, but
there is no Department model: the platform has no department tier yet
(`docs/architecture.md`, Multi-Tenancy). The enum value reserves the
vocabulary so the data model does not have to change when the tier arrives,
and `docs/ideas-domain.md` records what enforcement will need. Creating a
Department app here would be a feature belonging to a different sprint.
"""

from typing import ClassVar

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify


class Category(models.Model):
    """
    A platform-wide classification an idea can be filed under.

    Global rather than organization-scoped, and that is the point: it is what
    makes ideas comparable *across* tenants, which is the input a later
    cross-organization discovery or matching feature would need. An
    organization-scoped category would silently make "customer support" in
    one company unrelated to "customer support" in another.

    Kept deliberately shallow - a flat list, not a taxonomy. No parent
    pointer, no ordering column, no per-organization overrides.

    Uniqueness is enforced on **both** `name` and `slug` because both are
    user-visible identifiers (one in a picker, one in a URL) and a
    same-spelled category in the platform is a data-entry mistake, not two
    categories. `is_active` is the deactivation path: an idea keeps its
    history either way, so retiring a category must not orphan it, which is
    why `Idea.category` is `PROTECT` - only a category that has never been
    used can be deleted at all.
    """

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text='Unset to retire a category from new ideas without touching existing ones.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['name']
        verbose_name_plural: ClassVar[str] = 'categories'
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=['name'], name='unique_category_name'),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self._normalize()
        self.full_clean()
        return super().save(*args, **kwargs)

    def _normalize(self) -> None:
        """
        Derive and clean the slug, so it can never disagree with the name.

        Called from both `clean()` and `save()` because Django runs
        `clean_fields()` (which rejects a blank slug) *before* `clean()`: a
        caller that constructed a `Category` without a slug and called
        `full_clean()` directly would otherwise be told the slug is blank
        before anything had a chance to fill it in. Normalizing in `save()`
        first makes both paths behave identically, and mirrors
        organizations.models.Permission.save().
        """
        if self.name:
            self.slug = slugify(self.slug or self.name)
        if self.slug:
            self.slug = slugify(self.slug)[: Category._meta.get_field('slug').max_length]

    def clean(self) -> None:
        super().clean()
        self._normalize()


# The two controlled vocabularies are declared at module level rather than
# inline in the enums below because Django's `Meta` is a nested class body
# and cannot see names from the enclosing class body - and a CHECK
# constraint's condition is evaluated while `Idea` itself is being defined.
# Declaring the pairs once here is what lets the enum and the database
# constraint below share one definition instead of two that can drift.
IDEA_STATUS_CHOICES = (
    ('draft', 'Draft'),
    # --- the organization stage, new in the submission-context phase -------
    #
    # Only an ORGANIZATION-context idea passes through these three; an
    # individual or team idea goes from `draft` straight to `submitted`,
    # because there is no organization to confirm it. `organization_review` is
    # deliberately absent as a status: "a reviewer has it open" is a fact about
    # the `reviews.Review` row, and is exactly the duplication that
    # `reviews.Review`'s docstring argues against when it declines to carry a
    # `status` column. The idea is `submitted_to_organization` the whole time
    # somebody is looking at it.
    ('submitted_to_organization', 'Waiting for organization review'),
    ('organization_changes_requested', 'Changes requested by organization'),
    ('organization_confirmed', 'Organization confirmed'),
    # --- the platform track: the values below this comment are Sprint 2/3's,
    # unchanged in meaning. `submitted` is "submitted to the platform",
    # `under_review` is "a platform reviewer has it", `changes_requested`,
    # `rejected` and `approved` are the three platform decisions. -----------
    ('submitted', 'Submitted'),
    ('under_review', 'Under review'),
    ('changes_requested', 'Changes requested'),
    ('rejected', 'Rejected'),
    # `approved` is deliberately ONE state, not two ("platform approved" and
    # "platform approved, awaiting the owner's go-ahead"). The report is
    # generated in the same transaction that sets it, and the owner's go-ahead
    # is recorded as `owner_go_ahead_at` on the move to
    # `ready_for_implementation` below, so the second state would be a
    # distinction without a second possible action: from
    # PLATFORM_APPROVED the only move in the whole domain is the owner's
    # explicit go-ahead, and it is guarded by the same check either way. A
    # client renders it as "Platform Approved - Your Confirmation Needed".
    ('approved', 'Approved'),
    # The handoff to Sprint 4, and nothing more: the owner authorized this
    # approved idea to proceed toward implementation. It does NOT mean a
    # developer was selected, a project exists, or any work has started.
    ('ready_for_implementation', 'Ready for implementation'),
    ('automation_proposal', 'Automation proposal'),
)

# How an idea was put forward. **This is the field that replaces "an idea
# belongs to an organization".** An organization is optional - a user with no
# organization can file an individual idea and take it all the way through
# platform review - so the tenant cannot be inferred from the row and has to be
# stated.
#
# One `Idea`, one table, one lifecycle, three contexts. Deliberately *not*
# three models (`IndividualIdea` / `TeamIdea` / `OrganizationIdea`): a comment,
# a vote, an attachment, a review and a lifecycle status all mean the same thing
# in every context, and splitting the model would mean either duplicating all of
# them or making them multi-table foreign keys. What the context changes is
# *which tenant may see it* and *whose validation applies before the platform
# sees it* - never the shape of the row.
IDEA_SUBMISSION_CONTEXT_CHOICES = (
    ('individual', 'Individual'),
    ('team', 'Team'),
    ('organization', 'Organization'),
)

IDEA_VISIBILITY_CHOICES = (
    ('public', 'Public'),
    ('organization', 'Organization'),
    ('department', 'Department'),
    ('private', 'Private'),
)

# The closed vocabularies of the problem story (the guided intake form). Each
# is a set of plain-language answers a non-technical author can pick from -
# never a technology choice - and each gets a CHECK constraint derived from
# the same tuple, for the reason the status and visibility ones do. Human
# wording lives in the frontend; these labels are for the admin.
IDEA_FREQUENCY_CHOICES = (
    ('several_times_a_day', 'Several times a day'),
    ('daily', 'Daily'),
    ('several_times_a_week', 'Several times a week'),
    ('weekly', 'Weekly'),
    ('monthly', 'Monthly'),
    ('occasionally', 'Occasionally'),
    ('other', 'Other'),
)

IDEA_IMPACT_CHOICES = (
    ('too_much_time', 'It takes too much time'),
    ('repeated_work', 'People have to repeat the same work'),
    ('mistakes', 'Mistakes happen'),
    ('waiting', 'People have to wait'),
    ('delays', 'Work gets delayed'),
    ('overload', 'People become overloaded'),
    ('lost_information', 'Information gets lost'),
    ('complaints', 'Customers, students or users complain'),
    ('higher_costs', 'It increases costs'),
    ('other', 'Other'),
)

# What the author handles the work with *today*. Deliberately everyday tools
# rather than a technology stack: the question is "what do you use", not "what
# should it integrate with" - that is a later, professional stage's call.
IDEA_CURRENT_TOOL_CHOICES = (
    ('paper_forms', 'Paper forms'),
    ('excel', 'Excel'),
    ('google_sheets', 'Google Sheets'),
    ('email', 'Email'),
    ('whatsapp', 'WhatsApp'),
    ('phone_calls', 'Phone calls'),
    ('website', 'Website'),
    ('mobile_app', 'Mobile application'),
    ('computer_program', 'Computer program'),
    ('physical_files', 'Physical files'),
    ('other', 'Other'),
)


class Idea(models.Model):
    """
    One problem statement put forward for possible automation - the platform's
    central submission.

    The `PROBLEM`/`IDEA` distinction in the product lifecycle is recorded in
    the content itself - `description` and the problem-story fields below it
    (what happens today, who it affects, what better would look like) -
    rather than as separate entities: a submission is inseparable from the
    problem it describes, and splitting them would only add a join to every
    read.

    `organization` was the tenant boundary and is required on every row, so
    every idea belonged to a tenant by construction. That stopped being true:
    a user with no organization can file an idea of their own, so the tenant
    is now *stated* rather than implied - `submission_context` says which of
    the three shapes this row is, and `clean()` (plus
    `idea_submission_context_matches_tenants` in the database) enforces that
    the named tenants are exactly the ones that shape requires.
    """

    class Status(models.TextChoices):
        """
        The submission lifecycle.

        Only the vocabulary is established here; which status may follow
        which, and who may make the move, is `ideas/lifecycle.py`'s transition
        matrix. `ideas/states.py` maps each value to what a person should be
        told, and which single action is theirs to take next.

        The lifecycle is **the same for every submission context**; only the
        first hop differs. An `ORGANIZATION` idea is validated by its
        organization before the platform ever sees it
        (`SUBMITTED_TO_ORGANIZATION -> ORGANIZATION_CHANGES_REQUESTED |
        ORGANIZATION_CONFIRMED -> SUBMITTED`), while an `INDIVIDUAL` or `TEAM`
        idea goes straight from `DRAFT` to `SUBMITTED` and enters the platform
        track at exactly the same place.
        """

        (
            DRAFT,
            SUBMITTED_TO_ORGANIZATION,
            ORGANIZATION_CHANGES_REQUESTED,
            ORGANIZATION_CONFIRMED,
            SUBMITTED,
            UNDER_REVIEW,
            CHANGES_REQUESTED,
            REJECTED,
            APPROVED,
            READY_FOR_IMPLEMENTATION,
            AUTOMATION_PROPOSAL,
        ) = IDEA_STATUS_CHOICES

    class SubmissionContext(models.TextChoices):
        """
        Whether an idea is put forward by one person, by a team, or on behalf
        of an organization.

        - `INDIVIDUAL`: the author, alone. No organization and no team. This is
          the case that makes "organization is optional" true rather than
          aspirational.
        - `TEAM`: the author plus the other members of one `teams.Team`. The
          team is a collaboration, not a tenant: it validates nothing and
          cannot approve anything (see `teams/authorization.py`).
        - `ORGANIZATION`: put forward on behalf of an organization, which
          confirms it before the platform sees it.

        The value is immutable after creation (`clean` refuses a change), so an
        idea's tenant and the audience that can see it can never disagree
        retroactively. Changing the context means filing a new idea, which is
        the honest thing: the two audiences have been reading different
        things.
        """

        (INDIVIDUAL, TEAM, ORGANIZATION) = IDEA_SUBMISSION_CONTEXT_CHOICES

    class Visibility(models.TextChoices):
        """
        Who may read an idea, independent of who may act on it.

        - `PUBLIC`: any authenticated user of the platform, in any
          organization. The idea is discoverable and readable platform-wide.
        - `ORGANIZATION`: any active member of the idea's organization.
        - `DEPARTMENT`: any active member of the idea's organization who is
          also in the author's department. **Not yet enforceable** - the
          platform has no Department model, so the value is reserved and
          nothing sets or filters on it until that tier exists; see
          `docs/ideas-domain.md`.
        - `PRIVATE`: the author only.

        This is a *read* policy and is enforced server-side in
        `ideas/selectors.py` on top of `organizations.authorization`. The
        frontend may narrow what it shows, but that is presentation, never
        the control.
        """

        (
            PUBLIC,
            ORGANIZATION,
            DEPARTMENT,
            PRIVATE,
        ) = IDEA_VISIBILITY_CHOICES

    class Frequency(models.TextChoices):
        """How often the problem happens, in the author's own rough terms."""

        (
            SEVERAL_TIMES_A_DAY,
            DAILY,
            SEVERAL_TIMES_A_WEEK,
            WEEKLY,
            MONTHLY,
            OCCASIONALLY,
            OTHER,
        ) = IDEA_FREQUENCY_CHOICES

    class Impact(models.TextChoices):
        """What happens because of the problem. An idea may name several."""

        (
            TOO_MUCH_TIME,
            REPEATED_WORK,
            MISTAKES,
            WAITING,
            DELAYS,
            OVERLOAD,
            LOST_INFORMATION,
            COMPLAINTS,
            HIGHER_COSTS,
            OTHER,
        ) = IDEA_IMPACT_CHOICES

    class CurrentTool(models.TextChoices):
        """What the work is handled with today. An idea may name several."""

        (
            PAPER_FORMS,
            EXCEL,
            GOOGLE_SHEETS,
            EMAIL,
            WHATSAPP,
            PHONE_CALLS,
            WEBSITE,
            MOBILE_APP,
            COMPUTER_PROGRAM,
            PHYSICAL_FILES,
            OTHER,
        ) = IDEA_CURRENT_TOOL_CHOICES

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='ideas',
        # `db_index=False` because `Meta.indexes` below already leads two
        # composite indexes with this column, and a single-column index on it
        # would be a strict prefix of both - PostgreSQL could use either, so
        # keeping the singleton would only add write amplification. Applies
        # equally to the cascades and to every tenant filter. See
        # `ideas/tests/test_models.py::TestIdeaIndexes` for the assertion
        # that keeps this honest.
        db_index=False,
        null=True,
        blank=True,
        help_text=(
            'The organization this idea was filed for, when its submission '
            'context is `organization`. Null for an individual or team idea: '
            'an idea never exists outside a tenant, but a tenant is not '
            'always an organization.'
        ),
    )
    team = models.ForeignKey(
        'teams.Team',
        on_delete=models.CASCADE,
        related_name='ideas',
        null=True,
        blank=True,
        # It leads `ideas_team_created_idx`, so Django's automatic single-column
        # index would be a strict prefix of it - pure cost, paid on every write
        # to `Idea`, for a lookup the composite already answers.
        db_index=False,
        help_text=(
            'The team this idea was filed for, when its submission context is '
            '`team`. A team is a collaboration boundary, not a tenant: it '
            'never validates the idea and can never approve it.'
        ),
    )
    submission_context = models.CharField(
        max_length=16,
        choices=SubmissionContext.choices,
        default=SubmissionContext.ORGANIZATION,
        help_text='Who this idea is being put forward by.',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ideas',
        db_index=False,  # Prefix of `ideas_author_created_idx` below.
        help_text='The member who submitted this idea.',
    )
    title = models.CharField(max_length=200)
    # Blank rather than required: see the module docstring - completeness is
    # a rule of the DRAFT -> SUBMITTED transition, not of the schema.
    description = models.TextField(blank=True)
    # Not written by any service. `problem_statement` would duplicate
    # `description`, which *is* the problem statement the submission rule
    # checks, and `proposed_solution` is deliberately never asked of the
    # author: the customer describes the problem and the outcome they want,
    # and designing a solution belongs to later, professional stages.
    problem_statement = models.TextField(blank=True)
    proposed_solution = models.TextField(blank=True)

    # --- the problem story ------------------------------------------------
    #
    # The guided intake form's answers, one column per question rather than
    # one JSON blob, so a reviewer - and later a filter, a report or the
    # review criteria - can read and query each answer on its own. Every one
    # is optional and blank-able for the same reason `description` is: a
    # draft is incomplete by definition, and "what must be filled in to
    # submit" is `services._validate_for_submission`'s rule, not the schema's.
    # None of them asks for technology: they describe what happens, who it
    # touches, and what better would look like, in the author's own words.

    # How does this happen today? The real-world process, start to finish.
    current_process = models.TextField(blank=True)
    current_tools = ArrayField(
        models.CharField(max_length=32, choices=IDEA_CURRENT_TOOL_CHOICES),
        default=list,
        blank=True,
        help_text='What the work is handled with today (paper, Excel, email...).',
    )
    current_tools_other = models.CharField(
        max_length=200,
        blank=True,
        help_text="Anything else the work is handled with, in the author's words.",
    )

    # The impact: who does the work, who it affects, and what it costs them.
    performed_by = models.CharField(max_length=300, blank=True)
    affected_people = models.CharField(max_length=300, blank=True)
    frequency = models.CharField(max_length=32, choices=IDEA_FREQUENCY_CHOICES, blank=True)
    # Free text on purpose ("about 30 minutes", "several days"): a rough,
    # human answer is the useful one, and a unit-and-number pair would ask the
    # author for a precision they do not have.
    time_required = models.CharField(max_length=120, blank=True)
    people_involved = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Roughly how many people take part in the process. Null means not answered.',
    )
    impacts = ArrayField(
        models.CharField(max_length=32, choices=IDEA_IMPACT_CHOICES),
        default=list,
        blank=True,
        help_text='What happens because of the problem.',
    )
    impact_details = models.TextField(blank=True)

    # What would the author like to improve - the outcome, never the design.
    improvement_goal = models.TextField(blank=True)
    desired_outcome = models.TextField(blank=True)
    easier_for_people = models.TextField(blank=True)
    # How would they know it is solved: what would be better afterwards. The
    # pre-existing column, now written by the intake form, and the content
    # the `expected_benefit` review criterion assesses.
    expected_benefit = models.TextField(blank=True)
    # Anything important to consider: approvals, privacy, rules, other
    # offices. Named for the question the author is asked, not "constraints".
    important_considerations = models.TextField(blank=True)
    category = models.ForeignKey(
        Category,
        # PROTECT, not CASCADE: an idea that has been submitted, reviewed and
        # acted on must never be erased because someone tidied up the category
        # list. Retirement (`is_active=False`) is the supported operation.
        on_delete=models.PROTECT,
        related_name='ideas',
        null=True,
        blank=True,
        db_index=False,  # Prefix of `ideas_category_created_idx` below.
        help_text='Optional: an idea can be drafted before it is classified.',
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Set once, when the DRAFT -> SUBMITTED transition happens, and never
    # rewritten afterwards: "when was this put forward" is an audit fact, and
    # `created_at` (row creation) and `submitted_at` (deliberate act) are
    # genuinely different moments. Null means "never submitted".
    submitted_at = models.DateTimeField(null=True, blank=True)

    # --- the platform stage ----------------------------------------------------
    #
    # Five columns, and each exists because a fact about the platform track
    # has to be stored somewhere that is *not* derivable from `status`. They
    # are all written by `reviews.platform_review` and `ideas.go_ahead`, never
    # by a client, and all read by `ideas/states.py` to decide what the owner
    # is shown next.

    # When the current official platform submission was frozen. Set on the
    # first `-> SUBMITTED` and on every re-submission after the platform asked
    # for changes; never cleared, because unlocking is not an operation this
    # domain has (a revision is a *new* version, see `IdeaSubmissionVersion`).
    # Null means "not on the platform yet".
    platform_locked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the official platform submission was frozen. Never cleared.',
    )
    # The version number of the official submission, starting at 1. Paired with
    # `IdeaSubmissionVersion.version`, and the same value, so an idea row can
    # say which version it is showing without a join on the read path.
    platform_version = models.PositiveSmallIntegerField(
        default=0,
        help_text='Version number of the official platform submission. 0 means not submitted.',
    )
    # When the platform reviewer approved. Paired with the
    # `PlatformReviewReport` generated in the same transaction. Null until then.
    platform_approved_at = models.DateTimeField(null=True, blank=True)
    # The owner's explicit go-ahead. **Written only by `ideas.go_ahead`**, by
    # the author, from the confirmation dialog - never by opening the report,
    # never by receiving the notification or the email. `PROTECT` for the
    # reason `IdeaTransition.actor` is: an audit fact must not vanish with an
    # account.
    owner_go_ahead_at = models.DateTimeField(null=True, blank=True)
    owner_go_ahead_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='idea_go_aheads',
        null=True,
        blank=True,
    )

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at']
        verbose_name_plural: ClassVar[str] = 'ideas'
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # Derived from the same tuples the enums above are built from, so
            # the allowed values have exactly one definition. See the module
            # docstring for why `choices` alone is not enough.
            models.CheckConstraint(
                condition=models.Q(status__in=dict(IDEA_STATUS_CHOICES)),
                name='idea_status_is_known',
            ),
            models.CheckConstraint(
                condition=models.Q(visibility__in=dict(IDEA_VISIBILITY_CHOICES)),
                name='idea_visibility_is_known',
            ),
            # The submission context and the tenants it names must agree, in the
            # database as well as in `clean()`.
            #
            # This is the rule that makes "an idea is not inherently
            # organization-owned" safe rather than merely intended: without it,
            # an `individual` idea carrying an organization would silently
            # inherit that organization's tenancy on every read, and an
            # organization-context idea with no organization would be a row no
            # reviewer could ever reach. Exactly one of the three shapes, and
            # no fourth shape.
            models.CheckConstraint(
                condition=(
                    # Literal values, not `Idea.SubmissionContext.*`: `Meta`
                    # is a nested class body and cannot see names from the
                    # enclosing class, which is the same reason
                    # IDEA_STATUS_CHOICES is declared at module level. The two
                    # spellings agree because they are the same three strings.
                    # `_id__isnull`, never `organization__isnull`: a CHECK
                    # constraint cannot join, and Django refuses to build the
                    # SQL for a joined reference. The column is the column.
                    models.Q(
                        submission_context='individual',
                        organization_id__isnull=True,
                        team_id__isnull=True,
                    )
                    | models.Q(
                        submission_context='team',
                        organization_id__isnull=True,
                        team_id__isnull=False,
                    )
                    | models.Q(
                        submission_context='organization',
                        organization_id__isnull=False,
                        team_id__isnull=True,
                    )
                ),
                name='idea_submission_context_matches_tenants',
            ),
            # An idea that has gone to the platform carries the fact that it
            # did, and an idea that has not carries neither. This is what makes
            # "the official submission is locked" a database fact rather than
            # an application convention a management command could skip.
            models.CheckConstraint(
                condition=(
                    models.Q(platform_locked_at__isnull=True, platform_version=0)
                    | models.Q(platform_locked_at__isnull=False, platform_version__gte=1)
                ),
                name='idea_platform_lock_matches_version',
            ),
            # The owner's go-ahead is exactly the move into
            # `ready_for_implementation`, and belongs to nobody else. An idea
            # cannot be handed to implementation without it, and it cannot be
            # recorded for an idea that is not on that path.
            #
            # `automation_proposal` is on the path too, and deliberately: the
            # hand-off to the developer track follows the go-ahead rather than
            # replacing it, so the record of *who authorized this* has to survive
            # the hand-off. Constraining on `ready_for_implementation` alone
            # would have made the lifecycle's `READY_FOR_IMPLEMENTATION ->
            # AUTOMATION_PROPOSAL` pair unsatisfiable the moment the move was
            # made - the row would have to be saved with the go-ahead still set,
            # and the constraint would refuse it. The two states are named
            # explicitly rather than tested by "not before ready", because a
            # constraint that passes by exclusion stops being a fact about the
            # go-ahead the moment a fourth state is added.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=[
                            'ready_for_implementation',
                            'automation_proposal',
                        ]
                    )
                    | models.Q(owner_go_ahead_at__isnull=True, owner_go_ahead_by__isnull=True)
                ),
                name='idea_go_ahead_only_when_ready',
            ),
            # The story's vocabularies, on the same principle: blank (not
            # answered) or a known value, and every element of the two lists
            # a known value.
            models.CheckConstraint(
                condition=models.Q(frequency='')
                | models.Q(frequency__in=dict(IDEA_FREQUENCY_CHOICES)),
                name='idea_frequency_is_known',
            ),
            models.CheckConstraint(
                condition=models.Q(impacts__contained_by=list(dict(IDEA_IMPACT_CHOICES))),
                name='idea_impacts_are_known',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    current_tools__contained_by=list(dict(IDEA_CURRENT_TOOL_CHOICES))
                ),
                name='idea_current_tools_are_known',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # The organization idea feed, newest first - by far the most
            # common read ("show me my organization's ideas"). Leading column
            # is the tenant filter every idea query must include, so it also
            # serves the narrower tenant + status reads below.
            models.Index(fields=['organization', '-created_at'], name='ideas_org_created_idx'),
            # A tenant-scoped board filtered by state (Sprint 3's review
            # queue is the first consumer, but "mine, still a draft" is
            # already one). Separate from the index above because a status
            # filter is not a prefix of `organization` alone - without this,
            # PostgreSQL would have to scan every idea in the tenant.
            models.Index(fields=['organization', 'status'], name='ideas_org_status_idx'),
            # Browsing one category, newest first. Leads on the category so a
            # category page is a single index range rather than a filter over
            # the whole table; `status` is included because an idea's public
            # state is what decides whether it belongs in a category listing
            # at all.
            models.Index(fields=['category', '-created_at'], name='ideas_category_created_idx'),
            # Visibility-filtered discovery, newest first. Leads on
            # `visibility` rather than being a single-column index because the
            # almost-only way this is queried is "everything visible at this
            # level, newest first" - the ordering is part of the access path,
            # not a sorting step bolted on afterwards.
            models.Index(fields=['visibility', '-created_at'], name='ideas_vis_created_idx'),
            # "My ideas" (drafts, submissions, history) - the author-scoped
            # equivalent of the two indexes above.
            models.Index(fields=['author', '-created_at'], name='ideas_author_created_idx'),
            # A team's ideas, newest first. Leads on `team` for the same reason
            # the organization index leads on `organization`: it is the tenant
            # filter every team-scoped read includes, and without it a team
            # feed would scan the whole table.
            models.Index(fields=['team', '-created_at'], name='ideas_team_created_idx'),
            # The author's action list - "my drafts", "changes requested",
            # "reports awaiting my confirmation" - which is always *this*
            # author, one status, newest first. `ideas_author_created_idx`
            # cannot serve it: it has no status column, so the filter would be
            # a scan of that author's whole history.
            models.Index(
                fields=['author', 'status', '-created_at'],
                name='ideas_author_status_idx',
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        # Same reasoning as Category.save() and organizations.models'
        # Permission.save(): the invariants below are only worth anything if
        # every write path honours them, and `save()` is the one place all of
        # them pass through.
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        # `submitted_at` is a fact about the transition, not a free field: a
        # draft by definition has not been submitted, and anything past
        # DRAFT has. Catching the inconsistency here (and, for the enum, at
        # the database too) means no later code has to wonder which of the
        # two it is looking at.
        if self.status == self.Status.DRAFT and self.submitted_at is not None:
            raise ValidationError('A draft cannot have a submitted_at timestamp.')
        if self.status != self.Status.DRAFT and self.submitted_at is None:
            raise ValidationError('Only a draft may be left without a submitted_at timestamp.')

        self._clean_submission_context()

    def _clean_submission_context(self) -> None:
        """
        The context must name tenants that exist, and the tenants must be the
        ones the context means.

        The database says the same thing (`idea_submission_context_matches_tenants`);
        it is restated here because `clean()` is what turns it into a
        `ValidationError` naming the field at fault, which is what a form and a
        service can act on. The two cannot drift - both are written from the
        same three-way `Q`.
        """
        expected = {
            self.SubmissionContext.INDIVIDUAL: (True, True),
            self.SubmissionContext.TEAM: (True, False),
            self.SubmissionContext.ORGANIZATION: (False, True),
        }.get(self.submission_context)
        if expected is None:
            return  # An unknown value is the choices/CHECK constraint's problem.

        organization_expected, team_expected = expected
        if (self.organization_id is None) != organization_expected:
            raise ValidationError(
                {
                    'organization': (
                        'Only an organization submission names an organization.'
                        if organization_expected
                        else 'An individual or team submission has no organization.'
                    )
                }
            )
        if (self.team_id is None) != team_expected:
            raise ValidationError(
                {
                    'team': (
                        'Only a team submission names a team.'
                        if team_expected
                        else 'Only a team submission names a team; this one does not.'
                    )
                }
            )

        # A team submission must name a team the author could actually put
        # forward. Cheap here (one existence query) and the alternative is a
        # `clean()` that accepts an idea its author has no standing to file -
        # which would then sit in a team feed belonging to somebody else.
        if self.team_id is not None and self.author_id is not None:
            from teams.models import TeamMembership

            if not TeamMembership.objects.filter(
                team_id=self.team_id,
                user_id=self.author_id,
                status=TeamMembership.Status.ACTIVE,
            ).exists():
                raise ValidationError(
                    {'team': 'You can only submit an idea on behalf of a team you belong to.'}
                )

    @property
    def tenant_label(self) -> str:
        """
        The name of whoever this idea belongs to, or an empty string.

        A display convenience for the header and the idea card, and the one
        place the three shapes of tenant are turned into a noun. It is derived,
        so it cannot disagree with the row - which is the same reason there is
        no `tenant_name` column.
        """
        if self.organization_id is not None and self.organization is not None:
            return self.organization.name
        if self.team_id is not None and self.team is not None:
            return self.team.name
        return ''

    @property
    def is_locked(self) -> bool:
        """
        Whether the official platform submission is frozen.

        Read by the services that refuse writes (`update_idea`) and rendered by
        the UI. It is `platform_locked_at is not None` and nothing else, so
        "locked" has exactly one definition in the codebase rather than a
        status list that has to be kept in step.
        """
        return self.platform_locked_at is not None


# The problem-story columns, in the order the intake form asks them. One list
# so the readers that need "the whole story" - the review snapshot, the admin -
# cannot silently miss a question added later.
IDEA_STORY_FIELDS: tuple[str, ...] = (
    'current_process',
    'current_tools',
    'current_tools_other',
    'performed_by',
    'affected_people',
    'frequency',
    'time_required',
    'people_involved',
    'impacts',
    'impact_details',
    'improvement_goal',
    'desired_outcome',
    'easier_for_people',
    'expected_benefit',
    'important_considerations',
)


class Comment(models.Model):
    """
    A comment on one idea, and - since the replies feature - a comment that may
    be an answer to another comment on the same idea.

    Belongs to exactly one `Idea` and exactly one authenticated `User`; both
    are required, so a comment can never exist without an author to hold it
    accountable or without a parent idea to be found under.

    **One level of nesting, and the depth is a rule rather than a field.**
    `parent` is a nullable self-reference, so a reply names the comment it
    answers; a top-level comment leaves it `None`. Nothing stores a depth
    because nothing needs to know one: `clean` refuses a parent that is itself
    a reply, so the deepest thing that can exist is a reply to a top-level
    comment. That is what lets the client render a thread as "the comment, then
    the replies under it" without any query that has to count levels - and it
    is what keeps a reply from becoming a second mechanism for saying something
    three screens away from what it is about.

    Deleting a comment cascades to its replies (`CASCADE` below) rather than
    orphaning them: a reply whose question has been taken away has nothing left
    to hang from, and promoting it to a top-level comment would silently
    re-attribute the meaning of somebody's words.

    No moderation state, edit history or soft delete here: moderation is not
    part of S2-001, and modelling it now would mean modelling a policy that
    does not exist yet.
    """

    idea = models.ForeignKey(
        Idea,
        on_delete=models.CASCADE,
        related_name='comments',
        db_index=False,  # Prefix of the composite index in `Meta` below.
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='idea_comments',
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='replies',
        null=True,
        blank=True,
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at']
        indexes: ClassVar[list[models.Index]] = [
            # Comments are only ever read as "one idea's discussion, oldest
            # first", which is a range scan on exactly this pair. The FK index
            # on `idea_id` alone cannot order the result.
            models.Index(fields=['idea', 'created_at'], name='comments_idea_created_idx'),
        ]

    def __str__(self) -> str:
        return f'Comment(idea={self.idea_id}, author={self.author_id})'

    def save(self, *args, **kwargs):
        # Same reasoning as Category.save() and Idea.save(). `validate_unique`
        # is a no-op here (Comment has no unique constraints), so the cost of
        # this is a local validation and no extra query.
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        # A comment with no words in it is a bug, not a state: PostgreSQL
        # will happily store `''` in a NOT NULL text column, so `blank=False`
        # alone only stops a form, not a `Comment.objects.create()`. The rule
        # is here rather than in `add_comment` (S2-002) so that no write path
        # can produce a contentless row.
        if not (self.content or '').strip():
            raise ValidationError('A comment must have content.')

        # Three rules about a reply, all here rather than in the service so that
        # no write path can skip them:
        #
        # - **Same idea.** A parent from another idea would put a reply in one
        #   thread that reads as though it belongs to another, which is the one
        #   thing a client cannot be trusted to have checked.
        # - **One level.** Replying to a reply would need a depth rule, a
        #   recursive rendering, and a limit; one level gets the same reading
        #   without any of them.
        # - **No self-reference.** A comment cannot be its own parent. The row
        #   cannot exist yet at create time, so this is only reachable on an
        #   update, and `full_clean` in `save` is what makes it true.
        if self.parent_id is None:
            return
        if self.parent_id == self.pk:
            raise ValidationError('A comment cannot be a reply to itself.')
        parent = Comment.objects.filter(pk=self.parent_id).only('idea_id', 'parent_id').first()
        if parent is None:
            raise ValidationError('The comment being replied to does not exist.')
        if parent.idea_id != self.idea_id:
            raise ValidationError('A reply must be on the same idea as the comment it answers.')
        if parent.parent_id is not None:
            raise ValidationError('A reply cannot itself be replied to.')


class Vote(models.Model):
    """
    One user's single "this is worth doing" signal on one idea.

    Deliberately the smallest possible expression of support: one row per
    (user, idea), no value, no weight, no downvotes. Scoring and ranking are
    a later concern, and a design that keeps `value` at 1 today can still
    introduce downvotes later by adding one; a design that assumed them would
    have to decide now what "0" and "-1" mean to every consumer.

    `votes` is not nullable and there is no "abstain" row, because the
    absence of a row is already the answer: the user has not voted.

    The `(user, idea)` uniqueness is a **database** constraint, not a
    service-level check, so two concurrent votes for the same idea cannot
    both succeed regardless of which service or adapter issued them.
    """

    idea = models.ForeignKey(
        Idea,
        on_delete=models.CASCADE,
        related_name='votes',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='idea_votes',
        # Prefix of `unique_vote_per_user_idea`, which already indexes
        # (user_id, idea_id) - so "every vote by this user" is a prefix range
        # scan of that index, and the singleton would be strictly redundant.
        db_index=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['user', 'idea'],
                name='unique_vote_per_user_idea',
            ),
        ]

    def __str__(self) -> str:
        return f'Vote(idea={self.idea_id}, user={self.user_id})'


class Attachment(models.Model):
    """
    Metadata for a file attached to an idea. **The bytes are not here.**

    This row is a pointer, not a container: `storage_key` is the object's key
    in the attachment storage backend and the file's bytes live there. No
    `FileField`, no `BinaryField`, no base64 in a `TextField` - the reason is
    not taste but the storage tier: PostgreSQL is the transactional tier, and
    attachments are large, immutable blobs whose read pattern (stream a whole
    file, rarely) and backup/replication profile (every backup, every replica,
    forever) are exactly what a row-oriented database is worst at.

    S2-001 established this boundary; S2-007 built the flow on it. The bytes
    arrive by an HTTP `multipart/form-data` upload and leave by an HTTP
    download (`ideas/views.py`), both re-authorized per request from the
    bearer token - there is no signed URL and no presigned browser-to-bucket
    upload. They are written through
    Django's storage abstraction (`ideas/storage.py`, the
    `STORAGES['attachments']` setting) under a key the server generates
    (`ideas.storage.generate_storage_key`), never one derived from the
    client's filename. The default backend is the local filesystem; moving to
    object storage is a settings change, not a change to this model.

    `storage_key` is unique so two attachments cannot silently point at the
    same object, which would make deleting one of them a data-loss event that
    nothing in the database would flag.

    `idea` is the only link; organization is deliberately *not* duplicated
    onto the row. It is reachable through `attachment.idea.organization`, and
    a second copy would be free to disagree with the idea it belongs to.
    """

    idea = models.ForeignKey(
        Idea,
        on_delete=models.CASCADE,
        related_name='attachments',
        db_index=False,  # Prefix of the composite index in `Meta` below.
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='idea_attachments',
    )
    # The name the uploader's file had, kept for display. The authoritative
    # name is whatever the object store returns for `storage_key`.
    filename = models.CharField(max_length=255)
    content_type = models.CharField(
        max_length=255,
        help_text='MIME type as reported at upload, e.g. image/png.',
    )
    size = models.PositiveBigIntegerField(help_text='Size in bytes.')
    storage_key = models.CharField(
        max_length=500,
        unique=True,
        help_text='Key of the object in storage. The bytes live there, never in PostgreSQL.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at']
        indexes: ClassVar[list[models.Index]] = [
            # Attachments are listed per idea, oldest first. Same reasoning as
            # Comment's index above.
            models.Index(fields=['idea', 'created_at'], name='attachments_idea_created_idx'),
        ]

    def __str__(self) -> str:
        return f'Attachment(idea={self.idea_id}, filename={self.filename})'


class IdeaTransition(models.Model):
    """
    One successful change of an idea's status: the lifecycle's audit trail
    (S3-007, `docs/reviews-domain.md` D-10).

    Written by `ideas.lifecycle` and nothing else, in the same transaction as
    the status change it records - `transition_idea` for the author's moves and
    the hand-off, `apply_review_transition` for the moves a review makes - so a
    status change without its row, or a row without its change, cannot be
    committed. It records *that* the status moved, who moved it and when; *why*
    (the criteria, the feedback, the snapshot) is the `reviews.Review` the move
    belongs to, which is a different record for a different purpose.

    **Append-only.** `save()` refuses to rewrite an existing row and `delete()`
    refuses outright; there is no service or mutation that changes one, and the
    admin is read-only. The only removal is the database cascade that removes
    the whole idea.

    **`actor` is the authenticated caller** the lifecycle authorized, never an
    input: no operation takes an actor argument from a client. `PROTECT`, for
    the reason on `reviews.Review.reviewer` - users are deactivated, not
    deleted, and an audit record must not disappear with an account.

    There is no link to the review. `ideas` does not depend on `reviews`, and
    the review a move belongs to is recoverable from the idea and the time.
    """

    idea = models.ForeignKey(
        Idea,
        on_delete=models.CASCADE,
        related_name='transitions',
        db_index=False,  # Prefix of `ideas_trans_idea_created_idx` below.
    )
    from_status = models.CharField(max_length=32, choices=Idea.Status.choices)
    to_status = models.CharField(max_length=32, choices=Idea.Status.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='idea_transitions',
        help_text='The authenticated member who made the move.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['created_at', 'pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(from_status__in=dict(IDEA_STATUS_CHOICES)),
                name='idea_transition_from_is_known',
            ),
            models.CheckConstraint(
                condition=models.Q(to_status__in=dict(IDEA_STATUS_CHOICES)),
                name='idea_transition_to_is_known',
            ),
            models.CheckConstraint(
                condition=~models.Q(from_status=models.F('to_status')),
                name='idea_transition_changes_status',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['idea', 'created_at'], name='ideas_trans_idea_created_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.idea_id}: {self.from_status} -> {self.to_status}'

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('A recorded transition cannot be changed.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('A recorded transition cannot be deleted.')


class IdeaSubmissionVersion(models.Model):
    """
    One frozen copy of an idea as it was submitted to the platform.

    **This is what "the official submission is locked" means.** `Idea.status`
    and the fact that an idea is under platform review are mutable - the owner
    answers review feedback by editing, the reviewer takes over a stalled
    review, the idea goes back and forth. What must never move is the thing the
    platform actually looked at. So the moment an idea is submitted, its
    content is copied here and never written again; the platform review reads
    *this*, not the live row.

    The alternative - refusing the owner any edit while the platform holds the
    idea - would make a changes-requested idea unfixable, and the alternative to
    that, editing in place, would silently rewrite history: a reviewer
    approving "version 2" of an idea that reads differently now is an approval
    of nothing. Versioning gives the third thing: the working copy stays
    editable, the submission stays frozen, and both are readable side by side.

    **Append-only, like `IdeaTransition`.** `save()` refuses to rewrite a stored
    row and `delete()` refuses outright; only removing the whole idea takes
    these rows, by cascade.

    `version` is 1 for the first submission and increases by one per
    re-submission after the platform asks for changes. The `(idea, version)`
    uniqueness is a **database** constraint, so two concurrent submissions
    cannot both claim the same number.

    `snapshot` is a JSON object, not the idea's columns: a version has to be
    able to describe an idea *as it was*, which means it must not gain a column
    every time the intake form grows a question. It is written by
    `reviews.platform_review.freeze_submission` from the same field list the
    reviewer workspace renders, so a version and a reviewer's screen cannot
    disagree about what was on it.

    `is_current` marks the version the platform is holding. It is a flag rather
    than "the highest number" because the flag is set in the same transaction
    that advances `Idea.platform_version`, and exactly one row per idea is
    current; `unique_current_version_per_idea` below is the backstop.
    """

    idea = models.ForeignKey(
        Idea,
        on_delete=models.CASCADE,
        related_name='submission_versions',
        db_index=False,  # Prefix of `versions_idea_version_idx` below.
    )
    version = models.PositiveSmallIntegerField(
        help_text=('1 for the first platform submission, then one more per resubmission.'),
    )
    submission_context = models.CharField(
        max_length=16,
        choices=Idea.SubmissionContext.choices,
        help_text=(
            'The context at the moment of submission. A copy, because the live row may change.'
        ),
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='idea_submission_versions',
        help_text='The member who submitted this version to the platform.',
    )
    snapshot = models.JSONField(help_text="The idea's content, frozen at the moment of submission.")
    is_current = models.BooleanField(
        default=False,
        help_text=(
            'Whether the platform is holding this version. Exactly one per idea once submitted.'
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['idea', 'version']
        verbose_name_plural: ClassVar[str] = 'idea submission versions'
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['idea', 'version'],
                name='version_number_unique_per_idea',
            ),
            # At most one current version per idea: the backstop for two
            # submissions racing to be the official one.
            models.UniqueConstraint(
                fields=['idea'],
                condition=models.Q(is_current=True),
                name='unique_current_version_per_idea',
            ),
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name='version_number_is_positive',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # An idea's submission history, oldest version first, and the
            # "which version is current" probe the reviewer workspace opens
            # with. Both are this pair.
            models.Index(fields=['idea', 'version'], name='versions_idea_version_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.idea_id} v{self.version}'

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('A submitted version cannot be changed.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('A submitted version cannot be deleted.')
