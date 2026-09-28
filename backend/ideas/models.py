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
    ('submitted', 'Submitted'),
    ('under_review', 'Under review'),
    ('changes_requested', 'Changes requested'),
    ('rejected', 'Rejected'),
    ('approved', 'Approved'),
    ('automation_proposal', 'Automation proposal'),
)

IDEA_VISIBILITY_CHOICES = (
    ('public', 'Public'),
    ('organization', 'Organization'),
    ('department', 'Department'),
    ('private', 'Private'),
)


class Idea(models.Model):
    """
    One problem statement put forward for possible automation - the platform's
    central submission.

    The `PROBLEM`/`IDEA` distinction in the product lifecycle is recorded in
    the content itself (`problem_statement`, `proposed_solution`,
    `expected_benefit`) rather than as separate entities: a submission is
    inseparable from the problem it describes, and splitting them would only
    add a join to every read.

    `organization` is the tenant boundary and is required on every row. It is
    what makes `User -> Membership -> Organization -> Idea` enforceable: no
    idea exists outside an organization, so there is no "global idea" that
    could be reached by forgetting a tenant filter.
    """

    class Status(models.TextChoices):
        """
        The submission lifecycle.

        Only the vocabulary is established here; which status may follow
        which, and who may make the move, is `ideas/lifecycle.py`'s transition
        matrix. All of it shipped in Sprint 2: `DRAFT -> SUBMITTED` in S2-002,
        and the review transitions
        (`SUBMITTED -> UNDER_REVIEW -> CHANGES_REQUESTED|REJECTED|APPROVED`,
        `CHANGES_REQUESTED -> SUBMITTED`) and `APPROVED -> AUTOMATION_PROPOSAL`
        in S2-003.
        """

        (
            DRAFT,
            SUBMITTED,
            UNDER_REVIEW,
            CHANGES_REQUESTED,
            REJECTED,
            APPROVED,
            AUTOMATION_PROPOSAL,
        ) = IDEA_STATUS_CHOICES

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
        help_text='The owning organization. Required: an idea never exists outside a tenant.',
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
    problem_statement = models.TextField(blank=True)
    proposed_solution = models.TextField(blank=True)
    expected_benefit = models.TextField(blank=True)
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


class Comment(models.Model):
    """
    A discussion reply on one idea.

    Belongs to exactly one `Idea` and exactly one authenticated `User`; both
    are required, so a comment can never exist without an author to hold it
    accountable or without a parent to be found under. Comments are *not*
    threaded in S2-001 - a parent pointer is a real feature with real
    authorization depth (who may see a reply to a private comment), and is not
    implied by "design the domain".

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
