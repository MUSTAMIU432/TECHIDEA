"""
Invitations: the only way somebody joins an organization or a team.

One table for both, because the *security* is the whole of it and the security
is identical. An invitation is:

- **Bound to an email address.** Acceptance requires that the authenticated
  account's own stored address matches, case-insensitively. An account that is
  signed in with a different address is refused - not silently invited to
  anything, not silently switched.
- **Unguessable.** `token` is 32 bytes from `secrets.token_urlsafe`; only its
  SHA-256 digest is stored, so a dump of this table cannot be replayed. Lookup
  is by digest, which means the plaintext exists in exactly one place - the
  email - and is never recoverable from the database.
- **Single-use.** `PENDING -> ACCEPTED` exactly once, inside the transaction
  that grants the membership, and `status` is guarded by a conditional update so
  two simultaneous clicks on the link cannot both create a membership.
- **Expiring.** `expires_at`, checked on acceptance, not only on issue.
- **Revocable.** `PENDING -> REVOKED`, and a revoked invitation is refused on
  the same path as an expired one.

`scope` says which of the two tenants it names, and `clean` enforces that the
named tenant matches: exactly one of `organization`/`team` is set, and it is the
one the scope says. Two nullable foreign keys and a `CHECK` is what lets one
invitation flow serve both kinds of invitation without either of them growing a
second, subtly different, set of rules.

**Acceptance creates membership. Nothing else does.** That is the rule the
product asks for - "do not silently create membership simply because an email
was entered" - and it is enforced by there being no other writer:
`organizations.Membership` and `teams.TeamMembership` rows are created by
`create_organization_for_user`, `create_team`, and this module.
"""

from typing import ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

INVITATION_STATUS_CHOICES = (
    ('pending', 'Pending'),
    ('accepted', 'Accepted'),
    ('expired', 'Expired'),
    ('revoked', 'Revoked'),
)

INVITATION_SCOPE_CHOICES = (
    ('organization', 'Organization'),
    ('team', 'Team'),
)


class Invitation(models.Model):
    """
    One outstanding or spent invitation to join one organization or one team.

    `role_slug` is stored rather than a foreign key on purpose. A role belongs to
    a tenant, and a tenant can gain new roles after an invitation is sent; the
    invitation means "with the role this tenant calls `reviewer` **at the moment
    it is accepted**", which a foreign key frozen at send time could not say.
    Acceptance refuses a role the tenant does not have, so a stale slug fails
    loudly instead of silently granting the wrong permissions.
    """

    class Status(models.TextChoices):
        (PENDING, ACCEPTED, EXPIRED, REVOKED) = INVITATION_STATUS_CHOICES

    class Scope(models.TextChoices):
        (ORGANIZATION, TEAM) = INVITATION_SCOPE_CHOICES

    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.ORGANIZATION)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        related_name='invitations',
        null=True,
        blank=True,
    )
    team = models.ForeignKey(
        'teams.Team',
        on_delete=models.CASCADE,
        related_name='invitations',
        null=True,
        blank=True,
    )
    # Stored normalized (lowercased, stripped). `identity.User.email` is the
    # authority and is normalized the same way on registration, so the
    # comparison on acceptance is a plain equality rather than a case-folding
    # rule that could differ between the two paths.
    email = models.CharField(max_length=254, db_index=True)
    role_slug = models.CharField(
        max_length=100,
        help_text="The tenant's role slug this invitation grants, resolved on acceptance.",
    )
    # SHA-256 of the token. `db_index=True` because acceptance looks the
    # invitation up by this column and nothing else.
    token_digest = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='sent_invitations',
        help_text='The member who sent this invitation.',
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='accepted_invitations',
        null=True,
        blank=True,
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ['-created_at', '-pk']
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # The tenant matches the scope, exactly one of them, and no
            # fourth shape. The same rule in `clean`, for the error message.
            models.CheckConstraint(
                condition=(
                    # `_id__isnull`, not `organization__isnull`: a CHECK cannot
                    # join, and Django will not build the SQL for one.
                    Q(scope='organization', organization_id__isnull=False, team_id__isnull=True)
                    | Q(scope='team', organization_id__isnull=True, team_id__isnull=False)
                ),
                name='invitation_scope_matches_tenant',
            ),
            models.CheckConstraint(
                condition=Q(status__in=dict(INVITATION_STATUS_CHOICES)),
                name='invitation_status_is_known',
            ),
            models.CheckConstraint(
                condition=Q(scope__in=dict(INVITATION_SCOPE_CHOICES)),
                name='invitation_scope_is_known',
            ),
            # An accepted invitation has an acceptor and a moment; an
            # unaccepted one has neither. Without this, "accepted" and "who
            # accepted it" could disagree, and the audit of who joined what
            # would be unreliable exactly where it matters most.
            models.CheckConstraint(
                condition=(
                    Q(status='accepted', accepted_by__isnull=False, accepted_at__isnull=False)
                    | ~Q(status='accepted')
                ),
                name='invitation_accepted_iff_recorded',
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            # "What has this organization or team invited, and what is still
            # outstanding" - the InvitationCard list on both surfaces.
            models.Index(
                fields=['organization', 'status', '-created_at'], name='inv_org_status_idx'
            ),
            models.Index(fields=['team', 'status', '-created_at'], name='inv_team_status_idx'),
            # "What have I been invited to", for the recipient. Keyed on the
            # email rather than on `accepted_by`, because the point is to find
            # invitations for an address that may not have an account yet.
            models.Index(fields=['email', 'status'], name='inv_email_status_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.scope} invitation for {self.email} ({self.status})'

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if self.scope == self.Scope.ORGANIZATION and (
            self.organization_id is None or self.team_id is not None
        ):
            raise ValidationError('An organization invitation names an organization and no team.')
        if self.scope == self.Scope.TEAM and (
            self.team_id is None or self.organization_id is not None
        ):
            raise ValidationError('A team invitation names a team and no organization.')

    @property
    def is_open(self) -> bool:
        """Whether this invitation can still be accepted: pending, and not past its expiry."""
        from django.utils import timezone

        return self.status == self.Status.PENDING and self.expires_at > timezone.now()

    @property
    def tenant_label(self) -> str:
        """The organization's or team's name, for the email and the card."""
        if self.scope == self.Scope.ORGANIZATION and self.organization_id is not None:
            return self.organization.name
        if self.scope == self.Scope.TEAM and self.team_id is not None:
            return self.team.name
        return ''

    def tenant_object(self) -> object | None:
        """The organization or team this invitation names, or `None` if it is gone."""
        if self.scope == self.Scope.ORGANIZATION:
            return self.organization
        return self.team
