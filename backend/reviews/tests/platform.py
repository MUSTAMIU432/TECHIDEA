"""
Test support for the platform review track.

**Why this exists.** Platform review used to be authorized by an organization
role - `idea.review`, held by the organization's non-system Reviewer - because
there was only one review and it belonged to the tenant. The submission-context
phase split review in two, and the platform half is now authorized by a
**platform-scoped Django permission** and nothing else: no organization role and
no team role can reach it. That is the whole of "platform reviewers are
independent" and "an organization reviewer cannot platform-approve", and it is
structural rather than a check somebody could forget.

So a test that exercises the platform track has to build a *platform reviewer*,
and this is the one place that knows how. Every test file's `add_member(...,
reviewer=True)` calls it, so the grant lives in one function rather than in
twenty copies - a copy per file is exactly how the permission would end up
quietly different in the organization tests and the platform tests.

Two functions, and the distinction between them is the point of the whole phase:

    `grant_platform_reviewer`  may review submissions to the **platform**.
    the organization Reviewer role  may review **its own organization's** ideas.

A test that needs both asks for both. A test that needs to prove the separation
must **not** call `grant_platform_reviewer` - that is the assertion.
"""

from django.contrib.auth.models import Permission

from administration.authorization import (
    ACCESS_CONSOLE,
    ASSIGN_PLATFORM_REVIEWERS,
    REVIEW_PLATFORM_SUBMISSIONS,
)
from ideas.models import Idea


def grant_permission(user, code: str) -> None:
    """
    Hold one platform permission directly, by its codename.

    Direct rather than through a group: the production grant lives in
    `administration`'s `grant_platform_admin` / `grant_platform_reviewer`
    management commands and puts holders in a group, but the *mechanism* under
    test is Django's per-user permission backend answering yes to a
    platform-scoped codename. Granting it directly exercises exactly that, with
    no group in the way, and makes each test's authorization visible at the call
    site.

    `code` is the full permission code (`administration.review_platform_submissions`),
    split here into the `content_type`/`codename` pair Django actually stores -
    so the helper takes the same string `administration.authorization` compares
    against, and a typo is a failed lookup rather than a silently wrong grant.

    Idempotent, so a helper that grants it twice (directly and again through a
    fixture) does not raise.
    """
    app_label, _, codename = code.partition('.')
    permission = Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )
    user.user_permissions.add(permission)
    # Django caches a user's permissions for the life of the object, and a
    # `User` that has already answered `has_perm` in this test would keep the
    # stale answer. Dropping the cache is what makes the grant take effect
    # immediately rather than "after this request".
    _clear_permission_cache(user)


def _clear_permission_cache(user) -> None:
    for attr in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
        if hasattr(user, attr):
            delattr(user, attr)


def grant_platform_reviewer(user, *, console: bool = True) -> None:
    """
    `user` may review ideas submitted to the platform.

    `console=True` by default because `REVIEW_PLATFORM_SUBMISSIONS` is only ever
    honoured together with `ACCESS_CONSOLE` (see `administration.authorization`):
    the review queue is a console surface, and a platform reviewer who cannot
    open the console has nowhere to work. A test for that specific refusal should
    pass `console=False`.
    """
    if console:
        grant_permission(user, ACCESS_CONSOLE)
    grant_permission(user, REVIEW_PLATFORM_SUBMISSIONS)


def grant_platform_intake(user, *, console: bool = True) -> None:
    """
    `user` may route submissions to platform reviewers.

    Deliberately a different grant from `grant_platform_reviewer`: intake decides
    nothing about an idea, so the person who does it need not be the person who
    approves it.
    """
    if console:
        grant_permission(user, ACCESS_CONSOLE)
    grant_permission(user, ASSIGN_PLATFORM_REVIEWERS)


def grant_platform_admin(user) -> None:
    """
    `user` may do every platform thing this phase added.

    For the console's own tests, which are about the console rather than about
    the separation of the two review tracks.
    """
    for code in (
        ACCESS_CONSOLE,
        ASSIGN_PLATFORM_REVIEWERS,
        REVIEW_PLATFORM_SUBMISSIONS,
    ):
        grant_permission(user, code)


# --- building a genuinely submitted idea --------------------------------------------


def submit(idea):
    """
    Put an idea through the real submission path, so it is what the platform
    actually receives.

    `Idea.objects.create(status=SUBMITTED, ...)` writes a row that claims to have
    been submitted without ever freezing a submission version - and the platform
    review and the report are both *about* a frozen version. Such a row is refused
    by `reviews.platform_review.generate_report`, correctly: there is no
    submission to write a report about.

    So fixtures must submit, not impersonate. This helper is the one place that
    says so, because "build the row the state machine would have built" is
    exactly the shortcut that would make the freeze, the lock and the report
    untested.
    """
    from ideas.services import submit_idea, submit_to_platform

    if idea.submission_context in (
        Idea.SubmissionContext.ORGANIZATION,
        Idea.SubmissionContext.TEAM,
    ):
        # An organization or team idea is validated by its organization before the
        # platform ever sees it, so "submitted to the platform" is three moves:
        # to the organization, confirmed by an organization reviewer, then on.
        # Walking all three is the point - a fixture that skipped the
        # organization stage would leave the stage itself untested and would be
        # asserting a journey that cannot happen.
        if idea.status == Idea.Status.DRAFT:
            submit_idea(idea.author, idea.pk)
            # Re-read: the organization review moves the status on its own
            # instance, so this one still says DRAFT until it is refreshed.
            idea.refresh_from_db()
            confirm_for_organization(idea)
            idea.refresh_from_db()
        if idea.status == Idea.Status.ORGANIZATION_CONFIRMED:
            # The **owner** submits, not the organization: confirmation and
            # submission are deliberately two acts by two different actors.
            submit_to_platform(idea.author, idea.pk)
        # Re-read before returning: each move above worked on its own locked
        # instance, so this one still carries whatever status it had before them.
        # Handing a caller a stale row is how a fixture ends up producing an idea
        # in `ORGANIZATION_CONFIRMED` for a test that means to review a
        # `SUBMITTED` one.
        idea.refresh_from_db()
        return idea

    if idea.status == Idea.Status.DRAFT:
        submit_idea(idea.author, idea.pk)
        idea.refresh_from_db()
    return idea


def team_reviewer_for(team):
    """
    An account holding the team's Reviewer role, created once per team.

    Through the *team* mechanism (`teams.TeamMembershipRole`), the counterpart of
    `organization_reviewer_for`.
    """
    from identity.models import User
    from teams.models import TeamMembership, TeamMembershipRole, TeamRole
    from teams.services import REVIEWER_ROLE_SLUG

    email = f'team-reviewer+{team.pk}@tests.example'
    user = User.objects.filter(email=email).first()
    if user is not None:
        return user

    user = User.objects.create_user(
        email=email,
        first_name='Team',
        last_name='Reviewer',
        phone_number='+255700000002',
        password='a-strong-unique-pass-1',
    )
    membership = TeamMembership.objects.create(team=team, user=user)
    TeamMembershipRole.objects.create(
        membership=membership,
        role=TeamRole.objects.get(team=team, slug=REVIEWER_ROLE_SLUG),
    )
    return user


def confirm_for_organization(idea, confirmer=None):
    """
    Have `idea`'s organization confirm it, and return the organization review.

    Uses a per-organization helper account holding the organization Reviewer
    role, so no test has to care: the organization track is exercised here, and
    the tests that are *about* the organization track drive it themselves
    (`reviews/tests/test_organization_review.py`).

    One account per organization, created on first use, so repeated fixtures in
    one test do not collide on the email.

    `confirmer` overrides that with an account the test already has - which is
    what a test counting users or reviews should do, because the helper account
    is an extra user and an extra completed review, and a fixture that quietly
    adds a row to somebody else's totals makes those totals a worse assertion
    rather than a better one. It is also the more realistic actor: an
    organization confirms its ideas through the reviewers it already has.
    """
    from reviews import organization_review

    if confirmer is None:
        confirmer = (
            team_reviewer_for(idea.team)
            if idea.submission_context == Idea.SubmissionContext.TEAM
            else organization_reviewer_for(idea.organization)
        )
    review = organization_review.start_organization_review(confirmer, idea.pk)
    return organization_review.complete_organization_review(
        confirmer,
        organization_review.CompleteOrganizationReviewInput(
            idea_id=idea.pk,
            review_id=review.pk,
            decision='confirmed',
            feedback='This is what we want to submit.',
        ),
    )


def organization_reviewer_for(organization):
    """
    An account holding `idea.review` in `organization`, created once per tenant.

    Reached through the *organization* mechanism, not the platform one: this is
    the helper for the organization track, and using the platform grant here would
    quietly destroy the separation these tests exist to prove.
    """
    from identity.models import User
    from organizations.models import Membership, MembershipRole, Role
    from organizations.services import REVIEWER_ROLE_SLUG

    email = f'org-reviewer+{organization.pk}@tests.example'
    user = User.objects.filter(email=email).first()
    if user is not None:
        return user

    user = User.objects.create_user(
        email=email,
        first_name='Org',
        last_name='Reviewer',
        phone_number='+255700000001',
        password='a-strong-unique-pass-1',
    )
    membership = Membership.objects.create(user=user, organization=organization)
    MembershipRole.objects.create(
        membership=membership,
        role=Role.objects.get(organization=organization, slug=REVIEWER_ROLE_SLUG),
    )
    return user


def make_submitted(organization, author, **overrides):
    """
    A `DRAFT` idea, submitted to the platform the way the lifecycle does it.

    Creates the draft and then submits it in one call, so a test fixture cannot
    accidentally produce a SUBMITTED row with no submission version. Defaults are
    the ones the platform submission rules need (a category, a description long
    enough, and a visibility its reviewers can read) so the common case needs no
    arguments at all.
    """
    from ideas.models import Category, Idea

    # A unique name per call: several fixtures build several ideas in one test,
    # and `Category.name` is unique platform-wide, so a fixed name would collide.
    category = Category.objects.create(
        name=overrides.pop('category_name', f'Category {Category.objects.count() + 1}')
    )
    fields = {
        'organization': organization,
        'author': author,
        'title': 'Automate the invoice run',
        'description': 'A description long enough to be usable for a submission.',
        'category': category,
        'visibility': 'public',
        **overrides,
    }
    fields.setdefault('visibility', 'public')
    idea = Idea.objects.create(**fields)
    return submit(idea)


def build_idea(*, status: str = 'submitted', submitted_at=None, **fields):
    """
    An idea in `status`, built the way the lifecycle would have built it where
    that matters.

    `SUBMITTED` goes through `submit_idea`, so the submission is **frozen**: the
    idea has a `platform_version`, a `platform_locked_at` and a current
    `IdeaSubmissionVersion`. That version is what the platform reviewer reads and
    what the approval report is about, so a fixture that wrote a SUBMITTED row
    directly would be building an idea the report cannot be written about - and
    would leave the freeze untested.

    The other statuses are still written directly. That is not a shortcut in the
    same sense: they are the states a test is *about*, they are asserted through
    their own operations elsewhere in the suite, and manufacturing them through
    a chain of transitions would only make the fixture harder to read than the
    thing it sets up.
    """
    from django.utils import timezone

    from ideas.models import Idea

    idea = Idea.objects.create(status=Idea.Status.DRAFT, **fields)

    if status == Idea.Status.SUBMITTED:
        return submit(idea)

    if status and status != Idea.Status.DRAFT:
        idea.status = status
        idea.submitted_at = submitted_at or timezone.now()
        idea.save()

    return idea


def revoke_platform_reviewer(user, *, console: bool = True) -> None:
    """
    Take away the platform review permission.

    The counterpart of `grant_platform_reviewer`, and needed for the same reason:
    a test that wants to show somebody losing the ability to review must remove
    the thing that actually grants it. Removing an organization Reviewer role
    would leave platform review untouched and the test would pass for the wrong
    reason.
    """
    _revoke(user, REVIEW_PLATFORM_SUBMISSIONS)
    if console:
        _revoke(user, ACCESS_CONSOLE)
    _clear_permission_cache(user)


def revoke_platform_intake(user) -> None:
    """Take away the permission to route submissions to reviewers."""
    _revoke(user, ASSIGN_PLATFORM_REVIEWERS)
    _clear_permission_cache(user)


def _revoke(user, code: str) -> None:
    """
    Drop one permission from one user.

    `user.user_permissions` is a *related manager*, so a filtered queryset of it
    is a queryset of `Permission` - and `.delete()` on that would delete the
    platform's permission record, not this user's grant of it. That is a real bug
    to write into a test: every later holder of that permission, in this test and
    in every other, would silently stop having it. `.remove()` is the operation
    that means "this user no longer holds it".
    """
    app_label, _, codename = code.partition('.')
    permission = Permission.objects.filter(
        content_type__app_label=app_label,
        codename=codename,
    ).first()
    if permission is not None:
        user.user_permissions.remove(permission)


def release_proposal(idea):
    """
    Give `idea` a proposal its owner can act on: written by the reviewer who approved it, already
    released by an admin. For tests whose subject is what comes *after* the go-ahead - the go-ahead
    is refused without one, and the proposal flow itself is tested in `test_proposals.py`.
    """
    from django.utils import timezone

    from reviews.models import IdeaProposal, Review

    review = (
        Review.objects.filter(idea=idea, scope=Review.Scope.PLATFORM, decision='approved')
        .order_by('-completed_at', '-pk')
        .first()
    )
    writer = review.reviewer if review else idea.author
    now = timezone.now()
    proposal, _ = IdeaProposal.objects.update_or_create(
        idea=idea,
        defaults={
            'title': idea.title,
            'executive_summary': 'Automate it.',
            'problem': idea.description,
            'proposed_solution': 'A small tool.',
            'requirements_summary': '- Track every payment',
            'scope': 'The process described.',
            'deliverables': 'The tool.',
            'estimated_timeline': '4 weeks',
            'acceptance_criteria': 'It works.',
            'status': 'released',
            'created_by': writer,
            'submitted_by': writer,
            'submitted_at': now,
            'decided_at': now,
        },
    )
    return proposal
