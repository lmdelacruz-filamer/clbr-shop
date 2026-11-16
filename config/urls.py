from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as _serve

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),   # obscured Django admin
    path("ops/", include("store.urls_ops")),      # staff-only scanner
    path("", include("store.urls")),               # public storefront
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.SERVE_FILES and not settings.DEBUG:
    # Demo hosting only (SERVE_FILES=1). Real server: Caddy serves these instead.
    urlpatterns += [
        re_path(r"^static/(?P<path>.*)$", _serve, {"document_root": settings.STATIC_ROOT}),
        re_path(r"^media/(?P<path>.*)$", _serve, {"document_root": settings.MEDIA_ROOT}),
    ]
