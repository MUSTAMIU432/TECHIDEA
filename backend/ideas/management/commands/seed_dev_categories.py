from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ideas.dev_categories import seed_dev_categories


class Command(BaseCommand):
    help = (
        'Create the temporary development categories (see ideas/dev_categories.py). '
        'Idempotent and additive: existing categories are never changed or removed.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--allow-non-debug',
            action='store_true',
            help='Run even though DEBUG is off. The data is sample data, not a taxonomy.',
        )

    def handle(self, *args, allow_non_debug: bool = False, **options):
        if not settings.DEBUG and not allow_non_debug:
            raise CommandError(
                'These are temporary development categories. Refusing to seed with DEBUG off; '
                'pass --allow-non-debug if this really is a development or test database.'
            )

        result = seed_dev_categories()
        self.stdout.write(
            self.style.SUCCESS(
                f'Development categories: {len(result.created)} created, '
                f'{len(result.skipped)} already present.'
            )
        )
