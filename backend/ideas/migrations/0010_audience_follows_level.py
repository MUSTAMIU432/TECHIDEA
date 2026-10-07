"""
Every idea's audience becomes the one its level dictates.

An individual idea is private to its author, a team idea is the team's, an
organization idea is the organization's. Ideas that were public (or held an
audience their level does not have) are converted to their owner's level. Nothing
is deleted and no idea changes owner; some ideas simply stop being readable by
people outside the tenant they belong to, which is the point of the change.

Not reversible in any useful way - the previous audience of each row is not kept -
so the reverse is a no-op rather than a guess.
"""

from django.db import migrations

AUDIENCE = {'individual': 'private', 'team': 'team', 'organization': 'organization'}


def convert(apps, schema_editor):
    Idea = apps.get_model('ideas', 'Idea')
    for context, audience in AUDIENCE.items():
        Idea.objects.filter(submission_context=context).exclude(visibility=audience).update(
            visibility=audience
        )


class Migration(migrations.Migration):
    dependencies = [
        ('ideas', '0009_idea_team_visibility'),
    ]

    operations = [
        migrations.RunPython(convert, migrations.RunPython.noop),
    ]
