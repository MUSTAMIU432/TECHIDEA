"""
Commenting at the GraphQL boundary (S2-005).

`ideas/services.py` and `ideas/selectors.py` decide; these tests check that
the boundary neither widens nor narrows them. The properties that only exist at
this layer:

- **The client cannot supply an author or an idea it was not allowed to name.**
  `CreateCommentInput` has no `authorId` and no `content`-bearing fields beyond
  the text itself, so "post as somebody else" is not a request this schema can
  express. Asserted by *sending* such a request and requiring the type system
  to reject it.
- **A refusal is a payload, not a crash.** A rejected comment comes back as
  `success: false` with a message and a `field`, like every other mutation in
  this schema, because a thrown GraphQL error tells the client the server is
  broken and invites a retry of a request that was correctly refused.
- **Nothing about authorization leaks in the payload.** The comment type carries
  `authorId` and no member object, and a refusal message does not distinguish
  "not yours" from "does not exist".
- **The page is the S2-004 page.** `PageInfo` is the same type the ideas
  queries return, so the client has one set of pagination conventions.
"""

import json

import pytest
from django.test import Client
from django.utils import timezone

from ideas.models import Comment, Idea
from identity.models import User
from organizations.models import Membership, MembershipRole, Role

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'

COMMENTS_QUERY = """
query Comments($ideaId: ID!, $offset: Int, $limit: Int) {
  comments(ideaId: $ideaId, offset: $offset, limit: $limit) {
    items { id ideaId authorId content createdAt updatedAt }
    pageInfo { offset limit totalCount hasNextPage hasPreviousPage }
  }
}
"""

CREATE_COMMENT = """
mutation CreateComment($input: CreateCommentInput!) {
  createComment(input: $input) {
    success
    message
    field
    comment { id ideaId authorId content createdAt updatedAt }
  }
}
"""

UPDATE_COMMENT = """
mutation UpdateComment($input: UpdateCommentInput!) {
  updateComment(input: $input) {
    success
    message
    field
    comment { id content updatedAt }
  }
}
"""

DELETE_COMMENT = """
mutation DeleteComment($id: ID!) {
  deleteComment(id: $id) { success message field comment { id } }
}
"""


@pytest.fixture
def gql(client: Client):
    def post(query, variables=None, bearer=None):
        payload = {'query': query}
        if variables is not None:
            payload['variables'] = variables
        headers = {}
        if bearer is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bearer {bearer}'
        return client.post(
            '/graphql/', data=json.dumps(payload), content_type='application/json', **headers
        )

    return post


def run(gql, query, root_field, variables=None, bearer=None):
    response = gql(query, variables, bearer=bearer)
    assert response.status_code == 200, response.content
    body = response.json()
    assert 'errors' not in body, body
    return body['data'][root_field]


def fails(gql, query, root_field, variables=None, bearer=None):
    """A payload-shaped refusal, asserted rather than assumed."""
    result = run(gql, query, root_field, variables, bearer)
    assert result['success'] is False, result
    return result


def make_user(email='ada@example.com', **overrides):
    fields = {
        'email': email,
        'first_name': 'Ada',
        'last_name': 'Lovelace',
        'phone_number': '+255712345678',
    }
    fields.update(overrides)
    return User.objects.create_user(password=VALID_PASSWORD, **fields)


def make_organization(name='Acme Labs', owner=None):
    from organizations.services import CreateOrganizationInput, create_organization_for_user

    if owner is None:
        owner = make_user(f'{name.split()[0].lower()}@example.com')
    result = create_organization_for_user(owner, CreateOrganizationInput(name=name))
    return result.organization, result.membership


def add_active_member(organization, user):
    membership = Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )
    role = Role.objects.create(organization=organization, name='Contributor', slug='contributor')
    MembershipRole.objects.create(membership=membership, role=role)
    return membership


def sign_in(client: Client, user: User) -> str:
    """A real access token, obtained the way a browser obtains one."""
    response = client.post(
        '/graphql/',
        data=json.dumps(
            {
                'query': """
mutation Login($input: LoginInput!) {
  login(input: $input) { success accessToken }
}""",
                'variables': {'input': {'email': user.email, 'password': VALID_PASSWORD}},
            }
        ),
        content_type='application/json',
    )
    payload = response.json()['data']['login']
    assert payload['success'] is True, payload
    return payload['accessToken']


def make_idea(organization, author, **overrides):
    fields = {
        'organization': organization,
        'author': author,
        'title': 'Automate the invoice run',
        'description': DESCRIPTION,
        'visibility': Idea.Visibility.ORGANIZATION,
    }
    fields.update(overrides)
    return Idea.objects.create(**fields)


def submitted(idea):
    idea.status = Idea.Status.SUBMITTED
    idea.submitted_at = timezone.now()
    idea.save()
    return idea


def make_comment(idea, author, content='We do this by hand every month.'):
    return Comment.objects.create(idea=idea, author=author, content=content)


@pytest.fixture
def world(client: Client):
    author = make_user('author@example.com')
    organization, _ = make_organization(owner=author)
    colleague = make_user('colleague@example.com')
    add_active_member(organization, colleague)

    outsider = make_user('outsider@example.com')
    other, _ = make_organization(name='Other Co', owner=outsider)

    return {
        'author': author,
        'colleague': colleague,
        'outsider': outsider,
        'organization': organization,
        'other': other,
        'author_token': sign_in(client, author),
        'colleague_token': sign_in(client, colleague),
        'outsider_token': sign_in(client, outsider),
    }


def create(gql, bearer, idea_id, content='A comment.'):
    return run(
        gql,
        CREATE_COMMENT,
        'createComment',
        {'input': {'ideaId': str(idea_id), 'comment': {'content': content}}},
        bearer=bearer,
    )


# --- reading ------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommentsQuery:
    def test_a_member_reads_the_discussion(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk)},
            bearer=world['colleague_token'],
        )

        assert [item['id'] for item in page['items']] == [str(comment.pk)]
        assert page['items'][0]['content'] == comment.content
        assert page['items'][0]['authorId'] == str(world['colleague'].pk)
        assert page['items'][0]['ideaId'] == str(idea.pk)

    def test_the_discussion_is_oldest_first(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        first = make_comment(idea, world['author'], 'First.')
        second = make_comment(idea, world['colleague'], 'Second.')

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk)},
            bearer=world['author_token'],
        )

        assert [item['id'] for item in page['items']] == [str(first.pk), str(second.pk)]

    def test_it_returns_a_page_not_a_list(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        make_comment(idea, world['colleague'])

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk)},
            bearer=world['author_token'],
        )

        assert page['pageInfo'] == {
            'offset': 0,
            'limit': 20,
            'totalCount': 1,
            'hasNextPage': False,
            'hasPreviousPage': False,
        }

    def test_it_pages(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        for index in range(5):
            make_comment(idea, world['colleague'], f'Comment {index}')

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk), 'offset': 2, 'limit': 2},
            bearer=world['author_token'],
        )

        assert len(page['items']) == 2
        assert page['pageInfo']['totalCount'] == 5
        assert page['pageInfo']['hasNextPage'] is True
        assert page['pageInfo']['hasPreviousPage'] is True

    def test_an_oversized_limit_is_clamped_and_reported(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        make_comment(idea, world['colleague'])

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk), 'limit': 10_000},
            bearer=world['author_token'],
        )

        # The same ceiling as ideas, echoed back as applied.
        assert page['pageInfo']['limit'] == 50

    def test_a_private_ideas_discussion_is_an_empty_page(self, gql, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        make_comment(idea, world['author'])

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk)},
            bearer=world['colleague_token'],
        )

        # An empty page, not an error and not a refusal: the same answer as for
        # an idea that does not exist, so the comment list is not a better
        # oracle than the idea.
        assert page['items'] == []
        assert page['pageInfo']['totalCount'] == 0

    def test_another_tenants_discussion_is_an_empty_page(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        make_comment(idea, world['outsider'])

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk)},
            bearer=world['author_token'],
        )

        assert page['items'] == []

    def test_an_unknown_idea_answers_exactly_the_same(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        make_comment(idea, world['outsider'])
        token = world['author_token']

        unknown = run(gql, COMMENTS_QUERY, 'comments', {'ideaId': '999999'}, bearer=token)
        other_tenant = run(gql, COMMENTS_QUERY, 'comments', {'ideaId': str(idea.pk)}, bearer=token)

        assert unknown == other_tenant

    def test_an_unauthenticated_caller_gets_an_empty_page(self, gql, world):
        """
        The established behaviour for reads: no error, no data. A reader who is
        not signed in browses categories and sees an empty list.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PUBLIC)
        make_comment(idea, world['author'])

        page = run(gql, COMMENTS_QUERY, 'comments', {'ideaId': str(idea.pk)})

        assert page['items'] == []
        assert page['pageInfo']['totalCount'] == 0

    def test_a_closed_discussion_is_still_readable(self, gql, world):
        """
        Closing a discussion to new comments is not hiding it. The reasoning
        that is on the record should stay readable afterwards.
        """
        idea = submitted(make_idea(world['organization'], world['author']))
        comment = make_comment(idea, world['colleague'], 'My view on this.')
        idea.status = Idea.Status.REJECTED
        idea.save()

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk)},
            bearer=world['colleague_token'],
        )

        assert [item['id'] for item in page['items']] == [str(comment.pk)]

    def test_a_comment_from_another_idea_is_not_returned(self, gql, world):
        idea = make_idea(world['organization'], world['author'], title='One')
        other = make_idea(world['organization'], world['author'], title='Two')
        make_comment(other, world['colleague'])

        page = run(
            gql,
            COMMENTS_QUERY,
            'comments',
            {'ideaId': str(idea.pk)},
            bearer=world['author_token'],
        )

        assert page['items'] == []


# --- creating -----------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateComment:
    def test_a_member_posts_a_comment(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        result = create(gql, world['colleague_token'], idea.pk, 'We do this by hand.')

        assert result['success'] is True
        assert result['field'] is None
        comment = result['comment']
        assert comment['authorId'] == str(world['colleague'].pk)
        assert comment['ideaId'] == str(idea.pk)
        assert comment['content'] == 'We do this by hand.'

    def test_the_comment_is_persisted(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        create(gql, world['colleague_token'], idea.pk, 'Persisted.')

        assert Comment.objects.get().content == 'Persisted.'

    def test_the_idea_is_untouched(self, gql, world):
        idea = submitted(make_idea(world['organization'], world['author']))

        create(gql, world['colleague_token'], idea.pk)

        idea.refresh_from_db()
        assert idea.status == Idea.Status.SUBMITTED

    def test_an_unauthenticated_caller_is_refused(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        result = fails(
            gql,
            CREATE_COMMENT,
            'createComment',
            {'input': {'ideaId': str(idea.pk), 'comment': {'content': 'Hello?'}}},
        )

        assert result['message'] == 'You must be signed in to work with ideas.'
        assert Comment.objects.count() == 0

    @pytest.mark.parametrize('content', ['', '   ', '\n\n'])
    def test_a_blank_comment_is_refused(self, gql, world, content):
        idea = make_idea(world['organization'], world['author'])

        result = fails(
            gql,
            CREATE_COMMENT,
            'createComment',
            {'input': {'ideaId': str(idea.pk), 'comment': {'content': content}}},
            bearer=world['colleague_token'],
        )

        # The message names the field, so the frontend can put it next to the
        # box rather than in a banner.
        assert result['field'] == 'content'
        assert Comment.objects.count() == 0

    def test_an_overlong_comment_is_refused_and_not_truncated(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        result = fails(
            gql,
            CREATE_COMMENT,
            'createComment',
            {'input': {'ideaId': str(idea.pk), 'comment': {'content': 'x' * 2001}}},
            bearer=world['colleague_token'],
        )

        assert result['field'] == 'content'
        assert Comment.objects.count() == 0

    def test_a_private_idea_cannot_be_commented_on(self, gql, world):
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)

        result = fails(
            gql,
            CREATE_COMMENT,
            'createComment',
            {'input': {'ideaId': str(idea.pk), 'comment': {'content': 'A comment'}}},
            bearer=world['colleague_token'],
        )

        assert result['message'] == 'Idea is unavailable.'
        assert Comment.objects.count() == 0

    def test_another_tenants_idea_cannot_be_commented_on(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)

        result = fails(
            gql,
            CREATE_COMMENT,
            'createComment',
            {'input': {'ideaId': str(idea.pk), 'comment': {'content': 'A comment'}}},
            bearer=world['author_token'],
        )

        assert result['message'] == 'Idea is unavailable.'

    def test_an_unknown_idea_answers_exactly_like_an_invisible_one(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        token = world['author_token']

        unknown = fails(
            gql,
            CREATE_COMMENT,
            'createComment',
            {'input': {'ideaId': '999999', 'comment': {'content': 'A comment'}}},
            bearer=token,
        )
        invisible = fails(
            gql,
            CREATE_COMMENT,
            'createComment',
            {'input': {'ideaId': str(idea.pk), 'comment': {'content': 'A comment'}}},
            bearer=token,
        )

        assert unknown['message'] == invisible['message']

    def test_a_rejected_idea_is_refused_and_says_why(self, gql, world):
        idea = submitted(make_idea(world['organization'], world['author']))
        idea.status = Idea.Status.REJECTED
        idea.save()

        result = fails(
            gql,
            CREATE_COMMENT,
            'createComment',
            {'input': {'ideaId': str(idea.pk), 'comment': {'content': 'One more thought'}}},
            bearer=world['colleague_token'],
        )

        # Named rather than hidden: the reader can already see the idea and its
        # status, so a generic refusal would tell them less than the truth.
        assert result['message'] == 'This idea is no longer open for discussion.'

    def test_a_public_idea_in_another_tenant_can_be_commented_on(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)

        result = create(gql, world['author_token'], idea.pk, 'We hit this too.')

        assert result['success'] is True

    def test_an_author_cannot_be_supplied(self, gql, world):
        """
        Sent, not merely asserted absent: the type system rejects the field, so
        "post this as somebody else" is not a request the API can express.
        """
        idea = make_idea(world['organization'], world['author'])

        response = gql(
            CREATE_COMMENT,
            {
                'input': {
                    'ideaId': str(idea.pk),
                    'authorId': str(world['colleague'].pk),
                    'comment': {'content': 'A comment'},
                }
            },
            bearer=world['author_token'],
        )

        assert response.status_code == 200
        assert 'errors' in response.json()
        assert Comment.objects.count() == 0

    def test_an_idea_cannot_be_moved_by_the_input(self, gql, world):
        """
        `CommentInput` carries content and nothing else, so there is no
        organization, status or visibility to smuggle in beside the text.
        """
        idea = make_idea(world['organization'], world['author'])

        response = gql(
            CREATE_COMMENT,
            {
                'input': {
                    'ideaId': str(idea.pk),
                    'comment': {'content': 'A comment', 'organizationId': '99'},
                }
            },
            bearer=world['colleague_token'],
        )

        assert 'errors' in response.json()


# --- updating -----------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateComment:
    def test_the_author_edits_their_own_comment(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'], 'Original text.')

        result = run(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': 'Corrected text.'}}},
            bearer=world['colleague_token'],
        )

        assert result['success'] is True
        assert result['comment']['content'] == 'Corrected text.'
        comment.refresh_from_db()
        assert comment.content == 'Corrected text.'

    def test_another_user_cannot_edit_it(self, gql, world):
        """
        Not the idea's author, not an organization Owner. There is no elevated
        path at this layer either - the schema offers no argument that could
        express one.
        """
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'], 'Original text.')

        result = fails(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': 'Rewritten.'}}},
            bearer=world['author_token'],
        )

        assert result['message'] == 'Comment is unavailable.'
        comment.refresh_from_db()
        assert comment.content == 'Original text.'

    def test_another_tenant_cannot_edit_it(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        result = fails(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': 'Rewritten.'}}},
            bearer=world['outsider_token'],
        )

        assert result['message'] == 'Comment is unavailable.'

    def test_an_unauthenticated_caller_cannot_edit_it(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        fails(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': 'Rewritten.'}}},
        )

        # Not merely refused: not written either.
        comment.refresh_from_db()
        assert comment.content == 'We do this by hand every month.'

    def test_an_unknown_comment_answers_exactly_like_someone_elses(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])
        token = world['colleague_token']

        unknown = fails(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': '999999', 'comment': {'content': 'Edited.'}}},
            bearer=token,
        )
        not_mine = fails(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': 'Edited.'}}},
            bearer=world['author_token'],
        )

        assert unknown['message'] == not_mine['message']

    def test_a_blank_edit_is_refused(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'], 'Original text.')

        result = fails(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': '   '}}},
            bearer=world['colleague_token'],
        )

        assert result['field'] == 'content'
        comment.refresh_from_db()
        assert comment.content == 'Original text.'

    def test_an_overlong_edit_is_refused(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'], 'Original text.')

        result = fails(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': 'x' * 2001}}},
            bearer=world['colleague_token'],
        )

        assert result['field'] == 'content'
        comment.refresh_from_db()
        assert comment.content == 'Original text.'

    def test_the_idea_and_author_cannot_be_changed(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        run(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': 'Edited.'}}},
            bearer=world['colleague_token'],
        )

        comment.refresh_from_db()
        assert comment.idea_id == idea.pk
        assert comment.author_id == world['colleague'].pk

    def test_an_edit_survives_a_closed_discussion(self, gql, world):
        idea = submitted(make_idea(world['organization'], world['author']))
        comment = make_comment(idea, world['colleague'], 'Original text.')
        idea.status = Idea.Status.REJECTED
        idea.save()

        result = run(
            gql,
            UPDATE_COMMENT,
            'updateComment',
            {'input': {'id': str(comment.pk), 'comment': {'content': 'Corrected.'}}},
            bearer=world['colleague_token'],
        )

        assert result['success'] is True


# --- deleting -----------------------------------------------------------------------


@pytest.mark.django_db
class TestDeleteComment:
    def test_the_author_deletes_their_own_comment(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        result = run(
            gql,
            DELETE_COMMENT,
            'deleteComment',
            {'id': str(comment.pk)},
            bearer=world['colleague_token'],
        )

        assert result['success'] is True
        assert result['comment'] is None
        assert not Comment.objects.filter(pk=comment.pk).exists()

    def test_another_user_cannot_delete_it(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        result = fails(
            gql,
            DELETE_COMMENT,
            'deleteComment',
            {'id': str(comment.pk)},
            bearer=world['author_token'],
        )

        assert result['message'] == 'Comment is unavailable.'
        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_another_tenant_cannot_delete_it(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        result = fails(
            gql,
            DELETE_COMMENT,
            'deleteComment',
            {'id': str(comment.pk)},
            bearer=world['outsider_token'],
        )

        assert result['message'] == 'Comment is unavailable.'
        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_an_unauthenticated_caller_cannot_delete_it(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        fails(gql, DELETE_COMMENT, 'deleteComment', {'id': str(comment.pk)})

        assert Comment.objects.filter(pk=comment.pk).exists()

    def test_an_unknown_comment_answers_exactly_like_someone_elses(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        comment = make_comment(idea, world['colleague'])

        unknown = fails(
            gql,
            DELETE_COMMENT,
            'deleteComment',
            {'id': '999999'},
            bearer=world['colleague_token'],
        )
        not_mine = fails(
            gql,
            DELETE_COMMENT,
            'deleteComment',
            {'id': str(comment.pk)},
            bearer=world['author_token'],
        )

        assert unknown['message'] == not_mine['message']

    def test_a_comment_on_an_invisible_idea_cannot_be_deleted_by_id(self, gql, world):
        """
        A comment id is not a handle. The comment belongs to a private idea the
        caller cannot read, so the delete is refused exactly as an unknown id
        would be.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        comment = make_comment(idea, world['author'], 'Private reasoning.')

        result = fails(
            gql,
            DELETE_COMMENT,
            'deleteComment',
            {'id': str(comment.pk)},
            bearer=world['colleague_token'],
        )

        assert result['message'] == 'Comment is unavailable.'
        assert Comment.objects.filter(pk=comment.pk).exists()


# --- what the schema does not expose -------------------------------------------------


@pytest.mark.django_db
class TestSchemaSurface:
    def test_a_comment_carries_no_author_object(self, gql, world):
        """
        The same rule as `IdeaType`: a `PUBLIC` idea is readable platform-wide,
        so embedding a member would publish their email address to everybody.
        An id is enough for the client to decide which comments to offer Edit
        and Delete on.
        """
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)
        make_comment(idea, world['outsider'])

        response = gql(
            """
query Comments($ideaId: ID!) {
  comments(ideaId: $ideaId) { items { author { id email } } }
}""",
            {'ideaId': str(idea.pk)},
            bearer=world['author_token'],
        )

        assert 'errors' in response.json()

    def test_there_is_no_vote_or_attachment_operation(self, world):
        """
        S2-006 and S2-007. Asserted at the schema level so a future change
        cannot quietly widen the surface this sprint owns.
        """
        from graphql_api.schema import schema

        sdl = str(schema)
        for name in ('voteIdea', 'removeVote', 'addAttachment', 'votes', 'attachments'):
            assert name not in sdl, name
