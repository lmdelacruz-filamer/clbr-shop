from django.templatetags.static import static
from django.urls import path
from django.views.generic import RedirectView

from . import views_pages, views_public

app_name = "store"
urlpatterns = [
    path("", views_public.shop, name="shop"),
    path("radio/", views_public.radio, name="radio"),
    path("api/orders/", views_public.create_order, name="create_order"),
    path("shipping-returns/", views_pages.shipping, name="shipping"),
    path("terms/", views_pages.terms, name="terms"),
    path("privacy/", views_pages.privacy, name="privacy"),
    path("contact/", views_pages.contact, name="contact"),
    path("robots.txt", views_pages.robots, name="robots"),
    path("sitemap.xml", views_pages.sitemap, name="sitemap"),
    path("healthz", views_pages.healthz, name="healthz"),
    path("favicon.ico", RedirectView.as_view(url=static("store/img/favicon.ico"), permanent=True)),
]
