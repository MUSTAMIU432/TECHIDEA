"""
Model-level tests for the Ideas domain (S2-001).

These deliberately test only what S2-001 actually implements - the schema,
its constraints and its vocabularies. There are no service, GraphQL or
frontend operations to test yet, and nothing here pretends otherwise; see
`docs/ideas-domain.md` for where the behaviour lands in S2-002 onwards.
"""

from itertools import count

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from ideas.models import (
    IDEA_STATUS_CHOICES,
    IDEA_VISIBILITY_CHOICES,
    Attachment,
    Category,
    Comment,
    Idea,
    Vote,
)
from identity.models import User
from organizations.models import Organization

_email_counter = count(1)


def _make_user(email=None, **overrides):
    return User.objects.create_user(
        email=email or f'user{next(_email_counter)}@example.com',
        first_name=overrides.pop('first_name', 'Ada'),
        last_name=overrides.pop('last_name', 'Lovelace'),
        phone_number='+255712345678',
        password='a-strong-unique-pass-1',
        **overrides,
    )


def _make_organization(name=None, slug=None):
    suffix = next(_email_counter)
    return Organization.objects.create(
        name=name or f'Acme Labs {suffix}',
        slug=slug or f'acme-labs-{suffix}',
    )


def _make_idea(author=None, organization=None, **overrides):
    defaults = {
        'title': 'Stop re-keying invoice totals by hand',
        'description': 'Finance retypes the same totals from the supplier PDF every month.',
        'problem_statement': 'Month-end close is delayed by manual re-keying.',
        'proposed_solution': 'Read the PDF and post to the ledger automatically.',
        'expected_benefit': 'Two days of finance time back every month.',
    }
    defaults.update(overrides)
    return Idea.objects.create(
        author=author or _make_user(),
        organization=organization or _make_organization(),
        **defaults,
    )


class TestAppBoundary:
    def test_ideas_app_is_installed_with_its_own_config(self):
        config = apps.get_app_config('ideas')

        assert config.name == 'ideas'
        assert config.verbose_name == 'Ideas'
        assert config.default_auto_field == 'django.db.models.BigAutoField'

    def test_the_domain_owns_exactly_the_s2_001_entities(self):
        """
        Sprint scope guard.

        The product lifecycle continues past `IDEA` - validation,
        opportunities, proposals, developers, projects, impact - but each of
        those is a later sprint. If one of them ever appears in this app, it
        means a sprint boundary was crossed without noticing, which is worth
        failing a test over: an unreviewed review/proposal/developer model
        landing here is exactly how a domain app turns into a dumping ground.
        """
        model_names = {model.__name__ for model in apps.get_app_config('ideas').get_models()}

        # `IdeaTransition` (S3-007) is the lifecycle's own audit trail, decided
        # in `docs/reviews-domain.md` D-10 to live here because the lifecycle is
        # its only writer - not a later domain's entity.
        assert model_names == {
            'Category',
            'Idea',
            'Comment',
            'Vote',
            'Attachment',
            'IdeaTransition',
        }

    def test_no_attachment_field_stores_file_bytes_in_postgresql(self):
        """
        The storage boundary is a design decision, so it is asserted.

        `content_type`, `filename` and `size` are metadata; a `FileField`,
        `BinaryField` or `DataField` here would mean attachments live in
        PostgreSQL, which is the thing this domain deliberately does not do.
        """
        attachment_fields = {field.get_internal_type() for field in Attachment._meta.get_fields()}

        assert not attachment_fields & {'FileField', 'BinaryField', 'DataField'}


@pytest.mark.django_db
class TestCategoryModel:
    def test_category_can_be_created_and_is_active_by_default(self):
        category = Category.objects.create(name='Finance Ops')

        assert category.pk is not None
        assert category.slug == 'finance-ops'
        assert category.description == ''
        assert category.is_active is True
        assert category.created_at is not None
        assert category.updated_at is not None

    def test_slug_is_derived_from_the_name_when_not_given(self):
        assert Category.objects.create(name='Customer Support').slug == 'customer-support'

    def test_an_explicit_slug_is_kept_and_normalized(self):
        category = Category.objects.create(name='Customer Support', slug='Customer Support')

        assert category.slug == 'customer-support'

    def test_name_is_unique(self):
        Category.objects.create(name='Finance Ops')

        with pytest.raises((IntegrityError, ValidationError)):
            Category.objects.create(name='Finance Ops')

    def test_slug_is_unique(self):
        Category.objects.create(name='Finance Ops', slug='finance-ops')

        with pytest.raises((IntegrityError, ValidationError)):
            Category.objects.create(name='Finance', slug='finance-ops')

    def test_slug_longer_than_the_column_is_truncated(self):
        category = Category.objects.create(name='Finance Ops', slug='x' * 200)

        assert category.slug == 'x' * Category._meta.get_field('slug').max_length

    def test_str_returns_name(self):
        assert str(Category.objects.create(name='Finance Ops')) == 'Finance Ops'

    def test_a_category_in_use_cannot_be_deleted_but_can_be_retired(self):
        """
        PROTECT, not CASCADE.

        Deleting a category that an idea is filed under would silently
        destroy the classification of a submission that may already have been
        reviewed. `is_active` is the supported way to take a category out of
        circulation; deletion is only for one that was never used.
        """
        category = Category.objects.create(name='Finance Ops')
        _make_idea(category=category)

        with pytest.raises(ProtectedError):
            category.delete()

        category.is_active = False
        category.save()

        assert Category.objects.get(pk=category.pk).is_active is False

    def test_an_unused_category_can_be_deleted(self):
        category = Category.objects.create(name='Unused')

        category.delete()

        assert not Category.objects.filter(pk=category.pk).exists()


@pytest.mark.django_db
class TestIdeaLifecycleVocabulary:
    def test_status_vocabulary_is_exactly_the_product_lifecycle(self):
        assert list(Idea.Status.values) == [
            'draft',
            'submitted',
            'under_review',
            'changes_requested',
            'rejected',
            'approved',
            'automation_proposal',
        ]

    def test_visibility_vocabulary_is_exactly_the_four_levels(self):
        assert list(Idea.Visibility.values) == [
            'public',
            'organization',
            'department',
            'private',
        ]

    def test_enum_and_database_constraint_share_one_definition(self):
        """
        The `choices` and the CHECK constraints are generated from the same
        module-level tuples, so they cannot drift. This asserts they still
        agree.
        """
        assert Idea.Status.choices == list(IDEA_STATUS_CHOICES)
        assert Idea.Visibility.choices == list(IDEA_VISIBILITY_CHOICES)

    def test_an_unknown_status_is_rejected_by_model_validation(self):
        idea = _make_idea()
        idea.status = 'almost_reviewed'

        with pytest.raises(ValidationError):
            idea.full_clean()

    def test_an_unknown_visibility_is_rejected_by_model_validation(self):
        idea = _make_idea()
        idea.visibility = 'the_internet'

        with pytest.raises(ValidationError):
            idea.full_clean()

    def test_an_unknown_status_is_rejected_by_the_database(self):
        """
        `choices` alone only covers `full_clean()`; a `bulk_create`, a
        `QuerySet.update` or a management command bypasses it. The CHECK
        constraint is what makes the vocabulary an actual invariant.
        """
        idea = _make_idea()

        with pytest.raises(IntegrityError), transaction.atomic():
            Idea.objects.filter(pk=idea.pk).update(status='almost_reviewed')

    def test_an_unknown_visibility_is_rejected_by_the_database(self):
        idea = _make_idea()

        with pytest.raises(IntegrityError), transaction.atomic():
            Idea.objects.filter(pk=idea.pk).update(visibility='the_internet')

    def test_a_draft_carries_no_submitted_timestamp(self):
        idea = _make_idea()

        assert idea.status == Idea.Status.DRAFT
        assert idea.submitted_at is None

    def test_a_submitted_idea_must_carry_a_submitted_timestamp(self):
        with pytest.raises(ValidationError):
            _make_idea(status=Idea.Status.SUBMITTED)

    def test_a_draft_cannot_carry_a_submitted_timestamp(self):
        with pytest.raises(ValidationError):
            _make_idea(submitted_at=timezone.now())

    def test_a_reviewed_idea_keeps_its_submitted_timestamp(self):
        idea = _make_idea()

        idea.status = Idea.Status.SUBMITTED
        idea.submitted_at = timezone.now()
        idea.save()

        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED


@pytest.mark.django_db
class TestIdeaModel:
    def test_idea_defaults_fail_closed(self):
        """
        A new submission is a private draft.

        `PRIVATE` rather than `ORGANIZATION` so that code which forgets to
        consider visibility at all leaks nothing; widening it is a decision
        somebody has to make on purpose.
        """
        idea = _make_idea()

        assert idea.status == Idea.Status.DRAFT
        assert idea.visibility == Idea.Visibility.PRIVATE
        assert idea.submitted_at is None
        assert idea.category is None

    def test_idea_is_owned_by_exactly_one_organization_and_author(self):
        user = _make_user()
        organization = _make_organization()

        idea = _make_idea(author=user, organization=organization)

        assert idea.organization_id == organization.pk
        assert idea.author_id == user.pk
        assert list(organization.ideas.all()) == [idea]
        assert list(user.ideas.all()) == [idea]

    def test_title_and_tenant_are_required(self):
        user = _make_user()
        organization = _make_organization()

        with pytest.raises(ValidationError):
            Idea(author=user, organization=organization, title='').save()

        with pytest.raises(ValidationError):
            Idea(author=user, title='No tenant').save()

        with pytest.raises(ValidationError):
            Idea(organization=organization, title='No author').save()

    def test_a_draft_may_be_incomplete(self):
        """
        A draft is allowed to be partial - that is what a draft is. What must
        be present in order to *submit* is the `submit_idea` service's rule
        (S2-002), not this schema's.
        """
        idea = _make_idea(
            title='Something worth automating',
            description='',
            problem_statement='',
            proposed_solution='',
            expected_benefit='',
        )

        assert idea.pk is not None
        assert idea.problem_statement == ''

    def test_str_returns_title(self):
        assert str(_make_idea(title='Close the month faster')) == 'Close the month faster'

    def test_deleting_the_organization_deletes_its_ideas(self):
        """
        The tenant owns its data. Same rule as `Membership` in Sprint 1: a
        row that exists only inside a tenant cannot outlive the tenant.
        """
        organization = _make_organization()
        idea = _make_idea(organization=organization)

        organization.delete()

        assert not Idea.objects.filter(pk=idea.pk).exists()

    def test_ideas_are_not_shared_between_organizations(self):
        first = _make_organization(name='First', slug='first')
        second = _make_organization(name='Second', slug='second')

        first_idea = _make_idea(organization=first)
        _make_idea(organization=second)

        assert list(first.ideas.all()) == [first_idea]


class TestIdeaIndexes:
    def _index_columns(self, model):
        return {index.name: list(index.fields) for index in model._meta.indexes}

    def test_idea_carries_the_query_shaped_indexes_and_nothing_more(self):
        assert self._index_columns(Idea) == {
            'ideas_org_created_idx': ['organization', '-created_at'],
            'ideas_org_status_idx': ['organization', 'status'],
            'ideas_category_created_idx': ['category', '-created_at'],
            'ideas_vis_created_idx': ['visibility', '-created_at'],
            'ideas_author_created_idx': ['author', '-created_at'],
        }

    def test_foreign_keys_that_lead_an_index_do_not_also_get_a_singleton(self):
        """
        Every write to `Idea` updates an index, so an index that is a strict
        prefix of a composite one is pure cost. These are the columns that
        lead a composite index, so Django's automatic single-column FK index
        is switched off.
        """
        prefixed_by_a_composite = {
            field_name
            for index in Idea._meta.indexes
            for field_name in index.fields
            if not field_name.startswith('-')
        }

        for field_name in ('organization', 'author', 'category'):
            assert Idea._meta.get_field(field_name).db_index is False
            assert field_name in prefixed_by_a_composite

    def test_foreign_keys_without_a_leading_index_keep_the_default_index(self):
        # `Comment.author` and `Vote.idea` are looked up on their own (an
        # author's comments, an idea's votes) and lead no composite.
        assert Comment._meta.get_field('author').db_index is True
        assert Vote._meta.get_field('idea').db_index is True
        assert Attachment._meta.get_field('uploaded_by').db_index is True

    def test_comment_and_attachment_indexes_are_scoped_to_their_idea(self):
        for model in (Comment, Attachment):
            assert self._index_columns(model) == {
                f'{model.__name__.lower()}s_idea_created_idx': ['idea', 'created_at'],
            }
            assert model._meta.get_field('idea').db_index is False


@pytest.mark.django_db
class TestCommentModel:
    def test_comment_belongs_to_one_idea_and_one_author(self):
        user = _make_user()
        idea = _make_idea()

        comment = Comment.objects.create(idea=idea, author=user, content='We hit this too.')

        assert comment.idea_id == idea.pk
        assert comment.author_id == user.pk
        assert list(idea.comments.all()) == [comment]
        assert list(user.idea_comments.all()) == [comment]

    def test_comment_requires_an_idea_an_author_and_content(self):
        idea = _make_idea()

        with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
            Comment.objects.create(idea=idea, content='Anonymous')

        with pytest.raises((IntegrityError, ValidationError)), transaction.atomic():
            Comment.objects.create(author=_make_user(), content='No idea')

    def test_empty_content_is_rejected(self):
        idea = _make_idea()

        with pytest.raises(ValidationError):
            Comment(idea=idea, author=_make_user(), content='   ').save()

    def test_comments_of_an_idea_read_oldest_first(self):
        idea = _make_idea()
        Comment.objects.create(idea=idea, author=_make_user(), content='Second')
        Comment.objects.create(idea=idea, author=_make_user(), content='First')

        # Compared against the stored timestamps rather than against the
        # insertion order above: two rows created inside the same test
        # transaction can share a timestamp, and asserting a literal sequence
        # would then be asserting PostgreSQL's tie-breaking rather than the
        # ordering this model declares.
        comments = list(idea.comments.all())
        timestamps = [comment.created_at for comment in comments]

        assert sorted(comment.content for comment in comments) == ['First', 'Second']
        assert timestamps == sorted(timestamps)

    def test_deleting_an_idea_deletes_its_comments(self):
        idea = _make_idea()
        Comment.objects.create(idea=idea, author=_make_user(), content='We hit this too.')

        idea.delete()

        assert not Comment.objects.exists()

    def test_str_identifies_the_idea_and_author(self):
        user = _make_user()
        idea = _make_idea()
        comment = Comment.objects.create(idea=idea, author=user, content='We hit this too.')

        assert str(comment) == f'Comment(idea={idea.pk}, author={user.pk})'


@pytest.mark.django_db
class TestVoteModel:
    def test_vote_belongs_to_one_user_and_one_idea(self):
        user = _make_user()
        idea = _make_idea()

        vote = Vote.objects.create(idea=idea, user=user)

        assert vote.idea_id == idea.pk
        assert vote.user_id == user.pk
        assert list(idea.votes.all()) == [vote]
        assert list(user.idea_votes.all()) == [vote]

    def test_the_same_user_cannot_vote_twice_on_the_same_idea(self):
        """
        Enforced by the database, not by a service-level check, so two
        concurrent votes cannot both succeed whichever code path issued them.
        """
        user = _make_user()
        idea = _make_idea()
        Vote.objects.create(idea=idea, user=user)

        with pytest.raises(IntegrityError), transaction.atomic():
            Vote.objects.create(idea=idea, user=user)

    def test_different_users_can_vote_on_the_same_idea(self):
        idea = _make_idea()
        Vote.objects.create(idea=idea, user=_make_user())
        Vote.objects.create(idea=idea, user=_make_user(email='grace@example.com'))

        assert idea.votes.count() == 2

    def test_the_same_user_can_vote_on_different_ideas(self):
        user = _make_user()
        Vote.objects.create(idea=_make_idea(), user=user)
        Vote.objects.create(idea=_make_idea(title='Another idea'), user=user)

        assert Vote.objects.filter(user=user).count() == 2

    def test_a_vote_has_no_value_so_there_is_no_downvote_to_misinterpret(self):
        """
        Support is expressed by the row's existence. Adding a downvotes or
        scoring feature later means adding a column then - designing around
        one now would mean deciding what zero and negative weights mean to
        every reader, for a feature nobody has specified.
        """
        assert [field.name for field in Vote._meta.fields] == [
            'id',
            'idea',
            'user',
            'created_at',
        ]

    def test_deleting_an_idea_deletes_its_votes(self):
        idea = _make_idea()
        Vote.objects.create(idea=idea, user=_make_user())

        idea.delete()

        assert not Vote.objects.exists()

    def test_str_identifies_the_idea_and_user(self):
        user = _make_user()
        idea = _make_idea()
        vote = Vote.objects.create(idea=idea, user=user)

        assert str(vote) == f'Vote(idea={idea.pk}, user={user.pk})'


@pytest.mark.django_db
class TestAttachmentModel:
    def test_attachment_is_metadata_pointing_at_object_storage(self):
        user = _make_user()
        idea = _make_idea()

        attachment = Attachment.objects.create(
            idea=idea,
            uploaded_by=user,
            filename='invoice-sample.pdf',
            content_type='application/pdf',
            size=2048,
            storage_key='ideas/1/invoice-sample.pdf',
        )

        assert attachment.idea_id == idea.pk
        assert attachment.uploaded_by_id == user.pk
        assert attachment.size == 2048
        assert list(idea.attachments.all()) == [attachment]
        assert list(user.idea_attachments.all()) == [attachment]

    def test_storage_key_is_unique(self):
        """
        Two attachments pointing at one object would make deleting either a
        silent data-loss event that nothing in the database would flag.
        """
        first_idea = _make_idea()
        Attachment.objects.create(
            idea=first_idea,
            uploaded_by=_make_user(),
            filename='sample.pdf',
            content_type='application/pdf',
            size=10,
            storage_key='ideas/shared/sample.pdf',
        )

        with pytest.raises(IntegrityError), transaction.atomic():
            Attachment.objects.create(
                idea=_make_idea(title='Another idea'),
                uploaded_by=_make_user(),
                filename='sample.pdf',
                content_type='application/pdf',
                size=10,
                storage_key='ideas/shared/sample.pdf',
            )

    def test_size_cannot_be_negative(self):
        idea = _make_idea()

        with pytest.raises(IntegrityError), transaction.atomic():
            Attachment.objects.create(
                idea=idea,
                uploaded_by=_make_user(),
                filename='sample.pdf',
                content_type='application/pdf',
                size=-1,
                storage_key='ideas/1/negative.pdf',
            )

    def test_organization_is_reachable_through_the_idea_not_duplicated(self):
        organization = _make_organization()
        idea = _make_idea(organization=organization)

        attachment = Attachment.objects.create(
            idea=idea,
            uploaded_by=_make_user(),
            filename='sample.pdf',
            content_type='application/pdf',
            size=10,
            storage_key='ideas/1/sample.pdf',
        )

        # The tenant filter for an attachment is `idea__organization_id`; a
        # second `organization` column on this row would be free to disagree
        # with the idea it belongs to.
        assert not hasattr(Attachment, 'organization')
        assert list(organization.ideas.all()) == [idea]
        assert attachment.idea.organization_id == organization.pk

    def test_deleting_an_idea_deletes_its_attachment_metadata(self):
        idea = _make_idea()
        Attachment.objects.create(
            idea=idea,
            uploaded_by=_make_user(),
            filename='sample.pdf',
            content_type='application/pdf',
            size=10,
            storage_key='ideas/1/sample.pdf',
        )

        idea.delete()

        assert not Attachment.objects.exists()

    def test_str_identifies_the_idea_and_filename(self):
        idea = _make_idea()
        attachment = Attachment.objects.create(
            idea=idea,
            uploaded_by=_make_user(),
            filename='sample.pdf',
            content_type='application/pdf',
            size=10,
            storage_key='ideas/1/sample.pdf',
        )

        assert str(attachment) == f'Attachment(idea={idea.pk}, filename=sample.pdf)'
