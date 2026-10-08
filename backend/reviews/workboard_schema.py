"""GraphQL for a platform reviewer's work list (`reviews.workboard`)."""

import strawberry

from reviews import workboard


@strawberry.type(description='One idea a reviewer or their review team is handling.')
class WorkItemType:
    idea_id: strawberry.ID
    title: str
    owner_label: str = strawberry.field(description='Whose idea: a person, team or organization.')
    stage: str
    stage_label: str
    step: int = strawberry.field(description='Index into `reviewerWorkSteps`: how far it has come.')
    needs_me: bool
    action_label: str | None
    action_path: str | None = strawberry.field(description='The in-app page to act on it.')
    team_name: str | None
    is_lead: bool
    updated_at: str


@strawberry.type
class Query:
    @strawberry.field(description='The steps of the journey a work item moves through, in order.')
    def reviewer_work_steps(self) -> list[str]:
        return list(workboard.STEPS)

    @strawberry.field(
        description="The ideas the caller's review teams, or the caller, are handling - those "
        'waiting on them first. Empty for anybody who is not a platform reviewer.'
    )
    def reviewer_work(self, info: strawberry.Info) -> list[WorkItemType]:
        return [
            WorkItemType(
                idea_id=strawberry.ID(str(item.idea.pk)),
                title=item.idea.title,
                # An individual idea has no team or organization: name its author.
                owner_label=item.idea.tenant_label
                or item.idea.author.get_full_name()
                or item.idea.author.email,
                stage=item.stage,
                stage_label=item.stage_label,
                step=item.step,
                needs_me=item.needs_me,
                action_label=item.action_label,
                action_path=item.action_path,
                team_name=item.team_name,
                is_lead=item.is_lead,
                updated_at=item.updated_at.isoformat(),
            )
            for item in workboard.workboard_for(info.context.user)
        ]
