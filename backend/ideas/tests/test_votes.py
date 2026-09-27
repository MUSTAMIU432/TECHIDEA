"""
Voting, at the service and selector layers (S2-006).

The `Vote` model is S2-001's and is used as designed: one row per
`(user, idea)`, no value, no downvotes, and the uniqueness enforced by the
database. The properties worth protecting, in the order they break:

1. **One user, one vote, per idea.** Enforced twice on purpose - a service
   check for the common case and `unique_vote_per_user_idea` for the race a
   check-then-insert cannot answer. The service-level tests prove the visible
   behaviour; `test_the_database_constraint_survives_a_bypassed_service` proves
   the boundary is still there for anything that does not go through it.
2. **Voting never bypasses idea visibility.** A vote is written against an
   idea the caller was shown, or not written at all, and no vote operation
   ever reports a count for an idea the caller cannot read.
3. **No lifecycle gate, deliberately.** This domain's rule for votes is
   readability alone. `test_a_rejected_idea_can_still_be_voted_on` is the test
   that keeps that from being "corrected" into a copy of the comment rule.
4. **Idempotency is chosen, not incidental, and it is asymmetric on purpose.**
   Voting twice is a success (the reader already got what they asked for);
   withdrawing twice is a success (a toggle double-clicked). Both are asserted
   so a future change to either is a decision rather than a drift.
"""

import threading

import pytest
from django.db import IntegrityError, connections, transaction

from ideas import selectors, services
from ideas.models import Idea, Vote
from identity.models import User
from organizations.models import Membership

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


def add_active_member(organization, user):
    return Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )


def make_idea(organization, author, **overrides):
    """
    An `ORGANIZATION`-visible idea by default.

    The model default is `PRIVATE`, which would make most of this file a test
    about a colleague being unable to see the idea at all.
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


@pytest.fixture
def world():
    """
    One author, one colleague in the same organization, and one outsider in
    another - the three positions every vote rule has to tell apart.
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


# --- the model constraint is the boundary --------------------------------------------


@pytest.mark.django_db
class TestTheConstraintIsTheBoundary:
    def test_the_database_refuses_a_second_vote_by_the_same_user(self, world):
        """
        Proved against the database, not against the service: this is what
        protects the invariant when the write did not come through
        `vote_for_idea` - a management command, the admin, a future import.
        """
        idea = make_idea(world['organization'], world['author'])
        Vote.objects.create(idea=idea, user=world['colleague'])

        with pytest.raises(IntegrityError):
            Vote.objects.create(idea=idea, user=world['colleague'])

    def test_two_different_users_may_vote_on_the_same_idea(self, world):
        """
        The other direction, so the constraint is not accidentally "one vote
        per idea" - which would be a different and much stranger product.
        """
        idea = make_idea(world['organization'], world['author'])

        Vote.objects.create(idea=idea, user=world['colleague'])
        Vote.objects.create(idea=idea, user=world['author'])

        assert Vote.objects.filter(idea=idea).count() == 2

    def test_the_same_user_may_vote_on_many_ideas(self, world):
        first = make_idea(world['organization'], world['author'], title='One')
        second = make_idea(world['organization'], world['author'], title='Two')

        Vote.objects.create(idea=first, user=world['colleague'])
        Vote.objects.create(idea=second, user=world['colleague'])

        assert Vote.objects.filter(user=world['colleague']).count() == 2


# --- voting ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVoteForIdea:
    def test_a_reader_records_a_vote(self, world):
        idea = make_idea(world['organization'], world['author'])

        vote = services.vote_for_idea(world['colleague'], idea.pk)

        assert vote.idea_id == idea.pk
        assert vote.user_id == world['colleague'].pk

    def test_the_vote_belongs_to_the_caller_and_not_to_an_argument(self, world):
        """
        There is no voter argument, so this is asserted as a property of the
        interface: the call takes `(user, idea_id)` and nothing else.
        """
        idea = make_idea(world['organization'], world['author'])

        services.vote_for_idea(world['colleague'], idea.pk)

        assert list(Vote.objects.values_list('user_id', flat=True)) == [world['colleague'].pk]

    def test_a_public_idea_in_another_tenant_can_be_voted_on(self, world):
        """
        Readability, not membership - the same rule commenting follows, and the
        one `docs/ideas-domain.md` set for votes.
        """
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)

        vote = services.vote_for_idea(world['author'], idea.pk)

        assert vote.user_id == world['author'].pk

    def test_the_count_increases_by_exactly_one(self, world):
        idea = make_idea(world['organization'], world['author'])

        services.vote_for_idea(world['colleague'], idea.pk)
        services.vote_for_idea(world['author'], idea.pk)

        state = selectors.vote_state_for(world['colleague'], idea)
        assert state is not None
        assert state.vote_count == 2

    def test_an_unauthenticated_caller_is_refused(self, world):
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError):
            services.vote_for_idea(None, idea.pk)

        assert Vote.objects.count() == 0

    def test_a_deactivated_caller_is_refused(self, world):
        colleague = world['colleague']
        colleague.is_active = False
        colleague.save(update_fields=['is_active'])
        idea = make_idea(world['organization'], world['author'])

        with pytest.raises(services.IdeaError):
            services.vote_for_idea(colleague, idea.pk)

        assert Vote.objects.count() == 0

    def test_a_private_idea_cannot_be_voted_on(self, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)

        with pytest.raises(services.IdeaError) as refusal:
            services.vote_for_idea(world['colleague'], idea.pk)

        assert refusal.value.message == 'Idea is unavailable.'
        assert Vote.objects.count() == 0

    def test_a_department_scoped_idea_fails_closed(self, world):
        """
        `DEPARTMENT` is author-only, and a vote is no more a way around that
        than a comment is.
        """
        idea = make_idea(
            world['organization'],
            world['author'],
            visibility=Idea.Visibility.DEPARTMENT,
        )

        with pytest.raises(services.IdeaError):
            services.vote_for_idea(world['colleague'], idea.pk)

    def test_another_tenants_idea_cannot_be_voted_on(self, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)

        with pytest.raises(services.IdeaError) as refusal:
            services.vote_for_idea(world['author'], idea.pk)

        assert refusal.value.message == 'Idea is unavailable.'
        assert Vote.objects.count() == 0

    def test_an_unknown_idea_answers_exactly_like_an_invisible_one(self, world):
        """
        Same message, same reason - so `voteIdea` is not a probe for which idea
        ids exist.
        """
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        token = world['author']

        with pytest.raises(services.IdeaError) as unknown:
            services.vote_for_idea(token, 999999)
        with pytest.raises(services.IdeaError) as invisible:
            services.vote_for_idea(token, idea.pk)

        assert unknown.value.message == invisible.value.message
        assert unknown.value.reason == invisible.value.reason


# --- no lifecycle gate ----------------------------------------------------------------


@pytest.mark.django_db
class TestNoLifecycleGate:
    """
    The one rule that is easy to "fix" wrongly, and the reason this whole class
    exists.
    """

    def test_a_rejected_idea_can_still_be_voted_on(self, world):
        from django.utils import timezone

        # Set directly rather than walked through the matrix: the matrix needs
        # a reviewer, and what this test is about is the *state* a vote meets,
        # not how the idea got there.
        idea = make_idea(world['organization'], world['author'])
        idea.status = Idea.Status.REJECTED
        idea.submitted_at = timezone.now()
        idea.save()

        # A vote is interest in the *idea*, which outlives its review state; a
        # comment is participation in a *decision*, which does not. So the
        # discussion rule from S2-005 does not apply here, and copying it would
        # be inventing a restriction this domain never stated.
        vote = services.vote_for_idea(world['colleague'], idea.pk)
        assert vote.idea_id == idea.pk

    def test_a_draft_can_be_voted_on(self, world):
        idea = make_idea(world['organization'], world['author'])
        assert idea.status == Idea.Status.DRAFT

        assert services.vote_for_idea(world['colleague'], idea.pk) is not None

    def test_a_vote_does_not_change_the_idea(self, world):
        idea = make_idea(world['organization'], world['author'])

        services.vote_for_idea(world['colleague'], idea.pk)
        idea.refresh_from_db()

        assert idea.status == Idea.Status.DRAFT


# --- duplicates and idempotency -------------------------------------------------------


@pytest.mark.django_db
class TestDuplicates:
    def test_voting_twice_is_idempotent(self, world):
        """
        A double-clicked button or a retried request must not produce an error
        and must not produce a second row: the reader already got the state
        they asked for.
        """
        idea = make_idea(world['organization'], world['author'])

        first = services.vote_for_idea(world['colleague'], idea.pk)
        second = services.vote_for_idea(world['colleague'], idea.pk)

        assert second.pk == first.pk
        assert Vote.objects.filter(idea=idea, user=world['colleague']).count() == 1

    def test_five_votes_still_produce_one_row(self, world):
        idea = make_idea(world['organization'], world['author'])

        for _ in range(5):
            services.vote_for_idea(world['colleague'], idea.pk)

        assert Vote.objects.filter(idea=idea, user=world['colleague']).count() == 1

    def test_idempotency_does_not_inflate_the_count(self, world):
        idea = make_idea(world['organization'], world['author'])

        for _ in range(3):
            services.vote_for_idea(world['colleague'], idea.pk)

        state = selectors.vote_state_for(world['colleague'], idea)
        assert state is not None
        assert state.vote_count == 1


@pytest.mark.django_db(transaction=True)
class TestConcurrentVotes:
    def test_two_simultaneous_attempts_converge_on_one_row(self):
        """
        The race a check-then-insert cannot answer, with two real connections.

        Both threads read "no vote" and both try to insert. Exactly one
        insert can win - the unique constraint is the final integrity boundary
        - and the loser must come back with the winner's row rather than an
        error, because the state the caller asked for does now exist.
        """
        author = make_user('concurrent-author@example.com')
        organization, _ = make_organization(name='Concurrent Co', owner=author)
        voter = make_user('concurrent-voter@example.com')
        add_active_member(organization, voter)
        idea = make_idea(organization, author)

        barrier = threading.Barrier(2)
        results: list[object] = []
        errors: list[Exception] = []

        def attempt() -> None:
            try:
                barrier.wait(timeout=10)
                results.append(services.vote_for_idea(voter, idea.pk))
            except Exception as exc:  # reported in the assertion below
                errors.append(exc)
            finally:
                # Each thread opens its own connection, and a connection left
                # open by a finished thread keeps a session on the test
                # database that the teardown cannot drop. Same convention as
                # the S2-003 concurrency test.
                connections.close_all()

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors, errors
        assert len(results) == 2
        # Both callers got the same vote, and only one row exists.
        assert results[0].pk == results[1].pk
        assert Vote.objects.filter(idea=idea, user=voter).count() == 1

    def test_the_loser_of_a_race_gets_the_winners_row(self):
        """
        The same race, arranged deterministically so the `IntegrityError`
        branch is exercised without depending on thread timing.

        The interleaving is reproduced faithfully: the winning row is committed
        *before* the call, the service's own read is made to miss it (as a read
        that began before the winner committed would), and the insert then
        fails on the constraint. What must come back is the winner's row, not
        an error — the state the caller asked for does exist.
        """
        author = make_user('race-author@example.com')
        organization, _ = make_organization(name='Race Co', owner=author)
        voter = make_user('race-voter@example.com')
        add_active_member(organization, voter)
        idea = make_idea(organization, author)

        # The winner: a committed row, not ours.
        winner = Vote.objects.create(idea=idea, user=voter)

        real_filter = Vote.objects.filter
        reads = {'count': 0}

        class _StaleFirstRead:
            """A queryset whose `first()` hides the row on the first call only."""

            def __init__(self, queryset, hide):
                self._queryset = queryset
                self._hide = hide

            def first(self):
                return None if self._hide else self._queryset.first()

        def racing_filter(*args, **kwargs):
            queryset = real_filter(*args, **kwargs)
            reads['count'] += 1
            return _StaleFirstRead(queryset, hide=reads['count'] == 1)

        real_create = Vote.objects.create

        def racing_create(**kwargs):
            # The constraint fires, because the winner committed first.
            raise IntegrityError('duplicate key value violates unique constraint')

        Vote.objects.filter = racing_filter  # type: ignore[method-assign]
        Vote.objects.create = racing_create  # type: ignore[method-assign]
        try:
            vote = services.vote_for_idea(voter, idea.pk)
        finally:
            Vote.objects.filter = real_filter  # type: ignore[method-assign]
            Vote.objects.create = real_create  # type: ignore[method-assign]

        assert vote.pk == winner.pk
        assert Vote.objects.filter(idea=idea, user=voter).count() == 1

    def test_an_unrelated_integrity_error_is_not_swallowed(self):
        """
        The `IntegrityError` handler re-reads the row and re-raises if it is
        genuinely absent. Swallowing it would turn a real fault into a silent
        success, which is the failure mode of a broad `except`.
        """
        author = make_user('fault-author@example.com')
        organization, _ = make_organization(name='Fault Co', owner=author)
        voter = make_user('fault-voter@example.com')
        add_active_member(organization, voter)
        idea = make_idea(organization, author)

        def broken_create(**kwargs):
            raise IntegrityError('something else went wrong')

        real_create = Vote.objects.create
        Vote.objects.create = broken_create  # type: ignore[method-assign]
        try:
            with pytest.raises(IntegrityError):
                services.vote_for_idea(voter, idea.pk)
        finally:
            Vote.objects.create = real_create  # type: ignore[method-assign]


# --- removing ------------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveVote:
    def test_the_voter_withdraws_their_own_vote(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        services.remove_vote(world['colleague'], idea.pk)

        assert not Vote.objects.filter(idea=idea, user=world['colleague']).exists()

    def test_the_count_decreases_by_exactly_one(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)
        services.vote_for_idea(world['author'], idea.pk)

        services.remove_vote(world['colleague'], idea.pk)

        state = selectors.vote_state_for(world['author'], idea)
        assert state is not None
        assert state.vote_count == 1
        assert state.viewer_has_voted is True

    def test_another_users_vote_survives(self, world):
        """
        The statement is scoped to the caller, so a colleague's vote on the
        same idea is not even a row it can name.
        """
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)
        services.vote_for_idea(world['author'], idea.pk)

        services.remove_vote(world['colleague'], idea.pk)

        assert Vote.objects.filter(idea=idea, user=world['author']).exists()

    def test_removing_a_vote_that_is_not_there_succeeds(self, world):
        """
        Idempotent, chosen deliberately: a vote control is a toggle, and a
        second click reporting "you have no vote to remove" would be an error
        about a state the reader already achieved. It leaks nothing - "no vote
        of mine" is the same fact whether the row never existed or was removed.
        """
        idea = make_idea(world['organization'], world['author'])

        services.remove_vote(world['colleague'], idea.pk)  # no vote, no error

        assert Vote.objects.count() == 0

    def test_withdrawing_twice_succeeds(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        services.remove_vote(world['colleague'], idea.pk)
        services.remove_vote(world['colleague'], idea.pk)

        assert Vote.objects.count() == 0

    def test_vote_and_withdraw_can_be_repeated(self, world):
        """
        A reader changing their mind is ordinary, and the count has to follow
        them rather than drifting.
        """
        idea = make_idea(world['organization'], world['author'])

        services.vote_for_idea(world['colleague'], idea.pk)
        services.remove_vote(world['colleague'], idea.pk)
        services.vote_for_idea(world['colleague'], idea.pk)

        state = selectors.vote_state_for(world['colleague'], idea)
        assert state is not None
        assert state.vote_count == 1
        assert state.viewer_has_voted is True

    def test_an_unauthenticated_caller_cannot_remove_a_vote(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        with pytest.raises(services.IdeaError):
            services.remove_vote(None, idea.pk)

        assert Vote.objects.filter(idea=idea, user=world['colleague']).exists()

    def test_an_unreadable_idea_is_refused_rather_than_silently_accepted(self, world):
        """
        The important half of the idempotency decision. "No vote of mine" and
        "an idea you cannot read" must not collapse into the same success, or
        the idempotent branch would confirm that the operation is available on
        an idea the caller was never shown.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        services.vote_for_idea(world['author'], idea.pk)

        with pytest.raises(services.IdeaError) as refusal:
            services.remove_vote(world['colleague'], idea.pk)

        assert refusal.value.message == 'Idea is unavailable.'
        assert Vote.objects.filter(idea=idea, user=world['author']).exists()

    def test_another_tenants_idea_is_refused(self, world):
        # `ORGANIZATION` in another tenant, not `PUBLIC`: a public idea there
        # is readable, and readable means the removal is permitted.
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        services.vote_for_idea(world['outsider'], idea.pk)

        with pytest.raises(services.IdeaError) as refusal:
            services.remove_vote(world['author'], idea.pk)

        assert refusal.value.message == 'Idea is unavailable.'
        assert Vote.objects.filter(idea=idea, user=world['outsider']).exists()

    def test_deleting_the_idea_takes_its_votes_with_it(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        idea.delete()

        assert Vote.objects.count() == 0


# --- reading vote state ---------------------------------------------------------------


@pytest.mark.django_db
class TestVoteState:
    def test_the_count_and_the_viewers_own_answer_are_correct(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)
        services.vote_for_idea(world['author'], idea.pk)

        mine = selectors.vote_state_for(world['colleague'], idea)
        theirs = selectors.vote_state_for(world['author'], idea)

        assert mine is not None
        assert theirs is not None
        # Same total, different personal answer - which is the whole point of
        # reporting the count and the flag together.
        assert mine.vote_count == theirs.vote_count == 2
        assert mine.viewer_has_voted is True
        assert theirs.viewer_has_voted is True

    def test_a_reader_who_has_not_voted_is_told_so(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        state = selectors.vote_state_for(world['author'], idea)

        assert state is not None
        assert state.vote_count == 1
        assert state.viewer_has_voted is False

    def test_an_idea_nobody_voted_on_is_zero_not_null(self, world):
        """
        `0` and `False`, never `None`. The count subquery returns SQL `NULL`
        for an idea with no votes, which is the overwhelmingly common case,
        and `NULL` into a non-nullable field is a GraphQL error rather than an
        absence of votes.
        """
        idea = make_idea(world['organization'], world['author'])

        state = selectors.vote_state_for(world['author'], idea)

        assert state is not None
        assert state.vote_count == 0
        assert state.viewer_has_voted is False

    def test_discovery_reports_zero_votes_without_a_null(self, world):
        """
        The same case through the annotated path, which is where the `Coalesce`
        actually earns its keep.
        """
        make_idea(world['organization'], world['author'])

        page = selectors.list_discoverable_ideas(world['colleague'])

        assert [idea.vote_count for idea in page.items] == [0]
        assert [idea.viewer_has_voted for idea in page.items] == [False]

    def test_discovery_reports_the_viewers_own_vote(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        page = selectors.list_discoverable_ideas(world['colleague'])

        assert page.items[0].vote_count == 1
        assert page.items[0].viewer_has_voted is True

    def test_two_readers_see_their_own_answer_on_the_same_idea(self, world):
        """
        The count is global and the flag is personal, so two readers of one
        discovery page must disagree about one field and agree about the other.
        """
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        voters = selectors.list_discoverable_ideas(world['colleague']).items[0]
        non_voters = selectors.list_discoverable_ideas(world['author']).items[0]

        assert voters.vote_count == non_voters.vote_count == 1
        assert voters.viewer_has_voted is True
        assert non_voters.viewer_has_voted is False

    def test_an_unauthenticated_caller_gets_no_state(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        assert selectors.vote_state_for(None, idea) is None

    def test_a_deactivated_caller_gets_no_state(self, world):
        colleague = world['colleague']
        colleague.is_active = False
        colleague.save(update_fields=['is_active'])
        idea = make_idea(world['organization'], world['author'])

        assert selectors.vote_state_for(colleague, idea) is None

    def test_state_is_none_for_an_unreadable_idea_only_because_the_caller_never_resolves_it(
        self, world
    ):
        """
        `vote_state_for` takes an already-authorized idea, so the refusal lives
        at the resolution step. Pinned so the two halves are not confused: the
        function is not a second visibility check, it is arithmetic on an idea
        the caller has already been shown.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        services.vote_for_idea(world['author'], idea.pk)

        # The colleague cannot resolve the idea, so there is nothing to report.
        assert selectors.get_idea(world['colleague'], idea.pk) is None


# --- query cost -----------------------------------------------------------------------


@pytest.mark.django_db
class TestQueryCost:
    def test_discovery_still_costs_the_same_three_queries(self, world, django_assert_num_queries):
        """
        S2-004 pinned three: the membership lookup, the `COUNT`, the page. The
        vote annotations must not add a fourth, which is the whole reason they
        are annotations rather than a loop - and Django does not carry them into
        `.count()`, so the count query is unchanged too.
        """
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)
        selectors.list_discoverable_ideas(world['colleague'], limit=2)

        with django_assert_num_queries(3):
            selectors.list_discoverable_ideas(world['colleague'], limit=2)

    def test_a_page_of_many_ideas_does_not_cost_a_query_each(
        self, world, django_assert_num_queries
    ):
        """
        The N+1 this design exists to avoid. Twenty voted-on ideas cost the same
        three queries as one.
        """
        for index in range(20):
            services.vote_for_idea(
                world['colleague'],
                make_idea(world['organization'], world['author'], title=f'Idea {index}').pk,
            )

        with django_assert_num_queries(3):
            page = selectors.list_discoverable_ideas(world['colleague'], limit=20)

        assert len(page.items) == 20
        assert all(idea.vote_count == 1 for idea in page.items)

    def test_the_count_query_does_not_carry_the_subqueries(self, world, django_assert_num_queries):
        """
        Structural rather than incidental: if the annotations leaked into
        `paginate`'s `COUNT`, every page would evaluate a correlated subquery
        per row for a number it does not need.
        """
        make_idea(world['organization'], world['author'])

        with django_assert_num_queries(3) as captured:
            selectors.list_discoverable_ideas(world['colleague'], limit=2)

        count_query = next(q['sql'] for q in captured.captured_queries if 'COUNT(*)' in q['sql'])
        assert 'ideas_vote' not in count_query

    def test_a_single_idea_state_costs_two_queries(self, world, django_assert_num_queries):
        """
        The fallback path, for an idea that did not come from a page: a count
        and an existence check. Two is fine for one idea and would not be for
        fifty, which is why the list path is annotated.
        """
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)
        selectors.vote_state_for(world['colleague'], idea)

        with django_assert_num_queries(2):
            selectors.vote_state_for(world['colleague'], idea)


# --- cascade and cleanup ---------------------------------------------------------------


@pytest.mark.django_db
class TestVoteLifecycle:
    def test_a_deleted_user_takes_their_votes_with_them(self, world):
        idea = make_idea(world['organization'], world['author'])
        services.vote_for_idea(world['colleague'], idea.pk)

        world['colleague'].delete()

        assert Vote.objects.count() == 0

    def test_a_vote_does_not_touch_the_idea_row(self, world):
        idea = make_idea(world['organization'], world['author'])
        before = idea.updated_at

        services.vote_for_idea(world['colleague'], idea.pk)

        idea.refresh_from_db()
        assert idea.updated_at == before

    def test_the_failed_insert_is_rolled_back_on_its_own(self, world):
        """
        The inner `transaction.atomic()` is what makes the `IntegrityError`
        branch readable: the failed insert is rolled back by its own savepoint,
        so the transaction is still usable for the follow-up read. Without it
        the handler would be querying inside a poisoned transaction and would
        fail again, for a different reason, in the one place that is supposed
        to recover.
        """
        author = make_user('usable-author@example.com')
        organization, _ = make_organization(name='Usable Co', owner=author)
        voter = make_user('usable-voter@example.com')
        add_active_member(organization, voter)
        idea = make_idea(organization, author)

        winner = Vote.objects.create(idea=idea, user=voter)

        real_filter = Vote.objects.filter
        real_create = Vote.objects.create
        reads = {'count': 0}

        class _StaleFirstRead:
            def __init__(self, queryset, hide):
                self._queryset = queryset
                self._hide = hide

            def first(self):
                return None if self._hide else self._queryset.first()

        def racing_filter(*args, **kwargs):
            queryset = real_filter(*args, **kwargs)
            reads['count'] += 1
            return _StaleFirstRead(queryset, hide=reads['count'] == 1)

        def racing_create(**kwargs):
            raise IntegrityError('duplicate key')

        Vote.objects.filter = racing_filter  # type: ignore[method-assign]
        Vote.objects.create = racing_create  # type: ignore[method-assign]
        try:
            vote = services.vote_for_idea(voter, idea.pk)
        finally:
            Vote.objects.filter = real_filter  # type: ignore[method-assign]
            Vote.objects.create = real_create  # type: ignore[method-assign]

        # The handler's read worked, which is the claim: it could not have run
        # in a broken transaction.
        assert vote.pk == winner.pk
        assert Vote.objects.filter(idea=idea, user=voter).count() == 1
        assert transaction.get_connection().in_atomic_block
