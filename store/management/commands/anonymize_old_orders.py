"""Remove customers' personal details from old, finished orders (Data Privacy Act: keep data only as long as needed).

Keeps the money record (items, totals, status, dates) for your accounting; wipes name, phone, address, GPS
and the staff-scan IP addresses. Open orders (Pending / Verified / Shipped) are NEVER touched.
SAFE BY DEFAULT: without --apply it only shows what it WOULD do.

    docker compose exec web python manage.py anonymize_old_orders --days 730            (preview)
    docker compose exec web python manage.py anonymize_old_orders --days 730 --apply    (do it)
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from store.models import Order, ScanLog

DONE = (Order.Status.COMPLETED, Order.Status.EXPIRED, Order.Status.CANCELLED, Order.Status.RETURNED)
MARK = "Deleted customer"


class Command(BaseCommand):
    help = "Wipe personal details from finished orders older than --days (preview unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=730, help="age in days (default 730 = 2 years)")
        parser.add_argument("--apply", action="store_true", help="actually wipe; without it nothing changes")

    def handle(self, *args, days, apply, **opts):
        if days < 30:
            raise CommandError("--days must be at least 30, so a recent order can never be wiped by mistake.")
        cutoff = timezone.now() - timedelta(days=days)
        orders = Order.objects.filter(status__in=DONE, created_at__lt=cutoff).exclude(full_name=MARK)
        logs = ScanLog.objects.filter(created_at__lt=cutoff, ip_address__isnull=False)
        n_orders, n_logs = orders.count(), logs.count()
        if not apply:
            self.stdout.write(f"PREVIEW: would wipe personal details of {n_orders} finished order(s) and "
                              f"{n_logs} scan IP(s) older than {days} days. Add --apply to do it.")
            return
        with transaction.atomic():
            orders.update(full_name=MARK, phone_number="", address_line="", latitude=None, longitude=None)
            logs.update(ip_address=None)
        self.stdout.write(self.style.SUCCESS(f"Wiped {n_orders} order(s) and {n_logs} scan IP(s) older than {days} days."))
