"""
Writing ideas, at the service layer (S2-002).

`ideas/services.py` owns every rule in this file, and the rules are the
point: this suite is mostly about *who may do what*, because that is the part
of an ideas feature that a working happy path says nothing about. The
properties worth protecting, in the order they are most easily broken:

1. **The client is never trusted with ownership or tenancy.** `organization`
   is authorized and then used; `author` is the authenticated user and cannot
   be supplied. There is no input field to put either in, so these are
   asserted as a property of the *interface* - the dataclass has no such
   field - and not merely as a runtime check.
2. **Refusals never confirm existence.** A nonexistent idea, another
   author's idea, another tenant's idea and an already-submitted one all
   answer the same way, because a different answer for any of them turns
   `updateIdea` into a probe for which idea ids are real.
3. **Membership is re-checked on every write**, not only at creation: leaving
   an organization must take the ability to write into it with you.
4. **Submission is the only way to become SUBMITTED**, it stamps
   `submitted_at` in the same transaction, and it validates content that a
   draft is allowed to be missing.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from ideas import services
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership

VALID_PASSWORD = 'a-strong-unique-pass-1'
OTHER_PASSWORD = 'another-strong-pass-2'
MIN_DESCRIPTION = 'x' * services.MIN_DESCRIPTION_LENGTH


def make_user(email='ada@example.com', **overrides):
    fields = {
        'email': email,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password=VALID_PASSWORD, **fields)


def make_organization(name='Acme Labs', owner=None):
    """
    An organization, and the creator's membership in it.

    Built through the real bootstrap service rather than by hand, so the
    membership and the Owner role come out the way the product creates them
    instead of in a shape the application never produces.
    """
    from organizations.services import CreateOrganizationInput, create_organization_for_user

    if owner is None:
        owner = make_user()
    result = create_organization_for_user(owner, CreateOrganizationInput(name=name))
    return result.organization, result.membership


def add_active_member(organization, user):
    """
    Give `user` an ACTIVE membership of `organization`, with no roles.

    No roles on purpose: a membership with no roles holds no permissions, so a
    test that needs to prove *ownership* rather than *permission* can use this
    and be sure the refusal was about authorship.
    """
    return Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )


def make_category(name='Customer support', **overrides):
    fields = {'name': name}
    fields.update(overrides)
    return Category.objects.create(**fields)


def make_idea(organization, author, **overrides):
    fields = {
        'organization': organization,
        'author': author,
        'title': 'An idea',
        'description': MIN_DESCRIPTION,
    }
    fields.update(overrides)
    return Idea.objects.create(**fields)


def data(**overrides):
    fields = {'title': 'Automate the invoice run', 'description': MIN_DESCRIPTION}
    fields.update(overrides)
    return services.IdeaInput(**fields)


# --- create_idea --------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateIdea:
    def test_authenticated_member_creates_a_draft(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        idea = services.create_idea(user, organization.pk, data())

        assert idea.status == Idea.Status.DRAFT
        assert idea.submitted_at is None
        assert idea.title == 'Automate the invoice run'
        assert idea.organization_id == organization.pk

    def test_the_author_is_the_authenticated_user(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        idea = services.create_idea(user, organization.pk, data())

        assert idea.author_id == user.pk

    def test_the_idea_input_has_no_author_or_organization_field(self):
        """
        The strongest form of "never trust the client": there is no field to
        put them in. A future change that adds one fails here rather than
        quietly opening a way to file an idea as somebody else.
        """
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(services.IdeaInput)}

        assert 'author' not in field_names
        assert 'author_id' not in field_names
        assert 'organization' not in field_names
        assert 'organization_id' not in field_names
        assert 'status' not in field_names
        assert 'submitted_at' not in field_names

    def test_an_unauthenticated_caller_is_refused(self):
        organization, _ = make_organization()

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(None, organization.pk, data())

        assert exc_info.value.reason == 'unauthenticated'
        assert Idea.objects.count() == 0

    def test_a_deactivated_caller_is_refused(self):
        # The organization belongs to somebody else: a deactivated user cannot
        # bootstrap one, so this is the only way to give them a tenant they
        # are not entitled to act in.
        user = make_user(is_active=False)
        organization, _ = make_organization(owner=make_user('owner@example.com'))

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data())

        assert exc_info.value.reason == 'unauthenticated'
        assert Idea.objects.count() == 0

    def test_a_non_member_of_the_organization_is_refused(self):
        user = make_user()
        other_organization, _ = make_organization(name='Other Co', owner=make_user('owner@o.test'))

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, other_organization.pk, data())

        assert exc_info.value.reason == 'membership_required'
        assert Idea.objects.count() == 0

    def test_an_inactive_membership_is_refused(self):
        """A row that still exists is not a membership."""
        user = make_user()
        organization, membership = make_organization(owner=user)
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data())

        assert exc_info.value.reason == 'membership_required'
        assert Idea.objects.count() == 0

    def test_an_unknown_organization_is_refused_without_confirming_it_exists(self):
        user = make_user()

        with pytest.raises(services.IdeaError):
            services.create_idea(user, 999999, data())

    def test_a_malformed_organization_id_is_refused(self):
        user = make_user()

        with pytest.raises(services.IdeaError):
            services.create_idea(user, 'not-an-id', data())

    def test_visibility_defaults_to_private(self):
        """Fail closed: a new idea is visible to nobody but its author."""
        user = make_user()
        organization, _ = make_organization(owner=user)

        idea = services.create_idea(user, organization.pk, data())

        assert idea.visibility == Idea.Visibility.PRIVATE

    @pytest.mark.parametrize(
        'visibility',
        [Idea.Visibility.PUBLIC, Idea.Visibility.ORGANIZATION],
    )
    def test_a_chosen_visibility_is_honoured(self, visibility):
        user = make_user()
        organization, _ = make_organization(owner=user)

        idea = services.create_idea(user, organization.pk, data(visibility=visibility))

        assert idea.visibility == visibility

    def test_the_department_visibility_cannot_be_set(self):
        """
        Reserved vocabulary with no Department model behind it, so nothing
        could honour it. Storing it would mean the author believes the idea is
        department-scoped while every reader treats it as private or, later,
        organization-wide.
        """
        user = make_user()
        organization, _ = make_organization(owner=user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data(visibility='department'))

        assert exc_info.value.field == 'visibility'
        assert Idea.objects.count() == 0

    def test_an_unknown_visibility_is_refused(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data(visibility='secret-to-nobody'))

        assert exc_info.value.field == 'visibility'

    def test_visibility_is_normalized_rather_than_rejected_over_casing(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        idea = services.create_idea(user, organization.pk, data(visibility='PUBLIC'))

        assert idea.visibility == Idea.Visibility.PUBLIC

    def test_a_category_can_be_chosen(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        category = make_category()

        idea = services.create_idea(user, organization.pk, data(category_id=category.pk))

        assert idea.category_id == category.pk

    def test_a_category_is_optional_on_a_draft(self):
        """An idea can be written before it is classified."""
        user = make_user()
        organization, _ = make_organization(owner=user)

        idea = services.create_idea(user, organization.pk, data())

        assert idea.category_id is None

    def test_an_unknown_category_is_refused_rather_than_silently_dropped(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data(category_id=999999))

        assert exc_info.value.field == 'category'
        assert Idea.objects.count() == 0

    def test_a_malformed_category_id_is_refused(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data(category_id='nope'))

        assert exc_info.value.field == 'category'

    def test_a_retired_category_cannot_be_newly_chosen(self):
        """
        `is_active` is retirement, and `Idea.category` is PROTECT, so a retired
        category keeps its history and disappears from the picker. Filing
        something new under one is almost certainly a mistake.
        """
        user = make_user()
        organization, _ = make_organization(owner=user)
        category = make_category(is_active=False)

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data(category_id=category.pk))

        assert exc_info.value.field == 'category'

    @pytest.mark.parametrize('title', ['', '   ', None])
    def test_a_blank_title_is_refused(self, title):
        user = make_user()
        organization, _ = make_organization(owner=user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data(title=title))

        assert exc_info.value.field == 'title'
        assert Idea.objects.count() == 0

    def test_an_over_long_title_is_refused(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.create_idea(user, organization.pk, data(title='x' * 201))

        assert exc_info.value.field == 'title'

    def test_a_draft_may_have_no_description(self):
        """The whole point of a draft: a half-written idea is savable."""
        user = make_user()
        organization, _ = make_organization(owner=user)

        idea = services.create_idea(user, organization.pk, data(description=''))

        assert idea.description == ''

    def test_content_is_trimmed(self):
        user = make_user()
        organization, _ = make_organization(owner=user)

        idea = services.create_idea(
            user, organization.pk, data(title='  Padded title  ', description='  padded  ')
        )

        assert idea.title == 'Padded title'
        assert idea.description == 'padded'


# --- update_idea --------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateIdea:
    def test_the_author_can_edit_their_draft(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        updated = services.update_idea(user, idea.pk, data(title='A better title'))

        assert updated.title == 'A better title'
        updated.refresh_from_db()
        assert updated.title == 'A better title'

    def test_a_non_author_in_the_same_organization_is_refused(self):
        """
        Ownership, not membership, is the gate for editing: being in the
        organization is not permission to rewrite somebody else's submission.
        """
        author = make_user()
        organization, _ = make_organization(owner=author)
        colleague = make_user('colleague@example.com')
        add_active_member(organization, colleague)
        idea = make_idea(organization, author)

        with pytest.raises(services.IdeaError) as exc_info:
            services.update_idea(colleague, idea.pk, data(title='Hijacked'))

        assert exc_info.value.message == 'Idea is unavailable.'
        idea.refresh_from_db()
        assert idea.title == 'An idea'

    def test_a_member_of_another_tenant_is_refused(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        idea = make_idea(organization, author)

        with pytest.raises(services.IdeaError) as exc_info:
            services.update_idea(outsider, idea.pk, data(title='Hijacked'))

        assert exc_info.value.message == 'Idea is unavailable.'

    def test_an_unauthenticated_caller_is_refused(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.update_idea(None, idea.pk, data())

        assert exc_info.value.reason == 'unauthenticated'

    def test_the_author_who_left_the_organization_is_refused(self):
        """
        Membership is re-checked on every write, not only at creation: leaving
        an organization has to take the ability to write into it with you.
        """
        user = make_user()
        organization, membership = make_organization(owner=user)
        idea = make_idea(organization, user)
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])

        with pytest.raises(services.IdeaError) as exc_info:
            services.update_idea(user, idea.pk, data(title='Still here?'))

        assert exc_info.value.reason == 'membership_required'
        idea.refresh_from_db()
        assert idea.title == 'An idea'

    def test_a_submitted_idea_cannot_be_edited(self):
        """S2-002 owns DRAFT -> SUBMITTED and nothing after it."""
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(
            organization, user, status=Idea.Status.SUBMITTED, submitted_at=timezone.now()
        )

        with pytest.raises(services.IdeaError) as exc_info:
            services.update_idea(user, idea.pk, data(title='Rewritten after the fact'))

        assert exc_info.value.message == 'Only a draft can be edited.'
        idea.refresh_from_db()
        assert idea.title == 'An idea'

    def test_an_unknown_idea_is_refused_with_the_same_message_as_someone_elses(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        someone_elses = make_idea(organization, make_user('other@example.com'))

        with pytest.raises(services.IdeaError) as unknown:
            services.update_idea(user, 999999, data())
        with pytest.raises(services.IdeaError) as not_mine:
            services.update_idea(user, someone_elses.pk, data())

        assert unknown.value.message == not_mine.value.message == 'Idea is unavailable.'

    def test_a_malformed_idea_id_is_refused(self):
        user = make_user()
        make_organization(owner=user)

        with pytest.raises(services.IdeaError):
            services.update_idea(user, 'nope', data())

    def test_it_validates_content(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.update_idea(user, idea.pk, data(title=''))

        assert exc_info.value.field == 'title'
        idea.refresh_from_db()
        assert idea.title == 'An idea'

    def test_it_validates_the_visibility(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.update_idea(user, idea.pk, data(visibility='department'))

        assert exc_info.value.field == 'visibility'

    def test_it_validates_the_category(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.update_idea(user, idea.pk, data(category_id=999999))

        assert exc_info.value.field == 'category'

    def test_ownership_and_tenant_cannot_be_changed(self):
        """
        There is no input field to change them with, so the assertion is that
        the row is untouched after an edit that tried.
        """
        user = make_user()
        organization, _ = make_organization(owner=user)
        other_user = make_user('other@example.com')
        idea = make_idea(organization, user, description=MIN_DESCRIPTION)

        updated = services.update_idea(user, idea.pk, data(title='Edited'))

        updated.refresh_from_db()
        assert updated.author_id == user.pk
        assert updated.author_id != other_user.pk
        assert updated.organization_id == organization.pk
        assert updated.status == Idea.Status.DRAFT
        assert updated.submitted_at is None

    def test_a_visibility_can_be_widened(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        updated = services.update_idea(user, idea.pk, data(visibility=Idea.Visibility.PUBLIC))

        assert updated.visibility == Idea.Visibility.PUBLIC


# --- submit_idea --------------------------------------------------------------------


@pytest.mark.django_db
class TestSubmitIdea:
    def test_a_complete_draft_becomes_submitted(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        category = make_category()
        idea = make_idea(organization, user, category=category)

        submitted = services.submit_idea(user, idea.pk)

        assert submitted.status == Idea.Status.SUBMITTED
        submitted.refresh_from_db()
        assert submitted.status == Idea.Status.SUBMITTED

    def test_submission_stamps_submitted_at(self):
        """The model treats a non-draft with no timestamp as inconsistent, so
        the two are written together."""
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, category=make_category())
        before = timezone.now()

        submitted = services.submit_idea(user, idea.pk)

        assert submitted.submitted_at is not None
        assert before <= submitted.submitted_at <= timezone.now() + timedelta(seconds=5)

    def test_the_timestamp_is_not_rewritten_by_a_later_read(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, category=make_category())

        services.submit_idea(user, idea.pk)
        first = Idea.objects.get(pk=idea.pk).submitted_at
        Idea.objects.get(pk=idea.pk)

        assert Idea.objects.get(pk=idea.pk).submitted_at == first

    def test_a_draft_with_no_description_cannot_be_submitted(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, description='', category=make_category())

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(user, idea.pk)

        assert 'Describe the problem' in exc_info.value.message
        idea.refresh_from_db()
        assert idea.status == Idea.Status.DRAFT
        assert idea.submitted_at is None

    def test_a_too_short_description_cannot_be_submitted(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, description='too short', category=make_category())

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(user, idea.pk)

        assert str(services.MIN_DESCRIPTION_LENGTH) in exc_info.value.message

    def test_an_unclassified_draft_cannot_be_submitted(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user)

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(user, idea.pk)

        assert 'category' in exc_info.value.message
        idea.refresh_from_db()
        assert idea.status == Idea.Status.DRAFT

    def test_an_untitled_draft_cannot_be_submitted(self):
        """Defence in depth: the service validates, and the model's own
        `clean()` would refuse the row anyway."""
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, title='   ', category=make_category())
        Idea.objects.filter(pk=idea.pk).update(title='')

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(user, idea.pk)

        assert 'title' in exc_info.value.message

    def test_the_submission_failure_is_a_whole_form_error(self):
        """
        Reported with no field: the author's next action is to go and fill the
        gaps in the form above them, not to have one input highlighted.
        """
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, description='')

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(user, idea.pk)

        assert exc_info.value.field is None

    def test_a_non_author_cannot_submit(self):
        author = make_user()
        organization, _ = make_organization(owner=author)
        colleague = make_user('colleague@example.com')
        add_active_member(organization, colleague)
        idea = make_idea(organization, author, category=make_category())

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(colleague, idea.pk)

        assert exc_info.value.message == 'Idea is unavailable.'
        idea.refresh_from_db()
        assert idea.status == Idea.Status.DRAFT

    def test_a_member_of_another_tenant_cannot_submit(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        outsider = make_user('outsider@example.com')
        make_organization(name='Other Co', owner=outsider)
        idea = make_idea(organization, user, category=make_category())

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(outsider, idea.pk)

        assert exc_info.value.message == 'Idea is unavailable.'
        idea.refresh_from_db()
        assert idea.status == Idea.Status.DRAFT

    def test_an_unauthenticated_caller_cannot_submit(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, category=make_category())

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(None, idea.pk)

        assert exc_info.value.reason == 'unauthenticated'

    def test_an_already_submitted_idea_cannot_be_submitted_again(self):
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, category=make_category())
        services.submit_idea(user, idea.pk)
        first = Idea.objects.get(pk=idea.pk).submitted_at

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(user, idea.pk)

        assert exc_info.value.message == 'Only a draft can be edited.'
        assert Idea.objects.get(pk=idea.pk).submitted_at == first

    def test_the_author_who_left_cannot_submit(self):
        user = make_user()
        organization, membership = make_organization(owner=user)
        idea = make_idea(organization, user, category=make_category())
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])

        with pytest.raises(services.IdeaError) as exc_info:
            services.submit_idea(user, idea.pk)

        assert exc_info.value.reason == 'membership_required'

    def test_submission_does_not_advance_past_submitted(self):
        """
        Review (`UNDER_REVIEW`, `CHANGES_REQUESTED`, `REJECTED`, `APPROVED`)
        and `AUTOMATION_PROPOSAL` are Sprint 3's. Asserted so that a later
        change to this method cannot quietly ship a review workflow by
        accident.
        """
        user = make_user()
        organization, _ = make_organization(owner=user)
        idea = make_idea(organization, user, category=make_category())

        submitted = services.submit_idea(user, idea.pk)

        assert submitted.status == Idea.Status.SUBMITTED
        assert submitted.status not in {
            Idea.Status.UNDER_REVIEW,
            Idea.Status.CHANGES_REQUESTED,
            Idea.Status.REJECTED,
            Idea.Status.APPROVED,
            Idea.Status.AUTOMATION_PROPOSAL,
        }
