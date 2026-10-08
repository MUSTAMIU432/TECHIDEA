from django.db import migrations

#: The groups whose holders do their work outside the console, or are administrators already.
GROUPS = ('Platform reviewers', 'Platform intake', 'Proposal approvers')
#: What those groups no longer hold: the console is for platform administrators only.
CONSOLE = ('access_console', 'inspect_idea_content')


def take_the_console_away(apps, schema_editor):
    """
    A reviewer reviews from the review workspace, and intake and proposal approval are
    held by administrators, who have the console through "Platform administrators".
    So none of these groups grants the console any more.
    """
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    console = Permission.objects.filter(content_type__app_label='administration', codename__in=CONSOLE)
    for group in Group.objects.filter(name__in=GROUPS):
        group.permissions.remove(*console)


def give_it_back(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    console = Permission.objects.filter(content_type__app_label='administration', codename__in=CONSOLE)
    for group in Group.objects.filter(name__in=GROUPS):
        group.permissions.add(*console)


class Migration(migrations.Migration):
    dependencies = [
        ('administration', '0005_platform_roles'),
    ]

    operations = [
        migrations.RunPython(take_the_console_away, give_it_back),
    ]
