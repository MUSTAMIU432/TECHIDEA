"""
The Teams GraphQL adapter.

The same shape as `ideas/schema.py` and `reviews/schema.py`: types with
`from_model`, payloads carrying `(success, message, field)`, and resolvers that
only move data between the schema and `teams/selectors.py` / `teams/services.py`.
No rule is decided here.

What is exposed
---------------
- `teams` - the caller's teams. Always scoped to their own memberships; there is
  no `team(id)` that resolves a team they are not in, and the selector returns
  `None` rather than raising.
- `team(id)` - one team, for members.
- `teamMembers` - the roster, with each member's team role slugs.
- `createTeam`, `leaveTeam`, `addTeamMember` - the write side.

**Deliberately absent: inviting from here.** Invitations are their own domain
(`invitations`), because the invitation email, the accept flow and the single-use
token belong together and inviting is a different operation from creating a team.
The team schema does not grow a second, slightly different invitation surface.
"""

import strawberry

from identity.schema import UserType
from teams import selectors, services
from teams.models import Team, TeamMembership


@strawberry.type(description='One team, and the people in it.')
class TeamType:
    id: strawberry.ID
    name: str
    slug: str
    description: str
    owner_id: strawberry.ID
    member_count: int
    created_at: str

    @staticmethod
    def from_model(team: Team) -> 'TeamType':
        members = getattr(team, 'active_memberships', None)
        return TeamType(
            id=strawberry.ID(str(team.pk)),
            name=team.name,
            slug=team.slug,
            description=team.description,
            owner_id=strawberry.ID(str(team.owner_id)),
            member_count=len(members) if members is not None else 0,
            created_at=team.created_at.isoformat(),
        )


@strawberry.type(description="One person's place in a team, and the roles they hold there.")
class TeamMemberType:
    user: UserType
    role_slugs: list[str]
    joined_at: str

    @staticmethod
    def from_model(membership: TeamMembership) -> 'TeamMemberType':
        return TeamMemberType(
            user=UserType.from_model(membership.user),
            role_slugs=selectors.roles_for(membership),
            joined_at=membership.created_at.isoformat(),
        )


@strawberry.type(description='Result of creating a team.')
class CreateTeamPayload:
    success: bool
    message: str
    field: str | None = None
    team: TeamType | None = None


@strawberry.type(description='Result of a membership operation.')
class TeamMembershipPayload:
    success: bool
    message: str
    field: str | None = None


@strawberry.input(description='The writable content of a new team.')
class CreateTeamInput:
    name: str
    description: str = ''


@strawberry.input(description='Add somebody who already has an account to a team.')
class AddTeamMemberInput:
    team_id: strawberry.ID
    user_id: strawberry.ID


@strawberry.type
class Query:
    @strawberry.field(
        description=(
            'Every team you are an active member of, by name. A user can belong '
            'to any number of teams and to none at all; an empty list is the '
            'answer for both.'
        )
    )
    def teams(self, info: strawberry.Info) -> list[TeamType]:
        return [TeamType.from_model(team) for team in selectors.list_teams(info.context.user)]

    @strawberry.field(
        description=(
            'One team you are an active member of, or null. Null is also the '
            'answer for a team that does not exist and for one you are not in, '
            'so a team id cannot be used to discover other teams.'
        )
    )
    def team(self, info: strawberry.Info, id: strawberry.ID) -> TeamType | None:
        team = selectors.get_team(info.context.user, id)
        return TeamType.from_model(team) if team is not None else None

    @strawberry.field(description="A team's roster, oldest join first. Empty for anybody else.")
    def team_members(self, info: strawberry.Info, team_id: strawberry.ID) -> list[TeamMemberType]:
        team = selectors.get_team(info.context.user, team_id)
        if team is None:
            return []
        return [
            TeamMemberType.from_model(membership) for membership in selectors.list_members(team)
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description=(
            'Create a team, with you as its Owner and first member. A team needs '
            'no organization: a user with no organization can create one, file a '
            'team idea, and take it all the way to the platform.'
        )
    )
    def create_team(self, info: strawberry.Info, input: CreateTeamInput) -> CreateTeamPayload:
        try:
            team = services.create_team(
                info.context.user,
                services.TeamInput(name=input.name, description=input.description),
            )
        except services.TeamError as exc:
            return CreateTeamPayload(success=False, message=exc.message, field=exc.field)
        return CreateTeamPayload(
            success=True,
            message='Team created.',
            # Re-read through the selector so the returned team carries the
            # prefetched roster the type's `member_count` needs.
            team=TeamType.from_model(selectors.get_team(info.context.user, team.pk) or team),
        )

    @strawberry.mutation(
        description=(
            'Add somebody who already has an account to a team, as a Member. For '
            'everybody else, invite them by email instead - a membership is only '
            'ever created by accepting an invitation or by creating the team.'
        )
    )
    def add_team_member(
        self, info: strawberry.Info, input: AddTeamMemberInput
    ) -> TeamMembershipPayload:
        from identity.models import User

        user = info.context.user
        member = User.objects.filter(pk=_as_int(input.user_id), is_active=True).first()
        if member is None:
            return TeamMembershipPayload(
                success=False, message='That person is not available.', field='userId'
            )
        try:
            services.add_existing_member(user, input.team_id, member)
        except services.TeamError as exc:
            return TeamMembershipPayload(success=False, message=exc.message, field=exc.field)
        return TeamMembershipPayload(success=True, message=f'{member.email} added to the team.')

    @strawberry.mutation(
        description=(
            'Leave a team. Refused while you are its only member - a team nobody '
            'can administer cannot be repaired from the interface. The team and '
            'its ideas are not deleted.'
        )
    )
    def leave_team(self, info: strawberry.Info, team_id: strawberry.ID) -> TeamMembershipPayload:
        try:
            services.leave_team(info.context.user, team_id)
        except services.TeamError as exc:
            return TeamMembershipPayload(success=False, message=exc.message, field=exc.field)
        return TeamMembershipPayload(success=True, message='You have left the team.')

    @strawberry.mutation(
        description=(
            "Make a member one of the team's reviewers, or take that back. Only a "
            "member who can manage the team's members may do it. Reviewers verify "
            "the team's ideas, or send them back for changes or more documents."
        )
    )
    def set_team_reviewer(
        self,
        info: strawberry.Info,
        team_id: strawberry.ID,
        user_id: strawberry.ID,
        is_reviewer: bool,
    ) -> TeamMembershipPayload:
        try:
            services.set_member_reviewer(info.context.user, team_id, user_id, is_reviewer)
        except services.TeamError as exc:
            return TeamMembershipPayload(success=False, message=exc.message, field=exc.field)
        return TeamMembershipPayload(
            success=True,
            message='Reviewer added.' if is_reviewer else 'Reviewer removed.',
        )


def _as_int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0
