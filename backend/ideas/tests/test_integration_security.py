"""
Ideas domain integration and security boundaries (S2-008).

Every other file in this directory tests one feature. This one tests the
seams *between* them - the places where a boundary enforced by one layer has
to be honoured by another:

- two organizations, each looking at the other's ideas, in both directions;
- GraphQL and the attachment HTTP endpoints, which must agree about who may
  read what because they share one authorization model;
- secondary resources (comments, votes, attachments), which are only ever
  authorized through their idea and must not be a way round it;
- lifecycle and visibility, which are independent axes - a status never
  grants readability;
- a member who leaves, and an account that is deactivated while its token is
  still cryptographically valid.

It deliberately does not repeat the single-feature cases the per-feature
suites already pin. The fixture below builds one world and the tests walk
attack paths through it.

Tokens are issued directly with `identity.tokens.issue_access_token` rather
than by signing in, so the world can hold many users without tripping the
login throttle. They are real signed access tokens and are resolved by the
same `identity.authentication.get_authenticated_user` a signed-in session
goes through - that function, not the way a token was obtained, is the
boundary under test.
"""

import json
from dataclasses import dataclass

import pytest
from django.core.files.storage import FileSystemStorage, storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from ideas import services
from ideas.models import Attachment, Comment, Idea, Vote
from ideas.services import submit_idea
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'
PDF_BYTES = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\ntrailer\n<< >>\n%%EOF'


# --- world ------------------------------------------------------------------------------


def make_user(email):
    return User.objects.create_user(
        email=email,
        password=VALID_PASSWORD,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
    )


def make_organization(name, owner):
    from organizations.services import CreateOrganizationInput, create_organization_for_user

    return create_organization_for_user(owner, CreateOrganizationInput(name=name)).organization


def add_member(organization, user):
    return Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )


def make_idea(organization, author, visibility, *, status=Idea.Status.DRAFT, title='An idea'):
    """
    An idea row, with the category submission requires.

    The category is here rather than at the call sites because two of the paths
    below submit for real, and "Choose a category before submitting this idea"
    would be the first thing to fail in a test about tenancy.
    """
    from ideas.models import Category

    category, _ = Category.objects.get_or_create(name='Integration Fixture')
    return Idea.objects.create(
        organization=organization,
        author=author,
        title=title,
        description=DESCRIPTION,
        category=category,
        visibility=visibility,
        status=status,
        submitted_at=None if status == Idea.Status.DRAFT else timezone.now(),
    )


def make_submitted_platform_idea(organization, author, visibility):
    """
    An idea the platform has actually been sent, walked through the journey.

    Not `make_idea(..., status=SUBMITTED)`: that writes a row which claims to
    have been submitted without ever freezing a submission version, and the
    platform reviewer who must be able to *read* such an idea cannot - the
    visibility filter looks for the version. Every test that needs a
    platform-reviewer-vs-idea question to reach the authorization check rather
    than the visibility one has to build it this way.
    """
    from ideas.services import submit_to_platform
    from reviews.tests.platform import confirm_for_organization

    idea = make_idea(organization, author, visibility)
    submit_idea(author, idea.pk)
    idea.refresh_from_db()
    confirm_for_organization(idea)
    idea.refresh_from_db()
    submit_to_platform(author, idea.pk)
    idea.refresh_from_db()
    return idea


def make_private_submitted_idea(tenant):
    """
    A `PRIVATE` idea that really reached the platform - by writing the row,
    because a private idea cannot be submitted at all.

    That is the point being tested elsewhere in this file, and it means this row
    is the one case in the suite that has to be manufactured: it is the state the
    submission rule exists to prevent, holding it still so the *readability* rule
    can be checked against it. An idea like this can only exist because S3-008
    predates the rule.
    """
    idea = make_idea(tenant.organization, tenant.author, Idea.Visibility.PRIVATE)
    # Written directly, and that is the whole point of the helper: the state is
    # now unreachable on purpose. `submit_idea` refuses a private idea, and so
    # would `submit_to_platform`, because a reviewer who could not read it would
    # be waiting for it in vain. This row exists only so the *readability* rule
    # can be checked against an idea that claims to be waiting - which is the
    # situation S3-008 was written for, and the one a reader has to get right.
    idea.status = Idea.Status.SUBMITTED
    idea.submitted_at = timezone.now()
    idea.save()
    return idea


def attach(idea):
    """An attachment written through the real service, so it has real bytes."""
    upload = SimpleUploadedFile('evidence.pdf', PDF_BYTES, content_type='application/pdf')
    return services.upload_attachment(idea.author, idea.pk, upload)


def token_for(user):
    return issue_access_token(user.pk)[0]


@dataclass
class Tenant:
    organization: object
    # Holds the Owner (system) role: the tenant's reviewer.
    owner: User
    # An ordinary member who writes ideas.
    author: User
    # An ordinary member who reads them.
    colleague: User
    org_idea: Idea
    private_idea: Idea
    department_idea: Idea
    public_idea: Idea


def build_tenant(prefix):
    owner = make_user(f'{prefix}-owner@example.com')
    organization = make_organization(f'{prefix.upper()} Corp', owner)
    author = make_user(f'{prefix}-author@example.com')
    colleague = make_user(f'{prefix}-colleague@example.com')
    add_member(organization, author)
    add_member(organization, colleague)

    tenant = Tenant(
        organization=organization,
        owner=owner,
        author=author,
        colleague=colleague,
        org_idea=make_idea(organization, author, Idea.Visibility.ORGANIZATION),
        private_idea=make_idea(
            organization, author, Idea.Visibility.PRIVATE, status=Idea.Status.SUBMITTED
        ),
        department_idea=make_idea(organization, author, Idea.Visibility.DEPARTMENT),
        public_idea=make_idea(organization, author, Idea.Visibility.PUBLIC),
    )
    for idea in (
        tenant.org_idea,
        tenant.private_idea,
        tenant.department_idea,
        tenant.public_idea,
    ):
        attach(idea)
        Comment.objects.create(idea=idea, author=author, content='The author explains.')
    return tenant


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
def world(db):
    return {'a': build_tenant('a'), 'b': build_tenant('b')}


# --- transport --------------------------------------------------------------------------


def gql(client, query, variables=None, token=None):
    headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
    response = client.post(
        '/graphql/',
        data=json.dumps({'query': query, 'variables': variables or {}}),
        content_type='application/json',
        **headers,
    )
    body = response.json()
    assert 'errors' not in body, body
    return body['data']


def download(client, idea_id, attachment_id, token=None):
    headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
    return client.get(f'/ideas/{idea_id}/attachments/{attachment_id}/download/', **headers)


def upload(client, idea_id, token=None):
    headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
    file = SimpleUploadedFile('more.pdf', PDF_BYTES, content_type='application/pdf')
    return client.post(f'/ideas/{idea_id}/attachments/', data={'file': file}, **headers)


IDEA = 'query ($id: ID!) { idea(id: $id) { id availableTransitions } }'
ORG_IDEAS = """
query ($org: ID!, $status: IdeaStatus) {
  organizationIdeas(organizationId: $org, filters: {status: $status, limit: 50}) {
    items { id } pageInfo { totalCount }
  }
}"""
ALL_IDEAS = 'query { ideas(filters: {limit: 50}) { items { id } } }'
COMMENTS = 'query ($id: ID!) { comments(ideaId: $id) { items { id } pageInfo { totalCount } } }'
ATTACHMENTS = 'query ($id: ID!) { attachments(ideaId: $id) { items { id } } }'
ATTACHMENT = 'query ($id: ID!) { attachment(id: $id) { id } }'
CREATE_COMMENT = """
mutation ($id: ID!) {
  createComment(input: {ideaId: $id, comment: {content: "Hello"}}) { success message }
}"""
UPDATE_COMMENT = """
mutation ($id: ID!) {
  updateComment(input: {id: $id, comment: {content: "Rewritten"}}) { success message }
}"""
DELETE_COMMENT = 'mutation ($id: ID!) { deleteComment(id: $id) { success message } }'
VOTE = 'mutation ($id: ID!) { voteIdea(id: $id) { success message voteState { voteCount } } }'
REMOVE_VOTE = 'mutation ($id: ID!) { removeVote(id: $id) { success voteState { voteCount } } }'
DELETE_ATTACHMENT = 'mutation ($id: ID!) { deleteAttachment(id: $id) { success message } }'
UPDATE_IDEA = """
mutation ($id: ID!) {
  updateIdea(input: {id: $id, idea: {title: "Hijacked"}}) { success message }
}"""
TRANSITION = """
mutation ($id: ID!, $to: IdeaStatus!) {
  transitionIdea(id: $id, to: $to) { success message }
}"""
# `submissionContext` is required on the input: an idea always says which of the
# three contexts it is being filed in, because the context decides who reviews it
# and a tenant cannot be inferred from the row any more.
CREATE_IDEA = """
mutation ($org: ID!, $ctx: SubmissionContext!) {
  createIdea(input: {submissionContext: $ctx, organizationId: $org,
                      idea: {title: "New"}}) { success message }
}"""
CREATE_IDEA_NO_TENANT = """
mutation {
  createIdea(input: {submissionContext: INDIVIDUAL, idea: {title: "New"}}) { success message }
}"""


def graphql_can_read(client, idea, token):
    return gql(client, IDEA, {'id': idea.pk}, token)['idea'] is not None


def attachment_of(idea):
    return Attachment.objects.get(idea=idea)


# --- 1. two-organization isolation ------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(('reader_side', 'owner_side'), [('a', 'b'), ('b', 'a')])
def test_neither_organization_can_reach_the_others_protected_ideas(
    client, world, reader_side, owner_side
):
    """
    Every member of one tenant - its reviewer included - against every
    non-public idea of the other, through every read path: the idea itself,
    both listings, and each secondary resource. Symmetric by parametrization.
    """
    reader_tenant, owner_tenant = world[reader_side], world[owner_side]
    protected = [owner_tenant.org_idea, owner_tenant.private_idea, owner_tenant.department_idea]

    for reader in (reader_tenant.owner, reader_tenant.author, reader_tenant.colleague):
        token = token_for(reader)

        # The other tenant's feed is empty, not an error, and not partial.
        feed = gql(client, ORG_IDEAS, {'org': owner_tenant.organization.pk}, token)
        assert feed['organizationIdeas'] == {'items': [], 'pageInfo': {'totalCount': 0}}

        visible_everywhere = {
            item['id'] for item in gql(client, ALL_IDEAS, None, token)['ideas']['items']
        }

        for idea in protected:
            assert not graphql_can_read(client, idea, token)
            assert str(idea.pk) not in visible_everywhere
            assert gql(client, COMMENTS, {'id': idea.pk}, token)['comments']['items'] == []
            assert gql(client, ATTACHMENTS, {'id': idea.pk}, token)['attachments']['items'] == []

            attachment = attachment_of(idea)
            assert gql(client, ATTACHMENT, {'id': attachment.pk}, token)['attachment'] is None
            assert download(client, idea.pk, attachment.pk, token).status_code == 404

        # The one thing that crosses tenants by design: a PUBLIC idea is
        # platform-readable - in the platform-wide listing, but still not in
        # the other tenant's organization feed (checked above).
        assert str(owner_tenant.public_idea.pk) in visible_everywhere


# --- 2. GraphQL and HTTP agree ---------------------------------------------------------


@pytest.mark.django_db
def test_graphql_and_the_download_endpoint_answer_every_reader_identically(client, world):
    """
    One authorization model, two transports. For every (reader, idea) pair in
    the world, "GraphQL returns the idea" and "HTTP serves its attachment"
    must be the same answer - a disagreement in either direction is a bypass
    through whichever transport is looser.
    """
    a, b = world['a'], world['b']
    readers = [a.owner, a.author, a.colleague, b.owner, b.author, None]
    ideas = [
        idea
        for tenant in (a, b)
        for idea in (
            tenant.org_idea,
            tenant.private_idea,
            tenant.department_idea,
            tenant.public_idea,
        )
    ]

    outcomes = set()
    for reader in readers:
        token = token_for(reader) if reader else None
        for idea in ideas:
            attachment = attachment_of(idea)
            readable = graphql_can_read(client, idea, token)
            listed = gql(client, ATTACHMENTS, {'id': idea.pk}, token)['attachments']['items']
            status = download(client, idea.pk, attachment.pk, token).status_code

            assert status == (200 if readable else 404), (
                reader,
                idea.visibility,
                idea.organization,
            )
            assert bool(listed) is readable
            outcomes.add(readable)

    # The matrix produced both answers, so the agreement above is not vacuous.
    assert outcomes == {True, False}


# --- 3. readable is not writable: another tenant's PUBLIC idea --------------------------


@pytest.mark.django_db
def test_a_readable_public_idea_in_another_tenant_accepts_no_attachment_writes(client, world):
    a, b = world['a'], world['b']
    target = a.public_idea
    existing = attachment_of(target)
    outsider = token_for(b.owner)

    # Readable - that is what PUBLIC means ...
    assert graphql_can_read(client, target, outsider)
    assert download(client, target.pk, existing.pk, outsider).status_code == 200

    # ... and nothing more. Upload is author-only; readability does not grant it.
    response = upload(client, target.pk, outsider)
    assert response.status_code == 404
    assert response.json()['success'] is False

    deleted = gql(client, DELETE_ATTACHMENT, {'id': existing.pk}, outsider)['deleteAttachment']
    assert deleted['success'] is False

    assert list(Attachment.objects.filter(idea=target)) == [existing]


# --- 4. DEPARTMENT stays fail-closed ---------------------------------------------------


@pytest.mark.django_db
def test_department_ideas_are_author_only_for_every_attachment_operation(client, world):
    a = world['a']
    idea = a.department_idea
    attachment = attachment_of(idea)

    # Same organization, reviewer role included: still nothing.
    for member in (a.colleague, a.owner):
        token = token_for(member)
        assert not graphql_can_read(client, idea, token)
        assert gql(client, ATTACHMENTS, {'id': idea.pk}, token)['attachments']['items'] == []
        assert download(client, idea.pk, attachment.pk, token).status_code == 404
        assert upload(client, idea.pk, token).status_code == 404
        assert (
            gql(client, DELETE_ATTACHMENT, {'id': attachment.pk}, token)['deleteAttachment'][
                'success'
            ]
            is False
        )

    # The author keeps full access to their own evidence.
    author = token_for(a.author)
    assert download(client, idea.pk, attachment.pk, author).status_code == 200
    assert upload(client, idea.pk, author).status_code == 201
    assert Attachment.objects.filter(idea=idea).count() == 2


# --- 5. attachment id confusion --------------------------------------------------------


@pytest.mark.django_db
def test_an_attachment_id_cannot_be_borrowed_by_a_readable_idea_url(client, world):
    """
    The download URL names both the idea and the attachment. A reader who can
    see *some* idea must not be able to fetch any attachment by pairing its id
    with that idea - whether the attachment's real idea is invisible to them,
    in another tenant, or even readable.
    """
    a, b = world['a'], world['b']
    colleague = token_for(a.colleague)
    readable_url_idea = a.org_idea

    for foreign in (
        attachment_of(a.private_idea),  # same tenant, invisible
        attachment_of(a.department_idea),  # same tenant, fail-closed
        attachment_of(b.org_idea),  # other tenant, invisible
        attachment_of(b.public_idea),  # other tenant, readable - still the wrong idea
    ):
        response = download(client, readable_url_idea.pk, foreign.pk, colleague)
        assert response.status_code == 404
        assert b'%PDF' not in response.content

    # Control: the correctly paired URL works.
    own = attachment_of(readable_url_idea)
    assert download(client, readable_url_idea.pk, own.pk, colleague).status_code == 200


# --- 6. former member ------------------------------------------------------------------


@pytest.mark.django_db
def test_a_former_member_loses_every_membership_gated_operation(client, world):
    a = world['a']
    token = token_for(a.author)
    draft = a.org_idea
    attachment = attachment_of(draft)
    # Built **before** anyone leaves: submitting needs an active member to do it,
    # and what is under test afterwards is what a former member cannot do - not
    # whether a former member can still file.
    submitted = make_submitted_platform_idea(a.organization, a.colleague, Idea.Visibility.PUBLIC)
    Membership.objects.filter(user=a.author, organization=a.organization).update(
        status=Membership.Status.INACTIVE
    )

    # Writes into the tenant: refused, on both transports.
    assert upload(client, draft.pk, token).status_code == 403
    assert (
        gql(client, DELETE_ATTACHMENT, {'id': attachment.pk}, token)['deleteAttachment']['success']
        is False
    )
    assert gql(client, UPDATE_IDEA, {'id': draft.pk}, token)['updateIdea']['success'] is False
    assert (
        gql(client, TRANSITION, {'id': draft.pk, 'to': 'SUBMITTED'}, token)['transitionIdea'][
            'success'
        ]
        is False
    )
    assert (
        gql(
            client,
            CREATE_IDEA,
            {'org': a.organization.pk, 'ctx': 'ORGANIZATION'},
            token,
        )['createIdea']['success']
        is False
    )

    # Reading colleagues' organization-scoped work ends too.
    Membership.objects.filter(user=a.colleague, organization=a.organization).update(
        status=Membership.Status.INACTIVE
    )
    former_colleague = token_for(a.colleague)
    assert not graphql_can_read(client, draft, former_colleague)
    assert download(client, draft.pk, attachment.pk, former_colleague).status_code == 404

    # A former member cannot review an organization's ideas. The refusal is by the
    # *read* - an idea in a tenant you have left is invisible, exactly as it is to
    # a stranger - which is a stronger outcome than a role check: it also stops
    # this being a probe for which idea ids exist in a tenant somebody used to
    # belong to.
    Membership.objects.filter(user=a.owner, organization=a.organization).update(
        status=Membership.Status.INACTIVE
    )
    result = gql(
        client, TRANSITION, {'id': submitted.pk, 'to': 'UNDER_REVIEW'}, token_for(a.owner)
    )['transitionIdea']
    assert result['success'] is False
    submitted.refresh_from_db()
    assert submitted.status == Idea.Status.SUBMITTED

    # And on the idea their own organization *owns*, leaving the organization makes
    # it invisible to them - refused by the read, exactly as to a stranger, which
    # is what stops the endpoint being a probe for which idea ids exist in a
    # tenant somebody used to belong to. (`submitted` above is `PUBLIC`, which is
    # readable by anyone signed in, so it cannot show that.)
    assert not graphql_can_read(client, a.org_idea, token_for(a.owner))

    draft.refresh_from_db()
    submitted.refresh_from_db()
    assert draft.title == 'An idea'
    assert draft.status == Idea.Status.DRAFT
    assert submitted.status == Idea.Status.SUBMITTED
    assert Attachment.objects.filter(idea=draft).count() == 1


# --- 7. deactivated account with a still-valid token -----------------------------------


@pytest.mark.django_db
def test_a_valid_token_for_a_deactivated_account_authenticates_nothing(client, world):
    a = world['a']
    token = token_for(a.author)  # Issued while active, cryptographically valid.
    User.objects.filter(pk=a.author.pk).update(is_active=False)
    own_idea = a.public_idea
    attachment = attachment_of(own_idea)

    # HTTP: treated exactly like an anonymous request.
    assert upload(client, own_idea.pk, token).status_code == 401
    assert download(client, own_idea.pk, attachment.pk, token).status_code == 404

    # GraphQL: no reads, no writes - even of the account's own ideas.
    assert not graphql_can_read(client, own_idea, token)
    assert (
        gql(
            client,
            CREATE_IDEA,
            {'org': a.organization.pk, 'ctx': 'ORGANIZATION'},
            token,
        )['createIdea']['success']
        is False
    )
    assert gql(client, VOTE, {'id': own_idea.pk}, token)['voteIdea']['success'] is False
    assert (
        gql(client, CREATE_COMMENT, {'id': own_idea.pk}, token)['createComment']['success'] is False
    )
    assert (
        gql(client, DELETE_ATTACHMENT, {'id': attachment.pk}, token)['deleteAttachment']['success']
        is False
    )

    assert Vote.objects.count() == 0
    assert Attachment.objects.filter(pk=attachment.pk).exists()


# --- 8. storage failure is a server error ----------------------------------------------


@pytest.mark.django_db
def test_a_storage_failure_on_upload_is_a_server_error_not_a_missing_idea(
    client, world, monkeypatch
):
    """
    Regression (S2-008). A storage write failure was raised as an `IdeaError`
    with no explicit reason, which defaults to `'forbidden'`, which the view
    mapped to 404 - so a broken disk looked like an idea that does not exist.
    The failure is injected at the storage backend, so the real
    `ideas.storage.save_object` wrapping and `services.upload_attachment`
    translation both run.
    """
    a = world['a']

    def broken_save(self, name, content):
        raise OSError('disk full')

    monkeypatch.setattr(FileSystemStorage, '_save', broken_save)

    response = upload(client, a.org_idea.pk, token_for(a.author))

    assert response.status_code == 502
    body = response.json()
    assert body['success'] is False
    assert body['field'] is None
    assert body['message'] == 'The file could not be stored. Please try again.'
    assert Attachment.objects.filter(idea=a.org_idea).count() == 1  # Only the fixture's.


# --- 9. lifecycle and visibility are independent ---------------------------------------


@pytest.mark.django_db
def test_submitting_a_private_idea_does_not_make_it_reviewer_visible(client, world):
    """
    Status never grants readability. A PRIVATE idea that is SUBMITTED is
    still author-only, so the tenant's reviewer can neither find it nor act
    on it - the same idea at ORGANIZATION visibility is the control that
    proves the reviewer is otherwise able to.
    """
    a = world['a']
    reviewer = token_for(a.owner)
    # Submitted to the **platform** by walking the journey. The point of the test
    # is that a submitted idea is no more readable than a draft one, so the idea
    # has to be genuinely submitted - written directly into `SUBMITTED`, the
    # tenant's reviewer would not even be able to resolve it, and the refusal
    # would be testing the wrong thing.
    private = make_private_submitted_idea(a)

    assert not graphql_can_read(client, private, reviewer)
    queue = gql(client, ORG_IDEAS, {'org': a.organization.pk, 'status': 'SUBMITTED'}, reviewer)
    assert str(private.pk) not in {item['id'] for item in queue['organizationIdeas']['items']}

    # Starting a review is `startReview` since S3-004.
    start = 'mutation($id: ID!) { startReview(ideaId: $id) { success message } }'
    refused = gql(client, start, {'id': private.pk}, reviewer)
    assert refused['startReview'] == {'success': False, 'message': 'Idea is unavailable.'}
    private.refresh_from_db()
    assert private.status == Idea.Status.SUBMITTED
    assert private.visibility == Idea.Visibility.PRIVATE

    # Control: the same reviewer on a visible idea waiting for **organization**
    # review - which is the review this tenant's reviewer is authorized to make.
    # The two tracks are separate capabilities on separate queues, so the control
    # asks for the organization one; a platform reviewer would be offered this and
    # not the organization one, which is `test_submission_visibility` in the
    # service suite.
    visible = make_idea(a.organization, a.author, Idea.Visibility.ORGANIZATION)
    submit_idea(a.author, visible.pk)
    visible.refresh_from_db()

    offered = gql(
        client,
        'query($id: ID!) { idea(id: $id) { viewerCanStartOrganizationReview } }',
        {'id': visible.pk},
        reviewer,
    )['idea']['viewerCanStartOrganizationReview']
    assert offered is True
    started = gql(
        client,
        'mutation($id: ID!) { startOrganizationReview(ideaId: $id) { success message } }',
        {'id': visible.pk},
        reviewer,
    )['startOrganizationReview']
    assert started['success'] is True


# --- 10. secondary resources go through the idea ---------------------------------------


@pytest.mark.django_db
def test_an_unreadable_idea_is_unreachable_through_its_comments_votes_and_attachments(
    client, world
):
    """
    Comments, votes and attachments have no authorization of their own; each
    is authorized through its idea. So for an idea the caller cannot read -
    in their own tenant or another - every secondary operation must refuse,
    including ones addressed by the secondary resource's own id.
    """
    a, b = world['a'], world['b']
    colleague = token_for(a.colleague)

    for idea in (a.private_idea, a.department_idea, b.org_idea):
        comment = Comment.objects.get(idea=idea)
        attachment = attachment_of(idea)

        created = gql(client, CREATE_COMMENT, {'id': idea.pk}, colleague)['createComment']
        assert created == {'success': False, 'message': 'Idea is unavailable.'}
        assert (
            gql(client, UPDATE_COMMENT, {'id': comment.pk}, colleague)['updateComment']['success']
            is False
        )
        assert (
            gql(client, DELETE_COMMENT, {'id': comment.pk}, colleague)['deleteComment']['success']
            is False
        )

        voted = gql(client, VOTE, {'id': idea.pk}, colleague)['voteIdea']
        assert voted['success'] is False
        assert voted['voteState'] is None  # No count leaks for an unreadable idea.
        assert (
            gql(client, REMOVE_VOTE, {'id': idea.pk}, colleague)['removeVote']['success'] is False
        )

        assert (
            gql(client, DELETE_ATTACHMENT, {'id': attachment.pk}, colleague)['deleteAttachment'][
                'success'
            ]
            is False
        )
        assert upload(client, idea.pk, colleague).status_code == 404

        comment.refresh_from_db()
        assert comment.content == 'The author explains.'
        assert Attachment.objects.filter(idea=idea).count() == 1

    assert Vote.objects.count() == 0
    assert Comment.objects.filter(author=a.colleague).count() == 0


# --- unauthenticated -------------------------------------------------------------------


@pytest.mark.django_db
def test_an_anonymous_caller_gets_no_protected_ideas_operation(client, world):
    a = world['a']
    idea = a.public_idea
    attachment = attachment_of(idea)

    assert not graphql_can_read(client, idea, None)
    assert gql(client, ALL_IDEAS)['ideas']['items'] == []
    for mutation, variables, root in (
        # The individual context, deliberately: it is the one that needs no
        # organization, so this is the filing a stranger is most likely to try,
        # and it must still be refused - there is nothing here to be a member of.
        (CREATE_IDEA_NO_TENANT, {}, 'createIdea'),
        (UPDATE_IDEA, {'id': idea.pk}, 'updateIdea'),
        (TRANSITION, {'id': idea.pk, 'to': 'SUBMITTED'}, 'transitionIdea'),
        (CREATE_COMMENT, {'id': idea.pk}, 'createComment'),
        (VOTE, {'id': idea.pk}, 'voteIdea'),
        (DELETE_ATTACHMENT, {'id': attachment.pk}, 'deleteAttachment'),
    ):
        assert gql(client, mutation, variables)[root]['success'] is False, root

    assert upload(client, idea.pk).status_code == 401
    assert download(client, idea.pk, attachment.pk).status_code == 404
    assert Idea.objects.count() == 8
    assert Vote.objects.count() == 0
