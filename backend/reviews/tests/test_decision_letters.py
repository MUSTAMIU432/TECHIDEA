"""
Decision letters: an approval or a rejection reaches the owner only when an
administrator sends it; a changes request still goes straight to them.
"""

import json

import pytest
from django.core import mail

from administration import services as admin_services
from administration.authorization import AdministrationError
from administration.models import AdminAuditEntry
from ideas.models import Category, Idea
from identity.models import User
from identity.tokens import issue_access_token
from notifications.models import Notification
from reviews import decision_letters, proposals, services
from reviews.models import DecisionLetter, Review, ReviewCriterionAssessment
from reviews.tests.platform import grant_permission, grant_platform_reviewer, submit

PASSWORD = 'a-strong-unique-pass-1'
CRITERIA = [c.value for c in ReviewCriterionAssessment.Criterion]
QUERY_IDEA = 'query($i: ID!){ idea(id:$i){ status } }'


def make_user(email, first='Test'):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name=first,
        last_name='User',
        phone_number='+255712345678',
    )


def fresh(user):
    return User.objects.get(pk=user.pk)


@pytest.fixture
def gql(client):
    def post(query, variables=None, user=None):
        headers = {}
        if user is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {issue_access_token(user.pk)[0]}'
        response = client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables or {}}),
            content_type='application/json',
            **headers,
        )
        body = response.json()
        assert 'errors' not in body, body['errors']
        return body['data']

    return post


@pytest.fixture
def owner(db):
    return make_user('owner@example.com', first='Amina')


@pytest.fixture
def reviewer(db):
    user = make_user('reviewer@example.com')
    grant_platform_reviewer(user, console=False)
    return fresh(user)


@pytest.fixture
def admin(db):
    user = make_user('admin@example.com')
    admin_services.grant_platform_admin(user.email)
    grant_permission(user, 'administration.release_proposals')
    return fresh(user)


@pytest.fixture
def idea(owner):
    draft = Idea.objects.create(
        author=owner,
        title='Tax collection',
        description='Taxes are collected on paper and reconciled by hand.',
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        visibility=Idea.Visibility.PRIVATE,
        category=Category.objects.get_or_create(name='Letters Fixture')[0],
    )
    return submit(draft)


def decide(reviewer, idea, decision, feedback='A reason.'):
    services.start_review(reviewer, idea.pk)
    review = Review.objects.get(idea=idea, completed_at=None)
    services.complete_review(
        reviewer,
        services.CompleteReviewInput(
            idea_id=idea.pk,
            review_id=review.pk,
            decision=decision,
            feedback=feedback,
            assessments=tuple(services.AssessmentInput(c, 'meets', '') for c in CRITERIA),
        ),
    )
    return DecisionLetter.objects.filter(review=review).first()


def owner_kinds(owner):
    return list(Notification.objects.filter(user=owner).values_list('kind', flat=True))


@pytest.mark.django_db(transaction=True)
class TestTheOwnerWaitsForTheLetter:
    def test_an_approval_is_held_until_an_administrator_sends_it(
        self, gql, owner, reviewer, admin, idea
    ):
        letter = decide(reviewer, idea, 'approved')

        assert letter.status == DecisionLetter.Status.PENDING
        assert letter.message.startswith('Dear Amina,\n\nCongratulations!')
        # The owner hears nothing and still sees the idea under review...
        assert owner_kinds(owner) == []
        assert gql(QUERY_IDEA, {'i': str(idea.pk)}, user=owner)['idea']['status'] == (
            'UNDER_REVIEW'
        )
        # ...while the administrators are told a letter is waiting.
        assert 'review.decision_awaiting_release' in list(
            Notification.objects.filter(user=admin).values_list('kind', flat=True)
        )

        decision_letters.send(admin, letter.pk, letter.message + '\n\nP.S. Well done!')

        letter.refresh_from_db()
        assert letter.status == DecisionLetter.Status.SENT
        assert letter.sent_by == admin
        assert letter.message.endswith('P.S. Well done!')
        assert owner_kinds(owner) == ['idea.platform_approved']
        assert gql(QUERY_IDEA, {'i': str(idea.pk)}, user=owner)['idea']['status'] == 'APPROVED'
        sent = gql(
            'query($i: ID!){ ideaDecisionLetter(ideaId:$i){ decision message } }',
            {'i': str(idea.pk)},
            user=owner,
        )['ideaDecisionLetter']
        assert sent['decision'] == 'approved'
        assert sent['message'].endswith('P.S. Well done!')
        assert AdminAuditEntry.objects.filter(
            action=AdminAuditEntry.Action.DECISION_LETTER_SENT, actor=admin
        ).exists()

    def test_the_email_is_a_styled_announcement_without_the_letter(
        self, owner, reviewer, admin, idea
    ):
        letter = decide(reviewer, idea, 'rejected', 'The volumes are too small to automate.')
        mail.outbox.clear()

        decision_letters.send(admin, letter.pk)

        assert len(mail.outbox) == 1
        email = mail.outbox[0]
        assert email.to == [owner.email]
        assert 'An update on your idea' in email.subject
        html = email.alternatives[0][0]
        assert 'Read your letter' in html
        # The letter quotes the reviewer; the inbox copy does not.
        assert 'The volumes are too small' not in email.body
        assert 'The volumes are too small' not in html

    def test_a_rejection_letter_quotes_the_reviewer_kindly(self, reviewer, idea):
        letter = decide(reviewer, idea, 'rejected', 'The volumes are too small to automate.')

        assert 'we are sorry to tell you' in letter.message
        assert 'The volumes are too small to automate.' in letter.message
        assert 'we would genuinely welcome your next one' in letter.message

    def test_the_owner_does_not_see_the_deciding_round_or_the_report_until_sent(
        self, gql, owner, reviewer, admin, idea
    ):
        letter = decide(reviewer, idea, 'approved')
        rounds = 'query($i: ID!){ ideaReviews(ideaId:$i){ id } }'
        report = 'query($i: ID!){ ideaReviewReport(ideaId:$i){ id } }'

        assert gql(rounds, {'i': str(idea.pk)}, user=owner)['ideaReviews'] == []
        assert gql(report, {'i': str(idea.pk)}, user=owner)['ideaReviewReport'] is None

        decision_letters.send(admin, letter.pk)

        assert len(gql(rounds, {'i': str(idea.pk)}, user=owner)['ideaReviews']) == 1
        assert gql(report, {'i': str(idea.pk)}, user=owner)['ideaReviewReport'] is not None

    def test_sending_the_approval_tells_the_review_team_to_write_the_proposal(
        self, reviewer, admin, idea
    ):
        letter = decide(reviewer, idea, 'approved')
        assert not Notification.objects.filter(
            user=reviewer, kind='proposal.writing_opened'
        ).exists()

        decision_letters.send(admin, letter.pk)

        notice = Notification.objects.get(user=reviewer, kind='proposal.writing_opened')
        assert 'Tax collection' in notice.title

    def test_a_rejection_opens_no_proposal(self, reviewer, admin, idea):
        letter = decide(reviewer, idea, 'rejected', 'Too small.')
        decision_letters.send(admin, letter.pk)

        assert not Notification.objects.filter(kind='proposal.writing_opened').exists()

    def test_a_changes_request_still_goes_straight_to_the_owner(self, owner, reviewer, idea):
        letter = decide(reviewer, idea, 'changes_requested', 'Please attach the forms.')

        assert letter is None
        assert owner_kinds(owner) == ['idea.platform_changes_requested']


@pytest.mark.django_db
class TestSendingRules:
    def test_only_an_administrator_who_did_not_decide_it_sends_it(self, reviewer, admin, idea):
        letter = decide(reviewer, idea, 'approved')

        with pytest.raises(AdministrationError):
            decision_letters.send(reviewer, letter.pk)  # not an administrator
        grant_permission(reviewer, 'administration.access_console')
        grant_permission(reviewer, 'administration.release_proposals')
        with pytest.raises(AdministrationError, match='You decided this review'):
            decision_letters.send(fresh(reviewer), letter.pk)

        decision_letters.send(admin, letter.pk)
        with pytest.raises(AdministrationError, match='already been sent'):
            decision_letters.send(admin, letter.pk)

    def test_an_empty_letter_is_refused(self, reviewer, admin, idea):
        letter = decide(reviewer, idea, 'approved')

        with pytest.raises(AdministrationError, match='Write the letter'):
            decision_letters.send(admin, letter.pk, '   ')

    def test_the_proposal_waits_for_the_admin_to_confirm_the_approval(self, reviewer, admin, idea):
        letter = decide(reviewer, idea, 'approved')

        # Approved, but not confirmed: the team cannot start writing yet...
        with pytest.raises(proposals.ProposalError, match='has not confirmed this approval'):
            proposals.start_proposal(reviewer, idea.pk)
        # ...and nothing could be released to the owner either.
        with pytest.raises(proposals.ProposalError, match='approval letter'):
            proposals.release(admin, idea.pk)

        decision_letters.send(admin, letter.pk)

        assert proposals.start_proposal(reviewer, idea.pk).status == 'draft'

    def test_the_console_list_is_for_letter_senders_only(self, reviewer, admin, idea):
        decide(reviewer, idea, 'approved')

        assert [letter.idea_id for letter in decision_letters.list_letters(admin)] == [idea.pk]
        assert decision_letters.list_letters(reviewer) == []
        assert decision_letters.list_letters(None) == []
