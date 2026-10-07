"""
Give every existing team the Reviewer role, and the permission it carries.

Teams created from now on get it from `teams.services.seed_team_roles`; this brings
the ones that already exist level with them. Owners already hold every team
permission, so they also gain `team.ideas.review` here - which is intended: the
person who runs a team may check its ideas.
"""

from django.db import migrations

REVIEW = 'team.ideas.review'
MEMBER_CODES = ('team.view', 'team.members.view', 'team.ideas.submit')


def add_reviewer_role(apps, schema_editor):
    Permission = apps.get_model('organizations', 'Permission')
    Team = apps.get_model('teams', 'Team')
    TeamRole = apps.get_model('teams', 'TeamRole')
    TeamRolePermission = apps.get_model('teams', 'TeamRolePermission')

    if not Team.objects.exists():
        # Nothing to bring level, and the permission records are created with the
        # first team (`teams.services.seed_team_roles`) as they always were.
        return

    review, _ = Permission.objects.get_or_create(
        code=REVIEW, defaults={'name': 'Team ideas review'}
    )
    member_permissions = [
        Permission.objects.get_or_create(
            code=code, defaults={'name': code.replace('.', ' ').capitalize()}
        )[0]
        for code in MEMBER_CODES
    ]
    for team in Team.objects.all():
        reviewer, _ = TeamRole.objects.get_or_create(
            team=team,
            slug='reviewer',
            defaults={
                'name': 'Reviewer',
                'description': "Can verify the team's ideas, or send them back for changes or "
                'more documents.',
                'is_system': True,
            },
        )
        for permission in (*member_permissions, review):
            TeamRolePermission.objects.get_or_create(role=reviewer, permission=permission)
        owner = TeamRole.objects.filter(team=team, slug='owner').first()
        if owner is not None:
            TeamRolePermission.objects.get_or_create(role=owner, permission=review)


class Migration(migrations.Migration):
    dependencies = [
        ('teams', '0001_initial'),
        ('organizations', '0003_idea_review_permission_and_reviewer_role'),
    ]

    operations = [
        migrations.RunPython(add_reviewer_role, migrations.RunPython.noop),
    ]
