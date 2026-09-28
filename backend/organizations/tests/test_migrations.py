from importlib import import_module

import pytest
from django.apps import apps
from django.db import connection

from identity.models import User
from organizations.models import Membership, MembershipRole, Organization, Role


@pytest.mark.django_db
def test_existing_organizations_receive_owner_role_in_migration_backfill():
    user = User.objects.create_user(
        email='existing@example.com',
        first_name='Existing',
        last_name='Owner',
        phone_number='+255712345679',
        password='a-strong-unique-pass-1',
    )
    organization = Organization.objects.create(name='Existing Org', slug='existing-org')
    membership = Membership.objects.create(user=user, organization=organization)
    migration = import_module(
        'organizations.migrations.0002_permission_role_membershiprole_rolepermission_and_more'
    )

    with connection.schema_editor() as schema_editor:
        migration.create_permissions(apps, schema_editor)
        migration.migrate_existing_organizations(apps, schema_editor)

    owner_role = Role.objects.get(organization=organization, slug='owner')
    assert owner_role.is_system is True
    assert MembershipRole.objects.filter(membership=membership, role=owner_role).exists()
    # The backfill grants every permission row that exists. In a fully
    # migrated test database that includes `idea.review` (0003), so assert
    # the five 0002 defines rather than an exact count.
    assert set(owner_role.role_permissions.values_list('permission__code', flat=True)) >= {
        code for code, _, _ in migration.PERMISSION_DEFINITIONS
    }
