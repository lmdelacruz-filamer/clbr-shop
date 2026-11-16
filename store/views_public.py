import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .forms import OrderForm
from .models import Product
from .security import client_ip, hit
from .services import InsufficientStock, OrderError, build_payload, parse_items, place_order


LOOKBOOK = [
    ("store/img/myl-1.jpg", "MC MYL in the CLBR Crew jacket"),
    ("store/img/clark-1.jpg", "DJ CLARK, Weights tee"),
    ("store/img/rox-2.jpg", "DJ ROX at the decks in the CLBR snapback"),
    ("store/img/clark-2.jpg", "DJ CLARK, Weights tee back print"),
    ("store/img/myl-4.jpg", "MC MYL, Cult tee"),
    ("store/img/rox-1.jpg", "DJ ROX on the decks"),
    ("store/img/myl-5.jpg", "MC MYL on the stairs"),
    ("store/img/myl-2.jpg", "MC MYL, CLBR tee"),
]


def radio(request):
    """The CLBR Radio page: every active mixtape, newest first. A bad link saved through the Django admin skips that mixtape."""
    from .models import Mixtape
    from .radio import clean_soundcloud_url, parse_tracklist
    mixes = []
    for m in Mixtape.objects.filter(is_active=True)[:30]:
        try:
            url = clean_soundcloud_url(m.soundcloud_url)
        except ValueError:
            continue
        tracks = parse_tracklist(m.tracklist)[0]
        mixes.append({"id": m.id, "title": m.title, "dj": m.dj_name, "url": url, "tracks": tracks, "count": len(tracks)})
    return render(request, "store/radio.html", {"mixes": mixes, "mixes_json": mixes, "radio_hide": settings.RADIO_HIDE_PLAYER})


def shop(request):
    products = Product.objects.filter(is_active=True).prefetch_related("size_stock")
    data = [{
        "id": p.id, "name": p.name, "price": str(p.price),
        "stock": p.total_stock,
        "sizes": [{"size": r.size, "stock": r.stock_quantity} for r in p.sizes_ordered],
        "image": p.image.url if p.image else "",
    } for p in products]
    return render(request, "store/shop.html", {
        "products_json": data,
        "instagram_url": settings.INSTAGRAM_URL,
        "max_qty": settings.MAX_QTY_PER_LINE,
        "artists": settings.ARTISTS,
        "artists_instagram": settings.ARTISTS_INSTAGRAM,
        "size_unit": settings.SIZE_CHART_UNIT,
        "lookbook": LOOKBOOK,
        "size_chart": settings.SIZE_CHART,
        "size_chart_draft": not settings.SIZE_CHART_CONFIRMED,
    })


def _err(message, status=400, **extra):
    return JsonResponse({"error": message, **extra}, status=status)


@never_cache
@require_POST
def create_order(request):
    limit, window = settings.ORDER_RATE_LIMIT
    if not hit(f"order:{client_ip(request)}", limit, window):
        return _err("Too many orders from this connection. Try again in a few minutes.", 429)

    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _err("Malformed request.")
    if not isinstance(body, dict):
        return _err("Malformed request.")

    if body.get("website"):                      # honeypot — real users never fill this
        return _err("Rejected.", 400)

    form = OrderForm(body)
    if not form.is_valid():
        errors = {k: [e["message"] for e in v] for k, v in form.errors.get_json_data().items()}
        return _err("Please fix the highlighted fields.", 400, errors=errors)

    try:
        items = parse_items(body.get("items"))
        order, token = place_order(
            full_name=form.cleaned_data["full_name"],
            phone_number=form.cleaned_data["phone"],
            address_line=f'{form.cleaned_data["address_detail"]}, {form.cleaned_data["address"]}',
            latitude=form.cleaned_data["latitude"],
            longitude=form.cleaned_data["longitude"],
            items=items,
        )
    except InsufficientStock as e:
        return _err(str(e), 409)
    except OrderError as e:
        return _err(str(e), 400)

    return JsonResponse({
        "order_id": str(order.id),
        "short_ref": order.short_ref,
        "payload": build_payload(order, token),   # shown once; only its hash is stored
        "total": str(order.total_amount),
        "expires_at": order.expires_at.isoformat(),
    }, status=201)
