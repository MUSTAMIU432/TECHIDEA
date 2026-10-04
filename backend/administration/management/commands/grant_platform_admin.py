from django.core.management.base import BaseCommand, CommandError

from administration import services
from administration.authorization import AdministrationError


class Command(BaseCommand):
    help = (
        'Grant (or with --revoke, remove) platform administration: membership of the '
        '"Platform administrators" group, which holds every administration console '
        'permission. The only way to make an administrator besides Django superuser '
        'tools - the console itself cannot. Audited.'
    )

    def add_arguments(self, parser):
        parser.add_argument('email', help='The email address of an existing account.')
        parser.add_argument(
            '--revoke',
            action='store_true',
            help='Remove the account from the group instead of adding it.',
        )

    def handle(self, *args, email: str, revoke: bool = False, **options):
        try:
            if revoke:
                user = services.revoke_platform_admin(email)
            else:
                user = services.grant_platform_admin(email)
        except AdministrationError as exc:
            raise CommandError(exc.message) from None

        verb = 'removed from' if revoke else 'added to'
        self.stdout.write(
            self.style.SUCCESS(f'{user.email} {verb} "{services.PLATFORM_ADMIN_GROUP}".')
        )
