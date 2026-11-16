from datetime import date
from functools import lru_cache

from django.conf import settings
from django.contrib.staticfiles import finders


@lru_cache(maxsize=None)
def _have(path):
    return bool(finders.find(path))


def _radio_live():
    from .models import Mixtape
    return Mixtape.objects.filter(is_active=True).exists()


def site(request):
    """Variables available in every template."""
    return {
        "site": settings.BUSINESS,
        "policies": settings.POLICIES,
        "policies_draft": not settings.POLICIES_CONFIRMED,
        "hold_hours": settings.ORDER_HOLD_HOURS,
        "year": date.today().year,
        "radio_live": _radio_live,          # callable: templates call it only where used (the menu link)
        # Self-hosted copies (downloaded by `manage.py vendor_assets`, run automatically in the Docker build).
        # Templates fall back to the public CDN when a local copy is missing.
        "vendor": {
            "tailwind": _have("store/vendor/tailwind.js"),
            "qrcode": _have("store/vendor/qrcode.min.js"),
            "html5qr": _have("store/vendor/html5-qrcode.min.js"),
            "fonts": _have("store/vendor/fonts.css"),
        },
    }
