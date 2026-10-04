"""
Voting at the GraphQL boundary (S2-006).

`ideas/services.py` and `ideas/selectors.py` decide; these tests check that
the boundary neither widens nor narrows them. The properties that only exist at
this layer:

- **There is no voter argument.** `voteIdea` and `removeVote` take an idea and
  nothing else, so "vote on behalf of somebody else" is not a request this
  schema can express. Asserted by *sending* such a request and requiring the
  type system to reject it, rather than by reading the resolver.
- **A refusal carries no vote state.** The payload's `voteState` is null on
  every failure, because a caller who may not read an idea is not entitled to
  its vote count either.
- **The mutation returns the server's numbers**, so a client renders what the
  database holds rather than adjusting a count locally.
- **A discovery page carries vote state**, and the fields are per-reader: the
  same idea gives two readers the same count and different `viewerHasVoted`.
"""

import json

import pytest
from django.test import Client

from ideas.models import Category, Idea, Vote
from identity.models import User
from organizations.models import Membership, MembershipRole, Role

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'

VOTE_FIELDS = 'voteCount viewerHasVoted'

VOTE_IDEA = f"""
mutation VoteIdea($id: ID!) {{
  voteIdea(id: $id) {{
    success
    message
    field
    voteState {{ ideaId {VOTE_FIELDS} }}
  }}
}}
"""

REMOVE_VOTE = f"""
mutation RemoveVote($id: ID!) {{
  removeVote(id: $id) {{
    success
    message
    field
    voteState {{ ideaId {VOTE_FIELDS} }}
  }}
}}
"""

IDEAS_WITH_VOTES = f"""
query Ideas {{
  ideas {{
    items {{ id title {VOTE_FIELDS} }}
    pageInfo {{ totalCount }}
  }}
}}
"""

IDEA_QUERY = f"""
query Idea($id: ID!) {{
  idea(id: $id) {{ id title {VOTE_FIELDS} }}
}}
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


def vote(gql, bearer, idea_id):
    return run(gql, VOTE_IDEA, 'voteIdea', {'id': str(idea_id)}, bearer=bearer)


# --- voting --------------------------------------------------------------------------


@pytest.mark.django_db
class TestVoteIdea:
    def test_a_member_records_a_vote(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        result = vote(gql, world['colleague_token'], idea.pk)

        assert result['success'] is True
        assert result['field'] is None
        assert result['voteState'] == {
            'ideaId': str(idea.pk),
            'voteCount': 1,
            'viewerHasVoted': True,
        }

    def test_the_vote_is_persisted_against_the_caller(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        vote(gql, world['colleague_token'], idea.pk)

        assert list(Vote.objects.values_list('user_id', flat=True)) == [world['colleague'].pk]

    def test_voting_twice_is_one_row_and_one_vote(self, gql, world):
        """
        A double-clicked button is the normal way this arrives, so the second
        call is a success reporting the same state rather than an error.
        """
        idea = make_idea(world['organization'], world['author'])

        first = vote(gql, world['colleague_token'], idea.pk)
        second = vote(gql, world['colleague_token'], idea.pk)

        assert second['success'] is True
        assert second['voteState'] == first['voteState']
        assert Vote.objects.filter(idea=idea, user=world['colleague']).count() == 1

    def test_two_members_produce_a_count_of_two(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        vote(gql, world['colleague_token'], idea.pk)
        result = vote(gql, world['author_token'], idea.pk)

        assert result['voteState']['voteCount'] == 2

    def test_a_public_idea_in_another_tenant_can_be_voted_on(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.PUBLIC)

        result = vote(gql, world['author_token'], idea.pk)

        assert result['success'] is True

    def test_an_unauthenticated_caller_is_refused(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        result = fails(gql, VOTE_IDEA, 'voteIdea', {'id': str(idea.pk)})

        assert result['message'] == 'You must be signed in to work with ideas.'
        assert Vote.objects.count() == 0

    def test_a_private_idea_is_refused_and_leaks_no_count(self, gql, world):
        """
        The payload's `voteState` is null on a refusal. A count for an idea the
        caller cannot read is information they are not entitled to, and a
        zeroed count would be no better - it would confirm the idea exists.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        Vote.objects.create(idea=idea, user=world['author'])

        result = fails(
            gql, VOTE_IDEA, 'voteIdea', {'id': str(idea.pk)}, bearer=world['colleague_token']
        )

        assert result['message'] == 'Idea is unavailable.'
        assert result['voteState'] is None

    def test_another_tenants_idea_is_refused(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)

        result = fails(
            gql, VOTE_IDEA, 'voteIdea', {'id': str(idea.pk)}, bearer=world['author_token']
        )

        assert result['message'] == 'Idea is unavailable.'
        assert result['voteState'] is None
        assert Vote.objects.count() == 0

    def test_an_unknown_idea_answers_exactly_like_an_invisible_one(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        token = world['author_token']

        unknown = fails(gql, VOTE_IDEA, 'voteIdea', {'id': '999999'}, bearer=token)
        invisible = fails(gql, VOTE_IDEA, 'voteIdea', {'id': str(idea.pk)}, bearer=token)

        assert unknown['message'] == invisible['message']
        assert unknown['voteState'] == invisible['voteState'] is None

    def test_a_rejected_idea_can_still_be_voted_on(self, gql, world):
        """
        Voting has no lifecycle gate - a vote is interest in the idea, which
        outlives its review state. Contrast `createComment`, which refuses on
        the same idea, and which is the point of the two rules differing.
        """
        from django.utils import timezone

        idea = make_idea(world['organization'], world['author'])
        idea.status = Idea.Status.REJECTED
        idea.submitted_at = timezone.now()
        idea.save()

        result = vote(gql, world['colleague_token'], idea.pk)

        assert result['success'] is True

    def test_a_voter_cannot_be_supplied(self, gql, world):
        """
        Sent, not merely asserted absent: the type system rejects the field, so
        "vote as somebody else" is not a request the API can express.
        """
        idea = make_idea(world['organization'], world['author'])

        response = gql(
            """
mutation VoteIdea($id: ID!, $userId: ID!) {
  voteIdea(id: $id, userId: $userId) { success }
}""",
            {'id': str(idea.pk), 'userId': str(world['colleague'].pk)},
            bearer=world['author_token'],
        )

        assert response.status_code == 200
        assert 'errors' in response.json()
        assert Vote.objects.count() == 0

    def test_an_id_must_be_supplied(self, gql, world):
        response = gql(
            'mutation VoteIdea { voteIdea { success } }',
            bearer=world['author_token'],
        )

        assert 'errors' in response.json()

    def test_an_unusable_id_is_refused_rather_than_crashing(self, gql, world):
        response = gql(VOTE_IDEA, {'id': 'not-an-id'}, bearer=world['author_token'])

        body = response.json()
        assert 'errors' in body or body['data']['voteIdea']['success'] is False


# --- withdrawing ----------------------------------------------------------------------


@pytest.mark.django_db
class TestRemoveVote:
    def test_the_voter_withdraws_their_own_vote(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        vote(gql, world['colleague_token'], idea.pk)

        result = run(
            gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)}, bearer=world['colleague_token']
        )

        assert result['success'] is True
        assert result['voteState'] == {
            'ideaId': str(idea.pk),
            'voteCount': 0,
            'viewerHasVoted': False,
        }
        assert not Vote.objects.filter(idea=idea, user=world['colleague']).exists()

    def test_removing_twice_succeeds(self, gql, world):
        """
        Idempotent, so a second click on a toggle is not an error about a state
        the reader already reached.
        """
        idea = make_idea(world['organization'], world['author'])
        vote(gql, world['colleague_token'], idea.pk)

        run(gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)}, bearer=world['colleague_token'])
        result = run(
            gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)}, bearer=world['colleague_token']
        )

        assert result['success'] is True
        assert result['voteState']['voteCount'] == 0

    def test_removing_a_vote_that_was_never_cast_succeeds(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        result = run(
            gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)}, bearer=world['colleague_token']
        )

        assert result['success'] is True
        assert result['voteState']['viewerHasVoted'] is False

    def test_one_members_removal_leaves_another_members_vote(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        vote(gql, world['colleague_token'], idea.pk)
        vote(gql, world['author_token'], idea.pk)

        result = run(
            gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)}, bearer=world['colleague_token']
        )

        assert result['voteState'] == {
            'ideaId': str(idea.pk),
            'voteCount': 1,
            'viewerHasVoted': False,
        }
        assert Vote.objects.filter(idea=idea, user=world['author']).exists()

    def test_vote_and_withdraw_can_be_repeated(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        token = world['colleague_token']

        vote(gql, token, idea.pk)
        run(gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)}, bearer=token)
        result = vote(gql, token, idea.pk)

        assert result['voteState']['voteCount'] == 1
        assert result['voteState']['viewerHasVoted'] is True

    def test_an_unauthenticated_caller_cannot_remove_a_vote(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        vote(gql, world['colleague_token'], idea.pk)

        fails(gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)})

        assert Vote.objects.filter(idea=idea, user=world['colleague']).exists()

    def test_an_unreadable_idea_is_refused_rather_than_silently_accepted(self, gql, world):
        """
        The important half of the idempotency choice: "no vote of mine" and "an
        idea you cannot read" must not both be a success, or withdrawing would
        confirm the operation is available on an idea the caller never saw.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        Vote.objects.create(idea=idea, user=world['author'])

        result = fails(
            gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)}, bearer=world['colleague_token']
        )

        assert result['message'] == 'Idea is unavailable.'
        assert result['voteState'] is None
        assert Vote.objects.filter(idea=idea, user=world['author']).exists()

    def test_another_tenant_cannot_remove_a_vote(self, gql, world):
        idea = make_idea(world['other'], world['outsider'], visibility=Idea.Visibility.ORGANIZATION)
        Vote.objects.create(idea=idea, user=world['outsider'])

        result = fails(
            gql, REMOVE_VOTE, 'removeVote', {'id': str(idea.pk)}, bearer=world['author_token']
        )

        assert result['message'] == 'Idea is unavailable.'
        assert Vote.objects.filter(idea=idea, user=world['outsider']).exists()

    def test_an_unknown_comment_like_id_answers_exactly_the_same(self, gql, world):
        token = world['colleague_token']

        unknown = fails(gql, REMOVE_VOTE, 'removeVote', {'id': '999999'}, bearer=token)
        invisible = fails(gql, REMOVE_VOTE, 'removeVote', {'id': '1'}, bearer=token)

        assert unknown['message'] == invisible['message']


# --- reading vote state ---------------------------------------------------------------


@pytest.mark.django_db
class TestVoteStateInReads:
    def test_a_discovery_page_carries_the_count_and_the_viewers_own_answer(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        Vote.objects.create(idea=idea, user=world['colleague'])

        page = run(gql, IDEAS_WITH_VOTES, 'ideas', bearer=world['colleague_token'])

        # S2-004's page shape is untouched by S2-006: still `{ items, pageInfo }`.
        assert page['pageInfo'] == {'totalCount': 1}
        assert page['items'] == [
            {
                'id': str(idea.pk),
                'title': 'Automate the invoice run',
                'voteCount': 1,
                'viewerHasVoted': True,
            }
        ]

    def test_two_readers_get_the_same_count_and_different_own_state(self, gql, world):
        """
        The count is global and the flag is personal, which is the whole reason
        both are reported.
        """
        idea = make_idea(world['organization'], world['author'])
        Vote.objects.create(idea=idea, user=world['colleague'])

        voters = run(gql, IDEAS_WITH_VOTES, 'ideas', bearer=world['colleague_token'])
        others = run(gql, IDEAS_WITH_VOTES, 'ideas', bearer=world['author_token'])

        assert voters['items'][0]['voteCount'] == others['items'][0]['voteCount'] == 1
        assert voters['items'][0]['viewerHasVoted'] is True
        assert others['items'][0]['viewerHasVoted'] is False

    def test_an_idea_nobody_voted_on_reports_zero_not_null(self, gql, world):
        """
        `0`/`false`, never an error. The count subquery returns SQL `NULL` for
        an idea with no votes - the overwhelmingly common case - and `NULL`
        into a non-nullable field is a GraphQL error rather than an absence of
        votes.
        """
        make_idea(world['organization'], world['author'])

        page = run(gql, IDEAS_WITH_VOTES, 'ideas', bearer=world['author_token'])

        assert page['items'][0]['voteCount'] == 0
        assert page['items'][0]['viewerHasVoted'] is False

    def test_the_single_idea_query_carries_it_too(self, gql, world):
        idea = make_idea(world['organization'], world['author'])
        vote(gql, world['colleague_token'], idea.pk)

        result = run(gql, IDEA_QUERY, 'idea', {'id': str(idea.pk)}, bearer=world['colleague_token'])

        assert result['voteCount'] == 1
        assert result['viewerHasVoted'] is True

    def test_a_created_idea_reports_zero_votes_rather_than_a_missing_count(self, gql, world):
        """
        The row a mutation returns is not annotated, so `from_model` computes
        the state. Asserted because a fallback that defaulted to `0` without
        asking would be indistinguishable from a real zero - and this is the
        case where a *default* would be wrong the moment an idea already had
        votes, which a create cannot have. What matters here is that the field
        is present and typed, not absent or null.
        """
        created = run(
            gql,
            """
mutation CreateIdea($input: CreateIdeaInput!) {
  createIdea(input: $input) { success idea { id voteCount viewerHasVoted } }
}""",
            'createIdea',
            {
                'input': {
                    'submissionContext': 'ORGANIZATION',
                    'organizationId': str(world['organization'].pk),
                    'idea': {'title': 'A new idea', 'description': DESCRIPTION},
                }
            },
            bearer=world['author_token'],
        )

        assert created['success'] is True
        assert created['idea']['voteCount'] == 0
        assert created['idea']['viewerHasVoted'] is False

    def test_an_update_payload_reports_the_real_count(self, gql, world):
        """
        `updateIdea` builds its payload from an `Idea` that names no viewer -
        S2-002's existing shape, left exactly as it shipped - and a count that
        fell back to `0` there would report a wrong number on an idea other
        people had already voted for.

        The viewer's own answer is the part that genuinely needs a viewer, so
        it is the one reported as `false` here. The *total* is a fact about the
        idea and is counted from the idea, which is the whole content of this
        test: a wrong count on a real vote.
        """
        idea = make_idea(world['organization'], world['author'])
        Vote.objects.create(idea=idea, user=world['colleague'])

        result = run(
            gql,
            f"""
mutation UpdateIdea($input: UpdateIdeaInput!) {{
  updateIdea(input: $input) {{ success idea {{ id {VOTE_FIELDS} }} }}
}}""",
            'updateIdea',
            {'input': {'id': str(idea.pk), 'idea': {'title': 'Automate invoicing'}}},
            bearer=world['author_token'],
        )

        assert result['success'] is True
        assert result['idea']['voteCount'] == 1, result
        assert result['idea']['viewerHasVoted'] is False

    def test_a_submit_payload_reports_the_real_count(self, gql, world):
        """
        The same property on the third viewerless payload, `submitIdea`.

        The vote is the author's own, on their own draft: a colleague cannot
        see a draft, and voting is not lifecycle-gated, so a draft that its
        author has voted for is both the reachable setup and itself a
        consequence of the no-lifecycle-gate rule.
        """
        idea = make_idea(
            world['organization'],
            world['author'],
            category=Category.objects.create(name='Finance Ops'),
        )
        Vote.objects.create(idea=idea, user=world['author'])

        result = run(
            gql,
            f"""
mutation SubmitIdea($id: ID!) {{
  submitIdea(id: $id) {{ success message idea {{ id status {VOTE_FIELDS} }} }}
}}""",
            'submitIdea',
            {'id': str(idea.pk)},
            bearer=world['author_token'],
        )

        assert result['success'] is True, result
        # An organization idea's `submitIdea` goes to its organization first -
        # the operation resolves the stage from the idea's context, so this is
        # the same call a client makes and the same answer whichever context the
        # idea was filed in.
        assert result['idea']['status'] == 'SUBMITTED_TO_ORGANIZATION'
        assert result['idea']['voteCount'] == 1, result
        assert result['idea']['viewerHasVoted'] is False

    def test_the_count_on_a_viewerless_payload_does_not_leak_anything(self, gql, world):
        """
        The viewerless branch is only reachable on an idea a service has
        already authorized for this caller, so counting it leaks nothing - and
        the guarantee that matters still holds: an idea the caller may not read
        is refused before any payload exists.
        """
        private = make_idea(
            world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE
        )
        Vote.objects.create(idea=private, user=world['author'])

        refused = run(
            gql,
            """
mutation UpdateIdea($input: UpdateIdeaInput!) {
  updateIdea(input: $input) { success message idea { id voteCount } }
}""",
            'updateIdea',
            {'input': {'id': str(private.pk), 'idea': {'title': 'Renamed'}}},
            bearer=world['colleague_token'],
        )

        assert refused['success'] is False
        assert refused['idea'] is None

    def test_an_unreadable_idea_is_not_readable_at_all(self, gql, world):
        """
        The read-side guarantee, stated the only way it can be: an idea a
        caller may not read never becomes an `IdeaType`, so there is nothing
        for the vote fields to be empty on.
        """
        idea = make_idea(world['organization'], world['author'], visibility=Idea.Visibility.PRIVATE)
        Vote.objects.create(idea=idea, user=world['author'])

        result = run(gql, IDEA_QUERY, 'idea', {'id': str(idea.pk)}, bearer=world['colleague_token'])

        assert result is None

    def test_an_unauthenticated_caller_sees_no_vote_state(self, gql, world):
        make_idea(world['organization'], world['author'])

        page = run(gql, IDEAS_WITH_VOTES, 'ideas')

        assert page['items'] == []


# --- what the schema does not expose --------------------------------------------------


@pytest.mark.django_db
class TestSchemaSurface:
    def test_there_is_no_vote_listing_or_arbitrary_voter_query(self, world):
        """
        S2-006 exposes a count and the viewer's own answer, and nothing else.
        There is no "who voted" query, so no endpoint enumerates who supports
        an idea - which is the kind of aggregate this domain has no policy for.
        """
        from graphql_api.schema import schema

        sdl = str(schema)
        for name in ('votes(', 'voters(', 'ideaVotes(', 'voteByUser(', 'whoVoted'):
            assert name not in sdl, name

    def test_there_is_no_proposal_or_matching_operation(self, world):
        """
        S2-007's own surface is asserted present, not absent, by
        `test_attachment_schema.py`. What belongs here is later sprints:
        asserted absent so a future change cannot quietly widen the surface
        this sprint owns. `addAttachment` is deliberately not in this list
        either way - there never was one, and never will be: uploads are
        binary and go through `ideas/views.py`'s HTTP endpoint, not GraphQL.
        """
        from graphql_api.schema import schema

        sdl = str(schema)
        for name in (
            'createProposal',
            'proposals(',
            'developerProfile',
            'matchDevelopers',
        ):
            assert name not in sdl, name

    def test_the_vote_mutation_takes_no_id_beyond_the_idea(self, world):
        """
        The interface, stated structurally: one `ID!` for the idea, and nothing
        that could name a voter.
        """
        from graphql_api.schema import schema

        sdl = str(schema)
        assert 'voteIdea(id: ID!): VotePayload!' in sdl
        assert 'removeVote(id: ID!): VotePayload!' in sdl
