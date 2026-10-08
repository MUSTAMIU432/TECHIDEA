"""
Cross-feature security and concurrency for the review workflow (S3-008).

The S2-008 pattern extended to reviews: two organizations, each with an
author, a reviewer and ideas in every review state, probed in both directions
through the GraphQL API - the queue, the review and audit histories, the
capability flags and both review operations - with ids that exist, ids
borrowed from the other tenant, and ids that do not exist. Then the ways the
lifecycle could be bypassed, and the races the per-operation suites do not
already cover, each checked against the full record invariants
(`reviews/tests/invariants.py`), not just the winner's status.
"""

import json
import threading

import pytest
from django.db import connections
from django.utils import timezone

from graphql_api.schema import schema as root_schema
from ideas import services as idea_services
from ideas.lifecycle import REVIEW_OWNED_TRANSITIONS
from ideas.models import Category, Idea, IdeaTransition
from ideas.services import IdeaError
from identity.models import User
from identity.tokens import issue_access_token
from organizations.models import Membership, MembershipRole, Role
from organizations.services import (
    REVIEWER_ROLE_SLUG,
    CreateOrganizationInput,
    create_organization_for_user,
)
from reviews import services
from reviews.models import Review, ReviewCriterionAssessment
from reviews.tests.invariants import assert_review_records_consistent
from reviews.tests.platform import (
    confirm_for_organization,
    grant_platform_reviewer,
    revoke_platform_reviewer,
)

S = Idea.Status
CRITERIA = ReviewCriterionAssessment.Criterion.values


def make_user(email):
    return User.objects.create_user(
        email=email,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
        password='a-strong-unique-pass-1',
    )


def add_member(organization, user, *, reviewer=False):
    membership = Membership.objects.create(user=user, organization=organization)
    if reviewer:
        MembershipRole.objects.create(
            membership=membership,
            role=Role.objects.get(organization=organization, slug=REVIEWER_ROLE_SLUG),
        )
        # A **platform** reviewer as well as an organization one, because this
        # fixture builds submissions and their reviews - and platform review is
        # authorized by a platform permission and nothing else.
        #
        # That makes every holder of it cross-tenant-readable, which is the point
        # of platform review but the opposite of what the isolation tests below
        # are about. Those tests therefore **revoke it** from their caller first,
        # so they measure organization isolation rather than accidentally measuring
        # the platform track; and
        # `test_a_platform_reviewer_reads_across_tenants_and_cannot_act_on_someone
        # _elses_review` is what covers the platform side deliberately.
        grant_platform_reviewer(user)


def completion(review, decision, feedback='Because.'):
    return services.CompleteReviewInput(
        idea_id=review.idea_id,
        review_id=review.pk,
        decision=decision,
        feedback=feedback,
        assessments=tuple(services.AssessmentInput(c, 'meets') for c in CRITERIA),
    )


def build_tenant(name, category):
    owner = make_user(f'owner@{name}.example')
    organization = create_organization_for_user(
        owner, CreateOrganizationInput(name=name.title())
    ).organization
    author = make_user(f'author@{name}.example')
    add_member(organization, author)
    reviewer = make_user(f'reviewer@{name}.example')
    add_member(organization, reviewer, reviewer=True)

    def idea(visibility, title):
        created = idea_services.create_idea(
            author,
            organization.pk,
            idea_services.IdeaInput(
                title=title,
                description='A description long enough to be submitted.',
                category_id=category.pk,
            ),
        )
        idea_services.submit_idea(author, created.pk)
        # An organization idea is validated by its organization before the
        # platform sees it, so getting it to the platform takes three moves by
        # three actors. The organization helper reviewer does the middle one; the
        # author submits on.
        created.refresh_from_db()
        confirm_for_organization(created)
        created.refresh_from_db()
        idea_services.submit_to_platform(author, created.pk)
        return created

    ideas = {}
    for visibility in (Idea.Visibility.ORGANIZATION, Idea.Visibility.PUBLIC):
        waiting = idea(visibility, f'{name} {visibility} waiting')
        in_review = idea(visibility, f'{name} {visibility} in review')
        open_review = services.start_review(reviewer, in_review.pk)
        decided = idea(visibility, f'{name} {visibility} decided')
        closed = services.start_review(reviewer, decided.pk)
        services.complete_review(reviewer, completion(closed, 'changes_requested'))
        ideas[visibility] = {
            'waiting': waiting,
            'in_review': in_review,
            'open_review': open_review,
            'decided': decided,
            'closed_review': closed,
        }

    # Stored directly: submission refuses these since S3-008, but ideas moved
    # before it can exist, and must stay out of reach of every reviewer.
    hidden = {}
    for visibility in (Idea.Visibility.PRIVATE, Idea.Visibility.DEPARTMENT):
        hidden[visibility] = Idea.objects.create(
            organization=organization,
            author=author,
            title=f'{name} {visibility} legacy',
            description='A description long enough to be submitted.',
            category=category,
            visibility=visibility,
            status=S.SUBMITTED,
            submitted_at=timezone.now(),
        )

    return {
        'organization': organization,
        'owner': owner,
        'author': author,
        'reviewer': reviewer,
        'ideas': ideas,
        'hidden': hidden,
    }


@pytest.fixture
def tenants(db):
    category = Category.objects.create(name='Finance')
    return {'acme': build_tenant('acme', category), 'globex': build_tenant('globex', category)}


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
        assert 'errors' not in body, body
        return body['data']

    return post


QUEUE = 'query Q($o: ID!) { reviewQueue(organizationId: $o) { items { id } } }'
CAN_REVIEW = 'query Q($o: ID!) { viewerCanReviewIn(organizationId: $o) }'
REVIEWS = 'query Q($i: ID!) { ideaReviews(ideaId: $i) { id } }'
TRANSITIONS = 'query Q($i: ID!) { ideaTransitions(ideaId: $i) { toStatus } }'
IDEA = 'query Q($i: ID!) { idea(id: $i) { viewerCanStartReview viewerActiveReviewId } }'
START = 'mutation M($i: ID!) { startReview(ideaId: $i) { success message } }'
COMPLETE = """
mutation M($input: CompleteReviewInput!) { completeReview(input: $input) { success message } }
"""
TRANSITION = 'mutation M($i: ID!, $to: IdeaStatus!) { transitionIdea(id: $i, to: $to) { success } }'


def complete_vars(idea_id, review_id, decision='APPROVED'):
    return {
        'input': {
            'ideaId': idea_id,
            'reviewId': review_id,
            'decision': decision,
            'feedback': 'Because.',
            'assessments': [{'criterion': c.upper(), 'rating': 'MEETS'} for c in CRITERIA],
        }
    }


def snapshot():
    return (
        sorted(Review.objects.values_list('pk', 'decision', 'completed_at', 'reviewer_id')),
        sorted(IdeaTransition.objects.values_list('pk', flat=True)),
        sorted(Idea.objects.values_list('pk', 'status')),
    )


# --- A. tenant isolation, both directions -----------------------------------------------


@pytest.mark.parametrize(('home', 'away'), [('acme', 'globex'), ('globex', 'acme')])
@pytest.mark.parametrize('who', ['owner', 'reviewer', 'author'])
def test_nothing_of_another_tenants_reviews_is_reachable(gql, tenants, home, away, who):
    """
    A reviewer, owner or author of one organization, against every review
    surface of the other - including `PUBLIC` ideas they can read, and review
    ids borrowed under their own ideas.
    """
    caller = tenants[home][who]
    other = tenants[away]
    own_idea = tenants[home]['ideas'][Idea.Visibility.ORGANIZATION]['waiting']
    before = snapshot()

    # Measure **organization** isolation: the caller keeps their organization
    # Reviewer role and loses only the platform permission, so this test cannot
    # pass or fail because of the platform track.
    if who == 'reviewer':
        revoke_platform_reviewer(caller)

    assert gql(QUEUE, {'o': other['organization'].pk}, caller) == {'reviewQueue': {'items': []}}
    assert gql(CAN_REVIEW, {'o': other['organization'].pk}, caller) == {'viewerCanReviewIn': False}

    # Their own tenant's history, for comparison, and it is deliberately not
    # uniform: the **author** sees their own idea's reviews, while an organization
    # Owner or Reviewer sees its lifecycle (every row names who moved it) but not
    # the platform reviews. Two different audiences with two different claims -
    # which is the split this phase introduced.
    if who == 'author':
        assert gql(REVIEWS, {'i': own_idea.pk}, caller)['ideaReviews']
    else:
        assert gql(REVIEWS, {'i': own_idea.pk}, caller) == {'ideaReviews': []}
    assert gql(TRANSITIONS, {'i': own_idea.pk}, caller)['ideaTransitions']

    for states in other['ideas'].values():
        for key in ('waiting', 'in_review', 'decided'):
            idea_id = states[key].pk
            # Unreadable to this caller, whatever the idea's visibility: they are
            # in another organization and hold nothing platform-scoped.
            assert gql(REVIEWS, {'i': idea_id}, caller) == {'ideaReviews': []}
            assert gql(TRANSITIONS, {'i': idea_id}, caller) == {'ideaTransitions': []}
            assert gql(START, {'i': idea_id}, caller)['startReview']['success'] is False
            flags = gql(IDEA, {'i': idea_id}, caller)['idea']
            if flags is not None:  # PUBLIC: readable, still not reviewable
                assert flags == {'viewerCanStartReview': False, 'viewerActiveReviewId': None}
        for review_key in ('open_review', 'closed_review'):
            review = states[review_key]
            for idea_id in (review.idea_id, own_idea.pk):  # its own idea, and a borrowed one
                result = gql(COMPLETE, complete_vars(idea_id, review.pk), caller)
                assert result['completeReview'] == {
                    'success': False,
                    'message': result['completeReview']['message'],
                }
                assert result['completeReview']['message'] in {
                    'Idea is unavailable.',
                    'You are not allowed to review this idea.',
                    'Review is unavailable.',
                }

    assert snapshot() == before


def test_a_platform_reviewer_reads_across_tenants_and_cannot_act_on_someone_elses_review(
    gql, tenants
):
    """
    Platform review is cross-tenant, and that is the point of it.

    A platform reviewer has no organization of their own here, so this is the
    assertion that separates the two tracks completely: they can *read* every
    organization's submissions and review history, and can *act* on none of them
    - not on an open review somebody else holds, and not on a review they are not
    the reviewer of.
    """
    reviewer = tenants['acme']['reviewer']
    grant_platform_reviewer(reviewer)

    for tenant in ('acme', 'globex'):
        for states in tenants[tenant]['ideas'].values():
            for key in ('waiting', 'in_review', 'decided'):
                idea_id = states[key].pk
                rows = gql(REVIEWS, {'i': idea_id}, reviewer)['ideaReviews']
                assert rows, f'{tenant}/{key} is readable to a platform reviewer'
                assert gql(TRANSITIONS, {'i': idea_id}, reviewer)['ideaTransitions']
                # Called **once** and kept: `startReview` is a mutation, so
                # asking twice would claim it and then be refused, and the second
                # answer would say nothing about the first.
                claim = gql(START, {'i': idea_id}, reviewer)['startReview']
                if key == 'waiting':
                    # Waiting submissions are exactly what a platform reviewer may
                    # claim - across every tenant, which is the point.
                    assert claim['success'] is True, (tenant, key, states[key].visibility, claim)
                else:
                    assert claim['success'] is False, (tenant, key, claim)

                # Deciding somebody else's open review is refused, and so is
                # borrowing a review id across ideas.
                open_review = states['open_review']
                result = gql(COMPLETE, complete_vars(open_review.idea_id, open_review.pk), reviewer)
                # For `waiting` the platform reviewer has just claimed it above, so
                # they now hold that review - and completing somebody else's is
                # what is still refused.
                if key != 'waiting':
                    assert result['completeReview']['success'] is False
                borrowed = gql(COMPLETE, complete_vars(idea_id, open_review.pk), reviewer)
                assert borrowed['completeReview']['success'] is False

    # And nothing of the other tenant's *private* legacy rows. The platform
    # reviewer branch in the visibility filter is deliberately narrow - only an
    # idea that was actually submitted to the platform - so an idea that was never
    # submitted stays unreadable however platform-scoped the reader is.
    for visibility in (Idea.Visibility.PRIVATE, Idea.Visibility.DEPARTMENT):
        hidden = tenants['globex']['hidden'][visibility].pk
        assert gql(REVIEWS, {'i': hidden}, reviewer) == {'ideaReviews': []}
        assert gql(TRANSITIONS, {'i': hidden}, reviewer) == {'ideaTransitions': []}

    # ...and once claimed, it is theirs: the same claim is refused a second time
    # because an eligible reviewer's open review is never taken from them.
    already = tenants['globex']['ideas'][Idea.Visibility.PUBLIC]['waiting'].pk
    assert gql(START, {'i': already}, reviewer)['startReview']['success'] is False


def test_unavailable_and_nonexistent_answer_alike(gql, tenants):
    caller = tenants['acme']['reviewer']
    # Not a platform reviewer, or they could read the other tenant's submission
    # and this pair would not be alike for the wrong reason.
    revoke_platform_reviewer(caller)
    foreign = tenants['globex']['ideas'][Idea.Visibility.ORGANIZATION]['waiting'].pk
    missing = Idea.objects.order_by('-pk').first().pk + 1000

    answers = [
        (
            gql(START, {'i': idea_id}, caller),
            gql(REVIEWS, {'i': idea_id}, caller),
            gql(TRANSITIONS, {'i': idea_id}, caller),
        )
        for idea_id in (foreign, missing)
    ]

    assert answers[0] == answers[1]


@pytest.mark.parametrize('visibility', [Idea.Visibility.PRIVATE, Idea.Visibility.DEPARTMENT])
def test_private_and_department_ideas_stay_out_of_every_reviewers_reach(gql, tenants, visibility):
    acme = tenants['acme']
    hidden = acme['hidden'][visibility]
    for reviewer in (acme['reviewer'], acme['owner'], tenants['globex']['reviewer']):
        queue = gql(QUEUE, {'o': acme['organization'].pk}, reviewer)['reviewQueue']['items']
        assert str(hidden.pk) not in {item['id'] for item in queue}
        assert gql(IDEA, {'i': hidden.pk}, reviewer) == {'idea': None}
        assert gql(START, {'i': hidden.pk}, reviewer)['startReview']['success'] is False
        assert gql(REVIEWS, {'i': hidden.pk}, reviewer) == {'ideaReviews': []}
        assert gql(TRANSITIONS, {'i': hidden.pk}, reviewer) == {'ideaTransitions': []}
    assert not Review.objects.filter(idea=hidden).exists()


# --- E. the lifecycle cannot be bypassed ------------------------------------------------


@pytest.mark.parametrize('pair', sorted(REVIEW_OWNED_TRANSITIONS), ids=lambda p: f'{p[0]}->{p[1]}')
def test_no_review_owned_move_is_made_through_transition_idea(gql, tenants, pair):
    acme = tenants['acme']
    states = acme['ideas'][Idea.Visibility.ORGANIZATION]
    idea = states['waiting'] if pair[0] == S.SUBMITTED else states['in_review']
    before = snapshot()

    for caller in (acme['reviewer'], acme['owner'], acme['author']):
        result = gql(TRANSITION, {'i': idea.pk, 'to': pair[1].upper()}, caller)
        assert result == {'transitionIdea': {'success': False}}

    assert snapshot() == before


def _input_field_names(graphql_type, seen=None):
    """Every field name reachable through a mutation argument's input types."""
    from graphql import get_named_type, is_input_object_type

    seen = set() if seen is None else seen
    named = get_named_type(graphql_type)
    if not is_input_object_type(named) or named.name in seen:
        return set()
    seen.add(named.name)
    names = set(named.fields)
    for field in named.fields.values():
        names |= _input_field_names(field.type, seen)
    return names


def test_the_only_writes_to_reviews_and_the_trail_are_the_two_review_operations():
    mutations = root_schema._schema.mutation_type.fields

    # Four review mutations, because there are two tracks - and no way to write a
    # `Review` row directly, in either of them.
    # Every mutation that writes a `Review` row, in either track. Scoped on the
    # names that *start or complete* a review so that routing
    # (`assignPlatformReviewer`) is not counted here - it writes a `ReviewAssignment`,
    # which is a different table and a different operation.
    review_mutations = [
        m
        for m in mutations
        if m.startswith(
            ('startReview', 'completeReview', 'startOrganization', 'completeOrganization')
        )
    ]
    assert set(review_mutations) == {
        'startReview',
        'completeReview',
        'startOrganizationReview',
        'completeOrganizationReview',
    }
    assert {m for m in mutations if 'ransition' in m} == {'transitionIdea'}
    # The owner's go-ahead is the only way into `READY_FOR_IMPLEMENTATION`, and
    # it is a mutation rather than a status a client can ask for.
    assert {m for m in mutations if 'GoAhead' in m} == {'giveGoAhead'}
    # No write input carries a status, a reviewer, an actor, a round or a time:
    # each is the server's to decide.
    for mutation in (
        'createIdea',
        'updateIdea',
        'submitIdea',
        'startReview',
        'completeReview',
        'startOrganizationReview',
        'completeOrganizationReview',
    ):
        names = set(mutations[mutation].args)
        for argument in mutations[mutation].args.values():
            names |= _input_field_names(argument.type)
        forbidden = {'status', 'actorId', 'reviewerId', 'authorId', 'round', 'completedAt'}
        assert not names & forbidden, (mutation, names & forbidden)


def test_update_idea_cannot_carry_a_status(client, tenants):
    idea = tenants['acme']['ideas'][Idea.Visibility.ORGANIZATION]['waiting']
    token = issue_access_token(tenants['acme']['author'].pk)[0]
    query = """
        mutation Update($id: ID!) {
          updateIdea(input: {id: $id, idea: {title: "x", status: APPROVED}}) { success }
        }
    """

    body = client.post(
        '/graphql/',
        data=json.dumps({'query': query, 'variables': {'id': idea.pk}}),
        content_type='application/json',
        HTTP_AUTHORIZATION=f'Bearer {token}',
    ).json()

    assert 'errors' in body
    assert Idea.objects.get(pk=idea.pk).status == S.SUBMITTED


def test_a_review_cannot_be_completed_under_another_idea_of_the_same_tenant(tenants):
    acme = tenants['acme']
    states = acme['ideas'][Idea.Visibility.ORGANIZATION]
    other_in_review = acme['ideas'][Idea.Visibility.PUBLIC]['in_review']
    before = snapshot()

    with pytest.raises(services.ReviewError) as exc_info:
        services.complete_review(
            acme['reviewer'],
            services.CompleteReviewInput(
                idea_id=other_in_review.pk,
                review_id=states['open_review'].pk,
                decision='approved',
                feedback='',
                assessments=completion(states['open_review'], 'approved').assessments,
            ),
        )

    assert exc_info.value.message == 'Review is unavailable.'
    assert snapshot() == before


def test_every_record_built_by_the_fixtures_is_consistent(tenants):
    for tenant in tenants.values():
        for states in tenant['ideas'].values():
            for key in ('waiting', 'in_review', 'decided'):
                assert_review_records_consistent(states[key])


# --- F. races -------------------------------------------------------------------------


def race(*calls):
    """Run each call on its own connection, released together."""
    barrier = threading.Barrier(len(calls))
    outcomes = [None] * len(calls)

    def run(index, call):
        try:
            barrier.wait(timeout=10)
            outcomes[index] = ('ok', call())
        except (services.ReviewError, IdeaError) as exc:
            outcomes[index] = ('refused', exc.message)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


@pytest.fixture
def one_tenant(db):
    owner = make_user('owner@acme.example')
    acme = create_organization_for_user(owner, CreateOrganizationInput(name='Acme')).organization
    author = make_user('author@acme.example')
    add_member(acme, author)
    reviewer = make_user('reviewer@acme.example')
    add_member(acme, reviewer, reviewer=True)
    second = make_user('second@acme.example')
    add_member(acme, second, reviewer=True)
    idea = idea_services.create_idea(
        author,
        acme.pk,
        idea_services.IdeaInput(
            title='Automate the invoice run',
            description='A description long enough to be submitted.',
            category_id=Category.objects.create(name='Finance').pk,
        ),
    )
    idea_services.submit_idea(author, idea.pk)
    # On to the platform: an organization idea is confirmed by its organization
    # first, and only the author submits it afterwards.
    idea.refresh_from_db()
    confirm_for_organization(idea)
    idea.refresh_from_db()
    idea_services.submit_to_platform(author, idea.pk)
    return {'author': author, 'reviewer': reviewer, 'second': second, 'idea': idea}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    'decisions',
    [('changes_requested', 'approved'), ('changes_requested', 'rejected')],
    ids=lambda d: ' vs '.join(d),
)
def test_two_decisions_at_once_leave_exactly_one(one_tenant, decisions):
    review = services.start_review(one_tenant['reviewer'], one_tenant['idea'].pk)

    outcomes = race(
        *(
            lambda d=d: services.complete_review(one_tenant['reviewer'], completion(review, d))
            for d in decisions
        )
    )

    assert sorted(kind for kind, _ in outcomes) == ['ok', 'refused']
    winner = next(value for kind, value in outcomes if kind == 'ok')
    idea = Idea.objects.get(pk=one_tenant['idea'].pk)
    assert idea.status == winner.decision
    assert Review.objects.get(pk=review.pk).assessments.count() == len(CRITERIA)
    assert_review_records_consistent(idea)


@pytest.mark.django_db(transaction=True)
def test_two_reviewers_starting_at_once_leave_one_review(one_tenant):
    outcomes = race(
        lambda: services.start_review(one_tenant['reviewer'], one_tenant['idea'].pk),
        lambda: services.start_review(one_tenant['second'], one_tenant['idea'].pk),
    )

    assert sorted(kind for kind, _ in outcomes) == ['ok', 'refused']
    # One **platform** review: the organization's confirmation that got the idea
    # onto the platform is a separate round in the other track.
    assert Review.objects.filter(idea=one_tenant['idea'], scope=Review.Scope.PLATFORM).count() == 1
    assert_review_records_consistent(one_tenant['idea'])


@pytest.mark.django_db(transaction=True)
def test_resubmission_racing_the_completion_that_allows_it(one_tenant):
    """
    The author resubmits at the moment the reviewer requests changes. The
    idea row lock serializes them: either the resubmission ran first and was
    refused (the idea was still under review), or it ran second and succeeded.
    Both are valid; a half-state is not.
    """
    review = services.start_review(one_tenant['reviewer'], one_tenant['idea'].pk)

    complete, resubmit = race(
        lambda: services.complete_review(
            one_tenant['reviewer'], completion(review, 'changes_requested')
        ),
        lambda: idea_services.submit_idea(one_tenant['author'], one_tenant['idea'].pk),
    )

    assert complete[0] == 'ok'
    idea = Idea.objects.get(pk=one_tenant['idea'].pk)
    if resubmit[0] == 'ok':
        assert idea.status == S.SUBMITTED
    else:
        assert idea.status == S.CHANGES_REQUESTED
    assert Review.objects.filter(idea=idea, scope=Review.Scope.PLATFORM).count() == 1
    assert_review_records_consistent(idea)


@pytest.mark.django_db(transaction=True)
def test_a_take_over_racing_the_original_reviewers_reinstated_completion(one_tenant):
    """
    The reviewer loses eligibility and is reinstated just as another reviewer
    takes over. Whichever holds the idea lock first decides the outcome; the
    records agree with it either way.
    """
    review = services.start_review(one_tenant['reviewer'], one_tenant['idea'].pk)
    membership = Membership.objects.filter(user=one_tenant['reviewer'])
    membership.update(status=Membership.Status.INACTIVE)

    def reinstate_then_complete():
        membership.update(status=Membership.Status.ACTIVE)
        return services.complete_review(one_tenant['reviewer'], completion(review, 'approved'))

    completed, taken = race(
        reinstate_then_complete,
        lambda: services.start_review(one_tenant['second'], one_tenant['idea'].pk),
    )

    idea = Idea.objects.get(pk=one_tenant['idea'].pk)
    # The completion is refused only if the take-over withdrew the review first.
    assert [completed[0], taken[0]].count('ok') == 1
    if taken[0] == 'ok':
        assert completed[0] == 'refused'
        assert idea.status == S.UNDER_REVIEW
    else:
        assert idea.status == S.APPROVED
    assert_review_records_consistent(idea)
