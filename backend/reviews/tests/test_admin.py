"""
The review admin is read-only (S3-002).

Reviews are created and completed only by domain services that authorize the
reviewer and move the idea in the same transaction; an admin form would
bypass both and could rewrite an audit record. History stays viewable.
"""

import pytest
from django.contrib import admin
from django.test import RequestFactory

from reviews.admin import ReviewAdmin, ReviewCriterionAssessmentInline
from reviews.models import Review


@pytest.fixture
def staff_request(django_user_model):
    request = RequestFactory().get('/admin/')
    request.user = django_user_model(is_staff=True, is_superuser=True)
    return request


def test_review_is_registered():
    assert isinstance(admin.site._registry[Review], ReviewAdmin)


def test_a_superuser_can_view_but_not_add_change_or_delete(staff_request):
    review_admin = admin.site._registry[Review]

    assert review_admin.has_view_permission(staff_request)
    assert not review_admin.has_add_permission(staff_request)
    assert not review_admin.has_change_permission(staff_request)
    assert not review_admin.has_delete_permission(staff_request)
    assert review_admin.get_actions(staff_request) == {}


def test_assessments_are_read_only_inline(staff_request):
    inline = ReviewCriterionAssessmentInline(Review, admin.site)

    assert not inline.has_add_permission(staff_request)
    assert not inline.has_change_permission(staff_request)
    assert not inline.has_delete_permission(staff_request)
