"""
GraphQL for platform reviewers and review teams.

Business refusals are `success: false` payloads with the field they name, like every
other domain. Who may do what is `reviews.review_teams`' to decide; nothing here does.
"""

import strawberry

from administration.authorization import AdministrationError
from reviews import review_teams
from reviews.models import ReviewTeam


@strawberry.type
class ReviewerType:
    id: strawberry.ID
    email: str
    name: str


@strawberry.type
class ReviewTeamMemberType:
    id: strawberry.ID
    email: str
    name: str
    is_lead: bool


@strawberry.type
class ReviewTeamType:
    id: strawberry.ID
    name: str
    is_active: bool
    lead_id: strawberry.ID
    members: list[ReviewTeamMemberType]

    @staticmethod
    def from_model(team: ReviewTeam) -> 'ReviewTeamType':
        return ReviewTeamType(
            id=strawberry.ID(str(team.pk)),
            name=team.name,
            is_active=team.is_active,
            lead_id=strawberry.ID(str(team.lead_id)),
            members=[
                ReviewTeamMemberType(
                    id=strawberry.ID(str(m.user_id)),
                    email=m.user.email,
                    name=m.user.get_full_name() or m.user.email,
                    is_lead=m.user_id == team.lead_id,
                )
                for m in team.members.select_related('user').all()
            ],
        )


@strawberry.type
class ReviewerPayload:
    success: bool
    message: str
    field: str | None = None
    reviewer: ReviewerType | None = None


@strawberry.type
class ReviewTeamPayload:
    success: bool
    message: str
    field: str | None = None
    team: ReviewTeamType | None = None


@strawberry.type
class AssignTeamPayload:
    success: bool
    message: str
    field: str | None = None
    team: ReviewTeamType | None = None


def _reviewer(user) -> ReviewerType:
    return ReviewerType(
        id=strawberry.ID(str(user.pk)), email=user.email, name=user.get_full_name() or user.email
    )


def _camel(name: str | None) -> str | None:
    if not name:
        return None
    head, *rest = name.split('_')
    return head + ''.join(part.capitalize() for part in rest)


@strawberry.type
class Query:
    @strawberry.field(description='Every platform reviewer. Empty without `manageReviewers`.')
    def reviewers(self, info: strawberry.Info) -> list[ReviewerType]:
        return [_reviewer(u) for u in review_teams.list_reviewers(info.context.user)]

    @strawberry.field(
        description='Every review team. Empty for anyone who can neither manage reviewers '
        'nor route work.'
    )
    def review_teams(self, info: strawberry.Info) -> list[ReviewTeamType]:
        return [ReviewTeamType.from_model(t) for t in review_teams.list_teams(info.context.user)]

    @strawberry.field(description='The active review teams the caller is a member of.')
    def my_review_teams(self, info: strawberry.Info) -> list[ReviewTeamType]:
        return [ReviewTeamType.from_model(t) for t in review_teams.teams_of(info.context.user)]


@strawberry.type
class Mutation:
    @strawberry.mutation(description='Make an existing account a platform reviewer.')
    def grant_reviewer(self, info: strawberry.Info, email: str) -> ReviewerPayload:
        try:
            user = review_teams.grant_reviewer(info.context.user, email)
        except AdministrationError as exc:
            return ReviewerPayload(success=False, message=exc.message, field=_camel(exc.field))
        return ReviewerPayload(success=True, message='Reviewer created.', reviewer=_reviewer(user))

    @strawberry.mutation(description='Remove a platform reviewer, and take them out of every team.')
    def revoke_reviewer(self, info: strawberry.Info, user_id: strawberry.ID) -> ReviewerPayload:
        try:
            user = review_teams.revoke_reviewer(info.context.user, user_id)
        except AdministrationError as exc:
            return ReviewerPayload(success=False, message=exc.message, field=_camel(exc.field))
        return ReviewerPayload(success=True, message='Reviewer removed.', reviewer=_reviewer(user))

    @strawberry.mutation(description='Form a review team with a lead and its members.')
    def create_review_team(
        self,
        info: strawberry.Info,
        name: str,
        lead_id: strawberry.ID,
        member_ids: list[strawberry.ID] | None = None,
    ) -> ReviewTeamPayload:
        try:
            team = review_teams.create_team(info.context.user, name, lead_id, member_ids or [])
        except AdministrationError as exc:
            return ReviewTeamPayload(success=False, message=exc.message, field=_camel(exc.field))
        return ReviewTeamPayload(
            success=True, message='Team created.', team=ReviewTeamType.from_model(team)
        )

    @strawberry.mutation(
        description='Rename a team, change its lead or members, or retire it. Only what is '
        'passed changes.'
    )
    def update_review_team(
        self,
        info: strawberry.Info,
        id: strawberry.ID,
        name: str | None = None,
        lead_id: strawberry.ID | None = None,
        member_ids: list[strawberry.ID] | None = None,
        is_active: bool | None = None,
    ) -> ReviewTeamPayload:
        try:
            team = review_teams.update_team(
                info.context.user,
                id,
                name=name,
                lead_id=lead_id,
                member_ids=member_ids,
                is_active=is_active,
            )
        except AdministrationError as exc:
            return ReviewTeamPayload(success=False, message=exc.message, field=_camel(exc.field))
        return ReviewTeamPayload(
            success=True, message='Team saved.', team=ReviewTeamType.from_model(team)
        )

    @strawberry.mutation(description='Route a submitted idea to a review team.')
    def assign_idea_to_review_team(
        self, info: strawberry.Info, idea_id: strawberry.ID, team_id: strawberry.ID
    ) -> AssignTeamPayload:
        try:
            assignment = review_teams.assign_team(info.context.user, idea_id, team_id)
        except AdministrationError as exc:
            return AssignTeamPayload(success=False, message=exc.message, field=_camel(exc.field))
        return AssignTeamPayload(
            success=True,
            message='Assigned to the team.',
            team=ReviewTeamType.from_model(assignment.team),
        )
