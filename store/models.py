import hashlib
import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "XXXL"]


class Product(models.Model):
    name = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    image = models.ImageField(upload_to="products/")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def sizes_ordered(self):
        rows = list(self.size_stock.all())            # use prefetch_related("size_stock") in list views
        rows.sort(key=lambda r: (SIZE_ORDER.index(r.size) if r.size in SIZE_ORDER else 99, r.size))
        return rows

    @property
    def total_stock(self):
        return sum(r.stock_quantity for r in self.size_stock.all())

    @property
    def stock_summary(self):
        return "  ".join(f"{r.size}:{r.stock_quantity}" for r in self.sizes_ordered) or "no sizes set"

    def __str__(self):
        return self.name


class ProductSize(models.Model):
    """Stock for ONE size of ONE product. Set by the admin; deducted when an order is placed."""
    product = models.ForeignKey(Product, related_name="size_stock", on_delete=models.CASCADE)
    size = models.CharField(max_length=12, help_text="M, L, XL, XXL ... (caps: ONE SIZE)")
    # PositiveIntegerField adds a DB CHECK >= 0, so a size can never go negative.
    stock_quantity = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product", "size"], name="uniq_product_size")]
        ordering = ["id"]

    def save(self, *args, **kwargs):
        self.size = (self.size or "").strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} / {self.size}: {self.stock_quantity}"


class Order(models.Model):
    class Courier(models.TextChoices):
        JNT = "JNT", "J&T Express"
        LBC = "LBC", "LBC"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending Verification"
        VERIFIED = "VERIFIED", "Verified"
        SHIPPED = "SHIPPED", "Shipped"
        COMPLETED = "COMPLETED", "Completed"
        EXPIRED = "EXPIRED", "Expired (stock released)"
        CANCELLED = "CANCELLED", "Cancelled (stock returned)"
        RETURNED = "RETURNED", "Returned / refused (stock returned)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=120)
    phone_number = models.CharField(max_length=16)
    address_line = models.CharField(max_length=250)
    latitude = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)

    # SHA-256 hex digest of the secret token that lives only in the customer's QR code.
    # A leaked database therefore cannot be used to forge a valid QR.
    verification_hash = models.CharField(max_length=64, unique=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()           # stock hold deadline while PENDING
    terms_accepted_at = models.DateTimeField(null=True, blank=True)   # when the buyer ticked Terms + Privacy
    verified_at = models.DateTimeField(null=True, blank=True)
    courier = models.CharField(max_length=8, choices=Courier.choices, blank=True)
    tracking_number = models.CharField(max_length=40, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="verified_orders")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "expires_at"])]

    def __str__(self):
        return f"{self.short_ref} · {self.full_name}"

    @property
    def short_ref(self):
        return str(self.id)[:8].upper()

    @staticmethod
    def new_token():
        return secrets.token_urlsafe(32)          # 256 bits of entropy, 43 chars

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    product_name = models.CharField(max_length=120)                     # snapshot, survives renames
    size = models.CharField(max_length=12, blank=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)   # locked-in historical price

    def __str__(self):
        return f"{self.quantity} × {self.product_name} ({self.size})" if self.size else f"{self.quantity} × {self.product_name}"


class ScanLog(models.Model):
    """Append-only audit trail of every scanner action."""
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.SET_NULL, related_name="scan_logs")
    scanned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    outcome = models.CharField(max_length=16)       # invalid | pending | redeemed | expired | confirmed
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Mixtape(models.Model):
    """The mix shown in the CLBR Radio section. Audio streams from SoundCloud; we store only the link and the text around it."""
    title = models.CharField(max_length=120)
    dj_name = models.CharField("DJ / mixed by", max_length=80)
    soundcloud_url = models.URLField(max_length=300)
    tracklist = models.TextField(blank=True, max_length=8000,
                                 help_text="One track per line:  m:ss | Artist | Title")
    is_active = models.BooleanField(default=True, help_text="The newest active mixtape is the one shown on the site.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @classmethod
    def current(cls):
        return cls.objects.filter(is_active=True).first()

    def tracks(self):
        from .radio import parse_tracklist
        return parse_tracklist(self.tracklist)[0]

    def __str__(self):
        return self.title
