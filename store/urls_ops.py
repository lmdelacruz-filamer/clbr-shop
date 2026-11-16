from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views_ops

app_name = "ops"
urlpatterns = [
    path("login/", views_ops.OpsLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="ops:login"), name="logout"),   # POST only (Django 5)
    path("", views_ops.scanner, name="scanner"),
    path("api/lookup/", views_ops.api_lookup, name="lookup"),
    path("api/confirm/", views_ops.api_confirm, name="confirm"),
    path("home/", views_ops.home, name="home"),
    path("orders/", views_ops.orders, name="orders"),
    path("orders/<uuid:pk>/<str:action>/", views_ops.order_action, name="order_action"),
    path("reissued/", views_ops.reissued, name="reissued"),
    path("scans/", views_ops.scans, name="scans"),
    path("products/", views_ops.products, name="products"),
    path("products/new/", views_ops.product_edit, name="product_new"),
    path("products/<int:pk>/", views_ops.product_edit, name="product_edit"),
    path("radio/", views_ops.radio, name="radio"),
    path("radio/new/", views_ops.radio_edit, name="radio_new"),
    path("radio/<int:pk>/", views_ops.radio_edit, name="radio_edit"),
]
