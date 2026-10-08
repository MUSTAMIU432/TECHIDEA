"""The Reviews page's view: one row per reviewed idea, with its rounds inside."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from administration.tests.conftest import submitted_idea
from reviews import services as review_services

ROWS = """
query($filters: AdminReviewFiltersInput) {
  adminReviewedIdeas(filters: $filters) {
    items {
      idea { title }
      roundCount
      latest { round decision isCompleted }
      rounds { round decision }
    }
    pageInfo { totalCount }
  }
}
"""
ROUNDS = 'query{ adminReviews{ pageInfo{ totalCount } } }'


def titles(page):
    return sorted(item['idea']['title'] for item in page['items'])


@pytest.mark.django_db
class TestOneRowPerIdea:
    def test_each_idea_is_one_row_carrying_all_its_rounds(self, gql, world):
        page = gql(ROWS, user=world['admin'])['adminReviewedIdeas']

        # Six rounds on the platform, three ideas: every idea was confirmed by its
        # organization and then reviewed once by the platform.
        assert gql(ROUNDS, user=world['admin'])['adminReviews']['pageInfo']['totalCount'] == 6
        assert page['pageInfo']['totalCount'] == 3
        for item in page['items']:
            assert item['roundCount'] == 2
            assert item['rounds'][0]['decision'] == 'CONFIRMED'
            assert item['latest'] == {
                'round': 1,
                'decision': item['rounds'][-1]['decision'],
                'isCompleted': item['rounds'][-1]['decision'] is not None,
            }

    def test_the_state_reads_per_idea(self, gql, world):
        def rows(state):
            return gql(ROWS, {'filters': {'state': state}}, user=world['admin'])[
                'adminReviewedIdeas'
            ]

        assert titles(rows('OPEN')) == ['Open procurement']
        assert titles(rows('COMPLETED')) == ['Organization payroll', 'Public invoice run']

    def test_a_decision_is_the_latest_rounds(self, gql, world):
        def rows(decision):
            return gql(ROWS, {'filters': {'decisions': [decision]}}, user=world['admin'])[
                'adminReviewedIdeas'
            ]

        assert titles(rows('CHANGES_REQUESTED')) == ['Organization payroll']
        # Every idea has a CONFIRMED round, but none stands on it any more.
        assert titles(rows('CONFIRMED')) == []

    def test_nothing_for_anybody_but_an_administrator(self, gql, world):
        page = gql(ROWS, user=world['author'])['adminReviewedIdeas']

        assert page == {'items': [], 'pageInfo': {'totalCount': 0}}

    def test_a_page_costs_the_same_however_many_ideas_it_holds(self, gql, world):
        def count():
            with CaptureQueriesContext(connection) as context:
                gql(ROWS, user=world['admin'])
            return len(context.captured_queries)

        before = count()
        for index in range(3):
            idea = submitted_idea(
                world['author'],
                world['acme'],
                world['finance'],
                f'Extra {index}',
                'organization',
                confirmer=world['reviewer'],
            )
            review_services.start_review(world['reviewer'], idea.pk)

        assert count() == before
