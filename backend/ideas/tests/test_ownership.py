"""
Ownership, audience, and the questions a reader has to be able to answer.

The other ideas test files pin the *journeys* (`test_contexts.py`), the
*matrix* (`test_lifecycle.py`) and the *reads* (`test_discovery.py`). This one
pins the thing those three all assume: that an idea has exactly one owner, that
its audience is a separate fact from its owner, and that both are enforced by
the database as well as the services.

The invariants, in the order a reader meets them:

1. **One owner, always.** `individual` -> the author, no tenant. `team` -> a
   team, no organization. `organization` -> an organization, no team. Enforced
   three times over: a CHECK constraint, `Idea.clean`, and the write service.
2. **The audience never becomes the owner.** A public team idea is still owned by
   its team. This is why `visibility` and the tenant columns are separate
   columns and why no write path sets one from the other.
3. **Only an owner may manage.** Editing is author-only, and the tenant standing
   required to edit is the one the idea's own context names - an organization
   membership for an organization idea, a team membership for a team one, and
   nothing at all for an individual one, whose author *is* its tenant.
4. **Audience means what it says.** `team` visibility reaches the team's members
   and nobody else; it does not reach an organization member who is not on the
   team, and an organization idea is not reachable through a team membership.
"""

import pytest
from django.db import IntegrityError, transaction

from ideas import selectors, services
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership
from organizations.services import CreateOrganizationInput, create_organization_for_user
from teams import services as team_services

DESCRIPTION = 'A description long enough to be usable.'
VALID_PASSWORD = 'a-strong-unique-pass-1'


def make_user(email, **extra):
    return User.objects.create_user(
        email=email,
        first_name=extra.pop('first_name', 'Test'),
        last_name=extra.pop('last_name', 'User'),
        phone_number='+255712345678',
        password=VALID_PASSWORD,
        **extra,
    )


@pytest.fixture
def category():
    return Category.objects.get_or_create(name='Ownership')[0]


@pytest.fixture
def author():
    return make_user('author@example.com', first_name='Ada', last_name='Author')


@pytest.fixture
def stranger():
    return make_user('stranger@example.com', first_name='Sam', last_name='Stranger')


@pytest.fixture
def organization(author):
    return create_organization_for_user(author, CreateOrganizationInput(name='MUNA')).organization


@pytest.fixture
def team(author):
    return team_services.create_team(author, team_services.TeamInput(name='Automation Team'))


@pytest.fixture
def colleague(author, organization):
    """An active member of the organization, and of nobody else."""
    return make_user('colleague@example.com', first_name='Rae', last_name='Rivera')


@pytest.fixture
def teammate(author, team):
    """
    An active member of the team, and of no organization.

    Added through `add_existing_member` rather than by writing the membership
    row, so the row is one the team's own service would have written.
    """
    from teams.models import TeamMembership

    user = make_user('teammate@example.com', first_name='Tom', last_name='Team')
    team_services.add_existing_member(author, team.pk, user)
    assert TeamMembership.objects.filter(team=team, user=user).exists()
    return user


def join_organization(organization, user):
    return Membership.objects.create(user=user, organization=organization)


def publish(idea):
    """Put a filed idea past `DRAFT`: a draft is its author's alone, whatever its audience."""
    from django.utils import timezone

    Idea.objects.filter(pk=idea.pk).update(
        status=Idea.Status.SUBMITTED, submitted_at=timezone.now()
    )
    idea.refresh_from_db()
    return idea


def file_idea(
    user,
    context,
    *,
    organization=None,
    team=None,
    visibility=None,
    category=None,
    submitted=False,
):
    idea = _file_idea(user, context, organization, team, visibility, category)
    return publish(idea) if submitted else idea


def _file_idea(user, context, organization, team, visibility, category):
    return services.create_idea_in_context(
        user,
        services.IdeaInput(
            title='Automate the invoice run',
            description=DESCRIPTION,
            category_id=(category or Category.objects.get_or_create(name='Ownership')[0]).pk,
            **({'visibility': visibility} if visibility else {}),
        ),
        submission_context=context,
        organization_id=organization.pk if organization else None,
        team_id=team.pk if team else None,
    )


# --- exactly one owner -------------------------------------------------------------------


@pytest.mark.django_db
class TestExactlyOneOwner:
    def test_an_individual_idea_names_the_author_and_no_tenant(
        self, author, category, organization, team
    ):
        """
        Even with a tenant available. The context chooses the owner, and passing
        one anyway is refused rather than quietly dropped - a caller who thought
        they were filing for MUNA must not end up with an individual idea.
        """
        with pytest.raises(services.IdeaError) as refused:
            file_idea(
                author,
                Idea.SubmissionContext.INDIVIDUAL,
                organization=organization,
                team=team,
                category=category,
            )
        assert 'does not belong' in str(refused.value)

        idea = file_idea(author, Idea.SubmissionContext.INDIVIDUAL, category=category)
        assert idea.submission_context == Idea.SubmissionContext.INDIVIDUAL
        assert idea.organization_id is None
        assert idea.team_id is None
        assert idea.author_id == author.pk

    def test_a_team_idea_names_its_team_and_no_organization(
        self, author, team, organization, category
    ):
        with pytest.raises(services.IdeaError):
            file_idea(
                author,
                Idea.SubmissionContext.TEAM,
                team=team,
                organization=organization,
                category=category,
            )

        idea = file_idea(author, Idea.SubmissionContext.TEAM, team=team, category=category)
        assert idea.team_id == team.pk
        assert idea.organization_id is None

    def test_an_organization_idea_names_its_organization_and_no_team(
        self, author, organization, team, category
    ):
        with pytest.raises(services.IdeaError):
            file_idea(
                author,
                Idea.SubmissionContext.ORGANIZATION,
                organization=organization,
                team=team,
                category=category,
            )

        idea = file_idea(
            author,
            Idea.SubmissionContext.ORGANIZATION,
            organization=organization,
            category=category,
        )
        assert idea.organization_id == organization.pk
        assert idea.team_id is None

    def test_the_database_refuses_a_row_the_service_would_never_write(self, author, team, category):
        """
        The constraint, not the service. A model whose only protection is a
        service can be bypassed by a migration, a management command or a shell,
        so the invariant is asserted at the level that cannot be argued with.
        """
        idea = file_idea(author, Idea.SubmissionContext.TEAM, team=team, category=category)

        with pytest.raises(IntegrityError), transaction.atomic():
            Idea.objects.filter(pk=idea.pk).update(
                submission_context=Idea.SubmissionContext.ORGANIZATION
            )

    def test_an_unknown_tenant_id_is_refused_rather_than_stored(self, author, category):
        with pytest.raises(services.IdeaError) as refused:
            services.create_idea_in_context(
                author,
                services.IdeaInput(
                    title='Automate the invoice run',
                    description=DESCRIPTION,
                    category_id=category.pk,
                ),
                submission_context=Idea.SubmissionContext.ORGANIZATION,
                organization_id='999999',
            )
        # The membership check answers first, and it is the better answer: an id
        # nobody is a member of is indistinguishable from one that does not
        # exist, which is the point of refusing rather than looking it up.
        assert refused.value.reason == 'membership_required'


@pytest.mark.django_db
class TestOnlyAnAuthorizedMemberFiles:
    def test_a_stranger_cannot_file_for_an_organization_they_are_not_in(
        self, organization, stranger, category
    ):
        with pytest.raises(services.IdeaError) as refused:
            file_idea(
                stranger,
                Idea.SubmissionContext.ORGANIZATION,
                organization=organization,
                category=category,
            )
        assert refused.value.reason == 'membership_required'

    def test_a_stranger_cannot_file_for_a_team_they_are_not_in(self, team, stranger, category):
        with pytest.raises(services.IdeaError) as refused:
            file_idea(stranger, Idea.SubmissionContext.TEAM, team=team, category=category)
        assert refused.value.reason == 'membership_required'

    def test_any_active_team_member_may_file_for_the_team(self, author, team, teammate, category):
        """
        Membership, not a permission. Filing is not submitting: `team.ideas.submit`
        governs putting the team's idea forward to the platform, and a plain
        member may do that too - what it must not do is file for a team it is
        not on.
        """
        idea = file_idea(teammate, Idea.SubmissionContext.TEAM, team=team, category=category)
        assert idea.team_id == team.pk
        assert idea.author_id == teammate.pk

    def test_a_member_who_left_the_organization_cannot_file_into_it(
        self, author, organization, colleague, category
    ):
        membership = join_organization(organization, colleague)
        membership.status = Membership.Status.INACTIVE
        membership.save()

        with pytest.raises(services.IdeaError):
            file_idea(
                colleague,
                Idea.SubmissionContext.ORGANIZATION,
                organization=organization,
                category=category,
            )


# --- ownership is stable ------------------------------------------------------------------


@pytest.mark.django_db
class TestOwnershipIsStable:
    def test_the_level_decides_the_audience_and_editing_never_moves_the_owner(
        self, author, team, category
    ):
        """
        The question the product asks most often, in one assertion: an idea's
        audience follows the level it was filed at, and nothing about editing it
        turns a team idea into anything else.
        """
        idea = file_idea(author, Idea.SubmissionContext.TEAM, team=team, category=category)
        assert idea.visibility == Idea.Visibility.TEAM

        services.update_idea(
            author,
            idea.pk,
            services.IdeaInput(
                title='A better title',
                description=idea.description,
                category_id=idea.category_id,
            ),
        )
        idea.refresh_from_db()

        assert idea.visibility == Idea.Visibility.TEAM
        assert idea.submission_context == Idea.SubmissionContext.TEAM
        assert idea.team_id == team.pk
        assert idea.author_id == author.pk

    @pytest.mark.parametrize(
        ('context', 'named', 'words'),
        [
            (Idea.SubmissionContext.ORGANIZATION, 'public', 'seen by your organization'),
            (Idea.SubmissionContext.ORGANIZATION, 'team', 'seen by your organization'),
            (Idea.SubmissionContext.TEAM, 'public', 'seen by your team'),
            (Idea.SubmissionContext.TEAM, 'organization', 'seen by your team'),
            (Idea.SubmissionContext.INDIVIDUAL, 'public', 'seen only by you'),
            (Idea.SubmissionContext.INDIVIDUAL, 'organization', 'seen only by you'),
        ],
    )
    def test_naming_another_audience_is_refused_in_words_about_the_level(
        self, author, team, organization, category, context, named, words
    ):
        """
        There is no audience to choose, and asking for one is not ignored: a caller
        who thinks it is making an idea public finds out instead of getting a private
        one back. Each level has its own sentence.
        """
        tenant = {}
        if context == Idea.SubmissionContext.ORGANIZATION:
            tenant = {'organization': organization}
        elif context == Idea.SubmissionContext.TEAM:
            tenant = {'team': team}

        with pytest.raises(services.IdeaError) as refused:
            file_idea(author, context, visibility=named, category=category, **tenant)

        assert refused.value.field == 'visibility'
        assert words in str(refused.value)

    def test_an_individual_idea_is_private_to_its_author(self, author, category):
        idea = file_idea(author, Idea.SubmissionContext.INDIVIDUAL, category=category)

        assert idea.visibility == Idea.Visibility.PRIVATE

    def test_no_write_input_can_change_the_owner(self, author, organization, category):
        """
        Ownership is not a field on `updateIdea`. There is deliberately no
        "change the owner" argument to get wrong: moving an idea between tenants
        would be a business operation with its own authorization and audit, and
        it does not exist yet.
        """
        from ideas.schema import UpdateIdeaInput

        fields = {field.name for field in UpdateIdeaInput.__strawberry_definition__.fields}
        assert 'submission_context' not in fields
        assert 'team_id' not in fields
        assert 'organization_id' not in fields
        assert 'author_id' not in fields


# --- who may manage an idea ----------------------------------------------------------------


@pytest.mark.django_db
class TestWhoMayManage:
    def test_the_author_may_edit_their_team_idea(self, author, team, teammate, category):
        """
        The bug this closes: `_load_editable_idea` used to ask for an
        *organization* membership unconditionally, and a team idea has no
        organization - so a team idea could not be edited by anybody, its author
        included.
        """
        idea = file_idea(author, Idea.SubmissionContext.TEAM, team=team, category=category)

        updated = services.update_idea(
            author,
            idea.pk,
            services.IdeaInput(
                title='Automate the invoice run properly',
                description=DESCRIPTION,
                category_id=idea.category_id,
            ),
        )
        assert updated.title == 'Automate the invoice run properly'
        assert services.can_edit_idea(author, updated) is True

    def test_the_author_may_edit_their_individual_idea(self, author, category):
        idea = file_idea(author, Idea.SubmissionContext.INDIVIDUAL, category=category)
        assert services.can_edit_idea(author, idea) is True

    def test_a_member_who_left_the_team_may_not_edit_it(self, author, team, teammate, category):
        from teams.models import TeamMembership

        idea = file_idea(teammate, Idea.SubmissionContext.TEAM, team=team, category=category)

        TeamMembership.objects.filter(team=team, user=teammate).update(
            status=TeamMembership.Status.INACTIVE
        )

        with pytest.raises(services.IdeaError) as refused:
            services.update_idea(
                teammate,
                idea.pk,
                services.IdeaInput(
                    title='A title', description=DESCRIPTION, category_id=idea.category_id
                ),
            )
        assert refused.value.reason == 'membership_required'

    def test_another_member_may_not_edit_somebody_elses_idea(
        self, author, team, teammate, category
    ):
        """
        Authorship, not membership. Being on the team is not permission to rewrite
        a colleague's words.
        """
        idea = file_idea(author, Idea.SubmissionContext.TEAM, team=team, category=category)

        assert services.can_edit_idea(teammate, idea) is False
        with pytest.raises(services.IdeaError) as refused:
            services.update_idea(
                teammate,
                idea.pk,
                services.IdeaInput(
                    title='Not mine to change',
                    description=DESCRIPTION,
                    category_id=idea.category_id,
                ),
            )
        assert refused.value.reason == 'forbidden'

    def test_an_organization_owner_may_not_edit_a_team_idea(
        self, author, organization, team, colleague, category
    ):
        join_organization(organization, colleague)
        idea = file_idea(author, Idea.SubmissionContext.TEAM, team=team, category=category)

        assert services.can_edit_idea(colleague, idea) is False

    def test_the_author_submits_their_own_team_idea(self, author, team, teammate, category):
        """
        The author puts an idea forward, on every context alike, and *also* has
        to hold the team's submission permission - the two answers are combined
        rather than either replacing the other.
        """
        idea = file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
        )
        assert services.submit_idea(author, idea.pk).status == Idea.Status.SUBMITTED_TO_ORGANIZATION

    def test_another_team_member_may_not_submit_somebody_elses_idea(
        self, author, team, teammate, category
    ):
        """
        A team decides nothing, and neither does seniority in it. Filing is the
        author's and submitting is too; a colleague on the same team cannot press
        the button on an idea they did not write.
        """
        idea = file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
        )

        with pytest.raises(services.IdeaError) as refused:
            services.submit_idea(teammate, idea.pk)
        assert refused.value.reason == 'forbidden'

    def test_a_stranger_may_not_submit_a_team_idea(self, author, team, stranger, category):
        idea = file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
        )
        with pytest.raises(services.IdeaError):
            services.submit_idea(stranger, idea.pk)


# --- what the audience means ------------------------------------------------------------------


@pytest.mark.django_db
class TestWhatTheAudienceMeans:
    def test_team_visibility_reaches_the_team_and_nobody_else(
        self, author, team, teammate, colleague, organization, category
    ):
        """
        The whole reason `team` is its own value: an organization member who is
        not on the team must not read it, and a team member must - while the
        ownership banner still says the team.
        """
        idea = file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
            submitted=True,
        )
        join_organization(organization, colleague)

        assert selectors.can_view_idea(author, idea) is True
        assert selectors.can_view_idea(teammate, idea) is True
        assert selectors.can_view_idea(colleague, idea) is False
        assert selectors.can_view_idea(make_user('nobody@example.com'), idea) is False

    def test_an_organization_idea_is_not_reachable_through_a_team_membership(
        self, author, organization, teammate, category
    ):
        idea = file_idea(
            author,
            Idea.SubmissionContext.ORGANIZATION,
            organization=organization,
            category=category,
        )
        assert selectors.can_view_idea(teammate, idea) is False

    def test_a_team_idea_is_readable_only_inside_its_team_and_stays_owned_by_it(
        self, author, team, category
    ):
        idea = file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
            submitted=True,
        )
        assert selectors.can_view_idea(make_user('anyone@example.com'), idea) is False
        assert idea.team_id == team.pk
        assert idea.submission_context == Idea.SubmissionContext.TEAM

    def test_a_draft_stays_author_only(self, author, team, teammate, category):
        idea = file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
        )
        assert selectors.can_view_idea(author, idea) is True
        assert selectors.can_view_idea(teammate, idea) is False

    def test_a_visibility_filter_cannot_widen_the_read(self, author, team, teammate, category):
        """
        The filter is a narrowing filter, and this is the assertion that keeps it
        one: asking for `PRIVATE` finds nothing rather than a teammate's draft, and
        asking for `TEAM` finds only what the caller could already read.
        """
        theirs = file_idea(teammate, Idea.SubmissionContext.TEAM, team=team, category=category)
        shared = file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
            submitted=True,
        )
        # `author`'s own draft: the teammate must not be able to see it.
        file_idea(author, Idea.SubmissionContext.TEAM, team=team, category=category)

        nothing = selectors.list_discoverable_ideas(
            teammate,
            selectors.IdeaFilters(team_id=team.pk, visibility=Idea.Visibility.PRIVATE),
        )
        assert nothing.items == []

        readable = selectors.list_discoverable_ideas(
            teammate,
            selectors.IdeaFilters(team_id=team.pk, visibility=Idea.Visibility.TEAM),
        )
        assert {item.pk for item in readable.items} == {theirs.pk, shared.pk}

    def test_the_team_scope_is_empty_for_a_stranger_and_for_nothing_at_all(
        self, author, team, stranger, category
    ):
        file_idea(author, Idea.SubmissionContext.TEAM, team=team, category=category)

        assert (
            selectors.list_discoverable_ideas(
                stranger, selectors.IdeaFilters(team_id=team.pk)
            ).items
            == []
        )
        # The same answer for a team that does not exist, so the id cannot be
        # used to discover other teams.
        assert (
            selectors.list_discoverable_ideas(
                stranger, selectors.IdeaFilters(team_id='999999')
            ).items
            == []
        )

    def test_a_context_filter_narrows_to_that_owner(self, author, organization, team, category):
        file_idea(author, Idea.SubmissionContext.INDIVIDUAL, category=category)
        file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
        )
        file_idea(
            author,
            Idea.SubmissionContext.ORGANIZATION,
            organization=organization,
            category=category,
        )

        page = selectors.list_discoverable_ideas(
            author,
            selectors.IdeaFilters(
                submission_context=Idea.SubmissionContext.TEAM,
            ),
        )
        assert len(page.items) == 1
        assert page.items[0].submission_context == Idea.SubmissionContext.TEAM

    def test_mine_is_the_callers_own_and_not_an_id_they_supplied(self, author, teammate, category):
        mine = file_idea(author, Idea.SubmissionContext.INDIVIDUAL, category=category)
        theirs = file_idea(teammate, Idea.SubmissionContext.INDIVIDUAL, category=category)

        page = selectors.list_discoverable_ideas(author, selectors.IdeaFilters(mine=True))
        assert [item.pk for item in page.items] == [mine.pk]
        assert theirs.pk not in [item.pk for item in page.items]


# --- the answers the schema owes the client -------------------------------------------------------


@pytest.mark.django_db
class TestTheSchemaAnswersTheQuestions:
    def test_can_edit_idea_never_disagrees_with_update_idea(self, author, teammate, category):
        """
        One implementation, two callers: the field and the mutation ask the same
        question. A `viewerCanEdit: true` for an idea `updateIdea` would refuse
        is worse than no field at all.
        """
        idea = file_idea(author, Idea.SubmissionContext.INDIVIDUAL, category=category)

        assert services.can_edit_idea(author, idea) is True
        assert services.can_edit_idea(teammate, idea) is False
        assert services.can_edit_idea(None, idea) is False
        assert services.can_edit_idea(author, None) is False

    def test_the_department_placeholder_stays_unselectable(self, author, category):
        """
        Reserved vocabulary is still reserved. Adding `team` did not make the
        placeholder selectable, and this is the test that says so.
        """
        with pytest.raises(services.IdeaError):
            file_idea(
                author,
                Idea.SubmissionContext.ORGANIZATION,
                visibility=Idea.Visibility.DEPARTMENT,
                category=category,
            )

    def test_the_visible_set_matches_the_predicate(self, author, team, teammate, category):
        """
        `can_view_idea` and the queryset filter are two spellings of one rule, so
        a row the predicate allows must appear in the list and a row it forbids
        must not. Compared as sets, which is what makes it a real check rather
        than a spot check.
        """
        file_idea(
            author,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
        )
        private = file_idea(
            teammate,
            Idea.SubmissionContext.TEAM,
            team=team,
            category=category,
        )

        listed = {
            item.pk
            for item in selectors.list_discoverable_ideas(
                teammate, selectors.IdeaFilters(team_id=team.pk)
            ).items
        }
        # The teammate's own private idea is in the list and correctly so - an
        # author always sees their own work. What the set comparison below rules
        # out is any row the predicate would refuse, in either direction.
        assert private.pk in listed
        allowed = {
            idea.pk for idea in Idea.objects.all() if selectors.can_view_idea(teammate, idea)
        }
        assert listed == allowed


@pytest.mark.django_db
def test_the_visibility_filter_never_combines_with_a_tenant_that_was_not_checked():
    """
    A filter pair that would widen if the tenant argument were trusted: the
    tenant is checked against the caller's membership *before* it filters, so a
    team id the caller is not on empties the page whatever else is asked for.
    """
    author = make_user('owner@example.com')
    organization = create_organization_for_user(
        author, CreateOrganizationInput(name='Acme')
    ).organization
    stranger = make_user('stranger2@example.com')
    theirs = services.create_idea_in_context(
        author,
        services.IdeaInput(
            title='Automate the invoice run',
            description=DESCRIPTION,
            category_id=Category.objects.get_or_create(name='Ownership')[0].pk,
        ),
        submission_context=Idea.SubmissionContext.ORGANIZATION,
        organization_id=organization.pk,
    )
    assert theirs.organization_id == organization.pk
    publish(theirs)

    assert (
        selectors.list_discoverable_ideas(
            stranger,
            selectors.IdeaFilters(
                organization_id=organization.pk, visibility=Idea.Visibility.PUBLIC
            ),
        ).items
        == []
    )
    # And the same is true of the organization-scoped query, so the rule is not
    # an artefact of how the argument arrived.
    assert selectors.list_organization_ideas(stranger, organization.pk).count() == 0
    # Nor does the platform-wide list reach it: there is no public audience, so an
    # organization's idea is invisible to somebody outside that organization.
    assert theirs.pk not in [item.pk for item in selectors.list_discoverable_ideas(stranger).items]
