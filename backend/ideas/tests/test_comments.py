"""
Commenting on an idea, at the service and selector layers (S2-005).

A comment has no tenancy, no visibility and no state of its own, so every
question about it is a question about the idea it hangs from. That is what
this suite is mostly about, and it is why the assertions are ordered the way
they are:

1. **Reading a comment is reading the idea.** There is no second, comment-shaped
   visibility rule to be more permissive than the idea's, and the selectors
   are written to make that structural rather than a matter of remembering.
2. **Writing a comment follows readability, not membership.** Participation
   requires the ability to see the idea, which is what lets a `PUBLIC` idea be
   answered across tenants. Authorship of the *comment* is then required for
   every change to it, with no elevated path for anybody.
3. **Refusals are indistinguishable.** An unknown id, another author's
   comment and a comment on an idea the caller may not read all produce the
   same error, so neither a comment id nor an idea id becomes an oracle.
4. **Content is validated, normalized and never truncated.**
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from ideas import lifecycle, selectors, services
from ideas.models import Comment, Idea
from ideas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from identity.models import User
from organizations.models import Membership, MembershipRole, Role

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'x' * services.MIN_DESCRIPTION_LENGTH


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
    from organizations.services import CreateOrganizationInput, create_organization_for_user

    if owner is None:
        owner = make_user(f'{name.split()[0].lower()}@example.com')
    result = create_organization_for_user(owner, CreateOrganizationInput(name=name))
    return result.organization, result.membership


def add_active_member(organization, user, *, system_role=False):
    membership = Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )
    role = (
        Role.objects.get(organization=organization, is_system=True)
        if system_role
        else Role.objects.create(organization=organization, name='Contributor', slug='contributor')
    )
    MembershipRole.objects.create(membership=membership, role=role)
    return membership


def make_idea(organization, author, **overrides):
    """
    An idea, `ORGANIZATION`-visible by default.

    The model's own default is `PRIVATE`, which would make almost every test
    here a test about a colleague being unable to see the idea at all. The
    cases that care about visibility pass it explicitly.
    """
    fields = {
        'organization': organization,
        'author': author,
        'title': 'An idea',
        'description': DESCRIPTION,
        'visibility': Idea.Visibility.ORGANIZATION,
    }
    fields.update(overrides)
    return Idea.objects.create(**fields)


def make_comment(idea, author, content='A comment.', **overrides):
    fields = {'idea': idea, 'author': author, 'content': content}
    fields.update(overrides)
    return Comment.objects.create(**fields)


def submitted(idea):
    """An idea past the draft phase, which is where a discussion usually is."""
    idea.status = Idea.Status.SUBMITTED
    idea.submitted_at = timezone.now()
    idea.save()
    return idea


@pytest.fixture
def world():
    """
    One author, one colleague in the same organization, and one outsider in
    another - the three positions every comment rule has to tell apart.
    """
    author = make_user('author@example.com')
    organization, _ = make_organization(owner=author)
    colleague = make_user('colleague@example.com')
    add_active_member(organization, colleague)

    outsider = make_user('outsider@example.com')
    other, _ = make_organization(name='Other Co', owner=outsider)

    return {
        'author': author,
        'colleague': colleague,
        'outsider': outsider,
        'organization': organization,
        'other': other,
    }


# --- the discussion rule (lifecycle) ------------------------------------------------


@pytest.mark.django_db
class TestDiscussionRule:
    def test_every_closed_state_is_a_terminal_one(self):
        """
        The rule and the lifecycle must not be two answers, and the direction
        that matters is this one: a status the lifecycle can still leave is
        still a live idea, so its discussion stays open. If a future sprint
        adds a transition out of `REJECTED`, this fails and the rule is
        revisited.
        """
        terminal = {
            status
            for status in Idea.Status.values
            if not any(frm == status for frm, _to in lifecycle.TRANSITIONS)
        }

        assert set(lifecycle.DISCUSSION_CLOSED_STATUSES) <= terminal

    def test_rejected_is_closed_and_is_terminal(self):
        """
        `AUTOMATION_PROPOSAL` is terminal in this app as well, and is
        deliberately still open - an idea handed to the opportunities track is
        very much alive, and closing its discussion would cut off the
        conversation a handoff invites. `REJECTED` is closed because it is a
        verdict, which is a stronger claim than being terminal.
        """
        assert Idea.Status.REJECTED in lifecycle.DISCUSSION_CLOSED_STATUSES
        assert not any(frm == Idea.Status.REJECTED for frm, _to in lifecycle.TRANSITIONS)
        assert Idea.Status.AUTOMATION_PROPOSAL not in lifecycle.DISCUSSION_CLOSED_STATUSES

    def test_a_rejected_idea_is_closed(self, world):
        idea = submitted(make_idea(world['organization'], world['author']))
        idea.status = Idea.Status.REJECTED
        idea.save()

        assert lifecycle.discussion_is_open(idea) is False

    @pytest.mark.parametrize(
        'status',
        [
            'draft',
            'submitted',
            'under_review',
            'changes_requested',
            'approved',
            'automation_proposal',
        ],
    )
    def test_every_state_but_rejected_is_open(self, world, status):
        """
        Including `DRAFT`. A draft is the author's working copy, and a
        colleague asking a question while it is being written is ordinary; a
        rule that closed drafts would be a policy this domain never stated.
        """
        # Anything past DRAFT must arrive with its `submitted_at`, so the
        # timestamp is part of the create rather than a patch afterwards.
        fields = {'status': status}
        if status != 'draft':
            fields['submitted_at'] = timezone.now()
        idea = make_idea(world['organization'], world['author'], **fields)

        assert lifecycle.discussion_is_open(idea) is True


# --- creating -----------------------------------------------------------------------


@pytest.mark.django_db
class TestAddComment:
    def test_a_reader_posts_a_comment(self, world):
        idea = make_idea(world['organization'], world['author'])

        comment = services.add_comment(world['colleague'], idea.pk, 'We do this by hand.')

        assert comment.idea_id == idea.pk
        assert comment.author_id == world['colleague'].pk
        assert comment.content == 'We do this by hand.'

    def test_the_author_may_comment_on_their_own_idea(self, world):
        idea = make_idea(world['organization'], world['author'])

        comment = services.add_comment(world['author'], idea.pk, 'A note to self.')

        assert comment.author_id == world['author'].pk

    def test_a_reader_of_a_public_idea_from_another_tenant_may_comment(self, world):
        """
        Readability, not membership. A `PUBLIC` idea is platform-wide by
        definition, so refusing a comment from outside the organization would
        make `PUBLIC` mean "read-only to outsiders", which is not what the
        visibility tier says.
        """
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)

        comment = services.add_comment(world['author'], idea.pk, 'We hit this too.')

        assert comment.author_id == world['author'].pk

    def test_an_unauthenticated_caller_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError):
            services.add_comment(None, idea.pk, 'Hello?')

    def test_a_deactivated_caller_is_refused(self, world):
        colleague = world['colleague']
        colleague.is_active = False
        colleague.save(update_fields=['is_active'])
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError):
            services.add_comment(colleague, idea.pk, 'Hello?')

    def test_a_private_idea_cannot_be_commented_on_by_a_colleague(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)

        with pytest.raises(services.IdeaError) as refusal:
            services.add_comment(world['colleague'], idea.pk, 'A comment')

        # The author can see the idea, the colleague cannot, and the answer must
        # not tell them which of those it was.
        assert refusal.value.message == 'Idea is unavailable.'

    def test_a_department_scoped_idea_fails_closed_for_a_colleague(self, world):
        """
        Unchanged from S2-003: `DEPARTMENT` is author-only, and commenting is
        not a way round that. A discussion attached to an idea nobody else may
        read would be the most direct bypass of the rule there is.
        """
        idea = make_idea(
            world['organization'],
            world['author'],
            visibility=Idea.Visibility.DEPARTMENT,
        )

        with pytest.raises(services.IdeaError):
            services.add_comment(world['colleague'], idea.pk, 'A comment')

    def test_an_idea_in_another_tenant_cannot_be_commented_on(self, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)

        with pytest.raises(services.IdeaError) as refusal:
            services.add_comment(world['author'], idea.pk, 'A comment')

        assert refusal.value.message == 'Idea is unavailable.'

    def test_an_unknown_idea_answers_exactly_like_an_invisible_one(self, world):
        """
        The two must be identical, or `createComment` becomes a probe for which
        idea ids exist. `ORGANIZATION` in another tenant is the right control
        here: a `PUBLIC` idea there would be readable, and readable means
        commentable.
        """
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)

        with pytest.raises(services.IdeaError) as unknown:
            services.add_comment(world['author'], 999999, 'A comment')
        with pytest.raises(services.IdeaError) as invisible:
            services.add_comment(world['author'], idea.pk, 'A comment')

        assert unknown.value.message == invisible.value.message
        assert unknown.value.reason == invisible.value.reason

    def test_a_rejected_idea_is_closed_to_new_comments(self, world):
        idea = submitted(make_idea(world['organization'], world['author']))
        idea.status = Idea.Status.REJECTED
        idea.save()

        with pytest.raises(services.IdeaError) as refusal:
            services.add_comment(world['colleague'], idea.pk, 'One more thought')

        # Named rather than hidden: the reader can already see the idea and its
        # status, so a generic "unavailable" would say less than the truth.
        assert refusal.value.reason == 'discussion_closed'

    def test_a_comment_does_not_change_the_idea(self, world):
        idea = submitted(make_idea(world['organization'], world['author']))

        services.add_comment(world['colleague'], idea.pk, 'A comment')
        idea.refresh_from_db()

        # Commenting is not a lifecycle move and must not become one.
        assert idea.status == Idea.Status.SUBMITTED
        assert idea.submitted_at is not None


# --- content ------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommentContent:
    @pytest.mark.parametrize('content', ['', '   ', '\n\n', '\t', None])
    def test_a_contentless_comment_is_refused(self, world, content):
        """
        `"   "` is truthy, so the check has to be on the stripped value. The
        model's own `clean()` refuses it too; the service is here so the
        refusal is a field-level message the frontend can put next to the box
        rather than a 500.
        """
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError) as refusal:
            services.add_comment(world['colleague'], idea.pk, content)

        assert refusal.value.field == 'content'

    def test_a_comment_at_the_maximum_length_is_accepted(self, world):
        idea = make_idea(world['organization'], world['author'])

        comment = services.add_comment(
            world['colleague'], idea.pk, 'x' * services.MAX_COMMENT_LENGTH
        )

        assert len(comment.content) == services.MAX_COMMENT_LENGTH

    def test_an_overlong_comment_is_refused_and_not_truncated(self, world):
        """
        Refused, never shortened. Truncating would store text the author did
        not write and report success, so the only honest answer is a refusal
        the UI can show next to the box.
        """
        idea = make_idea(world['organization'], world['author'])
        too_long = 'x' * (services.MAX_COMMENT_LENGTH + 1)

        with pytest.raises(services.IdeaError) as refusal:
            services.add_comment(world['colleague'], idea.pk, too_long)

        assert refusal.value.field == 'content'
        assert Comment.objects.count() == 0

    def test_surrounding_whitespace_is_stripped(self, world):
        idea = make_idea(world['organization'], world['author'])

        comment = services.add_comment(world['colleague'], idea.pk, '  A comment.  \n')

        assert comment.content == 'A comment.'

    def test_line_endings_are_normalized(self, world):
        """
        The frontend's control is a `<textarea>`, and a browser on Windows
        submits CRLF. Storing that unchanged puts stray carriage returns in
        front of every other reader of the discussion.
        """
        idea = make_idea(world['organization'], world['author'])

        comment = services.add_comment(
            world['colleague'], idea.pk, 'First line.\r\nSecond line.\rThird.'
        )

        assert comment.content == 'First line.\nSecond line.\nThird.'

    def test_internal_whitespace_is_left_alone(self, world):
        """
        Indentation is somebody's content. Collapsing runs of spaces would
        destroy a pasted code block, and rewriting a comment into tidier prose
        is not the service's decision to make.
        """
        idea = make_idea(world['organization'], world['author'])

        comment = services.add_comment(
            world['colleague'], idea.pk, 'Here is the query:\n\n    SELECT 1\n'
        )

        assert comment.content == 'Here is the query:\n\n    SELECT 1'

    def test_a_comment_is_stored_verbatim_as_text(self, world):
        """
        No markup is stripped and none is escaped into the stored value. The
        content is plain text, returned as a plain GraphQL `String`, and the
        frontend renders it as text - escaping belongs at render time, and
        escaping here would mean the reader sees the escapes.
        """
        idea = make_idea(world['organization'], world['author'])
        markup = '<script>alert("x")</script> & "quotes"'

        comment = services.add_comment(world['colleague'], idea.pk, markup)

        assert comment.content == markup

    def test_the_model_refuses_a_contentless_row_too(self, world):
        """
        Defence in depth: the rule is in the service *and* on the model, so a
        write that goes through `save()` - which is every path the application
        has, including the admin - cannot produce an empty comment either.
        """
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(ValidationError):
            Comment.objects.create(idea=idea, author=world['colleague'], content='  ')

        assert Comment.objects.count() == 0

    def test_bulk_create_does_bypass_the_models_rule(self, world):
        """
        Pinned as a known gap rather than left for someone to discover.

        `Comment.clean()` runs from `save()`, and `bulk_create` and
        `QuerySet.update` never call `save()` - the same reason S2-002 had to
        add database CHECK constraints for `status` and `visibility` on top of
        `choices`. `content` has no such constraint, because a `TextField` with
        a blank check is a migration and this sprint's rule is the service's.
        Recorded here so the limitation is visible: a caller must go through
        `add_comment`/`update_comment`, and both do.
        """
        idea = make_idea(world['organization'], world['author'])

        Comment.objects.bulk_create([Comment(idea=idea, author=world['colleague'], content='  ')])

        assert Comment.objects.filter(content='  ').exists()


# --- updating -----------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateComment:
    def test_the_author_edits_their_own_comment(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'], 'Original text.')

        updated = services.update_comment(world['colleague'], comment.pk, 'Corrected text.')

        assert updated.pk == comment.pk
        assert updated.content == 'Corrected text.'

    def test_an_edit_cannot_move_or_reattribute_the_comment(self, world):
        """
        The service takes a comment id and some text. There is no argument that
        could name a different idea or a different author, so those cannot be
        changed even by a caller who means well.
        """
        idea = make_idea(world['organization'], world['author'])
        other_idea = make_idea(world['organization'], world['author'], title='Another')
        comment = make_comment(idea, world['colleague'])

        updated = services.update_comment(world['colleague'], comment.pk, 'Edited.')

        assert updated.idea_id == idea.pk
        assert updated.idea_id != other_idea.pk
        assert updated.author_id == world['colleague'].pk

    def test_an_edit_moves_the_edited_time_but_not_the_posted_time(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])
        posted_at = comment.created_at

        updated = services.update_comment(world['colleague'], comment.pk, 'Edited.')

        assert updated.created_at == posted_at
        assert updated.updated_at >= posted_at

    def test_another_user_cannot_edit_the_comment(self, world):
        """
        Not even the idea's author, and not even an organization Owner. There
        is no elevated path: `organizations.authorization` has no capability
        that means "moderate a discussion", and inventing one here would be the
        second authorization system S2-001 ruled out.
        """
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError) as refusal:
            services.update_comment(world['author'], comment.pk, 'Rewritten by the owner.')

        assert refusal.value.message == 'Comment is unavailable.'
        comment.refresh_from_db()
        assert comment.content == 'A comment.'

    def test_a_caller_from_another_tenant_cannot_edit_it(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError) as refusal:
            services.update_comment(world['outsider'], comment.pk, 'Rewritten.')

        assert refusal.value.message == 'Comment is unavailable.'

    def test_an_unknown_comment_answers_exactly_like_someone_elses(self, world):
        """
        Same message, same reason - so a comment id is not an oracle for what
        exists.
        """
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError) as unknown:
            services.update_comment(world['colleague'], 999999, 'Edited.')
        with pytest.raises(services.IdeaError) as not_mine:
            services.update_comment(world['author'], comment.pk, 'Edited.')

        assert unknown.value.message == not_mine.value.message

    def test_an_empty_edit_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError) as refusal:
            services.update_comment(world['colleague'], comment.pk, '   ')

        assert refusal.value.field == 'content'
        comment.refresh_from_db()
        assert comment.content == 'A comment.'

    def test_an_overlong_edit_is_refused_and_not_truncated(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError):
            services.update_comment(
                world['colleague'], comment.pk, 'x' * (services.MAX_COMMENT_LENGTH + 1)
            )

        comment.refresh_from_db()
        assert comment.content == 'A comment.'

    def test_an_edit_survives_a_closed_discussion(self, world):
        """
        Retraction stays available. Being able to take back what you wrote is
        not participating in the discussion, and a reviewer closing the thread
        must not be able to strand a comment nobody can ever remove or correct.
        """
        idea = submitted(make_idea(world['organization'], world['author']))
        comment = make_comment(idea, world['colleague'], 'Original.')
        idea.status = Idea.Status.REJECTED
        idea.save()

        updated = services.update_comment(world['colleague'], comment.pk, 'Corrected.')

        assert updated.content == 'Corrected.'


# --- deleting -----------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteComment:
    def test_the_author_deletes_their_own_comment(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        services.delete_comment(world['colleague'], comment.pk)

        assert not Comment.objects.filter(pk=comment.pk).exists()

    def test_another_user_cannot_delete_the_comment(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError) as refusal:
            services.delete_comment(world['author'], comment.pk)

        assert refusal.value.message == 'Comment is unavailable.'
        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_an_organization_owner_cannot_delete_it(self, world):
        """
        The same refusal as any other non-author. The idea's author owns the
        *idea*; ownership of a discussion is a different thing and this domain
        has not granted it to anybody.
        """
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError):
            services.delete_comment(world['author'], comment.pk)

        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_a_caller_from_another_tenant_cannot_delete_it(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError) as refusal:
            services.delete_comment(world['outsider'], comment.pk)

        assert refusal.value.message == 'Comment is unavailable.'
        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_an_unauthenticated_caller_cannot_delete_it(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        with pytest.raises(services.IdeaError):
            services.delete_comment(None, comment.pk)

        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_deleting_removes_the_row_hard(self, world):
        """
        No soft delete and no moderation state, because S2-001 declined to model
        a policy that does not exist. A `CASCADE` from the idea takes the
        discussion with it.
        """
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        services.delete_comment(world['colleague'], comment.pk)

        assert Comment.objects.count() == 0

    def test_deleting_the_idea_takes_its_discussion_with_it(self, world):
        idea = make_idea(world['organization'], world['author'])
        make_comment(idea, world['colleague'])

        idea.delete()

        assert Comment.objects.count() == 0

    def test_a_delete_survives_a_closed_discussion(self, world):
        idea = submitted(make_idea(world['organization'], world['author']))
        comment = make_comment(idea, world['colleague'])
        idea.status = Idea.Status.REJECTED
        idea.save()

        services.delete_comment(world['colleague'], comment.pk)

        assert not Comment.objects.filter(pk=comment.pk).exists()


# --- reading and ordering -----------------------------------------------------------


@pytest.mark.django_db
class TestListComments:
    def test_a_reader_sees_the_discussion(self, world):
        idea = make_idea(world['organization'], world['author'])
        mine = make_comment(idea, world['colleague'], 'A comment.')
        make_comment(idea, world['author'], 'Another.')

        page = selectors.list_comments(world['author'], idea.pk)

        assert mine in page.items
        assert page.total_count == 2

    def test_the_discussion_is_oldest_first(self, world):
        """
        Discussion order is chronological, which is how a conversation is read.
        Oldest first rather than newest first because the opening comment is
        what the rest of the thread answers.
        """
        idea = make_idea(world['organization'], world['author'])
        first = make_comment(idea, world['author'], 'First.')
        second = make_comment(idea, world['colleague'], 'Second.')
        third = make_comment(idea, world['author'], 'Third.')

        page = selectors.list_comments(world['author'], idea.pk)

        assert [c.pk for c in page.items] == [first.pk, second.pk, third.pk]

    def test_the_ordering_is_total_so_pages_do_not_overlap(self, world):
        """
        `created_at` is microsecond-resolution, so comments written in the same
        instant come back in an arbitrary order without a tie-breaker - and an
        arbitrary order lets a page boundary show one comment twice and skip
        another. `Meta.ordering` is `['created_at']` alone, so the tie-break is
        the selector's job.
        """
        idea = make_idea(world['organization'], world['author'])
        stamp = timezone.now()
        for index in range(5):
            comment = make_comment(idea, world['colleague'], f'Same {index}')
            Comment.objects.filter(pk=comment.pk).update(created_at=stamp)

        first = selectors.list_comments(world['author'], idea.pk, limit=2)
        second = selectors.list_comments(world['author'], idea.pk, offset=2, limit=2)

        assert [c.pk for c in first.items] == sorted((c.pk for c in first.items), reverse=False)
        assert not {c.pk for c in first.items} & {c.pk for c in second.items}

    def test_comments_are_paged(self, world):
        idea = make_idea(world['organization'], world['author'])
        for index in range(5):
            make_comment(idea, world['colleague'], f'Comment {index}')

        first = selectors.list_comments(world['author'], idea.pk, limit=2)

        assert len(first.items) == 2
        assert first.total_count == 5
        assert first.has_next_page is True
        assert first.has_previous_page is False

    def test_a_limit_above_the_maximum_is_clamped(self, world):
        idea = make_idea(world['organization'], world['author'])
        make_comment(idea, world['colleague'])

        page = selectors.list_comments(world['author'], idea.pk, limit=10_000)

        assert page.limit == MAX_PAGE_SIZE

    @pytest.mark.parametrize('offset', [None, 'abc', 3.5, -10, [1]])
    def test_an_unusable_offset_is_clamped_rather_than_refused(self, world, offset):
        """
        Paging arguments are hints, so a malformed one is brought inside the
        range rather than turned into an error - the same rule
        `pagination.clamp_offset` applies to idea discovery. `3.5` is refused
        rather than truncated to 3: it is not an offset anybody meant to send,
        and rounding it would invent an answer to a question nobody asked.
        """
        idea = make_idea(world['organization'], world['author'])
        make_comment(idea, world['colleague'])

        page = selectors.list_comments(world['author'], idea.pk, offset=offset)

        assert page.offset == 0
        assert len(page.items) == 1

    def test_the_default_page_size_is_the_ideas_one(self, world):
        """
        The same default as discovery. A second page size for comments would
        be a second convention to remember, and there is no reason for a
        discussion to differ from the list it hangs under.
        """
        idea = make_idea(world['organization'], world['author'])
        make_comment(idea, world['colleague'])

        page = selectors.list_comments(world['author'], idea.pk)

        assert page.limit == DEFAULT_PAGE_SIZE

    def test_an_unreadable_idea_gives_an_empty_page(self, world):
        """
        The same answer as for an idea that does not exist: a discussion must
        not be a better oracle than the idea it hangs from.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        make_comment(idea, world['author'])

        page = selectors.list_comments(world['colleague'], idea.pk)

        assert page.items == []
        assert page.total_count == 0

    def test_an_unknown_idea_gives_exactly_the_same_empty_page(self, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        make_comment(idea, world['outsider'])

        unknown = selectors.list_comments(world['author'], 999999)
        other_tenant = selectors.list_comments(world['author'], idea.pk)

        assert unknown.items == other_tenant.items == []
        assert unknown.total_count == other_tenant.total_count == 0

    def test_an_unauthenticated_caller_gets_an_empty_page(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PUBLIC)
        make_comment(idea, world['author'])

        assert selectors.list_comments(None, idea.pk).items == []

    def test_an_idea_with_no_discussion_gives_an_empty_page(self, world):
        idea = make_idea(world['organization'], world['author'])

        page = selectors.list_comments(world['author'], idea.pk)

        assert page.items == []
        assert page.total_count == 0

    def test_a_comment_from_another_idea_is_never_included(self, world):
        idea = make_idea(world['organization'], world['author'], title='One')
        other = make_idea(world['organization'], world['author'], title='Two')
        make_comment(other, world['colleague'], 'Belongs elsewhere.')

        page = selectors.list_comments(world['author'], idea.pk)

        assert page.items == []

    def test_reading_a_discussion_costs_a_fixed_number_of_queries(
        self, world, django_assert_num_queries
    ):
        """
        Four: the membership lookup the visibility filter needs, the idea read
        that resolves `idea_id` to something readable, the count, and the page
        fetch. Pinned because a per-comment query sneaking in here would make
        a page of 20 comments cost 40 queries without any test noticing, and
        because the idea read is the price of resolving the idea *through* the
        visibility filter rather than around it.
        """
        idea = make_idea(world['organization'], world['author'])
        for index in range(3):
            make_comment(idea, world['colleague'], f'Comment {index}')
        selectors.list_comments(world['author'], idea.pk, limit=2)

        with django_assert_num_queries(4):
            selectors.list_comments(world['author'], idea.pk, limit=2)

    def test_serializing_a_page_costs_no_extra_queries(self, world, django_assert_num_queries):
        """`select_related` on the author, so a page does not fan out."""
        idea = make_idea(world['organization'], world['author'])
        for index in range(3):
            make_comment(idea, world['colleague'], f'Comment {index}')
        page = selectors.list_comments(world['author'], idea.pk, limit=3)

        with django_assert_num_queries(0):
            for comment in page.items:
                assert comment.author.email
                assert comment.idea.title


# --- the single comment selector ----------------------------------------------------


@pytest.mark.django_db
class TestGetComment:
    def test_a_reader_resolves_a_comment(self, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        assert selectors.get_comment(world['author'], comment.pk).pk == comment.pk

    def test_it_is_none_for_a_comment_on_an_invisible_idea(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        comment = make_comment(idea, world['author'])

        assert selectors.get_comment(world['colleague'], comment.pk) is None

    def test_it_is_none_for_a_comment_in_another_tenant(self, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        comment = make_comment(idea, world['outsider'])

        assert selectors.get_comment(world['author'], comment.pk) is None

    @pytest.mark.parametrize('comment_id', [None, 'abc', '', [1], 3.5])
    def test_it_is_none_for_an_unusable_id(self, world, comment_id):
        assert selectors.get_comment(world['author'], comment_id) is None

    def test_it_is_none_for_an_unauthenticated_caller(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PUBLIC)
        comment = make_comment(idea, world['author'])

        assert selectors.get_comment(None, comment.pk) is None
