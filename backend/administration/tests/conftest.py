"""
Fixtures for the administration console's tests.

One realistic platform, built through the domains' own services so every row
is one a real user could have produced:

- Acme: an Owner, an author, a Reviewer and a plain member; Globex: its Owner.
- Four ideas by Acme's author: a PUBLIC one approved by the reviewer, an
  ORGANIZATION one sent back for changes (with evidence and a comment), an
  ORGANIZATION one under review (open round), and a PRIVATE draft.
- Three administrators: `admin` (the "Platform administrators" group, every
  console permission), `limited_admin` (ACCESS_CONSOLE only) and a superuser.
"""

import json

import pytest
from django.contrib.auth.models import Permission as AuthPermission
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile

from administration import services as admin_services
from ideas import services as idea_services
from ideas.models import Category, Comment, Idea
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import services as review_services
from reviews.models import ReviewCriterionAssessment
from reviews.tests.platform import confirm_for_organization, grant_platform_reviewer

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'
PDF_BYTES = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer\n<< >>\n%%EOF'
PRIVATE_TITLE = 'Secret salary spreadsheet'


def make_user(email, **extra):
    return User.objects.create_user(
        email=email,
        first_name=extra.pop('first_name', 'Test'),
        last_name=extra.pop('last_name', 'User'),
        phone_number='+255712345678',
        password=VALID_PASSWORD,
        **extra,
    )


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


def grant(user, *codenames):
    user.user_permissions.add(
        *AuthPermission.objects.filter(
            content_type__app_label='administration', codename__in=codenames
        )
    )
    # Django caches permissions on the instance.
    return User.objects.get(pk=user.pk)


def complete(reviewer, idea, review, decision, feedback=''):
    return review_services.complete_review(
        reviewer,
        review_services.CompleteReviewInput(
            idea_id=idea.pk,
            review_id=review.pk,
            decision=decision,
            feedback=feedback,
            assessments=tuple(
                review_services.AssessmentInput(
                    criterion=criterion, rating='meets', note=f'{criterion} note'
                )
                for criterion in ReviewCriterionAssessment.Criterion.values
            ),
        ),
    )


def submitted_idea(
    author, organization, category, title, visibility, *, evidence=False, confirmer=None
):
    """
    An idea the **platform** has received, walked through the whole journey.

    Three acts by three different people, and all three are needed: the author
    submits to their organization, the organization confirms it, and the author -
    not the organization - submits it on. A fixture that stopped after the first
    would leave the platform queue empty and every console test here would be
    about an organization review workspace instead.

    `confirm_for_organization` is the helper that does the middle act through a
    per-tenant Reviewer account, so no caller has to build one.
    """
    idea = idea_services.create_idea(
        author,
        organization.pk,
        idea_services.IdeaInput(
            title=title,
            description=DESCRIPTION,
            category_id=category.pk,
            visibility=visibility,
            current_process='We copy numbers by hand.',
        ),
    )
    if evidence:
        idea_services.upload_attachment(
            author,
            idea.pk,
            SimpleUploadedFile('evidence.pdf', PDF_BYTES, content_type='application/pdf'),
        )
    idea_services.submit_idea(author, idea.pk)
    idea.refresh_from_db()
    confirm_for_organization(idea, confirmer)
    idea.refresh_from_db()
    return idea_services.submit_to_platform(author, idea.pk)


@pytest.fixture(autouse=True)
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


@pytest.fixture
def world():
    acme_owner = make_user('owner@acme.example', first_name='Olive', last_name='Owner')
    acme = create_organization_for_user(
        acme_owner, CreateOrganizationInput(name='Acme')
    ).organization
    author = make_user('author@acme.example', first_name='Ada', last_name='Author')
    add_member(acme, author)
    reviewer = make_user('reviewer@acme.example', first_name='Rae', last_name='Reviewer')
    add_member(acme, reviewer, reviewer=True)
    member = make_user('member@acme.example')
    add_member(acme, member)
    globex_owner = make_user('owner@globex.example')
    globex = create_organization_for_user(
        globex_owner, CreateOrganizationInput(name='Globex')
    ).organization

    finance = Category.objects.create(name='Finance')

    # Every confirmation is made by **Acme's own reviewer**, the account this
    # world already has. The default helper account would work too, but it is a
    # ninth user and a fourth completed review, and both of those are asserted
    # elsewhere in this suite - a fixture that quietly changes somebody else's
    # totals makes those assertions weaker, not stronger.
    public_idea = submitted_idea(
        author, acme, finance, 'Public invoice run', 'public', confirmer=reviewer
    )
    review = review_services.start_review(reviewer, public_idea.pk)
    complete(reviewer, public_idea, review, 'approved', 'Clear and worth doing.')

    org_idea = submitted_idea(
        author,
        acme,
        finance,
        'Organization payroll',
        'organization',
        evidence=True,
        confirmer=reviewer,
    )
    review = review_services.start_review(reviewer, org_idea.pk)
    complete(reviewer, org_idea, review, 'changes_requested', 'Add the monthly volumes.')
    Comment.objects.create(idea=org_idea, author=member, content='We have this problem too.')

    open_idea = submitted_idea(
        author, acme, finance, 'Open procurement', 'organization', confirmer=reviewer
    )
    open_review = review_services.start_review(reviewer, open_idea.pk)

    private_idea = idea_services.create_idea(
        author,
        acme.pk,
        idea_services.IdeaInput(
            title=PRIVATE_TITLE, description='Very private words.', visibility='private'
        ),
    )

    admin = make_user('admin@platform.example', first_name='Pat', last_name='Admin')
    admin_services.grant_platform_admin(admin.email)
    admin = User.objects.get(pk=admin.pk)
    limited_admin = grant(make_user('support@platform.example'), 'access_console')
    superuser = User.objects.create_superuser(
        email='root@platform.example',
        password=VALID_PASSWORD,
        first_name='Root',
        last_name='User',
        phone_number='+255712345678',
    )

    return {
        'acme': acme,
        'globex': globex,
        'acme_owner': acme_owner,
        'author': author,
        'reviewer': reviewer,
        'member': member,
        'globex_owner': globex_owner,
        'finance': finance,
        'public_idea': Idea.objects.get(pk=public_idea.pk),
        'org_idea': Idea.objects.get(pk=org_idea.pk),
        'open_idea': Idea.objects.get(pk=open_idea.pk),
        'open_review': open_review,
        'private_idea': private_idea,
        'admin': admin,
        'limited_admin': limited_admin,
        'superuser': superuser,
    }


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
        assert response.status_code == 200, response.content
        body = response.json()
        assert 'errors' not in body, body['errors']
        return body['data']

    return post
