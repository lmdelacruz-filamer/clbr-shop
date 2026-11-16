from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from store.models import Product, ProductSize

IMG = Path(__file__).resolve().parents[2] / "static" / "store" / "img"

# !!! Names / prices / stock are PLACEHOLDERS. Stock below is PER SIZE. Edit it in the admin afterwards. !!!
SEED = [
    ("CULT TEE", "750.00", 5, "M,L,XL,XXL", "p-cult.jpg"),
    ("FIST TEE", "750.00", 5, "M,L,XL,XXL", "p-fist.jpg"),
    ("WEIGHTS TEE", "750.00", 5, "M,L,XL,XXL", "p-weights.jpg"),
    ("C DESIGN TEE", "750.00", 5, "M,L,XL,XXL", "p-cdesign.jpg"),
    ("CROSSHAIR SNAPBACK", "1500.00", 10, "ONE SIZE", "p-snapback.jpg"),
]


class Command(BaseCommand):
    help = "Create the starter CLBR products (stock per size) from the brand photos. Skips existing ones."

    def handle(self, *args, **options):
        made = 0
        for name, price, stock, sizes, img in SEED:
            product = Product.objects.filter(name=name).first()
            if product is None:
                product = Product(name=name, price=price)
                product.image.save(img, ContentFile((IMG / img).read_bytes()), save=False)
                product.save()
                made += 1
            for size in [x.strip() for x in sizes.split(",")]:
                ProductSize.objects.get_or_create(product=product, size=size, defaults={"stock_quantity": stock})
        self.stdout.write(self.style.SUCCESS(f"Created {made} product(s). Set the real stock per size in the admin."))
