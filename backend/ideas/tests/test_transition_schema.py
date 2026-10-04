"""
The lifecycle at the GraphQL boundary (S2-003).

`ideas.lifecycle` decides; these tests check that the boundary neither widens
nor narrows it. The properties that only exist at this layer:

- **A status cannot be set directly.** There is no mutation that takes one,
  and no write input with a `status` field, so the only way to change a status
  is `transitionIdea` - which means the matrix cannot be walked around by
  crafting a different request. Asserted by *sending* such a request and
  requiring the GraphQL type system to reject it, rather than by reading the
  input class.
- **The refusal is a payload, not a crash.** A refused transition comes back
  as `success: false` with a message, because a thrown GraphQL error would tell
  the client the server is broken and invite a retry of a request that was
  correctly refused.
- **The transition an idea offers is the viewer's, not a global one.** The
  `availableTransitions` field is computed per viewer, so a client cannot
  discover another member's authority by reading a shared field.
"""

import json

import pytest
from django.test import Client

from ideas.go_ahead import confirm_go_ahead
from ideas.models import Category, Idea
from identity.models import User
from organizations.models import Membership, MembershipRole, Role
from reviews.tests.platform import confirm_for_organization, grant_platform_reviewer

VALID_PASSWORD = 'a-strong-unique-pass-1'
DESCRIPTION = 'A description long enough to be usable.'

TRANSITION = """
mutation TransitionIdea($id: ID!, $to: IdeaStatus!) {
  transitionIdea(id: $id, to: $to) {
    success
    message
    field
    idea { id status submittedAt availableTransitions }
  }
}
"""

IDEA_QUERY = """
query Idea($id: ID!) {
  idea(id: $id) { id status availableTransitions }
}
"""

IDEAS_QUERY = """
query Ideas {
  ideas { items { id status availableTransitions } }
}
"""

CREATE_IDEA = """
mutation CreateIdea($input: CreateIdeaInput!) {
  createIdea(input: $input) { success message field idea { id status } }
}
"""

# The S2-002 write path, sent with a status the input type does not have.
UPDATE_IDEA_WITH_STATUS = """
mutation UpdateIdea($input: UpdateIdeaInput!) {
  updateIdea(input: $input) { success message idea { id status } }
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


def sign_in(client: Client, user: User) -> str:
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


def add_member(organization, user, *, system_role=False):
    membership = Membership.objects.create(
        user=user, organization=organization, status=Membership.Status.ACTIVE
    )
    if system_role:
        role = Role.objects.get(organization=organization, is_system=True)
    else:
        role, _ = Role.objects.get_or_create(
            organization=organization,
            slug='contributor',
            defaults={'name': 'Contributor', 'is_system': False},
        )
    MembershipRole.objects.create(membership=membership, role=role)
    return membership


def make_idea(organization, author, *, status=Idea.Status.DRAFT, **overrides):
    """
    An idea in `status`, arranged in two steps.

    The status and the timestamp cannot be set on one `create()`: the model's
    `clean()` reads the *model default* for a field the caller did not pass, so
    passing `submitted_at` while leaving `status` at its default produces a
    draft carrying a submission stamp, which it correctly refuses. Two writes
    is also what the lifecycle itself does.
    """
    from django.utils import timezone

    fields = {
        'organization': organization,
        'author': author,
        'title': 'An idea',
        'description': DESCRIPTION,
        'category': Category.objects.create(name=f'Cat {Category.objects.count() + 1}'),
        'visibility': Idea.Visibility.ORGANIZATION,
    }
    fields.update(overrides)
    idea = Idea.objects.create(**fields)
    if status != Idea.Status.DRAFT:
        idea.status = status
        idea.submitted_at = timezone.now()
        idea.save()
    return idea


@pytest.fixture
def world(client: Client):
    author = make_user('author@example.com')
    organization, _ = make_organization(owner=author)
    reviewer = make_user('reviewer@example.com')
    add_member(organization, reviewer, system_role=True)
    # A second reviewer, and the distinction is the whole point: `reviewer`
    # validates the organization's own ideas, `platform_reviewer` reviews
    # submissions to the platform and belongs to no organization at all. The
    # platform half is authorized by a platform-scoped permission, so granting
    # it is the only way to build somebody who may make these moves - which is
    # why the platform-track cases below cannot borrow the organization reviewer.
    platform_reviewer = make_user('platform@example.com')
    grant_platform_reviewer(platform_reviewer)
    # Somebody who is *both*: an active member of the organization who also
    # holds the platform review permission, and therefore files their own ideas
    # as well as reviewing other people's. Needed for the self-review property
    # at the list boundary - a person who holds the permission and is the author
    # is offered nothing on their own idea, and that is only observable on an
    # account that can do both.
    platform_author = make_user('platform-author@example.com')
    add_member(organization, platform_author)
    grant_platform_reviewer(platform_author)
    member = make_user('member@example.com')
    add_member(organization, member)
    return {
        'organization': organization,
        'platform_author': platform_author,
        'platform_author_token': sign_in(client, platform_author),
        'author': author,
        'author_token': sign_in(client, author),
        'reviewer': reviewer,
        'reviewer_token': sign_in(client, reviewer),
        'platform_reviewer': platform_reviewer,
        'platform_reviewer_token': sign_in(client, platform_reviewer),
        'member': member,
        'member_token': sign_in(client, member),
    }


def platform_idea(world, *, status=Idea.Status.SUBMITTED, author=None):
    """
    An organization-context idea that really reached the platform.

    Built through the journey - the author's submit, the organization's
    confirmation, the author's submit-on - because a platform reviewer outside
    the tenant can only *read* an idea the platform actually received: the
    visibility filter looks for a frozen submission version, so a `SUBMITTED`
    row written directly would be invisible to them and these cases would be
    testing a refusal rather than the boundary they are about.
    """
    from ideas.services import submit_idea, submit_to_platform

    idea = make_idea(world['organization'], author or world['author'])
    submit_idea(idea.author, idea.pk)
    idea.refresh_from_db()
    confirm_for_organization(idea)
    idea.refresh_from_db()
    submit_to_platform(idea.author, idea.pk)
    idea.refresh_from_db()
    if status != Idea.Status.SUBMITTED:
        idea.status = status
        idea.save()
    return idea


def platform_approve(world, idea, reviewer=None):
    """
    Approve a submitted idea through the real review operations.

    Not `idea.status = 'approved'`: the go-ahead refuses an approval whose report
    was never generated, and the report is written by the approval's own
    transaction. Writing the status by hand would produce an idea that cannot
    legitimately be handed over, which is a state the product has no name for -
    so the review is started and completed the way the review workspace does it.
    """
    from reviews import services as review_services

    reviewer = reviewer or world['platform_reviewer']
    review = review_services.start_review(reviewer, idea.pk)
    review_services.complete_review(
        reviewer,
        review_services.CompleteReviewInput(
            idea_id=idea.pk,
            review_id=review.pk,
            decision='approved',
            feedback='Worth automating.',
            assessments=tuple(
                review_services.AssessmentInput(criterion, 'meets', f'on {criterion}')
                for criterion in (
                    'PROBLEM_CLARITY',
                    'AUTOMATION_SUITABILITY',
                    'FEASIBILITY',
                    'EXPECTED_BENEFIT',
                    'EVIDENCE',
                )
            ),
        ),
    )
    return Idea.objects.get(pk=idea.pk)


def individual_idea(world, author=None, **overrides):
    """An INDIVIDUAL-context idea: no organization, so submission is submission."""
    from ideas.services import IdeaInput, create_idea_in_context

    fields = {
        'title': 'An idea of my own',
        'description': DESCRIPTION,
        'visibility': Idea.Visibility.PUBLIC,
        'category_id': Category.objects.create(name=f'Cat {Category.objects.count() + 1}').pk,
    }
    fields.update(overrides)
    return create_idea_in_context(
        author or world['author'],
        IdeaInput(**fields),
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
    )


def _introspect(gql, query, variables=None):
    """Run an introspection query against the real endpoint and return `data`."""
    response = gql(query, variables)
    body = response.json()
    assert 'errors' not in body, body
    return body['data']


MUTATION_ARGUMENTS_QUERY = """
query IntrospectMutations {
  __schema { mutationType { fields { name args { name } } } }
}
"""

TYPE_FIELDS_QUERY = """
query IntrospectType($name: String!) {
  __type(name: $name) { fields { name } }
}
"""


def _introspect_mutation_arguments(gql) -> dict[str, set[str]]:
    data = _introspect(gql, MUTATION_ARGUMENTS_QUERY)
    return {
        field['name']: {arg['name'] for arg in field['args']}
        for field in data['__schema']['mutationType']['fields']
    }


def _introspect_type_fields(gql, type_name: str) -> set[str]:
    data = _introspect(gql, TYPE_FIELDS_QUERY, {'name': type_name})
    return {field['name'] for field in data['__type']['fields']}


# --- valid and refused transitions ---------------------------------------------------


@pytest.mark.django_db
class TestTransitionIdeaMutation:
    @pytest.mark.parametrize(
        ('from_status', 'to'),
        [
            (Idea.Status.SUBMITTED, 'UNDER_REVIEW'),
            (Idea.Status.UNDER_REVIEW, 'CHANGES_REQUESTED'),
            (Idea.Status.UNDER_REVIEW, 'APPROVED'),
            (Idea.Status.UNDER_REVIEW, 'REJECTED'),
        ],
    )
    def test_a_reviewer_cannot_make_a_review_move_through_it(self, gql, world, from_status, to):
        """
        S3-004: starting and deciding a review are the review workspace's
        operations, which leave a `Review`. Through `transitionIdea` they would
        leave none, so they are refused here - as a payload, with the idea
        unmoved - even for a reviewer who could otherwise make them.

        Driven by the **platform** reviewer, because all four pairs are the
        platform track's; the organization's two pairs are checked the same way
        in `test_an_organization_reviewer_cannot_make_theirs_through_it`.
        """
        idea = platform_idea(world, status=from_status)

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': to},
            bearer=world['platform_reviewer_token'],
        )

        assert result['success'] is False
        assert 'review workspace' in result['message']
        assert Idea.objects.get(pk=idea.pk).status == from_status

    @pytest.mark.parametrize(
        ('from_status', 'to'),
        [
            ('SUBMITTED_TO_ORGANIZATION', 'ORGANIZATION_CONFIRMED'),
            ('SUBMITTED_TO_ORGANIZATION', 'ORGANIZATION_CHANGES_REQUESTED'),
        ],
    )
    def test_an_organization_reviewer_cannot_make_theirs_through_it(
        self, gql, world, from_status, to
    ):
        """
        The same boundary on the organization's track, with the organization
        Reviewer - somebody who *can* make both moves, in the review workspace.

        Asserted separately rather than folded into the platform cases because
        the two reviewer kinds are different permissions and a single
        parametrization would quietly let the organization reviewer stand in for
        the platform one.
        """
        # GraphQL spells the enum `SUBMITTED_TO_ORGANIZATION`; the column spells
        # it `submitted_to_organization`. Passed as one string and lowered once,
        # so the parametrization reads as the API does and the comparison below
        # is against the stored value.
        stored = from_status.lower()
        idea = make_idea(world['organization'], world['author'], status=stored)

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': to},
            bearer=world['reviewer_token'],
        )

        assert result['success'] is False
        assert 'review workspace' in result['message']
        assert Idea.objects.get(pk=idea.pk).status == stored

    def test_the_developer_handoff_follows_the_go_ahead(self, gql, world):
        """
        The hand-off to Sprint 4, and the fact that it is not reachable from
        `APPROVED` any more.

        Two things are asserted, because either alone would be misleading: the
        authorized move succeeds, and the shortcut a reader would assume still
        works - a platform reviewer handing an approved idea straight over -
        is refused. The refusal is the author's, and no permission buys it.
        """
        idea = platform_approve(world, platform_idea(world))
        assert idea.status == Idea.Status.APPROVED

        premature = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': 'AUTOMATION_PROPOSAL'},
            bearer=world['platform_reviewer_token'],
        )
        assert premature['success'] is False
        assert Idea.objects.get(pk=idea.pk).status == 'approved'

        confirm_go_ahead(world['author'], idea.pk)

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': 'AUTOMATION_PROPOSAL'},
            bearer=world['platform_reviewer_token'],
        )

        assert result['success'] is True
        assert result['idea']['status'] == 'AUTOMATION_PROPOSAL'

    def test_the_author_submits_a_draft(self, gql, world):
        """
        An organization draft goes to its organization, not to the platform.

        One mutation and one target in the enum, so the *client* has to know
        which door it is opening - unlike `submitIdea`, which resolves the target
        from the idea's context. That is the distinction worth pinning at the
        boundary: `transitionIdea` is the raw lifecycle, `submitIdea` is the
        operation a person actually presses.
        """
        organization_draft = make_idea(world['organization'], world['author'])

        to_organization = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(organization_draft.pk), 'to': 'SUBMITTED_TO_ORGANIZATION'},
            bearer=world['author_token'],
        )
        assert to_organization['success'] is True
        assert to_organization['idea']['submittedAt'] is not None

        individual_draft = individual_idea(world)
        to_platform = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(individual_draft.pk), 'to': 'SUBMITTED'},
            bearer=world['author_token'],
        )

        assert to_platform['success'] is True
        assert to_platform['idea']['submittedAt'] is not None

    def test_an_illegal_transition_is_a_payload(self, gql, world):
        """
        `DRAFT -> APPROVED`, by somebody who *is* allowed to approve things -
        so the refusal is about the lifecycle, not about the caller.
        """
        idea = make_idea(world['organization'], world['author'])

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': 'APPROVED'},
            bearer=world['reviewer_token'],
        )

        assert result['success'] is False
        assert result['idea'] is None
        assert 'cannot go from' in result['message']
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT

    def test_a_member_without_the_role_is_refused(self, gql, world):
        idea = make_idea(world['organization'], world['author'], status=Idea.Status.SUBMITTED)

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': 'UNDER_REVIEW'},
            bearer=world['member_token'],
        )

        assert result['success'] is False
        assert 'not allowed' in result['message']
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_the_author_cannot_review_their_own_idea(self, gql, world):
        """
        The author holds the Owner role - bootstrap gave it to them - so this
        request carries a genuine system role and is still refused. The
        self-review rule is not a role check that happens to work.
        """
        idea = make_idea(world['organization'], world['author'], status=Idea.Status.SUBMITTED)

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': 'UNDER_REVIEW'},
            bearer=world['author_token'],
        )

        assert result['success'] is False
        assert 'not allowed' in result['message']

    def test_an_unauthenticated_transition_is_refused(self, gql, world):
        idea = make_idea(world['organization'], world['author'])

        result = run(gql, TRANSITION, 'transitionIdea', {'id': str(idea.pk), 'to': 'SUBMITTED'})

        assert result['success'] is False
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT

    def test_a_cross_organization_actor_is_refused(self, gql, world, client):
        stranger = make_user('stranger@example.com')
        make_organization(name='Other Co', owner=stranger)
        token = sign_in(client, stranger)
        idea = make_idea(world['organization'], world['author'], status=Idea.Status.SUBMITTED)

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': 'UNDER_REVIEW'},
            bearer=token,
        )

        assert result['success'] is False
        assert result['message'] == 'Idea is unavailable.'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_an_unknown_status_is_refused_by_the_schema(self, gql, world):
        """
        The enum is the first line of defence: a value outside the vocabulary
        cannot even be sent, so a client learns nothing about which states
        exist from a validation error.
        """
        idea = make_idea(world['organization'], world['author'])

        response = gql(
            TRANSITION,
            {'id': str(idea.pk), 'to': 'APPROVED_BY_MYSELF'},
            bearer=world['author_token'],
        )

        assert 'errors' in response.json()
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.DRAFT


# --- status cannot be set directly ---------------------------------------------------


@pytest.mark.django_db
class TestStatusCannotBeManipulatedDirectly:
    def test_update_idea_has_no_status_field(self, gql, world):
        """
        The strongest form of the claim: the request is sent, and the schema
        rejects it. Reading the input class would only prove today's shape;
        this proves the boundary enforces it.
        """
        idea = make_idea(world['organization'], world['author'], status=Idea.Status.SUBMITTED)

        response = gql(
            UPDATE_IDEA_WITH_STATUS,
            {
                'input': {
                    'id': str(idea.pk),
                    'idea': {
                        'title': 'Promoted',
                        'description': DESCRIPTION,
                        'status': 'APPROVED',
                    },
                }
            },
            bearer=world['author_token'],
        )

        body = response.json()
        assert 'errors' in body, body
        assert Idea.objects.get(pk=idea.pk).title == 'An idea'
        assert Idea.objects.get(pk=idea.pk).status == Idea.Status.SUBMITTED

    def test_there_is_no_mutation_that_takes_a_bare_status(self, gql):
        """
        Every root mutation is enumerated and checked for a status argument.
        Catches a future mutation that reintroduces the shortcut under a
        different name, which a per-test assertion would not.

        Introspected over the real endpoint rather than through the schema
        object, so what is checked is what a client is actually offered.
        """
        arguments_by_field = _introspect_mutation_arguments(gql)

        # The only place a status may be named is the transition mutation.
        assert arguments_by_field['transitionIdea'] == {'id', 'to'}
        for name, arguments in arguments_by_field.items():
            if name == 'transitionIdea':
                continue
            assert 'status' not in arguments, name
            assert 'to' not in arguments, name

    def test_no_write_input_carries_a_status(self):
        """
        The service layer's inputs, checked directly: an `IdeaInput` with a
        `status` field would be a way to change a status without the matrix,
        whatever the schema currently exposes.
        """
        import dataclasses

        from ideas.services import IdeaInput

        assert 'status' not in {f.name for f in dataclasses.fields(IdeaInput)}


# --- what the client is offered -----------------------------------------------------


@pytest.mark.django_db
class TestAvailableTransitionsField:
    def test_it_reports_the_viewers_own_moves(self, gql, world):
        idea = make_idea(world['organization'], world['author'], status=Idea.Status.SUBMITTED)

        as_reviewer = run(
            gql, IDEA_QUERY, 'idea', {'id': str(idea.pk)}, bearer=world['reviewer_token']
        )
        as_author = run(gql, IDEA_QUERY, 'idea', {'id': str(idea.pk)}, bearer=world['author_token'])
        as_member = run(gql, IDEA_QUERY, 'idea', {'id': str(idea.pk)}, bearer=world['member_token'])

        # Starting a review is offered through `viewerCanStartReview` since
        # S3-004, not as a transition `transitionIdea` would refuse.
        assert as_reviewer['availableTransitions'] == []
        # The author holds the Owner role and is still offered nothing on their
        # own submitted idea.
        assert as_author['availableTransitions'] == []
        assert as_member['availableTransitions'] == []

    def test_it_is_empty_for_an_invisible_idea(self, gql, world):
        private = make_idea(
            world['organization'],
            world['author'],
            visibility=Idea.Visibility.PRIVATE,
            status=Idea.Status.SUBMITTED,
        )

        assert (
            run(gql, IDEA_QUERY, 'idea', {'id': str(private.pk)}, bearer=world['member_token'])
            is None
        )

    def test_a_list_reports_the_viewers_moves_per_item(self, gql, world):
        """
        Per item, not per request: a platform reviewer looking at a list is
        offered the hand-off on each idea that has been through it, and nothing
        on the ideas that have not - or on their own.
        """
        theirs = platform_approve(world, platform_idea(world))
        confirm_go_ahead(world['author'], theirs.pk)
        theirs.refresh_from_db()
        assert theirs.status == Idea.Status.READY_FOR_IMPLEMENTATION
        # The same viewer's **own** idea, at the same point in the journey, so
        # the only difference between the two rows is who wrote it. Holding the
        # platform permission is not a licence to review yourself: the
        # self-review rule offers the author nothing, which is what stops a
        # reviewer handing their own approved idea to the developer track.
        mine = platform_approve(world, platform_idea(world, author=world['platform_author']))
        confirm_go_ahead(world['platform_author'], mine.pk)
        mine.refresh_from_db()
        assert mine.status == Idea.Status.READY_FOR_IMPLEMENTATION

        page = run(gql, IDEAS_QUERY, 'ideas', bearer=world['platform_author_token'])
        by_id = {idea['id']: idea['availableTransitions'] for idea in page['items']}

        assert by_id[str(theirs.pk)] == ['AUTOMATION_PROPOSAL']
        assert by_id[str(mine.pk)] == []


# --- payload shape and safety -------------------------------------------------------


@pytest.mark.django_db
class TestPayloadShape:
    def test_a_success_reports_the_state_and_where_it_went_next(self, gql, world):
        idea = individual_idea(world)

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': 'SUBMITTED'},
            bearer=world['author_token'],
        )

        assert result['success'] is True
        assert result['field'] is None
        assert result['message'] == 'Idea moved to Submitted.'

    def test_a_refusal_carries_no_field(self, gql, world):
        """
        A refused transition is a whole-idea outcome, not a bad input, so there
        is no field to point at - the same convention S1-009 and S2-002 set.
        """
        idea = make_idea(world['organization'], world['author'])

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': 'APPROVED'},
            bearer=world['reviewer_token'],
        )

        assert result['success'] is False
        assert result['field'] is None

    @pytest.mark.parametrize(
        'to',
        ['SUBMITTED', 'SUBMITTED_TO_ORGANIZATION'],
        ids=['individual', 'organization'],
    )
    def test_an_incomplete_submission_is_refused_without_a_field(self, gql, world, to):
        """
        The completeness rule at the boundary, on **both** stages.

        The two stages submit the same idea, so they must accept the same idea:
        `SUBMISSION_TARGETS` deliberately includes `SUBMITTED_TO_ORGANIZATION`
        for exactly this reason, and an organization reviewer cannot read a
        `PRIVATE` idea, so submitting one to the organization would park it in a
        queue nobody in that organization could ever reach. Both refusals are
        about the *idea* - a field that is blank - so neither carries a `field`.
        """
        idea = make_idea(world['organization'], world['author'], description='')
        if to == 'SUBMITTED':
            # An individual idea, because `SUBMITTED` is that context's door.
            idea = individual_idea(world, description='')

        result = run(
            gql,
            TRANSITION,
            'transitionIdea',
            {'id': str(idea.pk), 'to': to},
            bearer=world['author_token'],
        )

        assert result['success'] is False
        assert result['field'] is None
        assert 'Describe the problem' in result['message']

    def test_the_idea_type_still_exposes_no_user_object(self, gql):
        """
        The S2-002 safety property, re-asserted now the type has gained a field:
        adding `availableTransitions` must not have opened a route to a member's
        email on a platform-readable idea.
        """
        fields = _introspect_type_fields(gql, 'IdeaType')

        assert 'availableTransitions' in fields
        for forbidden in ('author', 'user', 'email', 'password', 'token', 'hasUsablePassword'):
            assert forbidden not in fields, forbidden
