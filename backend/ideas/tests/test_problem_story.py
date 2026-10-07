"""
The problem story: the guided intake form's answers on `Idea`.

What is pinned here is the story's contract - every answer optional on a
draft, each validated for shape only, the closed vocabularies enforced by the
service *and* the database, the submission rule unchanged - and that the
answers travel the existing paths under the existing rules: written only by
the idea's author while it is editable, readable by exactly whoever may read
the idea, and frozen into a reviewer's snapshot with the rest of the idea.
"""

import json

import pytest
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from ideas import services
from ideas.dev_categories import seed_dev_categories
from ideas.models import IDEA_STORY_FIELDS, Attachment, Category, Idea
from ideas.services import IdeaError, IdeaInput
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import services as review_services
from reviews.tests.platform import confirm_for_organization, grant_platform_reviewer

DESCRIPTION = 'Every month our staff key payment forms into Excel by hand.'

STORY_FIELDS_QUERY = """
  id
  title
  description
  status
  currentProcess
  currentTools
  currentToolsOther
  performedBy
  affectedPeople
  frequency
  timeRequired
  peopleInvolved
  impacts
  impactDetails
  improvementGoal
  desiredOutcome
  easierForPeople
  expectedBenefit
  importantConsiderations
"""

CREATE_IDEA = f"""
mutation CreateIdea($input: CreateIdeaInput!) {{
  createIdea(input: $input) {{ success message field idea {{ {STORY_FIELDS_QUERY} }} }}
}}
"""

UPDATE_IDEA = f"""
mutation UpdateIdea($input: UpdateIdeaInput!) {{
  updateIdea(input: $input) {{ success message field idea {{ {STORY_FIELDS_QUERY} }} }}
}}
"""

SUBMIT_IDEA = """
mutation SubmitIdea($id: ID!) {
  submitIdea(id: $id) { success message idea { id status } }
}
"""

IDEA_QUERY = f"""
query Idea($id: ID!) {{
  idea(id: $id) {{ {STORY_FIELDS_QUERY} }}
}}
"""

# A complete story, as the form sends it over GraphQL.
FULL_STORY = {
    'currentProcess': 'Students fill a paper form; staff re-type it into Excel.',
    'currentTools': ['PAPER_FORMS', 'EXCEL'],
    'currentToolsOther': 'A shared notice board',
    'performedBy': 'Finance office staff',
    'affectedPeople': 'Students and finance staff',
    'frequency': 'MONTHLY',
    'timeRequired': 'Several days',
    'peopleInvolved': 6,
    'impacts': ['MISTAKES', 'WAITING'],
    'impactDetails': 'Students come back to the office when something is missing.',
    'improvementGoal': 'Collecting payment information should be quicker.',
    'desiredOutcome': 'Students submit their information once.',
    'easierForPeople': 'Staff should see what is missing straight away.',
    'expectedBenefit': 'Fewer mistakes and no repeat visits.',
    'importantConsiderations': 'Payment details are private.',
}


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password='a-strong-unique-pass-1',
    )


def make_member(email, organization_name):
    user = make_user(email)
    organization = create_organization_for_user(
        user, CreateOrganizationInput(name=organization_name)
    ).organization
    return user, organization


def add_member(organization, user, *, reviewer=False):
    membership = Membership.objects.create(user=user, organization=organization)
    if reviewer:
        MembershipRole.objects.create(
            membership=membership,
            role=Role.objects.get(organization=organization, slug=REVIEWER_ROLE_SLUG),
        )
        # Platform review is authorized by a platform-scoped permission and by
        # nothing else, so a reviewer built here is a *platform* reviewer too.
        # The organization Reviewer role above still governs the organization
        # review queue, which is a separate track with separate rules.
        grant_platform_reviewer(user)
    return membership


def run(client, query, root_field, variables=None, user=None):
    headers = {}
    if user is not None:
        headers['HTTP_AUTHORIZATION'] = f'Bearer {issue_access_token(user.pk)[0]}'
    body = client.post(
        '/graphql/',
        data=json.dumps({'query': query, 'variables': variables or {}}),
        content_type='application/json',
        **headers,
    ).json()
    assert 'errors' not in body, body
    return body['data'][root_field]


def graphql_errors(client, query, variables, user):
    body = client.post(
        '/graphql/',
        data=json.dumps({'query': query, 'variables': variables}),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {issue_access_token(user.pk)[0]}',
    ).json()
    return body.get('errors')


@pytest.fixture
def world():
    seed_dev_categories()
    author, acme = make_member('author@acme.example', 'Acme')
    colleague = make_user('colleague@acme.example')
    add_member(acme, colleague)
    reviewer = make_user('reviewer@acme.example')
    add_member(acme, reviewer, reviewer=True)
    outsider, other = make_member('outsider@other.example', 'Other Co')
    return {
        'author': author,
        'colleague': colleague,
        'reviewer': reviewer,
        'outsider': outsider,
        'acme': acme,
        'other': other,
        'finance': Category.objects.get(slug='finance'),
    }


def put_it_in_front_of_the_platform(client, world, idea_id):
    """
    Take an organization idea the rest of the way: its organization confirms it,
    then its author submits it to the platform.

    Two acts by two different people, which is why it is a helper rather than a
    line: a platform reviewer's snapshot is only meaningful for an idea the
    platform actually received, and walking both stages is what makes that true.
    """
    idea = Idea.objects.get(pk=idea_id)
    confirm_for_organization(idea)
    idea.refresh_from_db()
    services.submit_to_platform(idea.author, idea.pk)
    idea.refresh_from_db()
    return idea


def create_via_api(client, world, story=None, **idea):
    fields = {
        'title': 'Payment forms are re-typed by hand',
        'description': DESCRIPTION,
        'categoryId': str(world['finance'].pk),
        'visibility': 'ORGANIZATION',
        **(FULL_STORY if story is None else story),
        **idea,
    }
    return run(
        client,
        CREATE_IDEA,
        'createIdea',
        {
            'input': {
                'submissionContext': 'ORGANIZATION',
                'organizationId': str(world['acme'].pk),
                'idea': fields,
            }
        },
        user=world['author'],
    )


# --- the model --------------------------------------------------------------------------


@pytest.mark.django_db
class TestModel:
    def test_a_new_idea_has_an_unanswered_story(self, world):
        idea = Idea.objects.create(organization=world['acme'], author=world['author'], title='T')

        assert idea.current_tools == []
        assert idea.impacts == []
        assert idea.frequency == ''
        assert idea.people_involved is None
        assert idea.current_process == ''
        assert idea.expected_benefit == ''

    def test_the_story_field_list_names_real_columns(self):
        columns = {f.name for f in Idea._meta.get_fields()}

        assert set(IDEA_STORY_FIELDS) <= columns
        # `proposed_solution` is never asked of the author.
        assert 'proposed_solution' not in IDEA_STORY_FIELDS

    @pytest.mark.parametrize(
        ('column', 'value'),
        [
            ('frequency', 'hourly'),
            ('impacts', ['mistakes', 'boredom']),
            ('current_tools', ['excel', 'mainframe']),
        ],
    )
    def test_the_database_refuses_an_unknown_value(self, world, column, value):
        idea = Idea.objects.create(organization=world['acme'], author=world['author'], title='T')

        # `update()` skips `full_clean()`, so this is the CHECK constraint alone.
        with pytest.raises(IntegrityError), transaction.atomic():
            Idea.objects.filter(pk=idea.pk).update(**{column: value})

    def test_the_database_refuses_a_negative_headcount(self, world):
        idea = Idea.objects.create(organization=world['acme'], author=world['author'], title='T')

        with pytest.raises(IntegrityError), transaction.atomic():
            Idea.objects.filter(pk=idea.pk).update(people_involved=-1)


# --- the service ------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateWithAStory:
    def test_a_draft_needs_nothing_but_a_title(self, world):
        idea = services.create_idea(
            world['author'], world['acme'].pk, IdeaInput(title='Just a name')
        )

        assert idea.status == Idea.Status.DRAFT
        assert idea.description == ''
        assert idea.current_process == ''

    def test_a_partial_story_is_saved(self, world):
        idea = services.create_idea(
            world['author'],
            world['acme'].pk,
            IdeaInput(title='Half written', frequency='weekly', impacts=['waiting']),
        )

        idea.refresh_from_db()
        assert idea.frequency == 'weekly'
        assert idea.impacts == ['waiting']
        assert idea.performed_by == ''

    def test_answers_are_normalized(self, world):
        idea = services.create_idea(
            world['author'],
            world['acme'].pk,
            IdeaInput(
                title='Normalized',
                current_process='  Step one\r\nStep two  ',
                # Repeated, out of order, and in the wrong case.
                current_tools=['EMAIL', 'paper_forms', 'email'],
                impacts=['waiting', 'too_much_time'],
                frequency='DAILY',
                people_involved='12',
            ),
        )

        idea.refresh_from_db()
        assert idea.current_process == 'Step one\nStep two'
        assert idea.current_tools == ['paper_forms', 'email']
        assert idea.impacts == ['too_much_time', 'waiting']
        assert idea.frequency == 'daily'
        assert idea.people_involved == 12

    @pytest.mark.parametrize(
        ('overrides', 'field'),
        [
            ({'frequency': 'hourly'}, 'frequency'),
            ({'impacts': ['boredom']}, 'impacts'),
            ({'current_tools': ['mainframe']}, 'current_tools'),
            ({'people_involved': -1}, 'people_involved'),
            ({'people_involved': 1_000_001}, 'people_involved'),
            ({'people_involved': 'a few'}, 'people_involved'),
            ({'performed_by': 'x' * 301}, 'performed_by'),
            ({'time_required': 'x' * 121}, 'time_required'),
            ({'current_tools_other': 'x' * 201}, 'current_tools_other'),
            ({'current_process': 'x' * 5001}, 'current_process'),
            ({'expected_benefit': 'x' * 5001}, 'expected_benefit'),
        ],
    )
    def test_a_malformed_answer_is_refused_against_its_field(self, world, overrides, field):
        with pytest.raises(IdeaError) as refused:
            services.create_idea(
                world['author'], world['acme'].pk, IdeaInput(title='Bad', **overrides)
            )

        assert refused.value.field == field
        assert Idea.objects.count() == 0

    def test_a_long_answer_at_the_limit_is_kept_whole(self, world):
        idea = services.create_idea(
            world['author'],
            world['acme'].pk,
            IdeaInput(title='Long', desired_outcome='x' * services.MAX_STORY_ANSWER_LENGTH),
        )

        assert len(idea.desired_outcome) == services.MAX_STORY_ANSWER_LENGTH

    def test_a_non_member_cannot_file_a_story_into_an_organization(self, world):
        with pytest.raises(IdeaError):
            services.create_idea(
                world['outsider'], world['acme'].pk, IdeaInput(title='Nope', frequency='daily')
            )

        assert Idea.objects.count() == 0


@pytest.mark.django_db
class TestUpdateTheStory:
    def test_the_author_rewrites_the_story(self, world):
        idea = services.create_idea(
            world['author'],
            world['acme'].pk,
            IdeaInput(title='T', frequency='daily', impacts=['mistakes'], performed_by='Staff'),
        )

        services.update_idea(
            world['author'],
            idea.pk,
            IdeaInput(title='T', frequency='weekly', impacts=['delays', 'overload']),
        )

        idea.refresh_from_db()
        assert idea.frequency == 'weekly'
        assert idea.impacts == ['delays', 'overload']
        # The whole input is written: an answer left out is cleared.
        assert idea.performed_by == ''

    def test_a_colleague_cannot_edit_the_story(self, world):
        idea = services.create_idea(
            world['author'],
            world['acme'].pk,
            IdeaInput(title='T', performed_by='Staff'),
        )

        with pytest.raises(IdeaError, match='Idea is unavailable'):
            services.update_idea(
                world['colleague'], idea.pk, IdeaInput(title='T', performed_by='Me')
            )

        idea.refresh_from_db()
        assert idea.performed_by == 'Staff'

    def test_the_story_is_fixed_while_under_review(self, world):
        idea = services.create_idea(
            world['author'],
            world['acme'].pk,
            IdeaInput(
                title='T',
                description=DESCRIPTION,
                category_id=world['finance'].pk,
            ),
        )
        services.submit_idea(world['author'], idea.pk)

        with pytest.raises(IdeaError, match='Only a draft can be edited'):
            services.update_idea(
                world['author'], idea.pk, IdeaInput(title='T', desired_outcome='Changed')
            )

    def test_a_refused_answer_leaves_the_saved_story_alone(self, world):
        idea = services.create_idea(
            world['author'], world['acme'].pk, IdeaInput(title='T', performed_by='Staff')
        )

        with pytest.raises(IdeaError):
            services.update_idea(
                world['author'], idea.pk, IdeaInput(title='T', performed_by='New', frequency='?')
            )

        idea.refresh_from_db()
        assert idea.performed_by == 'Staff'


@pytest.mark.django_db
class TestSubmissionRuleIsUnchanged:
    def test_a_story_is_not_required_to_submit(self, world):
        idea = services.create_idea(
            world['author'],
            world['acme'].pk,
            IdeaInput(
                title='T',
                description=DESCRIPTION,
                category_id=world['finance'].pk,
            ),
        )

        submitted = services.submit_idea(world['author'], idea.pk)

        # The idea was filed for an organization, so submission is submission
        # *to that organization* - the one stage that comes before the platform.
        # Which stage it is has nothing to do with the story: the rule under test
        # is that a guided story is not required, and it does not change.
        assert submitted.status == Idea.Status.SUBMITTED_TO_ORGANIZATION

    @pytest.mark.parametrize(
        ('overrides', 'message'),
        [
            ({'description': 'Too short'}, 'at least 20 characters'),
            ({'category_id': None}, 'Choose a category'),
        ],
    )
    def test_a_full_story_does_not_stand_in_for_the_required_fields(
        self, world, overrides, message
    ):
        fields = {
            'title': 'T',
            'description': DESCRIPTION,
            'category_id': world['finance'].pk,
            'current_process': 'A long and detailed account of what happens today.',
            'desired_outcome': 'Something better.',
            **overrides,
        }
        idea = services.create_idea(world['author'], world['acme'].pk, IdeaInput(**fields))

        with pytest.raises(IdeaError, match=message):
            services.submit_idea(world['author'], idea.pk)

        idea.refresh_from_db()
        assert idea.status == Idea.Status.DRAFT


# --- GraphQL ----------------------------------------------------------------------------


@pytest.mark.django_db
class TestGraphQL:
    def test_create_and_read_back_the_whole_story(self, client, world):
        created = create_via_api(client, world)
        assert created['success'] is True, created

        idea = run(client, IDEA_QUERY, 'idea', {'id': created['idea']['id']}, user=world['author'])

        for key, value in FULL_STORY.items():
            assert idea[key] == value, key

    def test_an_unanswered_story_reads_as_blank(self, client, world):
        created = create_via_api(client, world, story={})

        idea = created['idea']
        assert idea['currentTools'] == []
        assert idea['impacts'] == []
        assert idea['frequency'] is None
        assert idea['peopleInvolved'] is None
        assert idea['currentProcess'] == ''

    def test_the_enums_refuse_an_unknown_value_before_the_service(self, client, world):
        variables = {
            'input': {
                'submissionContext': 'ORGANIZATION',
                'organizationId': str(world['acme'].pk),
                'idea': {'title': 'T', 'frequency': 'HOURLY'},
            }
        }

        assert graphql_errors(client, CREATE_IDEA, variables, world['author'])
        assert Idea.objects.count() == 0

    def test_a_service_refusal_names_its_field(self, client, world):
        created = create_via_api(client, world, story={'peopleInvolved': -3})

        assert created['success'] is False
        assert created['field'] == 'people_involved'
        assert Idea.objects.count() == 0

    def test_update_through_the_api(self, client, world):
        created = create_via_api(client, world)
        fields = {
            'title': 'Payment forms are re-typed by hand',
            'description': DESCRIPTION,
            'frequency': 'WEEKLY',
            'impacts': ['DELAYS'],
        }

        updated = run(
            client,
            UPDATE_IDEA,
            'updateIdea',
            {'input': {'id': created['idea']['id'], 'idea': fields}},
            user=world['author'],
        )

        assert updated['success'] is True, updated
        assert updated['idea']['frequency'] == 'WEEKLY'
        assert updated['idea']['impacts'] == ['DELAYS']
        assert updated['idea']['currentProcess'] == ''

    def test_a_filed_story_can_be_submitted(self, client, world):
        created = create_via_api(client, world)

        submitted = run(
            client, SUBMIT_IDEA, 'submitIdea', {'id': created['idea']['id']}, user=world['author']
        )

        assert submitted['success'] is True, submitted
        assert submitted['idea']['status'] == 'SUBMITTED_TO_ORGANIZATION'

    def test_categories_for_the_form_still_come_from_the_categories_query(self, client, world):
        categories = run(client, 'query { categories { id slug } }', 'categories')

        assert str(world['finance'].pk) in {c['id'] for c in categories}


# --- who can read the story ---------------------------------------------------------------


@pytest.mark.django_db
class TestReadingTheStory:
    def test_an_organization_idea_is_readable_by_a_colleague(self, client, world):
        created = create_via_api(client, world, visibility='ORGANIZATION')
        # A draft is its author's alone; the audience applies once it is submitted.
        from django.utils import timezone

        Idea.objects.filter(pk=created['idea']['id']).update(
            status=Idea.Status.SUBMITTED, submitted_at=timezone.now()
        )

        idea = run(
            client, IDEA_QUERY, 'idea', {'id': created['idea']['id']}, user=world['colleague']
        )

        assert idea['importantConsiderations'] == FULL_STORY['importantConsiderations']

    def test_an_organization_idea_is_not_readable_by_another_tenant(self, client, world):
        created = create_via_api(client, world, visibility='ORGANIZATION')

        idea = run(
            client, IDEA_QUERY, 'idea', {'id': created['idea']['id']}, user=world['outsider']
        )

        assert idea is None

    def test_a_draft_is_not_readable_by_a_colleague(self, client, world):
        created = create_via_api(client, world)

        idea = run(
            client, IDEA_QUERY, 'idea', {'id': created['idea']['id']}, user=world['colleague']
        )

        assert idea is None

    def test_an_anonymous_caller_reads_nothing(self, client, world):
        created = create_via_api(client, world)

        assert run(client, IDEA_QUERY, 'idea', {'id': created['idea']['id']}) is None


# --- the reviewer's snapshot --------------------------------------------------------------


@pytest.mark.django_db
class TestReviewSnapshot:
    def test_the_snapshot_freezes_the_story(self, client, world):
        created = create_via_api(client, world)
        idea_id = created['idea']['id']
        run(client, SUBMIT_IDEA, 'submitIdea', {'id': idea_id}, user=world['author'])
        put_it_in_front_of_the_platform(client, world, idea_id)

        review = review_services.start_review(world['reviewer'], idea_id)

        story = review.submission_snapshot['story']
        assert set(story) == set(IDEA_STORY_FIELDS)
        assert story['current_tools'] == ['paper_forms', 'excel']
        assert story['frequency'] == 'monthly'
        assert story['people_involved'] == 6
        assert story['expected_benefit'] == FULL_STORY['expectedBenefit']


# --- supporting documents -----------------------------------------------------------------

PDF_BYTES = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer\n<< >>\n%%EOF'


@pytest.fixture
def _isolated_attachment_storage(tmp_path, settings):
    settings.STORAGES = {
        **settings.STORAGES,
        'attachments': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
            'OPTIONS': {'location': str(tmp_path)},
        },
    }
    storages._storages.clear()
    yield
    storages._storages.clear()


@pytest.mark.django_db
@pytest.mark.usefixtures('_isolated_attachment_storage')
class TestSupportingDocuments:
    """The form's order: create the draft, attach its evidence, then submit."""

    def upload(self, client, idea_id, user):
        return client.post(
            f'/ideas/{idea_id}/attachments/',
            data={'file': SimpleUploadedFile('form.pdf', PDF_BYTES, 'application/pdf')},
            HTTP_AUTHORIZATION=f'Bearer {issue_access_token(user.pk)[0]}',
        )

    def test_evidence_attached_to_the_new_draft_survives_submission(self, client, world):
        idea_id = create_via_api(client, world)['idea']['id']

        assert self.upload(client, idea_id, world['author']).status_code == 201
        submitted = run(client, SUBMIT_IDEA, 'submitIdea', {'id': idea_id}, user=world['author'])

        assert submitted['idea']['status'] == 'SUBMITTED_TO_ORGANIZATION'
        assert Attachment.objects.filter(idea_id=idea_id).count() == 1

    def test_only_the_author_may_attach_to_the_draft(self, client, world):
        idea_id = create_via_api(client, world, visibility='ORGANIZATION')['idea']['id']

        response = self.upload(client, idea_id, world['colleague'])

        assert response.json()['success'] is False
        assert Attachment.objects.count() == 0
