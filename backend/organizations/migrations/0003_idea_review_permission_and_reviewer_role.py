"""
S3-002: the `idea.review` permission and the per-organization Reviewer role.

Existing organizations get what bootstrap gives a new one
(`organizations.services.create_organization_for_user`):

- every **Owner** role gains `idea.review`, so every Owner who could review
  before this migration (the reviewer gate was "holds a system role") still
  can, and the gate can switch to the permission without changing who passes;
- a non-system **Reviewer** role carrying `organization.view` and
  `idea.review`, which an Owner grants through `assignRoleToMembership`.

The Owner lookup is by the system flag and slug together, and an organization
without one is skipped rather than given one: provisioning owners is not this
migration's job. A pre-existing role that already uses the `reviewer` slug is
left exactly as it is rather than silently widened into a reviewing role.

Definitions are inlined rather than imported from `organizations.services`,
as in 0002, so that later edits to the service cannot change what this
migration did.
"""

from django.db import migrations

IDEA_REVIEW = (
    'idea.review',
    'Review ideas',
    "Review and decide on the organization's submitted ideas.",
)
REVIEWER_ROLE_SLUG = 'reviewer'
REVIEWER_ROLE_PERMISSION_CODES = ('organization.view', 'idea.review')


def grant_idea_review(apps, schema_editor):
    organization_model = apps.get_model('organizations', 'Organization')
    permission_model = apps.get_model('organizations', 'Permission')
    role_model = apps.get_model('organizations', 'Role')
    role_permission_model = apps.get_model('organizations', 'RolePermission')
    database_alias = schema_editor.connection.alias

    code, name, description = IDEA_REVIEW
    idea_review, _ = permission_model.objects.using(database_alias).get_or_create(
        code=code,
        defaults={'name': name, 'description': description},
    )
    reviewer_permissions = list(
        permission_model.objects.using(database_alias).filter(
            code__in=REVIEWER_ROLE_PERMISSION_CODES
        )
    )

    owner_roles = role_model.objects.using(database_alias).filter(slug='owner', is_system=True)
    for owner_role in owner_roles:
        role_permission_model.objects.using(database_alias).get_or_create(
            role=owner_role,
            permission=idea_review,
        )

    for organization in organization_model.objects.using(database_alias).order_by('pk'):
        reviewer_role, created = role_model.objects.using(database_alias).get_or_create(
            organization=organization,
            slug=REVIEWER_ROLE_SLUG,
            defaults={
                'name': 'Reviewer',
                'description': "Review and decide on the organization's submitted ideas.",
                'is_system': False,
            },
        )
        if not created:
            continue
        for permission in reviewer_permissions:
            role_permission_model.objects.using(database_alias).get_or_create(
                role=reviewer_role,
                permission=permission,
            )


def revoke_idea_review(apps, schema_editor):
    """
    Reverse: remove the permission (and with it every grant, by cascade) and
    the Reviewer roles this migration created.

    A Reviewer role is recognised by carrying `idea.review`, which no role
    could have held before this migration, so a pre-existing `reviewer`-slug
    role that the forward step left alone is left alone here too. Removing the
    roles also removes their membership assignments, which is the point of
    reversing a migration that introduced them.
    """
    permission_model = apps.get_model('organizations', 'Permission')
    role_model = apps.get_model('organizations', 'Role')
    database_alias = schema_editor.connection.alias

    role_model.objects.using(database_alias).filter(
        slug=REVIEWER_ROLE_SLUG,
        is_system=False,
        role_permissions__permission__code=IDEA_REVIEW[0],
    ).delete()
    permission_model.objects.using(database_alias).filter(code=IDEA_REVIEW[0]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('organizations', '0002_permission_role_membershiprole_rolepermission_and_more'),
    ]

    operations = [
        migrations.RunPython(grant_idea_review, revoke_idea_review),
    ]
