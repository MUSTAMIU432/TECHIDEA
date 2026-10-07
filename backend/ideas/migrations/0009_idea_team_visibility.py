"""
`TEAM` becomes a visibility value of its own.

**No data moves.** This migration only widens the CHECK constraint's allowed
set and updates the field's `choices`; every existing row keeps the visibility it
already had, and no row is rewritten to `team`. That is deliberate: an idea's
audience is a fact about who may read it, and inferring one from the tenant it
happens to belong to would silently change who can read other people's ideas on
the way to a schema that reads better.

Two things make the addition safe to run against a populated database:

- The constraint is removed and re-added with the wider set in one migration, so
  no intermediate state exists in which a row could be invalid.
- `team` is a new *value*, not a new meaning, so `idea_visibility_is_known`
  keeps refusing everything outside the five it now lists.

Written by hand-editing the generated file's header; the operations themselves
are Django's.
"""

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('ideas', '0008_idea_team_index_is_the_composite_one'),
        ('organizations', '0003_idea_review_permission_and_reviewer_role'),
        ('teams', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='idea',
            name='idea_visibility_is_known',
        ),
        migrations.AlterField(
            model_name='idea',
            name='visibility',
            field=models.CharField(choices=[('public', 'Public'), ('organization', 'Organization'), ('team', 'Team'), ('department', 'Department'), ('private', 'Private')], default='private', max_length=16),
        ),
        migrations.AddConstraint(
            model_name='idea',
            constraint=models.CheckConstraint(condition=models.Q(('visibility__in', {'department': 'Department', 'organization': 'Organization', 'private': 'Private', 'public': 'Public', 'team': 'Team'})), name='idea_visibility_is_known'),
        ),
    ]
