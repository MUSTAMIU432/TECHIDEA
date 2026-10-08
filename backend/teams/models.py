"""
Teams: people who put an idea forward together.

A team is a **collaboration boundary, not a tenant**. That one sentence is the
whole reason this app exists separately from `organizations`: a team is how a
handful of people write one idea together, it has no reviewer role that can
validate anything, it cannot approve anything, and it never appears on the
platform side of the journey. An organization is the opposite - a tenant with
members, roles and a reviewer who confirms what the organization wants to
submit. Keeping them in one table would have forced a single set of roles to
mean both, which is exactly how "the team approved its own idea" happens.

Five entities:

    Team                a group of users who file ideas together.
    TeamMembership      one user's place in one team.
    TeamRole            a named set of team-scoped capabilities.
    TeamRolePermission  which `organizations.Permission` a team role holds.
    TeamMembershipRole  which roles a membership holds.

**Why team roles are their own tables rather than `organizations.Role`.**
`Role` has a required `organization` foreign key, so every role belongs to one
organization. A team deliberately has none - it is not inside any organization -
so pointing at `Role` would require inventing a fake organization to hang team
roles on, and a fake organization is a tenant that could be filtered for and
membership in. Team roles are therefore a separate set of tables over the
**same** `organizations.Permission` records. That is what keeps this from being a
second permission framework: the permission codes, the
"which roles does this membership hold, and what do they allow" question and its
one implementation (`teams.authorization.membership_has_permission`) are the
organization ones, and a team role cannot invent a permission code that the
platform does not already define.

**Membership is only ever created by accepting an invitation.** There is no
"add member by typing their email" operation. A `TeamMembership` means "this
person accepted an invitation to this team"; nothing weaker creates one, and
`invitations.Invitation` is the only other writer. The alternative - a
membership row in a PENDING state created at invitation time - would make
"is this person in the team" ambiguous between *asked* and *joined*, and every
query over the team would then have to filter on a status whose meaning was
being used for two purposes at once.
"""

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify


class Team(models.Model):
    """
    A group of users who file ideas together.

    `owner` is the member who created the team and holds the Owner role in it.
    It is `PROTECT`: a team outlives any one person's participation - the others
    carry on - and cascading from an account would take their work with it.
    Removing a team entirely is not an operation this app offers; a team that
    is finished simply stops being used, and its ideas remain readable to the
    people they were filed for.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='owned_teams',
        help_text='The member who created this team. Holds the Owner role in it.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['name', 'created_at']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # A slug is a URL identifier, so two teams cannot share one; a name
            # is only unique per owner, because two people in different
            # organizations may each have a "University Automation Team".
            models.UniqueConstraint(fields=['owner', 'name'], name='unique_team_name_per_owner'),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.slug = slugify(self.slug or self.name)[: Team._meta.get_field('slug').max_length]
        self.full_clean()
        return super().save(*args, **kwargs)


class TeamMembership(models.Model):
    """
    One user's place in one team.

    Created only by accepting an invitation (`invitations.services.accept_invitation`)
    and by creating the team. There is no row for "we would like to invite
    them": that is an `invitations.Invitation`, which is a *request* and is
    revocable and expiring, whereas this is a fact about a real account that
    cannot be un-accepted.

    `INACTIVE` is "was a member, is not one now" - the row is kept so the team
    does not re-invite somebody who already declined, and so `created_at` still
    says when they joined. It is not a soft delete of the work they did: their
    ideas and comments stay theirs, and the ideas they filed stay filed for the
    team.
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='team_memberships',
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # One row per (team, user) forever, rather than one per active
            # period: re-inviting somebody who has left should activate the
            # row they already have, not create a second one with an ambiguous
            # `created_at`.
            models.UniqueConstraint(
                fields=['team', 'user'], name='unique_membership_per_team_user'
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # "This user's teams" - the team switcher and the team-scoped idea
            # visibility filter, both of which are a range scan on this pair.
            models.Index(fields=['user', 'status'], name='teams_member_status_idx'),
            # A team roster, and "who may act in this team" for one team.
            models.Index(fields=['team', 'status'], name='teams_team_status_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.user.email} - {self.team.name}'


class TeamRole(models.Model):
    """
    A named set of team-scoped capabilities.

    The same shape as `organizations.Role` and for the same reason, with the
    tenant being a team instead of an organization. `is_system` marks the roles
    `teams.seed_team_roles` creates (Owner, Member); they are per-team rows
    rather than global ones so a team owner can add a role of their own without
    touching anybody else's team.
    """

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='roles',
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['name', 'created_at']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=['team', 'slug'], name='unique_team_role_slug_per_team'),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['team', 'slug'], name='teams_role_team_slug_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.team.name}: {self.name}'


class TeamRolePermission(models.Model):
    """
    Which `organizations.Permission` a team role holds.

    A foreign key to the organization app's permission *records* rather than a
    copy of the codes: the codes must have exactly one definition in the
    platform, and a copy here would be a second one that could drift. It also
    means a team role cannot hold a capability the platform has never heard of.
    """

    role = models.ForeignKey(
        TeamRole,
        on_delete=models.CASCADE,
        related_name='role_permissions',
    )
    permission = models.ForeignKey(
        'organizations.Permission',
        on_delete=models.CASCADE,
        related_name='team_role_permissions',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['role', 'permission'],
                name='unique_team_permission_per_role',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.role} - {self.permission}'


class TeamMembershipRole(models.Model):
    """
    Which roles a team membership holds.

    The same constraint as `organizations.MembershipRole.clean` - a role must
    belong to the same team as the membership holding it - because the failure
    mode without it is a Team Owner role from *another* team granting its
    permissions inside this one, which is a cross-tenant authorization bug that
    no query filter would catch.
    """

    membership = models.ForeignKey(
        TeamMembership,
        on_delete=models.CASCADE,
        related_name='membership_roles',
    )
    role = models.ForeignKey(
        TeamRole,
        on_delete=models.CASCADE,
        related_name='membership_roles',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=['membership', 'role'],
                name='unique_role_per_team_membership',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=['membership', 'role'], name='teams_memrole_mem_idx'),
            models.Index(fields=['role', 'membership'], name='teams_memrole_role_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.membership} - {self.role}'

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.membership_id and self.role_id and self.membership.team_id != self.role.team_id:
            raise ValidationError('Membership and role must belong to the same team.')
