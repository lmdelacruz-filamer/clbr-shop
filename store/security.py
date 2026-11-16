from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse


def client_ip(request):
    """Real client IP. Only trust X-Real-IP when our Caddy proxy (which overwrites it) is in front."""
    if settings.BEHIND_PROXY:
        return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")
    return request.META.get("REMOTE_ADDR")


def hit(key, limit, window):
    """Fixed-window rate limiter. Returns True while the caller is under the limit.
    Fails OPEN (allows the request) if the cache backend is down, so a Redis outage can't stop sales."""
    try:
        cache.add(key, 0, window)
        try:
            count = cache.incr(key)
        except ValueError:                  # key expired between add() and incr()
            cache.set(key, 1, window)
            count = 1
    except Exception:
        return True
    return count <= limit


def is_staff(user):
    return user.is_authenticated and user.is_active and user.is_staff


def staff_api(view):
    """JSON endpoints answer 403 (not a login redirect) to non-staff callers."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_staff(request.user):
            return JsonResponse({"error": "Forbidden"}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


class LoginRateLimitMiddleware:
    """Throttle password guessing on BOTH login forms: /ops/login/ and the Django admin login."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path in (
                "/ops/login/", "/" + settings.ADMIN_URL + "login/"):
            limit, window = settings.LOGIN_RATE_LIMIT
            if not hit(f"login:{client_ip(request)}", limit, window):
                from django.http import HttpResponse
                return HttpResponse("Too many login attempts. Try again in 15 minutes.", status=429)
        return self.get_response(request)


class PermissionsPolicyMiddleware:
    """Camera + GPS only for our own origin; everything else denied."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(self), microphone=()")
        return response
