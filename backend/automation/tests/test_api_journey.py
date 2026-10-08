"""The whole delivery lifecycle, driven only through the GraphQL API."""

import json

import pytest
from django.test import Client

from automation.tests.test_opportunity import PASSWORD, make_user, opened, ready_idea
from reviews import review_teams
from reviews.tests.platform import grant_permission
from reviews.tests.test_proposals import approve_with_a_team

LOGIN = 'mutation($i: LoginInput!){ login(input: $i){ success accessToken } }'


class Api:
    def __init__(self, user):
        self.client = Client()
        data = self._post(LOGIN, {'i': {'email': user.email, 'password': PASSWORD}}, None)
        self.token = data['login']['accessToken']

    def _post(self, query, variables, token):
        extra = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
        response = self.client.post(
            '/graphql/',
            data=json.dumps({'query': query, 'variables': variables}),
            content_type='application/json',
            **extra,
        )
        body = response.json()
        assert 'errors' not in body, body
        return body['data']

    def __call__(self, query, variables=None):
        return self._post(query, variables or {}, self.token)

    def ok(self, query, root, variables=None):
        """Run a mutation that must succeed and return its payload."""
        payload = self(query, variables)[root]
        assert payload['success'] is True, payload
        return payload


@pytest.fixture
def people(db):
    author = make_user('author@example.com')
    manager = make_user('manager@example.com')
    grant_permission(manager, 'automation.manage_delivery')
    developer = make_user('dev@example.com')
    grant_permission(developer, 'automation.be_assignable')
    stranger = make_user('stranger@example.com')
    return {
        'author': author,
        'manager': manager,
        'developer': developer,
        'stranger': stranger,
        'api': {
            name: Api(user)
            for name, user in {
                'author': author,
                'manager': manager,
                'developer': developer,
                'stranger': stranger,
            }.items()
        },
    }


def admin(email, *codes):
    user = make_user(email)
    grant_permission(user, 'administration.access_console')
    for code in codes:
        grant_permission(user, code)
    return user


@pytest.mark.django_db
def test_an_approved_idea_becomes_a_measured_completed_project(people):
    api = people['api']
    author, manager, developer = api['author'], api['manager'], api['developer']

    # --- an idea a review team approved (the review itself is tested in `reviews`)
    team_manager = admin('teams@example.com', 'administration.manage_reviewers')
    router = admin('router@example.com', 'administration.assign_platform_reviewers')
    releaser = admin('releaser@example.com', 'administration.release_proposals')
    lead = review_teams.grant_reviewer(team_manager, make_user('lead@example.com').email)
    member = review_teams.grant_reviewer(team_manager, make_user('member@example.com').email)
    idea = approve_with_a_team(people['author'], router, team_manager, lead, member)
    iid = str(idea.pk)
    writer, team_lead, admin_api = Api(member), Api(lead), Api(releaser)

    # --- the review team writes the proposal; the lead sends it to the admin
    writer.ok(
        'mutation($i: ID!){ startIdeaProposal(ideaId:$i){ success } }',
        'startIdeaProposal',
        {'i': iid},
    )
    writer.ok(
        """mutation($i: ID!){ updateIdeaProposal(input:{ideaId:$i, executiveSummary:"Stop re-typing",
          problem:"Payments are re-typed", proposedSolution:"A dashboard", scope:"Import and match",
          deliverables:"Dashboard", estimatedTimeline:"6 weeks", acceptanceCriteria:"No re-typing",
          requirementsSummary:"- Track every payment", feasibility:"The bank exports a CSV",
          milestones:"Weeks 1-2 import; 3-6 dashboard", financialRequirements:"Hosting only",
          paymentRequired:"yes", paymentPlan:"Half on start, half on acceptance"}){ success } }""",
        'updateIdeaProposal',
        {'i': iid},
    )
    assert (
        writer('mutation($i: ID!){ submitIdeaProposal(ideaId:$i){ success } }', {'i': iid})[
            'submitIdeaProposal'
        ]['success']
        is False
    )  # only the lead sends it
    team_lead.ok(
        'mutation($i: ID!){ submitIdeaProposal(ideaId:$i){ success } }',
        'submitIdeaProposal',
        {'i': iid},
    )
    state = 'query($i: ID!){ ideaProposalState(ideaId:$i){ viewerRole proposal{ status } } }'
    assert author(state, {'i': iid})['ideaProposalState']['proposal'] is None  # not released yet

    # --- the admin releases it; the owner reads it, then gives the go-ahead
    admin_api.ok(
        'mutation($i: ID!){ releaseIdeaProposal(ideaId:$i){ success } }',
        'releaseIdeaProposal',
        {'i': iid},
    )
    seen = author(state, {'i': iid})['ideaProposalState']
    assert (seen['viewerRole'], seen['proposal']['status']) == ('owner', 'released')
    receipt = author.ok(
        'mutation($i: ID!){ recordProposalView(ideaId:$i){ success receipt{ viewerEmail } } }',
        'recordProposalView',
        {'i': iid},
    )['receipt']
    assert receipt['viewerEmail'] == people['author'].email
    author.ok('mutation($i: ID!){ giveGoAhead(id:$i){ success } }', 'giveGoAhead', {'i': iid})

    # --- the go-ahead opened the opportunity, ready for a developer, carrying the proposal
    opportunity = author(
        'query($i: ID!){ automationOpportunityForIdea(ideaId:$i){ id status } }', {'i': iid}
    )['automationOpportunityForIdea']
    assert opportunity['status'] == 'ready_for_assignment'
    oid = opportunity['id']
    agreed = author('query($o: ID!){ proposal(opportunityId:$o){ status scope } }', {'o': oid})[
        'proposal'
    ]
    assert agreed == {'status': 'accepted', 'scope': 'Import and match'}

    # --- developer queue and assignment
    queue = manager('query{ developerQueue{ id status } }')['developerQueue']
    assert [(q['id'], q['status']) for q in queue] == [(oid, 'ready_for_assignment')]
    assert api['stranger']('query{ developerQueue{ id } }')['developerQueue'] == []
    manager.ok(
        'mutation($o: ID!, $u: ID){ assignOpportunity(opportunityId:$o, assigneeUserId:$u){ success } }',
        'assignOpportunity',
        {'o': oid, 'u': str(people['developer'].pk)},
    )

    # --- project and development
    project = developer.ok(
        'mutation($o: ID!){ createProject(opportunityId:$o){ success project{ id status } } }',
        'createProject',
        {'o': oid},
    )['project']
    pid = project['id']
    developer.ok('mutation($i: ID!){ startProject(id:$i){ success } }', 'startProject', {'i': pid})
    milestone = developer.ok(
        'mutation($p: ID!){ createMilestone(projectId:$p, title:"MVP"){ success milestone{ id } } }',
        'createMilestone',
        {'p': pid},
    )['milestone']['id']
    task = developer.ok(
        'mutation($p: ID!, $m: ID){ createTask(projectId:$p, title:"Build", milestoneId:$m){ success task{ id } } }',
        'createTask',
        {'p': pid, 'm': milestone},
    )['task']['id']
    developer.ok(
        'mutation($t: ID!){ updateTask(input:{id:$t, status:"done"}){ success } }',
        'updateTask',
        {'t': task},
    )
    developer.ok(
        'mutation($m: ID!){ completeMilestone(id:$m){ success } }',
        'completeMilestone',
        {'m': milestone},
    )

    # --- testing blocks, then passes
    case = developer.ok(
        'mutation($p: ID!){ createTestCase(projectId:$p, title:"Import"){ success testCase{ id } } }',
        'createTestCase',
        {'p': pid},
    )['testCase']['id']
    developer.ok(
        'mutation($i: ID!){ startProjectTesting(id:$i){ success } }',
        'startProjectTesting',
        {'i': pid},
    )
    developer.ok(
        'mutation($c: ID!){ recordTestResult(id:$c, status:"fail"){ success } }',
        'recordTestResult',
        {'c': case},
    )
    blocked = developer(
        'mutation($i: ID!){ submitProjectForUat(id:$i){ success message } }', {'i': pid}
    )['submitProjectForUat']
    assert blocked['success'] is False
    assert '1 required test case has failed' in blocked['message']
    developer.ok(
        'mutation($c: ID!){ recordTestResult(id:$c, status:"pass"){ success } }',
        'recordTestResult',
        {'c': case},
    )
    developer.ok(
        'mutation($i: ID!){ submitProjectForUat(id:$i){ success } }',
        'submitProjectForUat',
        {'i': pid},
    )

    # --- UAT: only the owner accepts
    scenario = author.ok(
        'mutation($p: ID!){ createUatScenario(projectId:$p, scenario:"Finance sees payments"){ success record{ id } } }',
        'createUatScenario',
        {'p': pid},
    )['record']['id']
    denied = developer(
        'mutation($r: ID!){ recordUatResult(id:$r, result:"passed"){ success } }', {'r': scenario}
    )['recordUatResult']
    assert denied['success'] is False
    author.ok(
        'mutation($r: ID!){ recordUatResult(id:$r, result:"passed"){ success } }',
        'recordUatResult',
        {'r': scenario},
    )

    # --- deployment, verification, impact, completion
    deployment = developer.ok(
        'mutation($p: ID!){ createDeployment(projectId:$p, environment:"prod", version:"1.0"){ success deployment{ id } } }',
        'createDeployment',
        {'p': pid},
    )['deployment']['id']
    for status in ('in_progress', 'successful'):
        developer.ok(
            'mutation($d: ID!, $s: String!){ recordDeployment(id:$d, status:$s){ success } }',
            'recordDeployment',
            {'d': deployment, 's': status},
        )
    developer.ok(
        'mutation($d: ID!){ verifyDeployment(id:$d){ success } }',
        'verifyDeployment',
        {'d': deployment},
    )
    impact = author.ok(
        """mutation($p: ID!){ recordImpact(projectId:$p, values:{
            beforeProcessingMinutes:"240", afterProcessingMinutes:"30",
            estimatedHoursSavedPerWeek:"20", measuredHoursSavedPerWeek:"17.5"}){
          success impact{ status results{ timeSavedPercent hoursSavedVariance peopleReduced } } } }""",
        'recordImpact',
        {'p': pid},
    )['impact']
    assert impact['results']['timeSavedPercent'] == '87.5'
    assert impact['results']['hoursSavedVariance'] == '-2.5'
    assert (
        impact['results']['peopleReduced'] is None
    )  # nobody recorded people, so nothing is claimed
    completed = developer.ok(
        'mutation($i: ID!){ completeProject(id:$i){ success project{ status } } }',
        'completeProject',
        {'i': pid},
    )
    assert completed['project']['status'] == 'completed'

    # --- the chain is still whole, and only the right people can read it
    chain = author(
        'query($i: ID!){ ideaTraceability(ideaId:$i){ opportunityId proposalId projectId deploymentId impactId } }',
        {'i': iid},
    )['ideaTraceability']
    assert all(chain.values())
    progress = author(
        'query($p: ID!){ project(id:$p){ status progress{ stages{ stage state } } } }', {'p': pid}
    )['project']
    assert {s['state'] for s in progress['progress']['stages']} <= {'done', 'pending', 'current'}
    assert author('query($o: ID!){ opportunityActivity(opportunityId:$o){ action } }', {'o': oid})[
        'opportunityActivity'
    ]


@pytest.mark.django_db
def test_a_non_ready_idea_never_enters_and_strangers_see_nothing(people):
    from ideas.models import Idea

    api = people['api']
    idea = ready_idea(people['author'], status=Idea.Status.APPROVED)

    for who in ('author', 'manager'):
        refused = api[who](
            'mutation($i: ID!){ createAutomationOpportunity(input:{ideaId:$i}){ success message } }',
            {'i': str(idea.pk)},
        )['createAutomationOpportunity']
        assert refused['success'] is False
    assert 'ready for implementation' in refused['message']
    assert api['stranger']('query{ projects{ id } }')['projects'] == []


@pytest.mark.django_db
def test_only_delivery_managers_can_list_who_is_assignable(people):
    query = 'query{ assignableAssignees{ users{ id name } teams{ id } } }'

    seen = people['api']['manager'](query)['assignableAssignees']
    assert [u['id'] for u in seen['users']] == [str(people['developer'].pk)]

    for name in ('author', 'developer', 'stranger'):
        assert people['api'][name](query)['assignableAssignees'] == {'users': [], 'teams': []}


@pytest.mark.django_db
def test_the_console_inspects_everything_but_only_with_the_right_permissions(people):
    opened(people['author'])
    inspector = make_user('inspector@example.com')
    grant_permission(inspector, 'administration.access_console')
    grant_permission(inspector, 'administration.inspect_idea_content')
    console_only = make_user('console@example.com')
    grant_permission(console_only, 'administration.access_console')
    query = 'query{ adminAutomationOpportunities{ id status capabilities{ canTransition } } adminAutomationProjects{ id } }'

    seen = Api(inspector)(query)
    assert len(seen['adminAutomationOpportunities']) == 1
    # Oversight is not a bypass: an inspector reads, and is not handed the buttons.
    assert seen['adminAutomationOpportunities'][0]['capabilities']['canTransition'] is False

    for who in (Api(console_only), people['api']['author'], people['api']['stranger']):
        assert who(query) == {'adminAutomationOpportunities': [], 'adminAutomationProjects': []}
