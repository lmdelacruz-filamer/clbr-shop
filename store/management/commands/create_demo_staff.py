import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Demo hosting only: create (or reset) one staff login from DEMO_STAFF_USER / DEMO_STAFF_PASSWORD. "
            "Does nothing if either is missing. Not used on the real server.")

    def handle(self, *args, **options):
        name = os.environ.get("DEMO_STAFF_USER", "").strip()
        password = os.environ.get("DEMO_STAFF_PASSWORD", "")
        if not name or not password:
            self.stdout.write("DEMO_STAFF_USER / DEMO_STAFF_PASSWORD not set: no staff login created.")
            return
        if len(password) < 12:
            self.stderr.write("DEMO_STAFF_PASSWORD must be at least 12 characters. No staff login created.")
            return
        User = get_user_model()
        user, created = User.objects.get_or_create(username=name, defaults={"is_staff": True})
        user.is_staff = True
        user.is_active = True
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Staff login '{name}' {'created' if created else 'updated'}."))
