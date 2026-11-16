from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


def _page(template):
    @require_GET
    def view(request):
        return render(request, template)
    return view


privacy = _page("store/privacy.html")
terms = _page("store/terms.html")
shipping = _page("store/shipping.html")
contact = _page("store/contact.html")


def _base(request):
    return (settings.SITE_URL or f"{request.scheme}://{request.get_host()}").rstrip("/")


@require_GET
def robots(request):
    body = "User-agent: *\nDisallow: /ops/\nDisallow: /api/\n\nSitemap: %s/sitemap.xml\n" % _base(request)
    return HttpResponse(body, content_type="text/plain")


@require_GET
def sitemap(request):
    base = _base(request)
    names = ["store:shop", "store:radio", "store:shipping", "store:terms", "store:privacy", "store:contact"]
    urls = "".join(f"<url><loc>{base}{reverse(n)}</loc></url>" for n in names)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return HttpResponse(xml, content_type="application/xml")


@never_cache
@require_GET
def healthz(request):
    """For uptime monitors: 200 only if the app can reach the database."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        return HttpResponse("db down", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")
