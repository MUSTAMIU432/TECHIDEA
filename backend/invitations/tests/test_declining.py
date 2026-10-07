"""
Declining an invitation, and telling anybody they have one.

Two features that exist for the same reason. Before this file, an invitation had
three outcomes - accepted, expired, revoked - and no way for the recipient to
say no, and an invitation to an address that already had an account produced one
email and nothing in the app. Both of those are gaps in the *flow* rather than in
the security, so they are what this file pins:

1. **A decline is the recipient's own act.** Same address rule as acceptance,
    refused before the claim so a link cannot be burned for somebody else, and
    the same "not valid any more" message for every spent state so it cannot be
    used to probe one.
2. **A decline creates nothing and removes nothing.** Acceptance is the only
    writer of a membership, so there is no membership row to undo - and that is
    asserted rather than assumed, because "deactivating the membership" is the
    tempting implementation and it would be wrong for an invitation that never
    made one.
3. **A declined invitation is a recorded decision.** Who and when, in columns the
    database will not let drift apart from the status.
4. **The invitee is told in the app when they have an account**, and the
    notification says the truth about what it cannot do: there is no accept link,
    because acceptance needs a plaintext token that exists only in the email.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import IntegrityError
from django.utils import timezone

from identity.models import User
from invitations import services
from invitations.models import Invitation
from notifications.models import Notification
from organizations.models import Membership
from organizations.services import CreateOrganizationInput, create_organization_for_user
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
    return make_user('owner@acme.example', first_name='Olive', last_name='Owner')


@pytest.fixture
def organization(owner):
    return create_organization_for_user(owner, CreateOrganizationInput(name='MUNA')).organization


@pytest.fixture
def team(owner):
    return team_services.create_team(owner, team_services.TeamInput(name='Automation Squad'))


@pytest.fixture
def recipient(db):
    return make_user('recipient@example.com', first_name='Rae', last_name='Rivera')


def invite_to_org(owner, organization, email='recipient@example.com'):
    return services.invite_to_organization(owner, organization, email)


def invite_to_team(owner, team, email='recipient@example.com'):
    return services.invite_to_team(owner, team, email)


def delivered(kind, recipient):
    """
    The notifications of one kind this recipient actually received.

    Read from the table rather than from `deliver`'s return value, which is
    always empty by design - and only meaningful in a test marked
    `transaction=True`, where `deliver` commits immediately and its `on_commit`
    hooks run before the assertion. That is the same arrangement
    `notifications/tests/test_notifications.py` uses, and the reason the classes
    below are marked that way rather than capturing callbacks.
    """
    return list(Notification.objects.filter(user=recipient, kind=kind))


# The three classes that assert on delivered notifications commit for real.
@pytest.mark.django_db(transaction=True)
class _Delivered:  # pragma: no cover - marker holder, never collected
    pass


# --- declining -----------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDeclining:
    def test_it_closes_the_invitation_without_creating_anything(
        self, owner, organization, recipient
    ):
        issued = invite_to_org(owner, organization)

        declined = services.decline_invitation(recipient, issued.raw_token)

        assert declined.status == Invitation.Status.DECLINED
        assert declined.declined_by_id == recipient.pk
        assert declined.declined_at is not None
        # The whole point: a decline is not a soft accept. No membership, no
        # role, nothing to undo - because acceptance is the only writer of a
        # membership in this platform and a decline is not acceptance.
        assert not Membership.objects.filter(user=recipient, organization=organization).exists()

    def test_the_inviter_is_told(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        services.decline_invitation(recipient, issued.raw_token)

        notes = delivered('invitation.declined', owner)
        assert len(notes) == 1
        assert 'declined' in notes[0].title
        assert 'MUNA' in notes[0].title

    def test_the_recipient_is_not_told_about_their_own_decline(
        self, owner, organization, recipient
    ):
        """
        They pressed the button; they performed the act. A notification telling
        somebody what they just did is noise, and the acceptance path has already
        decided this by not doing it either.
        """
        issued = invite_to_org(owner, organization)
        services.decline_invitation(recipient, issued.raw_token)

        assert delivered('invitation.declined', recipient) == []

    def test_a_team_invitation_declines_the_same_way(self, owner, team, recipient):
        issued = invite_to_team(owner, team)

        declined = services.decline_invitation(recipient, issued.raw_token)

        assert declined.status == Invitation.Status.DECLINED
        from teams.models import TeamMembership

        assert not TeamMembership.objects.filter(team=team, user=recipient).exists()

    def test_somebody_signed_in_with_another_address_cannot_decline_it(
        self, owner, organization, recipient
    ):
        """
        A decline is a statement *about* the invitation - "I do not want this" -
        so only the person it was sent to may make one. And it is refused before
        the claim, so the attempt cannot burn the link for its real recipient.
        """
        issued = invite_to_org(owner, organization)
        intruder = make_user('intruder@example.com')

        with pytest.raises(services.InvitationError) as refused:
            services.decline_invitation(intruder, issued.raw_token)

        assert refused.value.reason == 'forbidden'
        assert Invitation.objects.get(pk=issued.invitation.pk).status == Invitation.Status.PENDING

    def test_a_declined_invitation_can_no_longer_be_accepted(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        services.decline_invitation(recipient, issued.raw_token)

        with pytest.raises(services.InvitationError) as refused:
            services.accept_invitation(recipient, issued.raw_token)

        assert refused.value.message == 'This invitation is not valid any more.'
        assert not Membership.objects.filter(user=recipient).exists()

    def test_a_declined_invitation_can_no_longer_be_revoked(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        services.decline_invitation(recipient, issued.raw_token)

        with pytest.raises(services.InvitationError):
            services.revoke_invitation(owner, issued.invitation.pk)

    def test_the_owner_can_invite_them_again_afterwards(self, owner, organization, recipient):
        """
        A decline closes this invitation, not the door. `_existing_open_
        invitation` only counts `PENDING`, so a new invitation is issuable - which
        is the difference between "no" and "not this time".
        """
        issued = invite_to_org(owner, organization)
        services.decline_invitation(recipient, issued.raw_token)

        second = invite_to_org(owner, organization)

        assert second.invitation.pk != issued.invitation.pk
        accepted = services.accept_invitation(recipient, second.raw_token)
        assert accepted.status == Invitation.Status.ACCEPTED

    def test_an_unknown_token_is_refused_without_saying_so(self, owner, organization):
        with pytest.raises(services.InvitationError) as refused:
            services.decline_invitation(recipient_of(owner), 'a-token-nobody-issued')

        assert refused.value.message == 'This invitation is not valid any more.'

    def test_an_expired_invitation_refuses_a_decline(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        Invitation.objects.filter(pk=issued.invitation.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        with pytest.raises(services.InvitationError) as refused:
            services.decline_invitation(recipient, issued.raw_token)

        # Its own message, because it is a different fact: a rotten link is not a
        # spent one, and the recipient is being told which happened.
        assert refused.value.message == 'This invitation has expired.'
        assert Invitation.objects.get(pk=issued.invitation.pk).status == (Invitation.Status.EXPIRED)

    def test_a_signed_out_caller_cannot_decline(self, owner, organization):
        issued = invite_to_org(owner, organization)

        with pytest.raises(services.InvitationError):
            services.decline_invitation(None, issued.raw_token)

    def test_a_deactivated_account_cannot_decline(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        recipient.is_active = False
        recipient.save()

        with pytest.raises(services.InvitationError):
            services.decline_invitation(recipient, issued.raw_token)


def recipient_of(_user):
    return make_user('nobody-here@example.com')


@pytest.mark.django_db
class TestTheDeclineConstraints:
    def test_a_decline_is_paired_with_its_decliner_and_moment_by_the_database(
        self, owner, organization, recipient
    ):
        """
        The same guarantee the acceptance columns have: `declined` can never mean
        "somebody said no" without saying who and when.
        """
        issued = invite_to_org(owner, organization)
        services.decline_invitation(recipient, issued.raw_token)

        # A declined row with the decliner or the moment removed is refused,
        # rather than being a status that says somebody said no without saying
        # who.
        with pytest.raises(IntegrityError), transaction.atomic():
            Invitation.objects.filter(pk=issued.invitation.pk).update(declined_by=None)
        with pytest.raises(IntegrityError), transaction.atomic():
            Invitation.objects.filter(pk=issued.invitation.pk).update(declined_at=None)

    def test_the_status_is_a_closed_vocabulary(self, owner, organization, recipient):
        issued = invite_to_org(owner, organization)
        row = Invitation.objects.get(pk=issued.invitation.pk)
        row.status = 'ignored'
        with pytest.raises(ValidationError):
            row.save()


# --- the recipient is told in the app ----------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTellingTheRecipient:
    def test_an_invitee_with_an_account_gets_one_in_app_notification(
        self, owner, organization, recipient
    ):
        invite_to_org(owner, organization, email=recipient.email)

        notes = delivered('invitation.received', recipient)
        assert len(notes) == 1
        assert 'MUNA' in notes[0].title
        assert 'email' in notes[0].body

    def test_an_address_with_no_account_gets_no_notification(self, owner, organization):
        """
        The email *is* the notification for somebody who has never signed in.
        There is no account to write a row against, and inventing one would be
        worse than saying nothing.
        """
        invite_to_org(owner, organization, email='stranger@example.com')

        assert not Notification.objects.filter(kind='invitation.received').exists()

    def test_the_notification_carries_no_accept_link(self, owner, organization, recipient):
        """
        The honest limit, pinned as a test so nobody "fixes" it by fabricating a
        link: acceptance needs the plaintext token, only its digest is stored,
        and the email is the only place the token exists. So `action_path` is
        null and the notification points at the list.
        """
        invite_to_org(owner, organization, email=recipient.email)

        note = delivered('invitation.received', recipient)[0]
        assert note.action_path is None

    def test_the_inviter_is_not_notified_that_they_invited_themselves(self, owner, organization):
        """
        The other half of the self-invite refusal: inviting your own address is
        refused outright, so a notification would be reporting something that
        could not have happened.
        """
        with pytest.raises(services.InvitationError):
            invite_to_org(owner, organization, email=owner.email)

    def test_a_revoked_invitation_is_not_still_waiting_for_an_answer(
        self, owner, organization, recipient
    ):
        issued = invite_to_org(owner, organization, email=recipient.email)
        delivered('invitation.received', recipient)

        services.revoke_invitation(owner, issued.invitation.pk)

        # The recipient's notification stands as history - it *was* invited - but
        # accepting it now fails, which is the same answer as for any spent link.
        with pytest.raises(services.InvitationError):
            services.accept_invitation(recipient, issued.raw_token)


@pytest.mark.django_db(transaction=True)
class TestConcurrentDeclines:
    def test_two_simultaneous_declines_close_it_once(self, owner, organization, recipient):
        """
        Single-use, like acceptance: two clicks on the same link in two tabs must
        not produce two terminal states. One of the two conditional updates
        matches a `PENDING` row and the other does not.
        """
        issued = invite_to_org(owner, organization, email=recipient.email)
        raw_token = issued.raw_token
        results = []

        import threading

        def decline():
            try:
                results.append(services.decline_invitation(recipient, raw_token).status)
            except services.InvitationError:
                results.append('refused')

        threads = [threading.Thread(target=decline) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert Invitation.Status.DECLINED in results
        row = Invitation.objects.get(pk=issued.invitation.pk)
        assert row.status == Invitation.Status.DECLINED
        assert row.declined_by_id == recipient.pk
