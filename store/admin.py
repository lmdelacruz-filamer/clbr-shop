from django.contrib import admin, messages

from django.forms.models import BaseInlineFormSet

from . import services
from .security import client_ip
from .models import Mixtape, Order, OrderItem, Product, ProductSize, ScanLog


DEFAULT_SIZES = ["M", "L", "XL", "XXL"]


class SizeFormSet(BaseInlineFormSet):
    """On a brand-new product, pre-fill the four standard sizes so you only type the quantities."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.initial = [{"size": s, "stock_quantity": 0} for s in DEFAULT_SIZES]


class ProductSizeInline(admin.TabularInline):
    model = ProductSize
    formset = SizeFormSet
    extra = 0
    verbose_name = "size & stock"
    verbose_name_plural = "SIZES & STOCK (set the quantity for each size; use ONE SIZE for caps)"

    def get_extra(self, request, obj=None, **kwargs):
        return len(DEFAULT_SIZES) if obj is None else 1


admin.site.site_header = "CLBR OPS"
admin.site.site_title = "CLBR OPS"
admin.site.index_title = "Orders, products & stock"


@admin.register(ProductSize)
class ProductSizeAdmin(admin.ModelAdmin):
    """One grid of every size with an editable stock box: change the numbers, click Save."""
    list_display = ("product", "size", "stock_quantity")
    list_display_links = ("size",)
    list_editable = ("stock_quantity",)
    list_filter = ("product",)
    search_fields = ("product__name", "size")
    list_per_page = 100

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("product").order_by("product__name", "id")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "stock_summary", "is_active", "created_at")
    list_editable = ("price", "is_active")
    search_fields = ("name",)
    inlines = [ProductSizeInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("size_stock")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("product", "product_name", "size", "quantity", "price_at_purchase")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("short_ref", "full_name", "phone_number", "status", "total_amount", "created_at", "expires_at")
    list_filter = ("status",)
    search_fields = ("full_name", "phone_number", "id")
    inlines = [OrderItemInline]
    actions = ["mark_shipped", "mark_completed", "cancel_and_return_stock"]
    readonly_fields = [f.name for f in Order._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False          # deleting would bypass stock accounting

    @admin.action(description="Mark VERIFIED orders as Shipped")
    def mark_shipped(self, request, queryset):
        n = queryset.filter(status=Order.Status.VERIFIED).update(status=Order.Status.SHIPPED)
        self.message_user(request, f"{n} order(s) marked Shipped.", messages.SUCCESS)

    @admin.action(description="Mark SHIPPED orders as Completed")
    def mark_completed(self, request, queryset):
        n = queryset.filter(status=Order.Status.SHIPPED).update(status=Order.Status.COMPLETED)
        self.message_user(request, f"{n} order(s) marked Completed.", messages.SUCCESS)


@admin.action(description="CANCEL selected orders and RETURN their stock (not for Completed orders)")
def _cancel_action(modeladmin, request, queryset):
    done = sum(1 for pk in queryset.values_list("pk", flat=True)
               if services.cancel_order(pk, user=request.user, ip=client_ip(request)))
    skipped = queryset.count() - done
    msg = f"{done} order(s) cancelled and stock returned."
    if skipped:
        msg += f" {skipped} skipped (already completed, expired or cancelled)."
    modeladmin.message_user(request, msg, messages.SUCCESS if done else messages.WARNING)


OrderAdmin.cancel_and_return_stock = _cancel_action


@admin.register(ScanLog)
class ScanLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "outcome", "order", "scanned_by", "ip_address")
    list_filter = ("outcome",)
    readonly_fields = [f.name for f in ScanLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Mixtape)
class MixtapeAdmin(admin.ModelAdmin):
    list_display = ("title", "dj_name", "is_active", "created_at")
    list_filter = ("is_active",)
