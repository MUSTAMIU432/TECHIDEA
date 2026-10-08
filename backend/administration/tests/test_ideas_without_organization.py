"""
Individual and team ideas belong to no organization, and the console must still read them.

Every console read used to assume `idea.organization`, so one submitted individual idea
took down the dashboard, the idea list and the review list together.
"""

import pytest

from administration import services
from ideas.models import Category, Idea
from identity.models import User
from reviews.tests.platform import submit

PASSWORD = 'a-strong-unique-pass-1'


def make_user(email):
    return User.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name='Test',
        last_name='User',
        phone_number='+255712345678',
    )


@pytest.fixture
def admin(db):
    user = make_user('admin@example.com')
    services.grant_platform_admin(user.email)
    return User.objects.get(pk=user.pk)


@pytest.fixture
def individual_idea(db):
    draft = Idea.objects.create(
        author=make_user('author@example.com'),
        title='Tax collection',
        description='Taxes are collected on paper and reconciled by hand every month.',
        submission_context=Idea.SubmissionContext.INDIVIDUAL,
        visibility=Idea.Visibility.PRIVATE,
        category=Category.objects.get_or_create(name='No Organization Fixture')[0],
    )
    return submit(draft)


@pytest.mark.django_db
def test_the_console_reads_an_individual_idea(gql, admin, individual_idea):
    home = 'submissionContext organization { id } teamName'

    overview = gql(
        f'query{{ adminOverview{{ recentActivity{{ ideaTitle {home} }} }} }}', user=admin
    )
    ideas = gql(f'query{{ adminIdeas{{ items{{ title {home} }} }} }}', user=admin)
    detail = gql(
        f'query($i: ID!){{ adminIdea(id:$i){{ title {home} }} }}',
        {'i': str(individual_idea.pk)},
        user=admin,
    )

    expected = {'submissionContext': 'INDIVIDUAL', 'organization': None, 'teamName': None}
    assert overview['adminOverview']['recentActivity'][0] == {
        'ideaTitle': 'Tax collection',
        **expected,
    }
    assert ideas['adminIdeas']['items'] == [{'title': 'Tax collection', **expected}]
    assert detail['adminIdea'] == {'title': 'Tax collection', **expected}


@pytest.mark.django_db
def test_the_console_names_the_review_team_an_idea_is_routed_to(gql, admin, individual_idea):
    from administration import platform_roles
    from reviews import review_teams

    lead = review_teams.grant_reviewer(admin, make_user('lead@example.com').email)
    team = review_teams.create_team(admin, 'Review Team 1', lead.pk, [])
    platform_roles.grant_role(admin, 'intake', admin.email)
    query = 'query{ adminIdeas{ items{ reviewTeam{ name } } } }'

    assert gql(query, user=admin)['adminIdeas']['items'] == [{'reviewTeam': None}]
    review_teams.assign_team(User.objects.get(pk=admin.pk), individual_idea.pk, team.pk)
    assert gql(query, user=admin)['adminIdeas']['items'] == [
        {'reviewTeam': {'name': 'Review Team 1'}}
    ]
