"""
Invitations: the security properties of the join.

One table serves organizations and teams, because the *security* is identical
for both and it is worth having exactly one implementation of it. These tests
are organised around that security, in the order the checks run in
`invitations.services.accept_invitation`, because the order **is** the security:

1. **Bound to an address.** The authenticated account's own stored email must
   match. A mismatch is refused, not adapted - and it is refused *before* the
   claim, so somebody holding a link cannot burn it for its real recipient.
2. **Unguessable, and not recoverable from the database.** 32 bytes from
   `secrets`, stored as a SHA-256 digest, looked up by digest.
3. **Single-use.** `PENDING -> ACCEPTED` exactly once, by a conditional update,
   so two simultaneous clicks cannot both create a membership.
4. **Expiring**, and an expired one is recorded as expired rather than left
   looking pending in the inviter's list.
5. **Revocable**, and authorized against the invitation's *own* tenant so an
   organization Owner cannot revoke a team's invitation.

Plus the two rules that make it a *membership* system and not a mailing list:
inviting requires the permission to manage members, and acceptance is the only
thing anywhere in the platform that creates a membership.
"""

import hashlib
import threading
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from identity.models import User
from invitations import services
from invitations.models import Invitation
from organizations.models import Membership, MembershipRole
from organizations.services import (
    MEMBER_ROLE_SLUG,
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from teams import authorization as team_authorization
from teams import services as team_services

VALID_PASSWORD = 'a-strong-unique-pass-1'


def make_user(email, **overrides):
    fields = {
        'email': email,
        'first_name': 'Test',
        'last_name': 'User',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password=VALID_PASSWORD, **fields)


@pytest.fixture
def owner():
    return make_user('owner@acme.example')


@pytest.fixture
def organization(owner):
    return create_organization_for_user(owner, CreateOrganizationInput(name='Acme')).organization


@pytest.fixture
def team(owner):
    return team_services.create_team(owner, team_services.TeamInput(name='Automation Squad'))


@pytest.fixture
def recipient(db):
    return make_user('recipient@example.com')


def invite_to_org(owner, organization, email='recipient@example.com', **kwargs):
    return services.invite_to_organization(owner, organization, email, **kwargs)


def invite_to_team(owner, team, email='recipient@example.com', **kwargs):
    return services.invite_to_team(owner, team, email, **kwargs)


# --- the token -------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheToken:
    def test_only_the_digest_is_stored(self, owner, organization):
        """
        A dump of the table cannot be replayed. The plaintext exists in exactly
        one place - the email - and is not recoverable from the database, so this
        is asserted against the row rather than against a helper's return value.
        """
        issued = invite_to_org(owner, organization)
        row = Invitation.objects.get(pk=issued.invitation.pk)

        expected = hashlib.sha256(issued.raw_token.encode('utf-8')).hexdigest()
        assert row.token_digest == expected
        assert len(row.token_digest) == 64
        assert issued.raw_token not in str(row.__dict__)

    def test_the_token_is_long_and_url_safe(self, owner, organization):
        issued = invite_to_org(owner, organization)

        # 32 bytes from `secrets.token_urlsafe`, which is 43 characters.
        assert len(issued.raw_token) == 43
        assert issued.raw_token.replace('-', '').replace('_', '').isalnum()

    def test_two_invitations_never_share_a_token(self, owner, organization):
        first = invite_to_org(owner, organization, 'one@example.com')
        second = invite_to_org(owner, organization, 'two@example.com')

        assert first.raw_token != second.raw_token
        assert first.invitation.token_digest != second.invitation.token_digest

    def test_the_token_is_looked_up_by_digest(self, owner, organization):
        issued = invite_to_org(owner, organization)

        found = services.get_invitation_for_token(issued.raw_token)

        assert found is not None
        assert found.pk == issued.invitation.pk

    @pytest.mark.parametrize('token', ['', None, 'not-a-token', 'x' * 43])
    def test_an_unusable_token_names_nothing(self, token):
        assert services.get_invitation_for_token(token) is None


# --- issuing -------------------------------------------------------------------------


@pytest.mark.django_db
class TestIssuing:
    def test_it_creates_a_pending_invitation_for_the_normalized_address(self, owner, organization):
        issued = invite_to_org(owner, organization, '  Recipient@Example.COM ')

        invitation = issued.invitation
        assert invitation.status == Invitation.Status.PENDING
        assert invitation.email == 'recipient@example.com'
        assert invitation.scope == Invitation.Scope.ORGANIZATION
        assert invitation.organization_id == organization.pk
        assert invitation.team_id is None
        assert invitation.invited_by_id == owner.pk
        assert invitation.is_open is True

    def test_the_email_goes_to_the_invited_address_and_carries_the_link(
        self, owner, organization, mailoutbox, django_capture_on_commit_callbacks
    ):
        with django_capture_on_commit_callbacks(execute=True):
            issued = invite_to_org(owner, organization)

        assert len(mailoutbox) == 1
        message = mailoutbox[0]
        assert message.to == ['recipient@example.com']
        assert issued.raw_token in message.body
        # Nothing about the tenant that the recipient is not yet entitled to: an
        # inbox is readable by more than its owner and outlives the account.
        assert 'ideas' not in message.body.lower()

    def test_a_delivery_failure_does_not_unsend_the_invitation(
        self, owner, organization, monkeypatch, django_capture_on_commit_callbacks
    ):
        """
        The invitation is the fact; the email is a notification about it.

        A mail failure is logged and swallowed, because an invitation that cannot
        be mailed must not become a pending row that blocks the inviter from
        trying again - they can revoke it and send another. Asserted against the
        real sender with only the transport broken, so what is being tested is
        the swallowing and not a mock's behaviour.
        """
        from django.core.mail import EmailMessage

        def explode(*args, **kwargs):
            raise OSError('mail server unreachable')

        monkeypatch.setattr(EmailMessage, 'send', explode)

        with django_capture_on_commit_callbacks(execute=True):
            issued = invite_to_org(owner, organization)

        # The row stands, and the inviter can revoke it and send another.
        assert Invitation.objects.filter(pk=issued.invitation.pk).exists()
        assert issued.invitation.status == Invitation.Status.PENDING
        assert services.revoke_invitation(owner, issued.invitation.pk).is_open is False

    def test_nothing_is_sent_before_the_transaction_commits(self, owner, organization, mailoutbox):
        """
        A rolled-back invitation must send nothing. The `on_commit` hook is what
        guarantees it, so the refusal is done inside a transaction that then
        raises.
        """

        def invite_then_fail():
            invite_to_org(owner, organization)
            raise RuntimeError('rolled back')

        with pytest.raises(RuntimeError), transaction.atomic():
            invite_then_fail()

        assert mailoutbox == []
        assert not Invitation.objects.exists()

    def test_it_is_sent_once_the_invitation_commits(
        self, owner, organization, mailoutbox, django_capture_on_commit_callbacks
    ):
        """
        The same hook, running: a committed invitation does send its email.

        `on_commit` never fires inside a `django_db` test - the whole test is one
        transaction that is rolled back - so the callbacks are captured and
        executed explicitly. That is the same mechanism the previous test proves
        does *not* fire on the rollback path, so the pair pins both halves of it.
        """
        with django_capture_on_commit_callbacks(execute=True):
            invite_to_org(owner, organization)

        assert len(mailoutbox) == 1

    def test_the_address_must_be_usable(self, owner, organization):
        for address in ('', '   ', None, 'x' * 250 + '@example.com'):
            with pytest.raises(services.InvitationError) as exc_info:
                invite_to_org(owner, organization, address)

            assert exc_info.value.field == 'email'

    def test_an_invitation_requires_the_permission_to_manage_members(
        self, owner, organization, recipient
    ):
        """
        A permission, not a role check somebody could forget - and the ordinary
        member is the person who must not be able to grow the organization.
        """
        issued = invite_to_org(owner, organization)
        services.accept_invitation(recipient, issued.raw_token)

        with pytest.raises(services.InvitationError) as exc_info:
            invite_to_organization_as(recipient, organization)

        assert exc_info.value.reason in ('forbidden', 'membership_required')
        assert not Invitation.objects.filter(email='invitee@example.com').exists()

    def test_ownership_cannot_be_handed_out_by_invitation(self, owner, organization):
        """
        Offering ownership would be a way to mint a second Owner, and the
        last-holder protection on a role change would never see it.
        """
        with pytest.raises(services.InvitationError) as exc_info:
            invite_to_org(owner, organization, role_slug='owner')

        assert exc_info.value.field == 'role'
        assert not Invitation.objects.exists()

    def test_a_role_the_tenant_does_not_have_is_refused_now(self, owner, organization):
        """
        Checked at issue time, so a typo cannot produce an invitation that fails
        at acceptance with a message the recipient cannot act on.
        """
        with pytest.raises(services.InvitationError) as exc_info:
            invite_to_org(owner, organization, role_slug='reviewr')

        assert exc_info.value.field == 'role'

    def test_you_cannot_invite_yourself(self, owner, organization):
        with pytest.raises(services.InvitationError) as exc_info:
            invite_to_org(owner, organization, owner.email)

        assert 'already a member' in exc_info.value.message

    def test_an_existing_member_is_not_re_invited(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        services.accept_invitation(recipient, issued.raw_token)

        with pytest.raises(services.InvitationError) as exc_info:
            invite_to_org(owner, organization)

        assert 'already a member' in exc_info.value.message

    def test_the_same_address_is_not_invited_twice_while_one_is_open(self, owner, organization):
        invite_to_org(owner, organization)

        with pytest.raises(services.InvitationError) as exc_info:
            invite_to_org(owner, organization)

        assert 'already been invited' in exc_info.value.message
        assert Invitation.objects.count() == 1

    def test_a_revoked_invitation_may_be_replaced(self, owner, organization):
        first = invite_to_org(owner, organization)
        services.revoke_invitation(owner, first.invitation.pk)

        second = invite_to_org(owner, organization)

        assert second.invitation.pk != first.invitation.pk
        assert Invitation.objects.filter(status=Invitation.Status.PENDING).count() == 1

    def test_an_expired_invitation_may_be_replaced(self, owner, organization):
        first = invite_to_org(owner, organization)
        Invitation.objects.filter(pk=first.invitation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        assert invite_to_org(owner, organization).invitation.pk != first.invitation.pk

    def test_a_deactivated_or_anonymous_caller_cannot_invite(self, owner, organization):
        owner.is_active = False
        owner.save(update_fields=['is_active'])

        for who in (None, owner):
            with pytest.raises(services.InvitationError) as exc_info:
                invite_to_organization_as(who, organization)

            assert exc_info.value.reason == 'unauthenticated'

    def test_the_tenant_must_be_one_the_caller_can_reach(self, owner, organization):
        """
        An organization that does not exist and one the caller cannot manage are
        the same answer, so the id cannot be used to discover organizations.
        """
        other = create_organization_for_user(
            make_user('other@globex.example'), CreateOrganizationInput(name='Globex')
        ).organization

        for tenant in (999999, other.pk):
            with pytest.raises(services.InvitationError):
                invite_to_organization_as(owner, tenant)


# --- teams ----------------------------------------------------------------------------


@pytest.mark.django_db
class TestTeamInvitations:
    def test_it_creates_a_team_scoped_invitation(self, team):
        issued = invite_to_team(team.owner, team)

        invitation = issued.invitation
        assert invitation.scope == Invitation.Scope.TEAM
        assert invitation.team_id == team.pk
        assert invitation.organization_id is None
        assert invitation.role_slug == team_services.MEMBER_ROLE_SLUG

    def test_acceptance_creates_the_team_membership(self, team, recipient):
        issued = invite_to_team(team.owner, team)

        services.accept_invitation(recipient, issued.raw_token)

        assert team_authorization.is_member_of(recipient, team) is True
        membership = team_authorization.get_membership(recipient, team)
        assert membership.user_id == recipient.pk

    def test_a_team_owner_may_offer_co_ownership(self, team, recipient):
        issued = invite_to_team(team.owner, team, role_slug=team_services.OWNER_ROLE_SLUG)

        services.accept_invitation(recipient, issued.raw_token)

        assert (
            team_authorization.has_permission(
                recipient, team, team_authorization.TEAM_MEMBERS_MANAGE
            )
            is True
        )

    def test_a_review_role_cannot_be_offered_because_no_team_has_one(self, team):
        """A team has no reviewer role, so the invitation cannot name one."""
        with pytest.raises(services.InvitationError) as exc_info:
            invite_to_team(team.owner, team, role_slug=REVIEWER_ROLE_SLUG)

        assert exc_info.value.field == 'role'

    def test_an_organization_owner_cannot_invite_to_somebody_elses_team(self, owner, organization):
        """
        A tenant is not a superset of another. Owning an organization grants
        nothing in a team the owner is not a member of - not even the permission
        to invite into it - because the two are separate tenants with separate
        rosters.
        """
        other_team = team_services.create_team(
            make_user('other@example.com'), team_services.TeamInput(name='Other Squad')
        )

        with pytest.raises(services.InvitationError) as exc_info:
            invite_to_team(owner, other_team)

        assert exc_info.value.reason == 'membership_required'
        assert not Invitation.objects.exists()


# --- accepting ------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccepting:
    def test_the_whole_join_happens_in_one_go(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)

        invitation = services.accept_invitation(recipient, issued.raw_token)

        assert invitation.status == Invitation.Status.ACCEPTED
        assert invitation.accepted_by_id == recipient.pk
        assert invitation.accepted_at is not None

        membership = Membership.objects.get(user=recipient, organization=organization)
        assert membership.status == Membership.Status.ACTIVE
        roles = list(
            MembershipRole.objects.filter(membership=membership).values_list(
                'role__slug', flat=True
            )
        )
        assert roles == [MEMBER_ROLE_SLUG]

    def test_the_role_is_resolved_at_the_moment_of_acceptance(self, owner, organization, recipient):
        """
        `role_slug` is stored rather than a foreign key on purpose: it means "the
        role this tenant calls `reviewer` **now**", and a tenant can gain roles
        after an invitation is sent.
        """
        issued = invite_to_org(owner, organization, role_slug=REVIEWER_ROLE_SLUG)

        services.accept_invitation(recipient, issued.raw_token)

        membership = Membership.objects.get(user=recipient, organization=organization)
        held = MembershipRole.objects.filter(membership=membership).values_list(
            'role__slug', flat=True
        )
        assert list(held) == [REVIEWER_ROLE_SLUG]

    def test_a_role_removed_after_issue_fails_loudly(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization, role_slug=REVIEWER_ROLE_SLUG)
        from organizations.models import Role

        Role.objects.filter(organization=organization, slug=REVIEWER_ROLE_SLUG).delete()

        with pytest.raises(services.InvitationError) as exc_info:
            services.accept_invitation(recipient, issued.raw_token)

        assert 'no longer available' in exc_info.value.message
        # And nothing half-happened.
        assert not Membership.objects.filter(user=recipient).exists()
        assert Invitation.objects.get(pk=issued.invitation.pk).status == Invitation.Status.PENDING

    def test_the_address_must_be_the_callers_own(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        stranger = make_user('stranger@example.com')

        with pytest.raises(services.InvitationError) as exc_info:
            services.accept_invitation(stranger, issued.raw_token)

        assert exc_info.value.message == ('This invitation was sent to a different email address.')
        # Nothing created, and - the part that matters - the invitation is still
        # usable by the person it was sent to.
        assert not Membership.objects.filter(user=stranger).exists()
        assert Invitation.objects.get(pk=issued.invitation.pk).status == (Invitation.Status.PENDING)

        services.accept_invitation(recipient, issued.raw_token)
        assert Membership.objects.filter(user=recipient).exists()

    def test_it_is_single_use(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        services.accept_invitation(recipient, issued.raw_token)

        with pytest.raises(services.InvitationError) as exc_info:
            services.accept_invitation(recipient, issued.raw_token)

        assert exc_info.value.message == 'This invitation is not valid any more.'
        assert Membership.objects.filter(user=recipient).count() == 1

    def test_an_expired_invitation_is_refused_and_recorded_as_expired(self, owner, organization):
        issued = invite_to_org(owner, organization)
        Invitation.objects.filter(pk=issued.invitation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        recipient = make_user('recipient@example.com')

        with pytest.raises(services.InvitationError) as exc_info:
            services.accept_invitation(recipient, issued.raw_token)

        assert exc_info.value.message == 'This invitation has expired.'
        # Recorded, so the inviter's list tells the truth about why it stopped
        # working rather than showing a pending invitation nobody can use.
        assert Invitation.objects.get(pk=issued.invitation.pk).status == (Invitation.Status.EXPIRED)

    def test_a_revoked_invitation_is_refused_on_the_same_path(self, owner, organization):
        issued = invite_to_org(owner, organization)
        services.revoke_invitation(owner, issued.invitation.pk)

        with pytest.raises(services.InvitationError) as exc_info:
            services.accept_invitation(make_user('recipient@example.com'), issued.raw_token)

        assert exc_info.value.message == 'This invitation is not valid any more.'

    def test_an_unknown_token_is_refused_with_the_same_message(self, owner, organization):
        """
        Not an existence oracle: an unknown token, a revoked one and an accepted
        one all answer the same way, so the endpoint cannot be used to test
        whether a token was ever real.
        """
        with pytest.raises(services.InvitationError) as exc_info:
            services.accept_invitation(make_user('recipient@example.com'), 'x' * 43)

        assert exc_info.value.message == 'This invitation is not valid any more.'

    def test_it_requires_an_authenticated_active_account(self, owner, organization):
        issued = invite_to_org(owner, organization)
        recipient = make_user('recipient@example.com')
        recipient.is_active = False
        recipient.save(update_fields=['is_active'])

        for who in (None, recipient):
            with pytest.raises(services.InvitationError) as exc_info:
                services.accept_invitation(who, issued.raw_token)

            assert exc_info.value.reason == 'unauthenticated'

        assert Invitation.objects.get(pk=issued.invitation.pk).status == (Invitation.Status.PENDING)

    def test_re_joining_reactivates_the_membership_rather_than_adding_one(
        self, owner, organization, recipient
    ):
        issued = invite_to_org(owner, organization)
        services.accept_invitation(recipient, issued.raw_token)
        membership = Membership.objects.get(user=recipient, organization=organization)
        membership.status = Membership.Status.INACTIVE
        membership.save(update_fields=['status'])

        second = invite_to_org(owner, organization, 'recipient@example.com')
        services.accept_invitation(recipient, second.raw_token)

        assert Membership.objects.filter(user=recipient, organization=organization).count() == 1
        assert Membership.objects.get(pk=membership.pk).status == Membership.Status.ACTIVE


# --- concurrency -----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_simultaneous_acceptances_create_one_membership(owner, organization):
    """
    The conditional claim, under real concurrency.

    Two threads, two connections, one token. `accept_invitation` locks the
    invitation row and then updates it `filter(status=PENDING)`, so exactly one
    of them can win: the other waits for the lock, re-reads `ACCEPTED`, and is
    refused. Without that, the membership would be created twice - and the
    uniqueness constraint on `(user, organization)` would turn that into an
    `IntegrityError` rather than a second membership, which is a much worse
    failure than a refusal.
    """
    issued = invite_to_org(owner, organization)
    recipient = make_user('recipient@example.com')

    outcomes = []
    barrier = threading.Barrier(2)

    def accept():
        barrier.wait()
        try:
            services.accept_invitation(recipient, issued.raw_token)
            outcomes.append('accepted')
        except services.InvitationError:
            outcomes.append('refused')
        finally:
            connections.close_all()

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ['accepted', 'refused']
    assert Membership.objects.filter(user=recipient, organization=organization).count() == 1


# --- revoking -------------------------------------------------------------------------


@pytest.mark.django_db
class TestRevoking:
    def test_a_pending_invitation_can_be_revoked(self, owner, organization):
        issued = invite_to_org(owner, organization)

        revoked = services.revoke_invitation(owner, issued.invitation.pk)

        assert revoked.status == Invitation.Status.REVOKED
        assert revoked.revoked_at is not None
        assert revoked.is_open is False

    def test_an_accepted_invitation_cannot_be_revoked(self, owner, organization, recipient):
        """
        It says so rather than pretending it worked: the membership it granted is
        a separate fact, and removing that is membership work, not invitation work.
        """
        issued = invite_to_org(owner, organization)
        services.accept_invitation(recipient, issued.raw_token)

        with pytest.raises(services.InvitationError) as exc_info:
            services.revoke_invitation(owner, issued.invitation.pk)

        assert exc_info.value.message == 'This invitation can no longer be revoked.'
        assert Membership.objects.filter(user=recipient).exists()

    def test_revocation_is_authorized_against_the_invitation_own_tenant(self, owner, organization):
        """
        An organization Owner cannot revoke a team's invitation, and a team Owner
        cannot revoke an organization's - which is what stops a cross-tenant
        revocation. Both tenants here belong to somebody else, so the refusals are
        about the tenant rather than about the permission.
        """
        other_user = make_user('other@globex.example')
        other_team = team_services.create_team(
            other_user, team_services.TeamInput(name='Other Squad')
        )
        other_org = create_organization_for_user(
            other_user, CreateOrganizationInput(name='Globex')
        ).organization

        team_issued = invite_to_team(other_user, other_team)
        org_issued = invite_to_organization_as(other_user, other_org)

        for issued in (team_issued, org_issued):
            with pytest.raises(services.InvitationError) as exc_info:
                services.revoke_invitation(owner, issued.invitation.pk)

            assert exc_info.value.reason == 'membership_required'
            assert Invitation.objects.get(pk=issued.invitation.pk).status == (
                Invitation.Status.PENDING
            )

        assert Invitation.objects.get(pk=team_issued.invitation.pk).status == (
            Invitation.Status.PENDING
        )

    def test_a_missing_or_malformed_id_is_unavailable(self, owner):
        for invitation_id in (999999, 'nope', None):
            with pytest.raises(services.InvitationError) as exc_info:
                services.revoke_invitation(owner, invitation_id)

            assert exc_info.value.message == 'Invitation is unavailable.'


# --- reading --------------------------------------------------------------------------


@pytest.mark.django_db
class TestListing:
    def test_an_organization_owner_sees_what_it_has_sent(self, owner, organization):
        invite_to_org(owner, organization, 'one@example.com')
        invite_to_org(owner, organization, 'two@example.com')

        invitations = services.list_for_organization(owner, organization.pk)

        assert {item.email for item in invitations} == {
            'one@example.com',
            'two@example.com',
        }
        assert all(item.invited_by_id == owner.pk for item in invitations)

    def test_a_team_owner_sees_the_team_s_invitations(self, team):
        invite_to_team(team.owner, team)

        invitations = services.list_for_team(team.owner, team.pk)

        assert [item.email for item in invitations] == ['recipient@example.com']

    def test_anybody_else_is_refused_the_same_way_for_everybody(
        self, owner, organization, recipient, team
    ):
        """
        A member, a stranger and an organization that does not exist all get the
        same refusal - the caller's `OrganizationError`/`TeamAuthorizationError`
        message, never a partial list - so the argument cannot be used to
        discover organizations or their invitations.
        """
        outsider = make_user('outsider@example.com')

        for who, tenant in (
            (recipient, organization.pk),
            (outsider, organization.pk),
            (owner, 999999),
        ):
            with pytest.raises(services.InvitationError):
                services.list_for_organization(who, tenant)
            with pytest.raises(services.InvitationError):
                services.list_for_team(who, tenant)

    def test_it_requires_an_authenticated_active_account(self, owner, organization):
        for who in (None,):
            with pytest.raises(services.InvitationError) as exc_info:
                services.list_for_organization(who, organization.pk)

            assert exc_info.value.reason == 'unauthenticated'


# --- the model is part of the security ---------------------------------------------------


@pytest.mark.django_db
class TestTheConstraints:
    def test_the_scope_must_match_the_tenant_it_names(self, owner, organization, team):
        """
        Two nullable foreign keys and a CHECK, so one flow can serve both kinds of
        invitation without either of them growing a second, subtly different, set
        of rules. A fourth shape - both tenants, or neither - is refused by the
        database.
        """
        for field, value in (
            ({'scope': Invitation.Scope.ORGANIZATION, 'team': team}, None),
            ({'scope': Invitation.Scope.TEAM, 'organization': organization}, None),
            ({'scope': Invitation.Scope.ORGANIZATION, 'organization': None}, None),
            ({'scope': Invitation.Scope.TEAM, 'team': None}, None),
        ):
            with pytest.raises((IntegrityError, Exception)) as exc_info:
                Invitation.objects.create(
                    **field,
                    email='someone@example.com',
                    role_slug=MEMBER_ROLE_SLUG,
                    token_digest=hashlib.sha256(
                        f'token-{(field["scope"], value, team.pk)}'.encode()
                    ).hexdigest(),
                    expires_at=timezone.now() + timedelta(days=1),
                    invited_by=owner,
                )
            assert 'organization' in str(exc_info.value).lower()

    def test_accepted_iff_recorded(self, owner, organization):
        """
        "Accepted" and "who accepted it, when" cannot disagree, so the audit of
        who joined what is reliable exactly where it matters most.
        """
        with pytest.raises((IntegrityError, ValidationError)):
            Invitation.objects.create(
                scope=Invitation.Scope.ORGANIZATION,
                organization=organization,
                email='someone@example.com',
                role_slug=MEMBER_ROLE_SLUG,
                token_digest=hashlib.sha256(b'accepted-without-a-record').hexdigest(),
                status=Invitation.Status.ACCEPTED,
                expires_at=timezone.now() + timedelta(days=1),
                invited_by=owner,
            )

    def test_an_accepted_invitation_records_its_acceptor(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)

        services.accept_invitation(recipient, issued.raw_token)

        stored = Invitation.objects.get(pk=issued.invitation.pk)
        assert stored.status == Invitation.Status.ACCEPTED
        assert stored.accepted_by_id == recipient.pk
        assert stored.accepted_at is not None

    def test_two_digests_cannot_collide(self, owner, organization):
        issued = invite_to_org(owner, organization)

        with pytest.raises((IntegrityError, ValidationError)):
            Invitation.objects.create(
                scope=Invitation.Scope.ORGANIZATION,
                organization=organization,
                email='other@example.com',
                role_slug=MEMBER_ROLE_SLUG,
                token_digest=issued.invitation.token_digest,
                expires_at=timezone.now() + timedelta(days=1),
                invited_by=owner,
            )

    def test_acceptance_is_the_only_way_a_membership_appears(self, owner, organization, recipient):
        """
        Stated as a fact about the suite rather than a test of this app: the
        invitation flow creates memberships, and so do creating an organization
        and creating a team. Nothing here creates one on its own, which is why an
        invitation that is refused leaves the roster untouched - asserted
        repeatedly above, and once more as the invariant it is.
        """
        assert not Membership.objects.filter(user=recipient).exists()

        issued = invite_to_org(owner, organization)
        assert not Membership.objects.filter(user=recipient).exists()

        services.accept_invitation(recipient, issued.raw_token)
        assert Membership.objects.filter(user=recipient).count() == 1


# --- helpers --------------------------------------------------------------------------


def invite_to_organization_as(user, organization):
    return services.invite_to_organization(user, organization, 'invitee@example.com')


@pytest.fixture
def mailoutbox(monkeypatch):
    """
    Invitation emails, captured.

    The project sends mail through Django's own `mail.outbox` in tests, and the
    invitation email is no exception - but this fixture exists so the assertion
    about *what was sent* is about the invitation rather than about the fixture
    the previous test happened to leave behind.
    """
    from django.core import mail

    outbox = mail.outbox
    outbox.clear()
    yield outbox
    outbox.clear()
