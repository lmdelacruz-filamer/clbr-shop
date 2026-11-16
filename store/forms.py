import re
import uuid
from decimal import Decimal
from io import BytesIO

from django import forms
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image, ImageOps

_PH_MOBILE = re.compile(r"^(?:\+?63|0)?(9\d{9})$")

# Rough bounding box of the Philippine archipelago (lat_min, lat_max, lng_min, lng_max).
# It is a coarse server-side guard; the browser additionally checks the reverse-geocoded country code.
PH_LAT = (4.5, 21.5)
PH_LNG = (116.0, 127.0)
PH_ONLY_MSG = "Delivery is only available within the Philippines."


class OrderForm(forms.Form):
    full_name = forms.CharField(min_length=2, max_length=120, strip=True)
    phone = forms.CharField(max_length=24)
    # Two parts so the rider always gets something findable. Combined they fit the 250-char column.
    address_detail = forms.CharField(
        min_length=5, max_length=90, strip=True,
        error_messages={"required": "Add your house no., street or landmark so the rider can find you.",
                        "min_length": "Add more detail: house no., street or a landmark (at least 5 characters)."})
    address = forms.CharField(
        min_length=10, max_length=155, strip=True,
        error_messages={"required": "Add your barangay, city and province.",
                        "min_length": "Add your barangay, city and province."})
    latitude = forms.FloatField(required=False, min_value=-90, max_value=90)
    longitude = forms.FloatField(required=False, min_value=-180, max_value=180)
    consent = forms.BooleanField(required=True, error_messages={"required": "Please accept the Terms and Privacy Notice to order."})

    def clean_phone(self):
        raw = re.sub(r"[\s\-().]", "", self.cleaned_data["phone"])
        m = _PH_MOBILE.match(raw)
        if not m:
            raise forms.ValidationError("Enter a valid PH mobile number, e.g. 0917 123 4567.")
        return "+63" + m.group(1)

    def clean(self):
        data = super().clean()
        lat, lng = data.get("latitude"), data.get("longitude")
        if (lat is None) != (lng is None):
            raise forms.ValidationError("Latitude and longitude must be sent together.")
        if lat is not None and not (PH_LAT[0] <= lat <= PH_LAT[1] and PH_LNG[0] <= lng <= PH_LNG[1]):
            raise forms.ValidationError(PH_ONLY_MSG)
        return data


def size_field_name(size):
    return "stock_" + re.sub(r"[^A-Z0-9]+", "_", size.upper())


class ProductEditForm(forms.Form):
    """Staff form to add / edit a shirt. A size is OFFERED when its stock box has a number (0 = sold out);
    leave the box EMPTY to not sell that size."""
    MAX_IMAGE_BYTES = 8 * 1024 * 1024

    name = forms.CharField(max_length=120, strip=True)
    price = forms.DecimalField(min_value=Decimal("1"), max_digits=10, decimal_places=2)
    image = forms.ImageField(required=False)
    is_active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.size_fields = []
        existing = {r.size: r.stock_quantity for r in product.size_stock.all()} if product else {}
        for size in settings.SIZE_OPTIONS:
            name = size_field_name(size)
            self.fields[name] = forms.IntegerField(required=False, min_value=0, max_value=9999)
            if product is not None and not self.is_bound:
                self.initial[name] = existing.get(size)
            # The number the staff member SAW when the page loaded. Only boxes they actually changed are saved,
            # so a stale form can never overwrite stock that customers have bought in the meantime.
            orig = existing.get(size) if not self.is_bound else self.data.get("orig_" + name, "")
            self.size_fields.append((size, self[name], "" if orig is None else orig))
        if product is not None and not self.is_bound:
            self.initial.update(name=product.name, price=product.price, is_active=product.is_active)
        self.extra_sizes = sorted(set(existing) - set(settings.SIZE_OPTIONS))      # e.g. "S" added via Django admin

    def clean_image(self):
        f = self.cleaned_data.get("image")
        if not f:
            return None
        if f.size > self.MAX_IMAGE_BYTES:
            raise forms.ValidationError("Photo is too big (max 8 MB).")
        img = f.image                                  # already verified by Django's ImageField
        if img.format not in ("JPEG", "PNG", "WEBP"):
            raise forms.ValidationError("Use a JPG, PNG or WEBP photo.")
        if img.width * img.height > 60_000_000:
            raise forms.ValidationError("Photo has too many pixels. Please use a smaller one.")
        f.seek(0)
        im = ImageOps.exif_transpose(Image.open(f))     # fix phone rotation
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            flat = Image.new("RGB", im.size, (10, 10, 10))
            flat.paste(im, mask=im.split()[-1])
            im = flat
        else:
            im = im.convert("RGB")
        im.thumbnail((1400, 1400))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=85, optimize=True, progressive=True)    # re-encoding also strips EXIF/GPS data
        return ContentFile(buf.getvalue(), name=f"{uuid.uuid4().hex[:12]}.jpg")

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        chosen = {s: data.get(size_field_name(s)) for s in settings.SIZE_OPTIONS}
        chosen = {s: n for s, n in chosen.items() if n is not None}
        if not chosen and not (self.product and self.extra_sizes):
            raise forms.ValidationError("Enter a stock number for at least one size (use 0 for sold out).")
        if "ONE SIZE" in chosen and len(chosen) > 1:
            raise forms.ValidationError("ONE SIZE (caps) can't be combined with M / L / XL / XXL.")
        if self.product is None and not data.get("image"):
            raise forms.ValidationError("Please upload a photo of the shirt.")
        data["_sizes"] = chosen
        return data

    @transaction.atomic
    def save(self):
        from .models import Product, ProductSize
        d = self.cleaned_data
        if self.product is None:
            product = Product(name=d["name"], price=d["price"], is_active=d["is_active"])
            product.image.save(d["image"].name, d["image"], save=False)
            product.save()
        else:
            product = Product.objects.select_for_update().get(pk=self.product.pk)
            product.name, product.price, product.is_active = d["name"], d["price"], d["is_active"]
            if d.get("image"):
                product.image.save(d["image"].name, d["image"], save=False)
            product.save()
        rows = {r.size: r for r in ProductSize.objects.select_for_update().filter(product=product)}
        for size in settings.SIZE_OPTIONS:
            qty = d["_sizes"].get(size)
            raw = self.data.get("orig_" + size_field_name(size))
            if raw is not None:                        # form tells us what the box showed at page load
                try:
                    seen = int(raw) if str(raw).strip() != "" else None
                except ValueError:
                    seen = "?"
                if seen == qty:
                    continue                           # untouched box: leave live stock alone
            if qty is None:
                if size in rows:
                    rows[size].delete()                # box emptied = stop selling this size
            elif size in rows:
                rows[size].stock_quantity = qty
                rows[size].save(update_fields=["stock_quantity"])
            else:
                ProductSize.objects.create(product=product, size=size, stock_quantity=qty)
        return product


class MixtapeForm(forms.ModelForm):
    """Staff form for the CLBR Radio mix. The link is cleaned to a bare SoundCloud URL; the tracklist is checked line by line."""
    class Meta:
        from .models import Mixtape as _M
        model = _M
        fields = ["title", "dj_name", "soundcloud_url", "tracklist", "is_active"]
        widgets = {"tracklist": forms.Textarea(attrs={"rows": 10})}

    def clean_title(self):
        return self.cleaned_data["title"].strip()

    def clean_dj_name(self):
        return self.cleaned_data["dj_name"].strip()

    def clean_soundcloud_url(self):
        from .radio import clean_soundcloud_url
        try:
            return clean_soundcloud_url(self.cleaned_data["soundcloud_url"])
        except ValueError as e:
            raise forms.ValidationError(str(e))

    def clean_tracklist(self):
        from .radio import parse_tracklist
        text = self.cleaned_data.get("tracklist", "").strip()
        _, errors = parse_tracklist(text)
        if errors:
            raise forms.ValidationError(errors[:5])
        return text
