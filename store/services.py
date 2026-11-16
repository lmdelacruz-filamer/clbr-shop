"""All inventory + verification business rules live here (views stay thin).

Inventory model — "reserve on order, release on expiry":
  * place_order() locks the product rows (SELECT ... FOR UPDATE, in id order to
    avoid deadlocks) and DEDUCTS stock immediately, so two shoppers can never
    buy the last shirt.
  * Every order carries expires_at. If nobody verifies it in time, a cron job
    (release_expired_orders) flips it to EXPIRED and returns the stock — abandoned
    checkouts can't freeze inventory forever.
  * Expiry/verification are guarded by the order row lock + status check, so stock
    is returned at most once and a code can be redeemed at most once.
"""
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import Order, OrderItem, Product, ProductSize, ScanLog

PAYLOAD_PREFIX = "CLBR1"
_PAYLOAD_RE = re.compile(r"^CLBR1:(?P<oid>[0-9a-fA-F-]{36}):(?P<token>[A-Za-z0-9_-]{32,64})$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")

log = logging.getLogger("store")

REDEEMED_STATES = (Order.Status.VERIFIED, Order.Status.SHIPPED, Order.Status.COMPLETED)


class OrderError(Exception):
    """Message is safe to show to the customer."""


class InsufficientStock(OrderError):
    pass


# ----------------------------------------------------------------------------
# Customer side
# ----------------------------------------------------------------------------
def parse_items(raw):
    """Returns {(product_id, size): qty}. Size is validated against the product later, under lock."""
    if not isinstance(raw, list) or not raw or len(raw) > settings.MAX_CART_LINES:
        raise OrderError("Your cart is empty or invalid.")
    merged = {}
    for row in raw:
        try:
            pid, qty = int(row["product_id"]), int(row["quantity"])
            size = str(row.get("size") or "").strip().upper()[:12]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise OrderError("Your cart contains an invalid item.")
        merged[(pid, size)] = merged.get((pid, size), 0) + qty
    if any(q < 1 or q > settings.MAX_QTY_PER_LINE for q in merged.values()):
        raise OrderError(f"Maximum {settings.MAX_QTY_PER_LINE} per item.")
    return merged


def build_payload(order, token):
    return f"{PAYLOAD_PREFIX}:{order.id}:{token}"


@transaction.atomic
def place_order(*, full_name, phone_number, address_line, latitude, longitude, items):
    """items: {(product_id, size): qty}. Prices come from the DB; stock is tracked PER SIZE.

    The size rows are locked (SELECT ... FOR UPDATE) before the check, so two shoppers can never
    buy the last XL at the same time. A size that reaches 0 is simply unavailable afterwards."""
    open_orders = Order.objects.filter(phone_number=phone_number, status=Order.Status.PENDING,
                                       expires_at__gt=timezone.now()).count()
    if open_orders >= settings.MAX_PENDING_PER_PHONE:
        raise OrderError("You already have unverified orders waiting. Send us your QR on Instagram "
                         "so we can verify them first.")

    pids = sorted({pid for pid, _ in items})
    products = {p.id: p for p in Product.objects.filter(id__in=pids, is_active=True)}
    if len(products) != len(pids):
        raise OrderError("One or more items are no longer available.")

    rows = {(r.product_id, r.size): r
            for r in ProductSize.objects.select_for_update().filter(product_id__in=pids).order_by("id")}

    total, lines, touched = 0, [], []
    for (pid, size) in sorted(items):
        qty, product, row = items[(pid, size)], products[pid], rows.get((pid, size))
        if row is None:
            raise OrderError(f"{product.name}: please choose a valid size.")
        if row.stock_quantity < qty:
            left = row.stock_quantity
            raise InsufficientStock(
                f"{product.name} ({size}): only {left} left." if left else f"{product.name} ({size}) is sold out.")
        row.stock_quantity -= qty
        touched.append(row)
        total += product.price * qty
        lines.append((product, size, qty))

    token = Order.new_token()
    order = Order.objects.create(
        full_name=full_name, phone_number=phone_number, address_line=address_line,
        latitude=latitude, longitude=longitude, total_amount=total,
        verification_hash=Order.hash_token(token),
        terms_accepted_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=settings.ORDER_HOLD_HOURS),
    )
    OrderItem.objects.bulk_create([
        OrderItem(order=order, product=p, product_name=p.name, size=size, quantity=q, price_at_purchase=p.price)
        for p, size, q in lines
    ])
    ProductSize.objects.bulk_update(touched, ["stock_quantity"])
    log.info("order placed ref=%s total=%s lines=%d", order.short_ref, order.total_amount, len(lines))
    return order, token


# ----------------------------------------------------------------------------
# Expiry (stock release)
# ----------------------------------------------------------------------------
def _restock(order):
    """Give every line of the order back to its size. Caller holds the order row lock."""
    for item in order.items.order_by("product_id", "size"):
        done = ProductSize.objects.filter(product_id=item.product_id, size=item.size).update(
            stock_quantity=F("stock_quantity") + item.quantity)
        if not done:                           # admin deleted that size row meanwhile: recreate it
            ProductSize.objects.create(product_id=item.product_id, size=item.size, stock_quantity=item.quantity)


def _expire_locked(order):
    """Caller must hold the order row lock inside a transaction and have checked PENDING."""
    _restock(order)
    order.status = Order.Status.EXPIRED
    order.save(update_fields=["status"])


def expire_order(order_id):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.PENDING:
            return False                       # already verified/expired — never restock twice
        _expire_locked(order)
        return True


TRANSITIONS = {
    "ship": (Order.Status.VERIFIED, Order.Status.SHIPPED),
    "complete": (Order.Status.SHIPPED, Order.Status.COMPLETED),
}


def advance_order(order_id, action, *, courier="", tracking=""):
    """VERIFIED -> SHIPPED (records courier + tracking), SHIPPED -> COMPLETED.
    Row-locked; returns False if the order was not in the right state."""
    before, after = TRANSITIONS[action]
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != before:
            return False
        order.status = after
        fields = ["status"]
        if action == "ship":
            order.courier, order.tracking_number, order.shipped_at = courier, tracking, timezone.now()
            fields += ["courier", "tracking_number", "shipped_at"]
        order.save(update_fields=fields)
    log.info("order %s ref=%s", after.lower(), order.short_ref)
    return True


CANCELLABLE = (Order.Status.PENDING, Order.Status.VERIFIED, Order.Status.SHIPPED)


def cancel_order(order_id, *, user=None, ip=None):
    """Cancel an order (e.g. buyer changed mind, COD parcel refused) and return its stock.
    Row-locked and status-checked, so stock goes back at most once. COMPLETED orders can't be cancelled."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status not in CANCELLABLE:
            return False
        _restock(order)
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status"])
        _log(user, ip, "cancelled", order)
    log.info("order cancelled ref=%s by=%s", order.short_ref, getattr(user, "username", None))
    return True


def mark_returned(order_id, *, user=None, ip=None):
    """A shipped COD parcel came back (refused / undeliverable): SHIPPED -> RETURNED and the stock goes back once."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.SHIPPED:
            return False
        _restock(order)
        order.status = Order.Status.RETURNED
        order.save(update_fields=["status"])
        _log(user, ip, "returned", order)
    log.info("order returned ref=%s by=%s", order.short_ref, getattr(user, "username", None))
    return True


def reissue_qr(order_id, *, user=None, ip=None):
    """Lost QR: rotate the secret. Only for an order that is still PENDING and not expired.
    Returns (order, new_token) or None. The old QR stops working immediately; the new token is shown once."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status != Order.Status.PENDING or order.expires_at <= timezone.now():
            return None
        token = Order.new_token()
        order.verification_hash = Order.hash_token(token)
        order.save(update_fields=["verification_hash"])
        _log(user, ip, "reissued", order)
    log.info("qr reissued ref=%s by=%s", order.short_ref, getattr(user, "username", None))
    return order, token


def release_expired_orders():
    due = Order.objects.filter(status=Order.Status.PENDING, expires_at__lte=timezone.now())
    return sum(1 for pk in list(due.values_list("pk", flat=True)) if expire_order(pk))


# ----------------------------------------------------------------------------
# Admin side: scan, anti-replay, confirm
# ----------------------------------------------------------------------------
@dataclass
class ScanResult:
    state: str            # invalid | pending | redeemed | expired | confirmed
    message: str
    order: Order | None = None


MSG_REDEEMED = "⚠️ Warning: This QR code has already been redeemed/verified"


def parse_scan(text):
    """Accepts the full QR payload or a bare token pasted by hand. Returns (order_id|None, token) or None."""
    text = (text or "").strip()
    m = _PAYLOAD_RE.match(text)
    if m:
        try:
            return str(uuid.UUID(m["oid"])), m["token"]
        except ValueError:
            return None
    if _TOKEN_RE.match(text):
        return None, text
    return None


def _log(user, ip, outcome, order=None):
    ScanLog.objects.create(order=order, scanned_by=user, outcome=outcome, ip_address=ip)


def _invalid(user, ip, msg="Not a valid CLBR code. Possible fake or tampered QR."):
    _log(user, ip, "invalid")
    return ScanResult("invalid", msg)


def _state_for(order):
    if order.status == Order.Status.PENDING:
        return ScanResult("pending", "✔ Authentic order — awaiting verification.", order)
    if order.status == Order.Status.CANCELLED:
        return ScanResult("cancelled", "✖ This order was CANCELLED. Do not fulfil it.", order)
    if order.status == Order.Status.RETURNED:
        return ScanResult("cancelled", "✖ This parcel was RETURNED / refused. Do not fulfil it.", order)
    if order.status == Order.Status.EXPIRED:
        return ScanResult("expired", "⏱ This order expired and its stock was released. Ask the buyer to re-order.", order)
    return ScanResult("redeemed", MSG_REDEEMED, order)


def _match(parsed):
    """Return a locked-or-not Order query for parsed input, or None if id/token disagree."""
    oid, token = parsed
    return oid, Order.hash_token(token)


def lookup(raw_text, *, user, ip):
    parsed = parse_scan(raw_text)
    if not parsed:
        return _invalid(user, ip)
    oid, digest = _match(parsed)
    order = (Order.objects.select_related("verified_by").prefetch_related("items")
             .filter(verification_hash=digest).first())
    if order is None or (oid and oid != str(order.id)):
        return _invalid(user, ip, "No matching order. Possible fake or tampered QR.")

    if order.status == Order.Status.PENDING and order.expires_at <= timezone.now():
        expire_order(order.pk)                 # lazy expiry so stale holds never get confirmed
        order.refresh_from_db()

    result = _state_for(order)
    _log(user, ip, result.state, order)
    return result


def confirm(raw_text, *, user, ip):
    """Atomic PENDING -> VERIFIED. The row lock makes double-clicks and parallel scans safe."""
    parsed = parse_scan(raw_text)
    if not parsed:
        return _invalid(user, ip)
    oid, digest = _match(parsed)

    with transaction.atomic():
        order = Order.objects.select_for_update().filter(verification_hash=digest).first()
        if order is None or (oid and oid != str(order.id)):
            return _invalid(user, ip, "No matching order. Possible fake or tampered QR.")

        if order.status == Order.Status.PENDING and order.expires_at <= timezone.now():
            _expire_locked(order)

        if order.status != Order.Status.PENDING:
            result = _state_for(order)
        else:
            order.status = Order.Status.VERIFIED
            order.verified_at = timezone.now()
            order.verified_by = user
            order.save(update_fields=["status", "verified_at", "verified_by"])
            result = ScanResult("confirmed", "✔ Order verified & locked in.", order)
            log.info("order verified ref=%s by=%s", order.short_ref, getattr(user, "username", None))
        _log(user, ip, result.state, order)

    order = (Order.objects.select_related("verified_by").prefetch_related("items").get(pk=order.pk))
    result.order = order
    return result


def serialize_order(order):
    lat, lng = order.latitude, order.longitude
    pinned = lat is not None and lng is not None
    return {
        "id": str(order.id),
        "ref": order.short_ref,
        "status": order.status,
        "status_label": order.get_status_display(),
        "full_name": order.full_name,
        "phone": order.phone_number,
        "address": order.address_line,
        "latitude": lat,
        "longitude": lng,
        "google_maps_url": f"https://www.google.com/maps?q={lat},{lng}" if pinned else None,
        "osm_url": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=18/{lat}/{lng}" if pinned else None,
        "total": str(order.total_amount),
        "created_at": order.created_at.isoformat(),
        "verified_at": order.verified_at.isoformat() if order.verified_at else None,
        "verified_by": order.verified_by.get_username() if order.verified_by_id else None,
        "items": [
            {"name": i.product_name, "size": i.size, "quantity": i.quantity, "unit_price": str(i.price_at_purchase)}
            for i in order.items.all()
        ],
    }
