from django.core.management.base import BaseCommand

from store.services import release_expired_orders


class Command(BaseCommand):
    help = "Release stock held by PENDING orders past their expires_at. Run from cron every ~10 min."

    def handle(self, *args, **options):
        n = release_expired_orders()
        self.stdout.write(self.style.SUCCESS(f"Released stock for {n} expired order(s)."))
