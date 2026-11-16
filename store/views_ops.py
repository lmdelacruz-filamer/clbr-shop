import json
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import services
from .forms import MixtapeForm, ProductEditForm
from .models import Mixtape, Order, Product, ProductSize, ScanLog
from .security import client_ip, hit, is_staff, staff_api

staff_only = user_passes_test(is_staff, login_url="ops:login")


class OpsLoginView(LoginView):
    template_name = "store/ops/login.html"
    # False on purpose: a logged-in NON-staff user would otherwise bounce between /ops/ and /ops/login/ forever.
    redirect_authenticated_user = False


@never_cache
@user_passes_test(is_staff, login_url="ops:login")
def scanner(request):
    return render(request, "store/ops/scanner.html", {
        "pending_count": Order.objects.filter(status=Order.Status.PENDING).count(),
    })


def _payload(request):
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    p = data.get("payload") if isinstance(data, dict) else None
    return p if isinstance(p, str) and len(p) <= 200 else None


def _respond(result):
    return JsonResponse({
        "state": result.state,
        "message": result.message,
        "order": services.serialize_order(result.order) if result.order else None,
    })


def _run(request, fn):
    if not hit(f"scan:{request.user.pk}", 120, 60):
        return JsonResponse({"error": "Slow down."}, status=429)
    text = _payload(request)
    if text is None:
        return JsonResponse({"error": "Malformed request."}, status=400)
    return _respond(fn(text, user=request.user, ip=client_ip(request)))


@never_cache
@staff_api
@require_POST
def api_lookup(request):
    return _run(request, services.lookup)


@never_cache
@staff_api
@require_POST
def api_confirm(request):
    return _run(request, services.confirm)


# ----------------------------------------------------------------------------
# Orders: who has NOT been scanned yet vs who has
# ----------------------------------------------------------------------------
STATUS_UI = {
    Order.Status.PENDING: ("NOT SCANNED YET", "bg-blood text-ink border-blood"),
    Order.Status.VERIFIED: ("SCANNED / VERIFIED", "bg-bone text-ink border-bone"),
    Order.Status.SHIPPED: ("WITH COURIER", "border-bone text-bone"),
    Order.Status.COMPLETED: ("COMPLETED", "border-ash text-bone/70"),
    Order.Status.CANCELLED: ("CANCELLED", "border-ash text-bone/50 line-through"),
    Order.Status.EXPIRED: ("EXPIRED", "border-ash text-bone/50"),
    Order.Status.RETURNED: ("RETURNED / REFUSED", "border-blood text-blood"),
}
ORDER_TABS = [
    ("pending", "Not scanned yet", [Order.Status.PENDING]),
    ("scanned", "Scanned / verified", [Order.Status.VERIFIED]),
    ("shipped", "Shipped", [Order.Status.SHIPPED]),
    ("completed", "Completed", [Order.Status.COMPLETED]),
    ("closed", "Cancelled / returned / expired", [Order.Status.CANCELLED, Order.Status.RETURNED, Order.Status.EXPIRED]),
    ("all", "All", None),
]


# Fixed courier tracking pages (the tracking number is NOT put in the URL; staff copy/paste it).
# Addresses came from third-party pages, so click-test them once.
TRACK_URLS = {
    "JNT": "https://www.jtexpress.ph/index/query/gzquery.html",
    "LBC": "https://www.lbcexpress.com/track/",
}


def _local_phone(p):
    return "0" + p[3:] if p.startswith("+63") else p


def _courier_text(o):
    """Plain text the staff pastes into the J&T / LBC booking. COD = item total only; the courier collects shipping."""
    lines = [f"Receiver: {o.full_name}", f"Phone: {_local_phone(o.phone_number)}", f"Address: {o.address_line}"]
    if o.latitude is not None:
        lines.append(f"Map pin: https://www.google.com/maps?q={o.latitude},{o.longitude}")
    lines.append("Items: " + "; ".join(f"{i.quantity}x {i.product_name}" + (f" ({i.size})" if i.size else "") for i in o.items.all()))
    lines.append(f"COD amount: PHP {o.total_amount} (items only; shipping fee is paid to the courier on delivery)")
    lines.append(f"Ref: {o.short_ref}")
    return "\n".join(lines)


@never_cache
@staff_only
def orders(request):
    tab = request.GET.get("tab", "pending")
    tabs = {key: statuses for key, _, statuses in ORDER_TABS}
    if tab not in tabs:
        tab = "pending"
    q = (request.GET.get("q") or "").strip()[:60]

    counts = {r["status"]: r["n"] for r in Order.objects.values("status").annotate(n=Count("id"))}
    tab_info = [{"key": k, "label": label, "active": k == tab,
                 "count": sum(counts.values()) if st is None else sum(counts.get(x, 0) for x in st)}
                for k, label, st in ORDER_TABS]

    qs = Order.objects.select_related("verified_by").prefetch_related("items")
    if tabs[tab] is not None:
        qs = qs.filter(status__in=tabs[tab])
    if q:
        cond = Q(full_name__icontains=q) | Q(phone_number__contains=re.sub(r"[^\d+]", "", q) or "@@no-match@@")
        hexq = re.sub(r"[^0-9a-fA-F]", "", q).lower()
        if len(hexq) >= 4 and len(hexq) == len(q.replace("-", "")):
            cond |= Q(id__startswith=hexq)
        qs = qs.filter(cond)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))

    now = timezone.now()
    for o in page:
        o.ui_label, o.ui_class = STATUS_UI[o.status]
        o.can_ship = o.status == Order.Status.VERIFIED
        o.can_complete = o.status == Order.Status.SHIPPED
        o.can_cancel = o.status in (Order.Status.PENDING, Order.Status.VERIFIED)
        o.can_return = o.status == Order.Status.SHIPPED
        o.can_reissue = o.status == Order.Status.PENDING and o.expires_at > now
        o.courier_label = o.get_courier_display() if o.courier else ""
        o.track_url = TRACK_URLS.get(o.courier, "")
        o.courier_text = _courier_text(o)
        o.hours_left = max(0, int((o.expires_at - now).total_seconds() // 3600)) if o.status == Order.Status.PENDING else None
    return render(request, "store/ops/orders.html", {
        "couriers": Order.Courier.choices, "page": page, "tabs": tab_info, "tab": tab, "q": q, "nav": "orders",
        "pending_count": counts.get(Order.Status.PENDING, 0),
        "hold_hours": settings.ORDER_HOLD_HOURS,
    })


@never_cache
@staff_only
@require_POST
def order_action(request, pk, action):
    if action not in ("ship", "complete", "cancel", "return", "reissue"):
        raise Http404
    get_object_or_404(Order, pk=pk)
    nxt = request.POST.get("next", "")
    back = redirect(nxt if nxt.startswith("/ops/orders/") else "ops:orders")
    if action == "reissue":
        res = services.reissue_qr(pk, user=request.user, ip=client_ip(request))
        if not res:
            messages.warning(request, "Cannot reissue: the order is no longer waiting for its first scan (or has expired).")
            return back
        order, token = res
        # Post/redirect/get: a browser refresh must NOT reissue again (that would kill the QR just shown).
        request.session["reissued"] = {"oid": str(order.id), "payload": services.build_payload(order, token)}
        return redirect("ops:reissued")
    if action == "cancel":
        ok = services.cancel_order(pk, user=request.user, ip=client_ip(request))
        done = "Order cancelled and its stock was returned."
    elif action == "return":
        ok = services.mark_returned(pk, user=request.user, ip=client_ip(request))
        done = "Marked as returned/refused. The stock went back to the shop."
    elif action == "ship":
        courier = request.POST.get("courier", "")
        tracking = re.sub(r"\s+", "", request.POST.get("tracking", ""))[:40]
        if courier not in Order.Courier.values:
            messages.warning(request, "Pick a courier first.")
            return back
        if (courier != Order.Courier.OTHER or tracking) and not re.fullmatch(r"[A-Za-z0-9-]{5,40}", tracking):
            messages.warning(request, "Enter the courier tracking number (letters, numbers and dashes only).")
            return back
        if tracking and Order.objects.filter(tracking_number__iexact=tracking).exclude(pk=pk).exists():
            messages.warning(request, "That tracking number is already on another order. Check for a typo.")
            return back
        ok = services.advance_order(pk, "ship", courier=courier, tracking=tracking)
        done = "Marked as shipped with its tracking number."
    else:
        ok = services.advance_order(pk, action)
        done = "Marked as completed."
    messages.success(request, done) if ok else messages.warning(request, "Nothing changed: the order was not in the right status for that action.")
    return back


# ----------------------------------------------------------------------------
# Scan log
# ----------------------------------------------------------------------------
SCAN_UI = {
    "pending": ("Checked, not yet confirmed", "text-bone"),
    "confirmed": ("CONFIRMED ✓", "text-bone font-bold"),
    "redeemed": ("DUPLICATE SCAN ⚠", "text-blood font-bold"),
    "expired": ("Expired order", "text-bone/60"),
    "cancelled": ("Cancelled order / cancel action", "text-bone/60"),
    "invalid": ("INVALID / FAKE CODE ✖", "text-blood font-bold"),
    "returned": ("Parcel returned / refused (stock back)", "text-blood"),
    "reissued": ("New QR issued (old one disabled)", "text-bone"),
}


@never_cache
@staff_only
def scans(request):
    rows = list(ScanLog.objects.select_related("order", "scanned_by")[:300])
    for r in rows:
        r.ui_label, r.ui_class = SCAN_UI.get(r.outcome, (r.outcome, "text-bone"))
    return render(request, "store/ops/scans.html", {
        "rows": rows, "nav": "scans",
        "pending_count": Order.objects.filter(status=Order.Status.PENDING).count(),
    })


# ----------------------------------------------------------------------------
# Products: add / edit a shirt with photo, sizes and stock per size
# ----------------------------------------------------------------------------
@never_cache
@staff_only
def products(request):
    q = request.GET.get("q", "").strip()[:60]
    items = Product.objects.prefetch_related("size_stock")
    total = items.count()
    if q:
        items = items.filter(name__icontains=q)
    return render(request, "store/ops/products.html", {
        "products": items, "nav": "products", "q": q, "total": total,
        "pending_count": Order.objects.filter(status=Order.Status.PENDING).count(),
    })


@never_cache
@staff_only
def product_edit(request, pk=None):
    product = get_object_or_404(Product.objects.prefetch_related("size_stock"), pk=pk) if pk else None
    if request.method == "POST":
        form = ProductEditForm(request.POST, request.FILES, product=product)
        if form.is_valid():
            saved = form.save()
            services.log.info("product saved id=%s name=%s by=%s", saved.pk, saved.name, request.user.get_username())
            messages.success(request, f"{'Updated' if product else 'Added'} “{saved.name}”. It is {'live in' if saved.is_active else 'hidden from'} the shop.")
            return redirect("ops:products")
    else:
        form = ProductEditForm(product=product)
    return render(request, "store/ops/product_form.html", {
        "form": form, "product": product, "nav": "products",
        "pending_count": Order.objects.filter(status=Order.Status.PENDING).count(),
    })


# ----------------------------------------------------------------------------
# CLBR Radio: the mixtape shown on the homepage
# ----------------------------------------------------------------------------
@never_cache
@staff_only
def radio(request):
    mixes = list(Mixtape.objects.all()[:50])
    live = next((m for m in mixes if m.is_active), None)
    return render(request, "store/ops/radio.html", {
        "mixes": mixes, "live": live, "nav": "radio",
        "pending_count": Order.objects.filter(status=Order.Status.PENDING).count(),
    })


@never_cache
@staff_only
def radio_edit(request, pk=None):
    mix = get_object_or_404(Mixtape, pk=pk) if pk else None
    if request.method == "POST":
        form = MixtapeForm(request.POST, instance=mix)
        if form.is_valid():
            saved = form.save()
            services.log.info("mixtape saved id=%s title=%s by=%s", saved.pk, saved.title, request.user.get_username())
            messages.success(request, f"{'Updated' if mix else 'Added'} “{saved.title}”. It is {'live on' if saved.is_active else 'hidden from'} the site.")
            return redirect("ops:radio")
    else:
        form = MixtapeForm(instance=mix)
    return render(request, "store/ops/radio_form.html", {
        "form": form, "mix": mix, "nav": "radio",
        "pending_count": Order.objects.filter(status=Order.Status.PENDING).count(),
    })


# ----------------------------------------------------------------------------
# Home: the numbers a new shop needs at a glance
# ----------------------------------------------------------------------------
LOW_STOCK_AT = 2


@never_cache
@staff_only
def home(request):
    now = timezone.now()
    start_today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = {r["status"]: r["n"] for r in Order.objects.values("status").annotate(n=Count("id"))}
    shipped = Order.objects.filter(status=Order.Status.SHIPPED)
    transit = list(shipped.order_by("shipped_at", "created_at")[:15])
    for o in transit:
        o.days_out = (now - (o.shipped_at or o.created_at)).days
    low = ProductSize.objects.filter(product__is_active=True, stock_quantity__lte=LOW_STOCK_AT) \
        .select_related("product").order_by("stock_quantity", "product__name")
    return render(request, "store/ops/home.html", {
        "nav": "home",
        "pending_count": counts.get(Order.Status.PENDING, 0),
        "to_ship": counts.get(Order.Status.VERIFIED, 0),
        "in_transit": counts.get(Order.Status.SHIPPED, 0),
        "today": Order.objects.filter(created_at__gte=start_today).count(),
        "cod_out": shipped.aggregate(t=Sum("total_amount"))["t"] or 0,
        "transit": transit, "low": low, "low_at": LOW_STOCK_AT,
    })


@never_cache
@staff_only
def reissued(request):
    data = request.session.pop("reissued", None)          # shown once, then gone
    order = Order.objects.filter(pk=data["oid"]).first() if data else None
    if not order:
        messages.warning(request, "That QR was already shown once and cannot be displayed again. Use Reissue to make a new one.")
        return redirect("ops:orders")
    return render(request, "store/ops/reissued.html", {
        "order": order, "payload": data["payload"], "nav": "orders",
        "pending_count": Order.objects.filter(status=Order.Status.PENDING).count()})
