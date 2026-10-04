"""
Temporary development categories: a bootstrap seed, not the taxonomy.

Category management in Django Admin is not built yet, so a fresh database has
no categories - and without one an idea cannot be submitted
(`services._validate_for_submission`). This module fills that gap for local
development and end-to-end testing, and nothing else.

The boundary is deliberate:

- **One storage.** The seed writes ordinary `Category` rows - the same records
  `Query.categories` serves and the same records the future Admin UI will
  create, edit, activate and deactivate. There is no second list anywhere,
  and the frontend receives these exactly as it will receive
  administrator-made ones: `id`, `name`, `slug`, `description`.
- **No meaning.** Nothing in the platform may branch on these names or slugs.
  They are sample data; the production taxonomy is an administrator's
  decision.
- **Additive and idempotent.** A category is created only when neither its
  slug nor its name is taken. An existing row is never updated, reactivated
  or deleted, so running the seed again - or after an administrator has
  renamed, retired or added categories - changes nothing that is already
  there.

Future architecture::

    Django Admin -> Category records -> GraphQL (Query.categories) -> Idea Intake Form

Run it with ``python manage.py seed_dev_categories``.
"""

from dataclasses import dataclass, field

from django.db import transaction

from ideas.models import Category

# (name, slug, description). Stable slugs so a re-run recognises its own rows.
DEV_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ('Finance', 'finance', 'Invoicing, payments, budgeting and reporting.'),
    ('Education', 'education', 'Teaching, learning and student administration.'),
    ('Human Resources', 'human-resources', 'Hiring, onboarding, leave and staff records.'),
    ('Operations', 'operations', 'Day-to-day running of the organization.'),
    (
        'Information Technology',
        'information-technology',
        'Systems, access, support and infrastructure.',
    ),
    ('Customer Service', 'customer-service', 'Enquiries, complaints and customer support.'),
    ('Healthcare', 'healthcare', 'Patient care, records and clinical administration.'),
    ('Procurement', 'procurement', 'Purchasing, suppliers and tenders.'),
    ('Logistics', 'logistics', 'Transport, stock and distribution.'),
    (
        'Government & Public Services',
        'government-public-services',
        'Public administration and services to citizens.',
    ),
    ('Sales & Marketing', 'sales-marketing', 'Leads, campaigns and sales operations.'),
    ('Other', 'other', 'Anything that does not fit another category yet.'),
)


@dataclass
class SeedResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@transaction.atomic
def seed_dev_categories() -> SeedResult:
    """
    Create whichever development categories are missing; touch nothing else.

    A slug match means the row is this seed's (or was made from it) and is
    left exactly as it is, including an administrator's edits to it. A name
    match under another slug is an administrator's own category - `name` is
    unique - and is left alone too rather than shadowed.
    """
    result = SeedResult()
    for name, slug, description in DEV_CATEGORIES:
        taken = (
            Category.objects.filter(slug=slug).exists()
            or Category.objects.filter(name__iexact=name).exists()
        )
        if taken:
            result.skipped.append(slug)
            continue
        Category.objects.create(name=name, slug=slug, description=description)
        result.created.append(slug)
    return result
