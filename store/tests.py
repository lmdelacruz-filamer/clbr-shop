import uuid
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
import threading
import unittest
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import Order, Product, ProductSize


def make_product(stock=3, price="750.00", name="CLBR Tee", sizes=("M", "L", "XL", "XXL")):
    """`stock` is the quantity of EACH size."""
    p = Product.objects.create(name=name, price=Decimal(price), image="products/x.jpg")
    for s in sizes:
        ProductSize.objects.create(product=p, size=s, stock_quantity=stock)
    return p


def stock(product, size):
    return ProductSize.objects.get(product=product, size=size).stock_quantity


def make_order(product, qty=1):
    return services.place_order(
        full_name="Juan Dela Cruz", phone_number="+639171234567",
        address_line="12 Rizal St, Roxas City, Capiz", latitude=11.585, longitude=122.751,
        items={(product.id, "M"): qty})


class InventoryTests(TestCase):
    def test_order_deducts_only_that_size_and_locks_price(self):
        p = make_product(stock=3)
        order, token = make_order(p, 2)                     # 2 x size M
        self.assertEqual(stock(p, "M"), 1)
        self.assertEqual(stock(p, "L"), 3)                   # other sizes untouched
        self.assertEqual(order.total_amount, Decimal("1500.00"))
        self.assertEqual(order.verification_hash, Order.hash_token(token))
        self.assertNotEqual(order.verification_hash, token)
        p.price = Decimal("999.00"); p.save()
        self.assertEqual(order.items.first().price_at_purchase, Decimal("750.00"))
        self.assertEqual(order.items.first().size, "M")

    def test_size_at_zero_cannot_be_ordered_but_others_can(self):
        p = make_product(stock=1)
        make_order(p, 1)                                     # last M gone
        self.assertEqual(stock(p, "M"), 0)
        with self.assertRaises(services.InsufficientStock):
            make_order(p, 1)                                 # M is sold out
        services.place_order(full_name="Ana Reyes", phone_number="+639171234567",
                             address_line="12 Rizal St, Roxas City", latitude=None, longitude=None,
                             items={(p.id, "L"): 1})         # L still fine
        self.assertEqual(stock(p, "L"), 0)

    def test_cannot_order_more_than_size_stock(self):
        p = make_product(stock=2)
        with self.assertRaises(services.InsufficientStock):
            make_order(p, 3)
        self.assertEqual(stock(p, "M"), 2)                   # nothing was deducted

    def test_failed_multi_line_order_deducts_nothing(self):
        p = make_product(stock=1)
        with self.assertRaises(services.InsufficientStock):
            services.place_order(full_name="Ana Reyes", phone_number="+639171234567",
                                 address_line="12 Rizal St, Roxas City", latitude=None, longitude=None,
                                 items={(p.id, "M"): 1, (p.id, "L"): 2})
        self.assertEqual(stock(p, "M"), 1)
        self.assertEqual(Order.objects.count(), 0)

    def test_expiry_restocks_that_size_exactly_once(self):
        p = make_product(stock=2)
        order, _ = make_order(p, 2)
        self.assertEqual(stock(p, "M"), 0)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(services.release_expired_orders(), 1)
        self.assertEqual(services.release_expired_orders(), 0)
        self.assertEqual(stock(p, "M"), 2)


class ScannerLogicTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="x" * 14, is_staff=True)
        self.product = make_product()
        self.order, self.token = make_order(self.product)
        self.payload = services.build_payload(self.order, self.token)

    def test_replay_is_blocked(self):
        first = services.confirm(self.payload, user=self.user, ip="1.1.1.1")
        self.assertEqual(first.state, "confirmed")
        again = services.confirm(self.payload, user=self.user, ip="1.1.1.1")
        self.assertEqual(again.state, "redeemed")
        self.assertIn("already been redeemed", again.message)
        self.assertEqual(services.lookup(self.payload, user=self.user, ip=None).state, "redeemed")

    def test_manual_token_and_fakes(self):
        self.assertEqual(services.lookup(self.token, user=self.user, ip=None).state, "pending")
        self.assertEqual(services.lookup("CLBR1:" + str(self.order.id) + ":" + "A" * 43, user=self.user, ip=None).state, "invalid")
        self.assertEqual(services.lookup("garbage", user=self.user, ip=None).state, "invalid")

    def test_id_must_match_token(self):
        other, _ = make_order(make_product(name="Other"))
        forged = f"CLBR1:{other.id}:{self.token}"
        self.assertEqual(services.lookup(forged, user=self.user, ip=None).state, "invalid")

    def test_expired_order_cannot_be_confirmed(self):
        Order.objects.filter(pk=self.order.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(services.confirm(self.payload, user=self.user, ip=None).state, "expired")


@override_settings(ORDER_RATE_LIMIT=(1000, 600))      # the limiter has its own test below
class EndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.product = make_product(stock=5)

    def _post(self, **over):
        body = {"full_name": "Juan Dela Cruz", "phone": "0917 123 4567",
                "address_detail": "12 Rizal St, green gate", "address": "Brgy. Tiza, Roxas City, Capiz", "latitude": 11.58, "longitude": 122.75,
                "website": "", "consent": True, "items": [{"product_id": self.product.id, "size": "M", "quantity": 2}]}
        body.update(over)
        return self.client.post(reverse("store:create_order"), json.dumps(body), content_type="application/json")

    def test_create_order_ok_and_server_side_price(self):
        r = self._post()
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["total"], "1500.00")
        self.assertTrue(r.json()["payload"].startswith("CLBR1:"))

    def test_address_is_two_parts_and_stored_combined(self):
        r = self._post(address_detail="Block 4 Lot 7, green gate", address="Brgy. Tiza, Roxas City, Capiz")
        self.assertEqual(r.status_code, 201)
        o = Order.objects.latest("created_at")
        self.assertEqual(o.address_line, "Block 4 Lot 7, green gate, Brgy. Tiza, Roxas City, Capiz")
        self.assertLessEqual(len(o.address_line), 250)

    def test_house_or_landmark_is_required(self):
        for bad in ({"address_detail": ""}, {"address_detail": "  "}, {"address_detail": "ab"}):
            r = self._post(**bad)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn("address_detail", r.json()["errors"])
        self.assertEqual(Order.objects.count(), 0)

    def test_area_is_required_and_lengths_are_capped(self):
        self.assertEqual(self._post(address="").status_code, 400)
        self.assertEqual(self._post(address="x" * 156).status_code, 400)
        self.assertEqual(self._post(address_detail="y" * 91).status_code, 400)
        r = self._post(address_detail="y" * 90, address="x" * 155)             # the longest legal pair still fits the column
        self.assertEqual(r.status_code, 201)
        self.assertLessEqual(len(Order.objects.latest("created_at").address_line), 250)

    def test_old_single_field_clients_are_rejected_cleanly(self):
        r = self._post(address_detail=None)
        self.assertEqual(r.status_code, 400)

    def test_validation_and_honeypot(self):
        self.assertEqual(self._post(phone="12345").status_code, 400)
        self.assertEqual(self._post(latitude=999).status_code, 400)
        self.assertEqual(self._post(latitude=35.68, longitude=139.69).status_code, 400)   # Tokyo: not PH
        self.assertEqual(self._post(latitude=14.5995, longitude=120.9842).status_code, 201)  # Manila
        self.assertEqual(self._post(website="http://spam").status_code, 400)
        self.assertEqual(self._post(items=[{"product_id": self.product.id, "size": "M", "quantity": 99}]).status_code, 400)

    def test_sold_out_size_is_409_but_other_size_still_orders(self):
        item = lambda size, q: [{"product_id": self.product.id, "size": size, "quantity": q}]
        self.assertEqual(self._post(items=item("M", 5)).status_code, 201)
        self.assertEqual(self._post(items=item("M", 1)).status_code, 409)     # M is at 0
        self.assertEqual(self._post(items=item("L", 1)).status_code, 201)     # L unaffected
        self.assertEqual(stock(self.product, "M"), 0)
        self.assertEqual(stock(self.product, "L"), 4)

    def test_size_must_be_valid(self):
        bad = self._post(items=[{"product_id": self.product.id, "size": "XXXL", "quantity": 1}])
        self.assertEqual(bad.status_code, 400)

    def test_each_size_is_deducted_separately_and_recorded(self):
        r = self._post(items=[{"product_id": self.product.id, "size": "XL", "quantity": 3},
                              {"product_id": self.product.id, "size": "L", "quantity": 2}])
        self.assertEqual(r.status_code, 201)
        order = Order.objects.get()
        self.assertEqual(sorted((i.size, i.quantity) for i in order.items.all()), [("L", 2), ("XL", 3)])
        self.assertEqual(stock(self.product, "XL"), 2)
        self.assertEqual(stock(self.product, "L"), 3)
        self.assertEqual(stock(self.product, "M"), 5)

    def test_storefront_renders(self):
        r = self.client.get(reverse("store:shop"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Size chart")
        self.assertContains(r, "DJ CLARK")
        self.assertContains(r, "MC MYL")
        self.assertContains(r, "Boxy crop")

    def test_storefront_shows_sold_out_size(self):
        ProductSize.objects.filter(product=self.product, size="XXL").update(stock_quantity=0)
        r = self.client.get(reverse("store:shop"))
        self.assertContains(r, '"size": "XXL", "stock": 0')

    def test_scanner_requires_staff(self):
        for name in ("ops:lookup", "ops:confirm"):
            self.assertEqual(self.client.post(reverse(name), "{}", content_type="application/json").status_code, 403)
        self.assertEqual(self.client.get(reverse("ops:scanner")).status_code, 302)
        User = get_user_model()
        self.client.force_login(User.objects.create_user("cust", password="x" * 14))   # logged in but NOT staff
        self.assertEqual(self.client.post(reverse("ops:lookup"), "{}", content_type="application/json").status_code, 403)
        self.assertEqual(self.client.get(reverse("ops:scanner")).status_code, 302)


@override_settings(ORDER_RATE_LIMIT=(2, 600))
class RateLimitTests(TestCase):
    def test_third_order_from_same_ip_is_blocked(self):
        cache.clear()
        p = make_product(stock=5)
        body = json.dumps({"full_name": "Juan Dela Cruz", "phone": "0917 123 4567",
                           "address_detail": "12 Rizal St, green gate", "address": "Brgy. Tiza, Roxas City, Capiz", "website": "", "consent": True,
                           "items": [{"product_id": p.id, "size": "M", "quantity": 1}]})
        codes = [self.client.post(reverse("store:create_order"), body, content_type="application/json").status_code
                 for _ in range(3)]
        self.assertEqual(codes, [201, 201, 429])


@override_settings(LOGIN_RATE_LIMIT=(3, 900))
class LoginSecurityTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_ops_and_admin_login_are_both_throttled(self):
        from django.conf import settings
        for url in (reverse("ops:login"), "/" + settings.ADMIN_URL + "login/"):
            cache.clear()
            codes = [self.client.post(url, {"username": "x", "password": "y"}).status_code for _ in range(5)]
            self.assertEqual(codes[3:], [429, 429], url)
            self.assertNotIn(429, codes[:3], url)

    def test_logged_in_non_staff_does_not_loop(self):
        self.client.force_login(get_user_model().objects.create_user("cust", password="x" * 14))
        r = self.client.get(reverse("ops:login"))
        self.assertEqual(r.status_code, 200)              # shows the form, no redirect to /ops/ and back


@override_settings(MAX_PENDING_PER_PHONE=2, ORDER_RATE_LIMIT=(1000, 600))
class PendingCapTests(TestCase):
    def setUp(self):
        cache.clear()
        self.p = make_product(stock=10)

    def _order(self, phone="0917 123 4567"):
        body = {"full_name": "Juan Dela Cruz", "phone": phone, "address_detail": "12 Rizal St, green gate", "address": "Brgy. Tiza, Roxas City, Capiz",
                "website": "", "consent": True, "items": [{"product_id": self.p.id, "size": "M", "quantity": 1}]}
        return self.client.post(reverse("store:create_order"), json.dumps(body), content_type="application/json")

    def test_phone_cannot_hoard_stock(self):
        self.assertEqual([self._order().status_code for _ in range(3)], [201, 201, 400])
        self.assertEqual(self._order("0928 555 1234").status_code, 201)   # other phones unaffected
        self.assertEqual(stock(self.p, "M"), 7)                           # the blocked order took nothing


class ConsentAndPagesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.p = make_product(stock=5)

    def _body(self, **over):
        b = {"full_name": "Juan Dela Cruz", "phone": "0917 123 4567", "address_detail": "12 Rizal St, green gate", "address": "Brgy. Tiza, Roxas City, Capiz",
             "website": "", "consent": True, "items": [{"product_id": self.p.id, "size": "M", "quantity": 1}]}
        b.update(over)
        return json.dumps(b)

    def test_order_requires_consent_and_records_it(self):
        url = reverse("store:create_order")
        r = self.client.post(url, self._body(consent=False), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(stock(self.p, "M"), 5)
        ok = self.client.post(url, self._body(), content_type="application/json")
        self.assertEqual(ok.status_code, 201)
        self.assertIsNotNone(Order.objects.get().terms_accepted_at)

    def test_legal_and_info_pages_render(self):
        for name, needle in (("store:privacy", "Data Privacy Act"), ("store:terms", "cash on delivery"),
                             ("store:shipping", "Philippines only"), ("store:contact", "clbrwear@gmail.com")):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, needle)
            self.assertContains(r, "Privacy Notice")                   # footer on every page

    def test_storefront_has_consent_box_and_seo(self):
        r = self.client.get(reverse("store:shop"))
        self.assertContains(r, 'id="consent"')
        self.assertContains(r, 'property="og:image"')
        self.assertContains(r, "application/ld+json")
        self.assertContains(r, 'rel="canonical"')

    def test_robots_sitemap_health_and_404(self):
        self.assertContains(self.client.get("/robots.txt"), "Disallow: /ops/")
        self.assertContains(self.client.get("/sitemap.xml"), "/privacy/")
        self.assertContains(self.client.get("/healthz"), "ok")
        self.assertEqual(self.client.get("/definitely-not-a-page/").status_code, 404)

    def test_ops_pages_are_not_indexable(self):
        r = self.client.get(reverse("ops:login"))
        self.assertContains(r, "noindex")
        self.assertNotContains(r, "og:title")


class CancelOrderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="x" * 14, is_staff=True)
        self.p = make_product(stock=3)

    def test_cancel_returns_stock_once_and_blocks_qr(self):
        order, token = make_order(self.p, 2)                          # 2 x M
        services.confirm(services.build_payload(order, token), user=self.user, ip=None)   # verified
        self.assertEqual(stock(self.p, "M"), 1)
        self.assertTrue(services.cancel_order(order.pk, user=self.user))
        self.assertEqual(stock(self.p, "M"), 3)
        self.assertFalse(services.cancel_order(order.pk, user=self.user))   # second click does nothing
        self.assertEqual(stock(self.p, "M"), 3)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        res = services.lookup(services.build_payload(order, token), user=self.user, ip=None)
        self.assertEqual(res.state, "cancelled")
        self.assertIn("CANCELLED", res.message)

    def test_cancelled_order_never_expires_or_restocks_again(self):
        order, _ = make_order(self.p, 1)
        services.cancel_order(order.pk)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(services.release_expired_orders(), 0)
        self.assertEqual(stock(self.p, "M"), 3)

    def test_completed_orders_cannot_be_cancelled(self):
        order, _ = make_order(self.p, 1)
        Order.objects.filter(pk=order.pk).update(status=Order.Status.COMPLETED)
        self.assertFalse(services.cancel_order(order.pk))
        self.assertEqual(stock(self.p, "M"), 2)

    def test_admin_action_cancels_and_returns_stock(self):
        order, _ = make_order(self.p, 2)
        admin_user = get_user_model().objects.create_superuser("boss", password="y" * 14)
        self.client.force_login(admin_user)
        from django.conf import settings
        r = self.client.post(f"/{settings.ADMIN_URL}store/order/",
                             {"action": "cancel_and_return_stock", "_selected_action": [str(order.pk)]}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(stock(self.p, "M"), 3)


@unittest.skipUnless(connection.vendor == "postgresql", "row-lock race test needs PostgreSQL")
class ConcurrencyTests(TransactionTestCase):
    """20 shoppers race for the last 3 pieces of one size. Exactly 3 may succeed; stock must end at 0, never negative."""

    def test_no_overselling_under_parallel_orders(self):
        from django.db import connections
        p = make_product(stock=3, sizes=("M",))
        results, lock = [], threading.Lock()

        def buy(i):
            try:
                services.place_order(full_name=f"Buyer {i}", phone_number=f"+63917000{i:04d}",
                                     address_line="12 Rizal St, Roxas City", latitude=None, longitude=None,
                                     items={(p.id, "M"): 1})
                outcome = "ok"
            except services.InsufficientStock:
                outcome = "sold_out"
            finally:
                connections.close_all()
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=buy, args=(i,)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results.count("ok"), 3)
        self.assertEqual(results.count("sold_out"), 17)
        self.assertEqual(stock(p, "M"), 0)
        self.assertEqual(Order.objects.count(), 3)


class StockGridAdminTests(TestCase):
    def test_stock_can_be_edited_per_size_from_the_grid(self):
        from django.conf import settings
        p = make_product(stock=5)
        boss = get_user_model().objects.create_superuser("boss", password="y" * 14)
        self.client.force_login(boss)
        url = f"/{settings.ADMIN_URL}store/productsize/"
        self.assertContains(self.client.get(url), "CLBR Tee")
        rows = list(ProductSize.objects.filter(product=p).order_by("id"))
        data = {"form-TOTAL_FORMS": str(len(rows)), "form-INITIAL_FORMS": str(len(rows)),
                "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000", "_save": "Save"}
        for i, r in enumerate(rows):
            data[f"form-{i}-id"] = str(r.pk)
            data[f"form-{i}-stock_quantity"] = str(10 + i)
        self.assertEqual(self.client.post(url, data, follow=True).status_code, 200)
        self.assertEqual([r.stock_quantity for r in ProductSize.objects.filter(product=p).order_by("id")], [10, 11, 12, 13])


class OpsAreaTests(TestCase):
    def setUp(self):
        cache.clear()
        U = get_user_model()
        self.staff = U.objects.create_user("clerk", password="y" * 14, is_staff=True)
        self.client.force_login(self.staff)

    def _png(self, name="shirt.png", size=(40, 40)):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = io.BytesIO()
        Image.new("RGB", size, "red").save(buf, "PNG")
        return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")

    def test_pages_require_staff(self):
        self.client.logout()
        cust = get_user_model().objects.create_user("cust", password="y" * 14)
        self.client.force_login(cust)
        for name in ("ops:orders", "ops:scans", "ops:products", "ops:product_new"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/ops/login/", r["Location"])

    def test_pages_render_for_staff(self):
        p = make_product()
        make_order(p)
        for name in ("ops:scanner", "ops:orders", "ops:scans", "ops:products", "ops:product_new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("ops:product_edit", args=[p.pk])).status_code, 200)

    def test_orders_split_scanned_and_not_scanned(self):
        p = make_product(stock=5)
        o1, _ = make_order(p)
        o2, t2 = make_order(p)
        services.confirm(f"CLBR1:{o2.id}:{t2}", user=self.staff, ip="127.0.0.1")
        r = self.client.get(reverse("ops:orders") + "?tab=pending")
        self.assertContains(r, "NOT SCANNED YET")
        self.assertContains(r, o1.short_ref)
        self.assertNotContains(r, o2.short_ref)
        r = self.client.get(reverse("ops:orders") + "?tab=scanned")
        self.assertContains(r, o2.short_ref)
        self.assertNotContains(r, o1.short_ref)

    def test_orders_search_is_safe(self):
        make_order(make_product())
        for q in ("<script>", "%", "'; drop table", "zz", "ab12", "+63917"):
            self.assertEqual(self.client.get(reverse("ops:orders"), {"tab": "all", "q": q}).status_code, 200, q)

    def test_ship_complete_cancel_actions(self):
        p = make_product(stock=2)
        o, t = make_order(p)
        url = lambda a: reverse("ops:order_action", args=[o.pk, a])
        self.client.post(url("ship"))                       # not verified yet: no change
        o.refresh_from_db(); self.assertEqual(o.status, Order.Status.PENDING)
        services.confirm(f"CLBR1:{o.id}:{t}", user=self.staff, ip="127.0.0.1")
        self.client.post(url("ship"), {"courier": "JNT", "tracking": "JT12345678"}); o.refresh_from_db(); self.assertEqual(o.status, Order.Status.SHIPPED)
        self.client.post(url("complete")); o.refresh_from_db(); self.assertEqual(o.status, Order.Status.COMPLETED)
        self.assertEqual(self.client.post(url("bogus")).status_code, 404)
        self.assertEqual(self.client.get(url("ship")).status_code, 405)

    def test_cancel_action_returns_stock_and_rejects_open_redirect(self):
        p = make_product(stock=2)
        o, _ = make_order(p)
        self.assertEqual(stock(p, "M"), 1)
        r = self.client.post(reverse("ops:order_action", args=[o.pk, "cancel"]), {"next": "https://evil.example/"})
        self.assertEqual(r["Location"], reverse("ops:orders"))
        self.assertEqual(stock(p, "M"), 2)

    def test_scan_log_lists_scans(self):
        p = make_product()
        o, t = make_order(p)
        services.confirm(f"CLBR1:{o.id}:{t}", user=self.staff, ip="127.0.0.1")
        self.assertContains(self.client.get(reverse("ops:scans")), "CONFIRMED")

    def test_add_product_with_photo_sizes_and_stock(self):
        r = self.client.post(reverse("ops:product_new"), {
            "name": "NEW TEE", "price": "800", "image": self._png(), "is_active": "on",
            "stock_M": "4", "stock_L": "0", "stock_XL": "7"})
        self.assertRedirects(r, reverse("ops:products"))
        prod = Product.objects.get(name="NEW TEE")
        self.assertTrue(prod.image.name.endswith(".jpg"))
        self.assertEqual({s.size: s.stock_quantity for s in prod.size_stock.all()}, {"M": 4, "L": 0, "XL": 7})
        self.client.post(reverse("ops:product_edit", args=[prod.pk]), {
            "name": "NEW TEE", "price": "800", "is_active": "on", "stock_M": "9", "stock_L": "", "stock_XL": "7"})
        self.assertEqual({s.size: s.stock_quantity for s in prod.size_stock.all()}, {"M": 9, "XL": 7})

    def test_add_product_rejects_bad_input(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        base = {"name": "X", "price": "800", "stock_M": "1"}
        for extra in ({}, {"image": SimpleUploadedFile("a.png", b"not an image", "image/png")},
                      {"image": self._png(), "price": "0"}, {"image": self._png(), "stock_M": "-3"},
                      {"image": self._png(), "stock_M": ""}):
            r = self.client.post(reverse("ops:product_new"), {**base, **extra})
            self.assertEqual(r.status_code, 200, extra)
        self.assertEqual(Product.objects.count(), 0)


class CourierFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = get_user_model().objects.create_user("clerk2", password="y" * 14, is_staff=True)
        self.client.force_login(self.staff)

    def act(self, o, action, data=None):
        return self.client.post(reverse("ops:order_action", args=[o.pk, action]), data or {})

    def verified(self, p):
        o, t = make_order(p)
        services.confirm(f"CLBR1:{o.id}:{t}", user=self.staff, ip="127.0.0.1")
        return o, t

    def test_ship_needs_courier_and_tracking(self):
        o, _ = self.verified(make_product())
        for data in ({}, {"courier": "FEDEX", "tracking": "ABC12345"}, {"courier": "JNT", "tracking": ""},
                     {"courier": "LBC", "tracking": "ab"}, {"courier": "JNT", "tracking": "<script>1"}):
            self.act(o, "ship", data)
            o.refresh_from_db(); self.assertEqual(o.status, Order.Status.VERIFIED, data)
        self.act(o, "ship", {"courier": "JNT", "tracking": "JT 0012-3456"})
        o.refresh_from_db()
        self.assertEqual((o.status, o.courier, o.tracking_number), (Order.Status.SHIPPED, "JNT", "JT0012-3456"))
        self.assertIsNotNone(o.shipped_at)
        self.assertContains(self.client.get(reverse("ops:orders") + "?tab=shipped"), "JT0012-3456")

    def test_other_courier_tracking_optional(self):
        o, _ = self.verified(make_product())
        self.act(o, "ship", {"courier": "OTHER", "tracking": ""})
        o.refresh_from_db(); self.assertEqual(o.status, Order.Status.SHIPPED)

    def test_returned_restocks_once_and_only_from_shipped(self):
        p = make_product(stock=2)
        o, _ = self.verified(p)
        self.assertEqual(stock(p, "M"), 1)
        self.act(o, "return"); o.refresh_from_db()
        self.assertEqual(o.status, Order.Status.VERIFIED)           # not shipped yet: no change
        self.act(o, "ship", {"courier": "LBC", "tracking": "LBC99887766"})
        self.act(o, "return"); self.act(o, "return")                 # twice
        o.refresh_from_db()
        self.assertEqual(o.status, Order.Status.RETURNED)
        self.assertEqual(stock(p, "M"), 2)                           # restocked exactly once
        self.assertFalse(services.cancel_order(o.pk))                # cannot be cancelled afterwards
        self.assertEqual(stock(p, "M"), 2)

    def test_returned_qr_is_refused_at_scan(self):
        p = make_product()
        o, t = self.verified(p)
        self.act(o, "ship", {"courier": "JNT", "tracking": "JT12345678"})
        self.act(o, "return")
        r = services.lookup(f"CLBR1:{o.id}:{t}", user=self.staff, ip="127.0.0.1")
        self.assertEqual(r.state, "cancelled")

    def test_reissue_qr_rotates_secret(self):
        p = make_product()
        o, old = make_order(p)
        r = self.act(o, "reissue")
        self.assertRedirects(r, reverse("ops:reissued"), fetch_redirect_response=False)
        r = self.client.get(reverse("ops:reissued"))
        self.assertContains(r, "CLBR1:%s:" % o.id)
        new = r.context["payload"].split(":")[2]
        again = self.client.get(reverse("ops:reissued"))            # refresh: shown once only
        self.assertRedirects(again, reverse("ops:orders"), fetch_redirect_response=False)
        self.assertNotEqual(new, old)
        self.assertEqual(services.lookup(f"CLBR1:{o.id}:{old}", user=self.staff, ip="127.0.0.1").state, "invalid")
        self.assertEqual(services.lookup(f"CLBR1:{o.id}:{new}", user=self.staff, ip="127.0.0.1").state, "pending")
        self.assertEqual(services.confirm(f"CLBR1:{o.id}:{new}", user=self.staff, ip="127.0.0.1").state, "confirmed")

    def test_reissue_refused_when_verified_or_expired(self):
        p = make_product()
        o, _ = self.verified(p)
        self.assertEqual(self.act(o, "reissue").status_code, 302)
        o2, _ = make_order(p)
        Order.objects.filter(pk=o2.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertIsNone(services.reissue_qr(o2.pk))

    def test_reissue_needs_post_and_staff(self):
        o, _ = make_order(make_product())
        self.assertEqual(self.client.get(reverse("ops:order_action", args=[o.pk, "reissue"])).status_code, 405)
        self.client.logout()
        self.assertEqual(self.act(o, "reissue").status_code, 302)

    def test_home_dashboard_and_low_stock(self):
        p = make_product(stock=2)
        ProductSize.objects.filter(product=p, size="L").update(stock_quantity=0)
        make_order(p)
        r = self.client.get(reverse("ops:home"))
        self.assertContains(r, "SOLD OUT")
        self.assertContains(r, "Waiting to be scanned")

    def test_courier_text_has_cod_items_only(self):
        o, _ = make_order(make_product())
        r = self.client.get(reverse("ops:orders") + "?tab=pending")
        self.assertContains(r, "COD amount: PHP 750.00")
        self.assertContains(r, "09171234567")


    def test_stale_form_cannot_overwrite_stock_customers_bought(self):
        p = make_product(stock=5)
        form_page = self.client.get(reverse("ops:product_edit", args=[p.pk]))
        self.assertContains(form_page, 'name="orig_stock_M"')
        make_order(p, 2)                                             # customer buys 2 of M while the form is open
        self.assertEqual(stock(p, "M"), 3)
        data = {"name": "CLBR Tee", "price": "800", "is_active": "on"}
        for s in ("M", "L", "XL", "XXL"):
            data["stock_" + s] = "5"; data["orig_stock_" + s] = "5"  # staff only changes the price
        self.client.post(reverse("ops:product_edit", args=[p.pk]), data)
        self.assertEqual(stock(p, "M"), 3)                           # NOT reset to 5
        data["stock_L"] = "9"                                        # an intentional change still applies
        self.client.post(reverse("ops:product_edit", args=[p.pk]), data)
        self.assertEqual((stock(p, "M"), stock(p, "L")), (3, 9))

    def test_duplicate_tracking_number_is_refused(self):
        p = make_product(stock=3)
        a, _ = self.verified(p); b, _ = self.verified(p)
        self.act(a, "ship", {"courier": "JNT", "tracking": "JT55555555"})
        self.act(b, "ship", {"courier": "JNT", "tracking": "jt55555555"})
        b.refresh_from_db(); self.assertEqual(b.status, Order.Status.VERIFIED)

    def test_track_button_shows_on_shipped_orders(self):
        o, _ = self.verified(make_product())
        self.act(o, "ship", {"courier": "LBC", "tracking": "LBC12345678"})
        r = self.client.get(reverse("ops:orders") + "?tab=shipped")
        self.assertContains(r, "https://www.lbcexpress.com/track/")
        self.assertContains(r, "WITH COURIER")


class NavTests(TestCase):
    def test_nav_on_shop_and_info_pages(self):
        r = self.client.get(reverse("store:shop"))
        self.assertContains(r, 'id="menu-btn"')
        self.assertContains(r, 'id="nav-cart-count"')          # cart badge only on the shop
        self.assertContains(r, 'id="cart-bar"')
        for name in ("store:terms", "store:privacy", "store:shipping", "store:contact"):
            r = self.client.get(reverse(name))
            self.assertContains(r, 'id="menu-btn"', msg_prefix=name)
            self.assertNotContains(r, 'id="nav-cart-count"', msg_prefix=name)


class ScannerDropZoneTests(TestCase):
    def test_scanner_has_drop_zone_and_file_input(self):
        u = get_user_model().objects.create_user("dz", password="y" * 14, is_staff=True)
        self.client.force_login(u)
        r = self.client.get(reverse("ops:scanner"))
        self.assertContains(r, 'id="drop-zone"')
        self.assertContains(r, 'id="file-input"')


class LogoFontTests(TestCase):
    def test_english_towne_is_self_hosted_heading_font(self):
        from django.contrib.staticfiles import finders
        self.assertTrue(finders.find("store/fonts/english-towne.woff"))
        r = self.client.get(reverse("store:shop"))
        self.assertContains(r, 'font-family: "English Towne"')
        self.assertContains(r, "store/fonts/english-towne.woff")
        self.assertContains(r, """gothic: ['"English Towne"', '"Pirata One"', 'serif']""")


class OpsProductSearchTests(TestCase):
    def setUp(self):
        from decimal import Decimal
        self.client.force_login(get_user_model().objects.create_user("srch", password="y" * 14, is_staff=True))
        for n in ("C DESIGN TEE", "Skull Tee", "CLBR Snapback"):
            Product.objects.create(name=n, price=Decimal("100"), image="products/x.jpg")

    def test_search_filters_case_insensitively_and_shows_count(self):
        r = self.client.get(reverse("ops:products"), {"q": "tee"})
        self.assertContains(r, "C DESIGN TEE")
        self.assertContains(r, "Skull Tee")
        self.assertNotContains(r, "CLBR Snapback")
        self.assertContains(r, "2 of 3 shirts match")

    def test_no_match_message_and_clear_link(self):
        r = self.client.get(reverse("ops:products"), {"q": "zzz"})
        self.assertContains(r, "No shirt matches")
        self.assertContains(r, "Show all")

    def test_empty_query_lists_everything(self):
        r = self.client.get(reverse("ops:products"), {"q": "  "})
        self.assertContains(r, "Snapback")
        self.assertNotContains(r, "match")

    def test_search_text_is_escaped(self):
        r = self.client.get(reverse("ops:products"), {"q": "<script>alert(1)</script>"})
        self.assertNotContains(r, "<script>alert(1)</script>")

    def test_search_requires_staff(self):
        self.client.logout()
        r = self.client.get(reverse("ops:products"), {"q": "tee"})
        self.assertEqual(r.status_code, 302)


class AnonymizeOldOrdersTests(TestCase):
    def _order(self, status, age_days):
        from datetime import timedelta
        from decimal import Decimal
        from django.utils import timezone
        o = Order.objects.create(full_name="Juan Dela Cruz", phone_number="+639171234567",
                                 address_line="Block 4 Lot 7, Roxas City", latitude=11.5, longitude=122.7,
                                 total_amount=Decimal("750"), status=status,
                                 verification_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                                 expires_at=timezone.now())
        Order.objects.filter(pk=o.pk).update(created_at=timezone.now() - timedelta(days=age_days))
        return o

    def run_cmd(self, *a):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("anonymize_old_orders", *a, stdout=out)
        return out.getvalue()

    def test_preview_changes_nothing(self):
        o = self._order(Order.Status.COMPLETED, 800)
        self.assertIn("PREVIEW", self.run_cmd("--days", "730"))
        o.refresh_from_db(); self.assertEqual(o.full_name, "Juan Dela Cruz")

    def test_apply_wipes_only_old_finished_orders(self):
        old_done = self._order(Order.Status.COMPLETED, 800)
        old_returned = self._order(Order.Status.RETURNED, 800)
        recent_done = self._order(Order.Status.COMPLETED, 100)
        old_open = [self._order(s, 800) for s in (Order.Status.PENDING, Order.Status.VERIFIED, Order.Status.SHIPPED)]
        self.run_cmd("--days", "730", "--apply")
        for o in (old_done, old_returned):
            o.refresh_from_db()
            self.assertEqual((o.full_name, o.phone_number, o.address_line, o.latitude, o.longitude),
                             ("Deleted customer", "", "", None, None))
            self.assertEqual(str(o.total_amount), "750.00")          # money record kept
        for o in [recent_done] + old_open:
            o.refresh_from_db(); self.assertEqual(o.full_name, "Juan Dela Cruz")

    def test_refuses_dangerously_small_window(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.run_cmd("--days", "5", "--apply")


class PrebuiltCssTests(TestCase):
    """The site loads a pre-built stylesheet. These tests stop us shipping it stale or missing."""
    ROOT = None

    @classmethod
    def _sources(cls):
        import hashlib
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        skip = {"migrations", "__pycache__", "img", "vendor", "fonts", "css", "management"}
        files = []
        for p in (repo / "store").rglob("*"):
            rel = p.relative_to(repo / "store")
            if p.is_file() and p.suffix in (".html", ".js", ".py") and p.name != "tests.py" and not (set(rel.parts[:-1]) & skip):
                files.append(p)
        files.sort(key=lambda p: p.relative_to(repo).as_posix())
        h = hashlib.sha256()
        for p in files:
            h.update(p.relative_to(repo).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
        return repo, h.hexdigest()[:16]

    def test_stylesheet_is_fresh(self):
        repo, digest = self._sources()
        css = (repo / "store/static/store/css/site.css").read_text(encoding="utf-8")
        self.assertIn(f"clbr-src-hash:{digest}", css,
                      "site.css is stale. Run: node tools/build_css.mjs (needs Tailwind v4 + esbuild)")

    def test_pages_load_prebuilt_css_with_old_browser_fallback(self):
        for name in ("store:shop", "store:terms"):
            r = self.client.get(reverse(name))
            self.assertContains(r, 'id="site-css"')
            self.assertContains(r, "store/css/site.css")
            self.assertContains(r, "cdn.tailwindcss.com/3.4.17")       # fallback for old browsers only

    def test_motion_is_opt_in_and_reduced_motion_safe(self):
        r = self.client.get(reverse("store:shop"))
        self.assertContains(r, "prefers-reduced-motion: no-preference")
        self.assertContains(r, "store/js/reveal.js")
        self.assertContains(r, 'id="artist" data-reveal')
        self.assertNotContains(r, 'id="checkout" data-reveal')      # the order form is never faded


class MobileGridTests(TestCase):
    def test_shop_columns_cannot_be_stretched_by_long_cart_lines(self):
        """A long cart line (nowrap text) once widened the whole phone page by ~24px. Grid tracks must be allowed to shrink."""
        html = self.client.get(reverse("store:shop")).content.decode()
        self.assertIn("grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_440px]", html)
        self.assertIn('class="scroll-mt-20 min-w-0"', html)


class TvIntroTests(TestCase):
    def setUp(self):
        self.html = self.client.get(reverse("store:shop")).content.decode()

    def test_intro_is_gated_and_skippable(self):
        self.assertIn('id="intro" aria-hidden="true"', self.html)
        self.assertIn("prefers-reduced-motion: reduce", self.html)          # gate script skips it for reduced motion
        self.assertIn("sessionStorage.getItem('clbr-intro')", self.html)     # once per visit
        self.assertIn("location.hash", self.html)                           # not on deep links like /#checkout
        self.assertIn("store/js/intro.js", self.html)
        # hidden by default, so it can never cover the page when JavaScript is off
        self.assertIn("#intro { display: none; }", self.html)
        # CSS-only failsafe removes it even if intro.js fails
        self.assertIn("animation: introOut .4s ease 1.4s forwards", self.html)

    def test_only_home_page_has_the_intro(self):
        for name in ("store:terms", "store:privacy"):
            self.assertNotContains(self.client.get(reverse(name)), 'id="intro"')

    def test_hero_has_grain(self):
        self.assertIn('class="crt-hero relative', self.html)


class RadioHelpersTests(TestCase):
    def test_soundcloud_link_is_cleaned(self):
        from .radio import clean_soundcloud_url
        u = "https://soundcloud.com/some-dj/my-mix?utm_source=clipboard&utm_medium=text&utm_campaign=social_sharing"
        self.assertEqual(clean_soundcloud_url(u), "https://soundcloud.com/some-dj/my-mix")
        self.assertEqual(clean_soundcloud_url("https://m.soundcloud.com/a/b/s-Xy12/"), "https://soundcloud.com/a/b/s-Xy12")

    def test_bad_links_are_rejected(self):
        from .radio import clean_soundcloud_url
        for bad in ("http://soundcloud.com/a/b", "https://evil.com/a/b", "https://soundcloud.com.evil.com/a/b",
                    "https://soundcloud.com/a", "https://soundcloud.com/discover/x", "javascript:alert(1)",
                    "https://soundcloud.com:444/a/b", "https://u:p@soundcloud.com/a/b", "https://soundcloud.com/a/b c", ""):
            with self.assertRaises(ValueError, msg=bad):
                clean_soundcloud_url(bad)

    def test_tracklist_parsing(self):
        from .radio import parse_tracklist
        tracks, errors = parse_tracklist("7:10 | C | Three\n0:00 | A | One\n\n1:02:03 | B | Two")
        self.assertEqual(errors, [])
        self.assertEqual([(t["start"], t["label"], t["artist"], t["title"]) for t in tracks],
                         [(0, "0:00", "A", "One"), (430, "7:10", "C", "Three"), (3723, "1:02:03", "B", "Two")])

    def test_tracklist_errors_name_the_line(self):
        from .radio import parse_tracklist
        _, errors = parse_tracklist("0:00 | A | One\nnot a line\n3:75 | B | Two")
        self.assertEqual(len(errors), 2)
        self.assertIn("Line 2", errors[0])
        self.assertIn("Line 3", errors[1])


class RadioTests(TestCase):
    URL = "https://soundcloud.com/some-dj/my-mix"

    def make(self, **kw):
        from .models import Mixtape
        d = dict(title="Vol. 01", dj_name="The Founder", soundcloud_url=self.URL, tracklist="0:00 | A | One\n3:24 | B | Two", is_active=True)
        d.update(kw)
        return Mixtape.objects.create(**d)

    def test_homepage_has_no_radio_section_and_keeps_its_numbering(self):
        self.make()
        r = self.client.get(reverse("store:shop"))
        self.assertNotContains(r, 'id="radio"')
        self.assertNotContains(r, "radio.js")
        self.assertContains(r, "[ 04 ] Lookbook")
        self.assertContains(r, "[ 05 ] Fit guide")

    def test_nav_links_to_the_radio_page_only_when_a_mixtape_is_live(self):
        self.assertNotContains(self.client.get(reverse("store:shop")), reverse("store:radio"))
        self.make()
        self.assertContains(self.client.get(reverse("store:shop")), reverse("store:radio"))

    def test_page_without_mixtapes_is_friendly(self):
        r = self.client.get(reverse("store:radio"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "No mixtapes yet")
        self.assertNotContains(r, "radio.js")

    def test_page_lists_every_active_mixtape_with_credits_and_loads_nothing_from_soundcloud(self):
        self.make(title="Older mix")
        self.make(title="Hidden mix", is_active=False)
        self.make(title="Newest mix", dj_name="Clawd")
        r = self.client.get(reverse("store:radio"))
        self.assertContains(r, "Newest mix")
        self.assertContains(r, "Older mix")
        self.assertNotContains(r, "Hidden mix")
        self.assertContains(r, "Mix by Clawd")
        self.assertContains(r, "CLBR FM 00.0")
        self.assertContains(r, "ONLY ON THIS SITE")
        self.assertContains(r, 'type="range"')                      # the CLBR scrub bar
        self.assertContains(r, "Listen on SoundCloud")             # per-mix fallback link
        # Newest first, and nothing is loaded from SoundCloud until a mix is picked: no iframe, no player script, no player URL.
        html = r.content.decode()
        self.assertLess(html.index("Newest mix"), html.index("Older mix"))
        self.assertNotContains(r, "w.soundcloud.com")
        self.assertNotContains(r, "<iframe")

    def test_tracklist_and_urls_reach_the_page_as_data(self):
        self.make()
        r = self.client.get(reverse("store:radio"))
        self.assertContains(r, 'id="rd-data"')
        self.assertContains(r, "https://soundcloud.com/some-dj/my-mix")
        self.assertContains(r, '"artist": "B"')

    def test_a_bad_link_saved_in_django_admin_is_skipped(self):
        self.make(title="Bad one", soundcloud_url="https://evil.example/a/b")
        self.make(title="Good one")
        r = self.client.get(reverse("store:radio"))
        self.assertContains(r, "Good one")
        self.assertNotContains(r, "Bad one")

    def test_soundcloud_player_is_hidden_by_default_and_can_be_shown(self):
        self.make()
        self.assertContains(self.client.get(reverse("store:radio")), "rd-offscreen")
        with override_settings(RADIO_HIDE_PLAYER=False):
            self.assertNotContains(self.client.get(reverse("store:radio")), "rd-offscreen")

    def test_motion_is_opt_in_and_assets_are_loaded_on_the_radio_page(self):
        self.make()
        r = self.client.get(reverse("store:radio"))
        self.assertContains(r, "store/css/radio.css")
        self.assertContains(r, "store/js/radio.js")
        from django.contrib.staticfiles import finders
        css = open(finders.find("store/css/radio.css"), encoding="utf-8").read()
        self.assertIn("prefers-reduced-motion:no-preference", css.replace(" ", ""))

    def test_player_frame_allows_autoplay_and_encrypted_media(self):
        from django.contrib.staticfiles import finders
        js = open(finders.find("store/js/radio.js"), encoding="utf-8").read()
        self.assertIn("autoplay; encrypted-media", js)

    def test_radio_page_is_in_the_sitemap(self):
        self.assertContains(self.client.get(reverse("store:sitemap")), reverse("store:radio"))

    def test_privacy_notice_mentions_soundcloud(self):
        self.assertContains(self.client.get(reverse("store:privacy")), "SoundCloud")


class RadioOpsTests(TestCase):
    URL = "https://soundcloud.com/some-dj/my-mix?utm_source=x"

    def setUp(self):
        self.staff = get_user_model().objects.create_user("radiostaff", password="y" * 14, is_staff=True)

    def test_ops_radio_requires_staff(self):
        for name in ("ops:radio", "ops:radio_new"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302)
            self.assertIn("/ops/login/", r["Location"])
        self.client.force_login(get_user_model().objects.create_user("cust3", password="y" * 14))
        self.assertEqual(self.client.get(reverse("ops:radio")).status_code, 302)

    def test_staff_can_add_edit_and_hide(self):
        from .models import Mixtape
        self.client.force_login(self.staff)
        data = {"title": "Vol. 01", "dj_name": "The Founder", "soundcloud_url": self.URL,
                "tracklist": "0:00 | A | One\n3:24 | B | Two", "is_active": "on"}
        r = self.client.post(reverse("ops:radio_new"), data)
        self.assertRedirects(r, reverse("ops:radio"))
        m = Mixtape.objects.get()
        self.assertEqual(m.soundcloud_url, "https://soundcloud.com/some-dj/my-mix")        # tracking removed
        self.assertEqual(len(m.tracks()), 2)
        data.pop("is_active"); data["title"] = "Vol. 01 (edit)"
        self.client.post(reverse("ops:radio_edit", args=[m.pk]), data)
        m.refresh_from_db()
        self.assertFalse(m.is_active)
        self.assertEqual(m.title, "Vol. 01 (edit)")
        self.assertContains(self.client.get(reverse("ops:radio")), "No active mixtape")

    def test_bad_link_and_bad_tracklist_are_refused_with_a_message(self):
        from .models import Mixtape
        self.client.force_login(self.staff)
        r = self.client.post(reverse("ops:radio_new"), {"title": "T", "dj_name": "D", "soundcloud_url": "https://youtube.com/watch?v=1",
                                                        "tracklist": "oops"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "soundcloud.com")
        self.assertContains(r, "Line 1")
        self.assertEqual(Mixtape.objects.count(), 0)

    def test_ops_menu_has_radio_tab(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("ops:home")), reverse("ops:radio"))
