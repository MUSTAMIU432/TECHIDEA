"""
Freezing a submission: the one thing that makes "locked" real.

An idea's *working copy* (`ideas.Idea`) is editable for as long as the author is
answering feedback. What must not move is the **submission** the platform is
holding. So the moment an idea is submitted to the platform, its content is
copied into an `IdeaSubmissionVersion` and never written again, and the
platform reviews the version rather than the row.

Three consequences, and each of them is a requirement this module exists for:

1. **A reviewer approves a specific thing.** `reviews.Review.submission_snapshot`
   has always frozen what a reviewer looked at; the version is the same idea, one
   level up, and it is what a later revision is compared against.
2. **Answering feedback never rewrites history.** The author edits the working
   copy, resubmits, and version n+1 appears beside version n. Nothing is
   overwritten and the old round's review still describes the old submission.
3. **"Locked" needs no special case in the edit path.** Because the official copy
   is a separate row, `ideas.services.update_idea` does not have to know that
   locking exists in order to be correct - the thing that must not change cannot
   be changed by editing the row that is allowed to change.

`freeze_submission` is called from `ideas.lifecycle` inside the transaction that
moves the idea to `SUBMITTED`, so a status of "submitted to the platform" without
a version cannot be committed, and a version cannot exist for an idea that was
not submitted.
"""

import logging

from django.db import transaction
from django.utils import timezone

from ideas.models import IDEA_STORY_FIELDS, Idea, IdeaSubmissionVersion

logger = logging.getLogger(__name__)

# The fields a version freezes. Title, description, the whole problem story, the
# classification, the visibility and the context: everything a reader of the
# submission is entitled to see, and nothing about who read it or when.
#
# Declared once, in the order the review workspace renders them, so the version
# a reviewer reads and the version on screen cannot be two different lists.
SNAPSHOT_FIELDS: tuple[str, ...] = (*('title', 'description', 'visibility'), *IDEA_STORY_FIELDS)


def build_snapshot(idea: Idea) -> dict:
    """
    `idea`'s content as a plain dict, ready to be frozen.

    `ArrayField` values are lists and `people_involved` is an int or `None`;
    both are already JSON-native, so this is a straight copy rather than a
    serialization step. `datetime` fields are formatted explicitly because a
    naive `str()` would render in the server's local time and freeze a fact that
    reads differently in another timezone.
    """
    snapshot: dict = {name: getattr(idea, name) for name in SNAPSHOT_FIELDS}
    snapshot['category'] = (
        {'id': idea.category_id, 'name': idea.category.name, 'slug': idea.category.slug}
        if idea.category_id
        else None
    )
    snapshot['submission_context'] = idea.submission_context
    snapshot['organization_id'] = idea.organization_id
    snapshot['team_id'] = idea.team_id
    snapshot['submitted_at'] = idea.submitted_at.isoformat() if idea.submitted_at else None
    return snapshot


@transaction.atomic
def freeze_submission(idea: Idea, submitted_by) -> IdeaSubmissionVersion:
    """
    Copy `idea` into the next version and mark it current.

    Takes an **already locked** `idea` and runs inside the caller's transaction -
    it opens a savepoint, not a transaction of its own, for the same reason
    `lifecycle._apply_transition_locked` refuses to run outside one: the version
    and the status that claims it must commit together or not at all.

    It also stamps `Idea.platform_locked_at`, because the lock and the version
    number are one fact (`idea_platform_lock_matches_version` requires the pair)
    and writing them in two saves would have to pass through a row that fact
    forbids.

    The previous version is demoted to `is_current=False` in the same
    statement-free sequence, under the idea's lock, so an idea never has two
    current versions - the `unique_current_version_per_idea` constraint is the
    backstop if anything ever skipped the lock.
    """
    next_version = (idea.platform_version or 0) + 1

    IdeaSubmissionVersion.objects.filter(idea=idea, is_current=True).update(is_current=False)

    version = IdeaSubmissionVersion.objects.create(
        idea=idea,
        version=next_version,
        submission_context=idea.submission_context,
        submitted_by=submitted_by,
        snapshot=build_snapshot(idea),
        is_current=True,
    )

    # The lock and the version go on together: the CHECK constraint
    # `idea_platform_lock_matches_version` requires the pair, so a single save
    # that satisfies it is the only shape this row can have.
    idea.platform_locked_at = timezone.now()
    idea.platform_version = next_version
    idea.save(update_fields=['platform_locked_at', 'platform_version', 'updated_at'])

    logger.info(
        'Platform submission frozen (idea=%s, version=%s, submitted_by=%s).',
        idea.pk,
        version.pk,
        getattr(submitted_by, 'pk', None),
    )
    return version


def current_version(idea: Idea) -> IdeaSubmissionVersion | None:
    """
    The version the platform is holding, or `None` for an idea never submitted.

    Takes an already-resolved idea and does **one** indexed read. Used by the
    platform reviewer workspace and the report generator, both of which must
    describe the submission rather than the working copy.
    """
    if idea is None or not idea.pk:
        return None
    return (
        IdeaSubmissionVersion.objects.select_related('submitted_by')
        .filter(idea=idea, is_current=True)
        .first()
    )


def version_history(idea: Idea) -> list[IdeaSubmissionVersion]:
    """Every version of this idea, oldest first - the auditable submission history."""
    if idea is None or not idea.pk:
        return []
    return list(
        IdeaSubmissionVersion.objects.select_related('submitted_by')
        .filter(idea=idea)
        .order_by('version')
    )
