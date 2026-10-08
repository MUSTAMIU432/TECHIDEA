"""
Teams: collaboration boundaries that are not tenants.

The tests are organised around the claims `teams.models` and
`teams.authorization` make, because those are the ones a team that was built
as "just another organization" would break:

1. **A team is not a tenant.** No review permission, no approval, ever - and
   the absence is *structural*: `ALL_TEAM_PERMISSIONS` has no such code, so
   there is nothing to grant and nothing to check.
2. **Membership is a fact about a real account.** There is no
   `add_member(email)`. Either somebody accepts an invitation (tested in
   `invitations/tests/`) or somebody already on the platform is added
   deliberately, and both routes go through `team.members.manage`.
3. **A team outlives its people and its owner.** `owner` is `PROTECT`, and
   leaving deactivates a membership rather than deleting anybody's work.
4. **The last member cannot leave**, because a team nobody can administer
   cannot be repaired from the UI.

`ideas.tests.test_contexts` covers the idea side of a team - filing for one,
submitting for one, and a team's idea going straight to the platform. What is
here is the team itself.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from ideas import services as idea_services
from ideas.models import Idea
from identity.models import User
from teams import authorization, selectors, services
from teams.models import Team, TeamMembership, TeamMembershipRole, TeamRole, TeamRolePermission

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password=VALID_PASSWORD,
    )


@pytest.fixture
def owner():
    return make_user('owner@example.com')


@pytest.fixture
def member():
    return make_user('member@example.com')


@pytest.fixture
def team(owner):
    return services.create_team(owner, services.TeamInput(name='Automation Squad'))


# --- creating a team ------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateTeam:
    def test_the_creator_owns_it_and_is_its_first_member(self, team, owner):
        """All three writes - team, roles, membership - or none."""
        assert team.owner == owner
        assert team.slug == 'automation-squad'

        membership = authorization.get_membership(owner, team)
        assert membership is not None
        assert selectors.roles_for(membership) == [services.OWNER_ROLE_SLUG]

    def test_the_system_roles_are_seeded_with_their_permissions(self, team):
        owner_role = TeamRole.objects.get(team=team, slug=services.OWNER_ROLE_SLUG)
        member_role = TeamRole.objects.get(team=team, slug=services.MEMBER_ROLE_SLUG)

        held = lambda role: set(  # noqa: E731
            role.role_permissions.values_list('permission__code', flat=True)
        )
        assert held(owner_role) == set(authorization.ALL_TEAM_PERMISSIONS)
        assert held(member_role) == {
            authorization.TEAM_VIEW,
            authorization.TEAM_MEMBERS_VIEW,
            authorization.TEAM_IDEAS_SUBMIT,
        }

    def test_seeding_twice_changes_nothing(self, team):
        before = TeamRolePermission.objects.count()
        services.seed_team_roles(team)
        assert TeamRolePermission.objects.count() == before

    def test_a_team_needs_no_organization(self, team):
        """
        The reason the app exists: a user who belongs to no tenant can still file
        a team idea, so "an idea belongs to an organization" cannot be the rule.
        """
        assert team.owner.memberships.count() == 0

    def test_two_people_may_each_have_a_team_with_the_same_name(self, owner, member):
        """The ordinary case, not an abuse: slug is global, name is per owner."""
        first = services.create_team(owner, services.TeamInput(name='University Team'))
        second = services.create_team(member, services.TeamInput(name='University Team'))

        assert first.slug != second.slug
        assert first.name == second.name == 'University Team'

    @pytest.mark.parametrize('name', ['', '   ', 'x' * 201])
    def test_an_unusable_name_is_refused(self, owner, name):
        with pytest.raises(services.TeamError) as exc_info:
            services.create_team(owner, services.TeamInput(name=name))

        assert exc_info.value.field == 'name'
        assert not Team.objects.exists()

    def test_an_anonymous_or_deactivated_caller_cannot_create_one(self, team):
        for who in (None, User.objects.get(pk=team.owner.pk)):
            if who is not None:
                who.is_active = False
                who.save(update_fields=['is_active'])

            with pytest.raises(services.TeamError) as exc_info:
                services.create_team(who, services.TeamInput(name='Nope'))

            assert exc_info.value.reason == 'unauthenticated'

        assert Team.objects.count() == 1

    def test_one_person_cannot_hold_one_team_twice(self, team, member):
        services.add_existing_member(team.owner, team, member)
        with pytest.raises(IntegrityError):
            TeamMembership.objects.create(team=team, user=member)


# --- what a team role may hold ---------------------------------------------------------


@pytest.mark.django_db
class TestWhatATeamRoleCanHold:
    def test_a_code_the_platform_has_not_declared_is_refused(self, team):
        """The guard that makes "a team cannot self-approve" structural."""
        for code in ('team.ideas.approve', 'organization.members.manage', 'made.up'):
            with pytest.raises(services.TeamError) as exc_info:
                services.assert_known_permissions([code])

            assert exc_info.value.field == 'permissions'

    def test_a_team_can_review_but_there_is_no_approval_permission_to_hold(self):
        """
        Written as an assertion about the vocabulary rather than about a refusal,
        because the refusal is the absence of a code: there is nothing for a
        caller to try, so nothing to test. A team's reviewers may verify its ideas
        (`team.ideas.review`); approval is the platform's, so if somebody adds
        `team.ideas.approve` this fails the day it is added rather than the day it
        is used.
        """
        for code in authorization.ALL_TEAM_PERMISSIONS:
            assert 'approve' not in code

        assert set(authorization.ALL_TEAM_PERMISSIONS) == {
            'team.view',
            'team.update',
            'team.members.view',
            'team.members.manage',
            'team.ideas.submit',
            'team.ideas.review',
        }

    def test_a_custom_team_role_can_hold_any_declared_code(self, team, owner):
        role = TeamRole.objects.create(team=team, slug='lead', name='Lead')
        permission = TeamRolePermission.objects.create(
            role=role, permission=team.roles.get(slug='member').role_permissions.first().permission
        )

        assert permission.permission.code == authorization.TEAM_VIEW
        assert authorization.has_permission(owner, team, authorization.TEAM_VIEW) is True

    def test_a_role_from_another_team_cannot_be_held_here(self, team, owner, member):
        other = services.create_team(member, services.TeamInput(name='Other Squad'))
        foreign_role = TeamRole.objects.get(team=other, slug=services.OWNER_ROLE_SLUG)

        with pytest.raises(ValidationError) as exc_info:
            TeamMembershipRole.objects.create(
                membership=authorization.get_membership(owner, team),
                role=foreign_role,
            )

        # A cross-team role grant is a cross-tenant authorization bug, so it is
        # refused by the model rather than by a filter some query would have to
        # remember.
        assert 'same team' in str(exc_info.value)


# --- membership -----------------------------------------------------------------------


@pytest.mark.django_db
class TestMembership:
    def test_any_member_may_add_an_account_that_already_exists(self, team, member):
        services.add_existing_member(team.owner, team, member)

        assert authorization.is_member_of(member, team) is True
        assert selectors.roles_for(authorization.get_membership(member, team)) == [
            services.MEMBER_ROLE_SLUG
        ]

    def test_adding_grants_nothing_but_the_member_role(self, team, member):
        services.add_existing_member(team.owner, team, member)

        for code in (authorization.TEAM_UPDATE, authorization.TEAM_MEMBERS_MANAGE):
            assert authorization.has_permission(member, team, code) is False

    def test_only_somebody_who_may_manage_members_may_add_anybody(self, team, member, owner):
        services.add_existing_member(owner, team, member)

        outsider = make_user('outsider@example.com')
        for index, actor in enumerate((None, outsider, member)):
            candidate = make_user(f'candidate{index}@example.com')
            with pytest.raises(services.TeamError):
                services.add_existing_member(actor, team, candidate)

        # And the owner still can, so the refusals above were about authority and
        # not about the operation being impossible.
        assert services.add_existing_member(owner, team, outsider) is not None

    def test_a_deactivated_account_cannot_be_added(self, team, member):
        member.is_active = False
        member.save(update_fields=['is_active'])

        with pytest.raises(services.TeamError) as exc_info:
            services.add_existing_member(team.owner, team, member)

        assert exc_info.value.message == 'That account is not active.'

    def test_re_adding_reactivates_the_row_rather_than_adding_one(self, team, member):
        first = services.add_existing_member(team.owner, team, member)
        services.leave_team(member, team)

        second = services.add_existing_member(team.owner, team, member)

        assert second.pk == first.pk
        assert TeamMembership.objects.filter(team=team, user=member).count() == 1
        assert authorization.is_member_of(member, team) is True

    def test_leaving_keeps_the_ideas(self, team, member):
        services.add_existing_member(team.owner, team, member)
        idea = idea_services.create_idea_in_context(
            member,
            idea_services.IdeaInput(
                title='Filed together',
                description=DESCRIPTION,
                category_id=_category().pk,
            ),
            submission_context=Idea.SubmissionContext.TEAM,
            team_id=team.pk,
        )

        services.leave_team(member, team)

        assert Idea.objects.filter(pk=idea.pk).exists()
        assert idea.team_id == team.pk
        assert idea.author_id == member.pk

    def test_the_only_member_cannot_leave(self, team, owner):
        """
        Refused because a team nobody can administer cannot be repaired: fixing it
        needs `team.members.manage`, which is what they would be giving up.
        """
        with pytest.raises(services.TeamError) as exc_info:
            services.leave_team(owner, team)

        assert 'only member' in exc_info.value.message
        assert authorization.is_member_of(owner, team) is True

    def test_the_owner_may_leave_once_somebody_else_is_there(self, team, member):
        services.add_existing_member(team.owner, team, member)

        services.leave_team(team.owner, team)

        assert authorization.is_member_of(team.owner, team) is False
        assert authorization.is_member_of(member, team) is True

    def test_a_non_member_cannot_leave(self, team, member):
        with pytest.raises(services.TeamError) as exc_info:
            services.leave_team(member, team)

        assert exc_info.value.message == 'You are not a member of this team.'

    def test_the_owner_is_protected_from_deletion(self, team, owner):
        """
        A team outlives any one person's participation. Cascading from the account
        would take the other members' work with it, so `owner` is `PROTECT`.
        """
        with pytest.raises(IntegrityError):
            owner.delete()

        assert Team.objects.filter(pk=team.pk).exists()


# --- reading ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSelectors:
    def test_a_member_sees_their_team_and_its_roster(self, team, member, owner):
        services.add_existing_member(owner, team, member)

        assert selectors.get_team(member, team.pk) is not None
        assert [membership.user for membership in selectors.list_members(team)] == [owner, member]
        assert list(selectors.list_teams(member)) == [team]

    def test_a_stranger_sees_nothing_and_learns_nothing(self, team, member):
        outsider = make_user('stranger@example.com')

        assert selectors.get_team(outsider, team.pk) is None
        # Same answer for a team that does not exist, so the id cannot be used to
        # discover which team ids are real.
        assert selectors.get_team(outsider, 999999) is None
        assert list(selectors.list_teams(outsider)) == []

    def test_an_anonymous_or_inactive_caller_sees_nothing(self, team, owner):
        assert selectors.get_team(None, team.pk) is None
        owner.is_active = False
        assert selectors.get_team(owner, team.pk) is None
        assert selectors.list_teams(None).count() == 0

    def test_a_former_member_drops_out_of_the_listing(self, team, owner, member):
        services.add_existing_member(owner, team, member)
        services.leave_team(member, team)

        assert selectors.get_team(member, team.pk) is None
        assert list(selectors.list_teams(member)) == []

    def test_owned_teams_is_the_narrower_list(self, team, owner, member):
        services.add_existing_member(owner, team, member)

        assert list(selectors.owned_teams(member)) == []
        assert list(selectors.owned_teams(owner)) == [team]
        assert list(selectors.list_teams(member)) == [team]

    def test_malformed_ids_are_refused_rather_than_raising(self, team, owner):
        assert selectors.get_team(owner, 'not-an-id') is None
        assert selectors.get_team(owner, None) is None
        assert authorization.active_team_ids(owner) == [team.pk]


# --- the team as a tenant for ideas -----------------------------------------------------


def _category():
    from ideas.models import Category

    category, _ = Category.objects.get_or_create(name='Teams Fixture')
    return category


@pytest.mark.django_db
class TestTeamIdeas:
    def test_any_active_member_may_file_for_the_team(self, team, member, owner):
        """
        Filing is not submitting, so the membership is the proof - a plain member
        can write the draft. What they cannot do is submit it for somebody else
        unless they hold `team.ideas.submit`, which the Member role does.
        """
        services.add_existing_member(owner, team, member)
        category = _category()

        for author in (owner, member):
            idea = idea_services.create_idea_in_context(
                author,
                idea_services.IdeaInput(
                    title=f'Filed by {author.email}',
                    description=DESCRIPTION,
                    category_id=category.pk,
                ),
                submission_context=Idea.SubmissionContext.TEAM,
                team_id=team.pk,
            )
            assert idea.team_id == team.pk
            assert idea.organization_id is None

        assert authorization.can_submit_for(member, team) is True

    def test_a_stranger_cannot_file_for_the_team(self, team):
        outsider = make_user('outsider@example.com')

        with pytest.raises(idea_services.IdeaError) as exc_info:
            idea_services.create_idea_in_context(
                outsider,
                idea_services.IdeaInput(
                    title='Not yours',
                    description=DESCRIPTION,
                    category_id=_category().pk,
                ),
                submission_context=Idea.SubmissionContext.TEAM,
                team_id=team.pk,
            )

        assert exc_info.value.reason == 'membership_required'
        assert not Idea.objects.exists()

    def test_a_team_idea_goes_to_its_team_before_the_platform(self, team, owner):
        """
        The team's own reviewers check it first; only after they verify it does the
        owner publish it to the platform. Asserted through the same call the author
        makes, because that is where the stage is chosen.
        """
        idea = idea_services.create_idea_in_context(
            owner,
            idea_services.IdeaInput(
                title='Checked by the team first',
                description=DESCRIPTION,
                category_id=_category().pk,
            ),
            submission_context=Idea.SubmissionContext.TEAM,
            team_id=team.pk,
        )

        submitted = idea_services.submit_idea(owner, idea.pk)

        assert submitted.status == Idea.Status.SUBMITTED_TO_ORGANIZATION
        assert submitted.submitted_at is not None

    def test_a_reviewer_cannot_review_their_own_idea(self, team, owner):
        """
        The team's reviewers include its Owner, so the author exclusion is what keeps
        them from verifying what they wrote - for every permission they hold.
        """
        assert authorization.can_review_for(owner, team) is True

        idea = idea_services.create_idea_in_context(
            owner,
            idea_services.IdeaInput(
                title='Nobody reviews their own',
                description=DESCRIPTION,
                category_id=_category().pk,
            ),
            submission_context=Idea.SubmissionContext.TEAM,
            team_id=team.pk,
        )
        idea_services.submit_idea(owner, idea.pk)
        idea.refresh_from_db()

        from ideas import lifecycle

        assert lifecycle.is_organization_reviewer(owner, idea) is False
        assert lifecycle.is_platform_reviewer(owner, idea) is False
        assert lifecycle.available_transitions(owner, idea) == []
